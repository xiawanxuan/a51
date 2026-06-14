from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from .metadata_collector import (
    SchemaInfo, TableInfo, ForeignKeyInfo, PartitionInfo, TriggerInfo
)
from .diff_engine import SchemaDiff, TableDiff, ColumnDiff, DiffType
from .config_manager import ConfigManager
from .logger import SyncLogger


class IssueLevel(Enum):
    ERROR = 'ERROR'
    WARNING = 'WARNING'


class IssueCategory(Enum):
    FOREIGN_KEY = 'FOREIGN_KEY'
    PARTITION = 'PARTITION'
    TRIGGER = 'TRIGGER'
    GENERAL = 'GENERAL'


@dataclass
class CheckIssue:
    level: IssueLevel
    category: IssueCategory
    message: str
    table_name: str = ''
    object_name: str = ''
    detail: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'level': self.level.value,
            'category': self.category.value,
            'table_name': self.table_name,
            'object_name': self.object_name,
            'message': self.message,
            'detail': self.detail,
        }


@dataclass
class DependencyCheckResult:
    issues: List[CheckIssue] = field(default_factory=list)
    passed: bool = True

    @property
    def errors(self) -> List[CheckIssue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    @property
    def warnings(self) -> List[CheckIssue]:
        return [i for i in self.issues if i.level == IssueLevel.WARNING]

    def add(self, issue: CheckIssue):
        self.issues.append(issue)
        if issue.level == IssueLevel.ERROR:
            self.passed = False

    def summary(self) -> Dict[str, Any]:
        return {
            'passed': self.passed,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'issues': [i.to_dict() for i in self.issues],
        }


class DependencyChecker:
    def __init__(self, config: ConfigManager, logger: SyncLogger):
        self.config = config
        self.logger = logger

    def _norm(self, name: str) -> str:
        case_sensitive = self.config.rules.get('rules', {}).get('case_sensitive', False)
        return name if case_sensitive else name.lower()

    def _collect_diff_tables(self, schema_diff: SchemaDiff) -> Dict[str, Dict[str, Any]]:
        info: Dict[str, Dict[str, Any]] = {}
        for td in schema_diff.table_diffs:
            tbl_key = self._norm(td.table_name)
            info[tbl_key] = {
                'diff_type': td.diff_type,
                'table_diff': td,
                'added_columns': set(),
                'dropped_columns': set(),
                'modified_columns': set(),
            }
            for cd in td.column_diffs:
                col_key = self._norm(cd.column_name)
                if cd.diff_type == DiffType.COLUMN_ADD:
                    info[tbl_key]['added_columns'].add(col_key)
                elif cd.diff_type == DiffType.COLUMN_DROP:
                    info[tbl_key]['dropped_columns'].add(col_key)
                elif cd.diff_type == DiffType.COLUMN_MODIFY:
                    info[tbl_key]['modified_columns'].add(col_key)
        return info

    def run(self, source_schema: SchemaInfo, target_schema: SchemaInfo,
            schema_diff: SchemaDiff) -> DependencyCheckResult:
        self.logger.step_start('DEPENDENCY_CHECK')
        result = DependencyCheckResult()

        if not self.config.is_dependency_check_enabled():
            self.logger.info('依赖校验已禁用（配置 dependency_check.enabled=false）', step='DEPENDENCY_CHECK')
            self.logger.step_success('DEPENDENCY_CHECK')
            return result

        diff_tables = self._collect_diff_tables(schema_diff)

        if self.config.should_check_foreign_keys():
            self._check_foreign_keys(source_schema, target_schema, schema_diff, diff_tables, result)
        if self.config.should_check_partitions():
            self._check_partitions(source_schema, target_schema, schema_diff, diff_tables, result)
        if self.config.should_check_triggers():
            self._check_triggers(source_schema, target_schema, schema_diff, diff_tables, result)

        summary = result.summary()
        self.logger.info(
            f'依赖校验完成: 通过={summary["passed"]}, '
            f'错误={summary["error_count"]}, 警告={summary["warning_count"]}',
            step='DEPENDENCY_CHECK'
        )
        for issue in result.issues:
            prefix = '❌ ERROR' if issue.level == IssueLevel.ERROR else '⚠️  WARNING'
            self.logger.info(
                f'{prefix} [{issue.category.value}] '
                f'{issue.table_name + "." if issue.table_name else ""}{issue.object_name}: '
                f'{issue.message}',
                step='DEPENDENCY_CHECK'
            )
            if issue.detail:
                self.logger.debug(f'  详情: {issue.detail}', step='DEPENDENCY_CHECK')

        if not result.passed:
            self.logger.step_failed('DEPENDENCY_CHECK')
            if self.config.is_dependency_check_blocking():
                self.logger.error('依赖校验未通过，已阻止后续同步操作（可通过 dependency_check.blocking=false 关闭）',
                                  step='DEPENDENCY_CHECK')
        else:
            self.logger.step_success('DEPENDENCY_CHECK')

        return result

    def _check_foreign_keys(self, source_schema: SchemaInfo, target_schema: SchemaInfo,
                             schema_diff: SchemaDiff, diff_tables: Dict[str, Dict[str, Any]],
                             result: DependencyCheckResult):
        self.logger.debug('开始检查外键依赖...', step='DEPENDENCY_CHECK')

        target_tables_norm = {self._norm(n): t for n, t in target_schema.tables.items()}
        source_tables_norm = {self._norm(n): t for n, t in source_schema.tables.items()}
        added_tables: Set[str] = set()
        dropped_tables: Set[str] = set()
        for tbl_key, info in diff_tables.items():
            if info['diff_type'] == DiffType.TABLE_ADD:
                added_tables.add(tbl_key)
            elif info['diff_type'] == DiffType.TABLE_DROP:
                dropped_tables.add(tbl_key)

        for tbl_key, info in diff_tables.items():
            td: TableDiff = info['table_diff']
            if info['diff_type'] == DiffType.TABLE_DROP:
                self._check_fk_refs_to_dropped_table(
                    td.table_name, target_schema, source_schema, result
                )
                continue

            if info['diff_type'] == DiffType.TABLE_ADD:
                src_tbl = td.source_table
                if src_tbl:
                    self._check_new_table_fk_refs(
                        td.table_name, src_tbl, target_tables_norm,
                        source_tables_norm, added_tables, result
                    )

            dropped_cols = info['dropped_columns']
            modified_cols = info['modified_columns']
            if dropped_cols or modified_cols:
                tgt_tbl = target_schema.tables.get(td.table_name) or source_schema.tables.get(td.table_name)
                if tgt_tbl:
                    self._check_fk_refs_to_modified_columns(
                        td.table_name, dropped_cols, modified_cols,
                        target_schema, source_schema, result
                    )

    def _check_fk_refs_to_dropped_table(self, table_name: str, target_schema: SchemaInfo,
                                         source_schema: SchemaInfo, result: DependencyCheckResult):
        tbl_key = self._norm(table_name)
        for other_name, other_tbl in target_schema.tables.items():
            if self._norm(other_name) == tbl_key:
                continue
            for fk in other_tbl.foreign_keys.values():
                if self._norm(fk.ref_table) == tbl_key:
                    result.add(CheckIssue(
                        level=IssueLevel.ERROR,
                        category=IssueCategory.FOREIGN_KEY,
                        table_name=other_name,
                        object_name=fk.name,
                        message=f'待删除表 {table_name} 被表 {other_name} 的外键 {fk.name} 引用',
                        detail=f'外键列: {fk.columns} -> {fk.ref_table}.{fk.ref_columns}',
                    ))
        for other_name, other_tbl in source_schema.tables.items():
            if self._norm(other_name) == tbl_key:
                continue
            for fk in other_tbl.foreign_keys.values():
                if self._norm(fk.ref_table) == tbl_key:
                    result.add(CheckIssue(
                        level=IssueLevel.WARNING,
                        category=IssueCategory.FOREIGN_KEY,
                        table_name=other_name,
                        object_name=fk.name,
                        message=f'源库表 {table_name} 被表 {other_name} 的外键 {fk.name} 引用，同步后需重建或验证',
                        detail=f'外键列: {fk.columns} -> {fk.ref_table}.{fk.ref_columns}',
                    ))

    def _check_new_table_fk_refs(self, table_name: str, src_tbl: TableInfo,
                                  target_tables_norm: Dict[str, TableInfo],
                                  source_tables_norm: Dict[str, TableInfo],
                                  added_tables: Set[str], result: DependencyCheckResult):
        for fk in src_tbl.foreign_keys.values():
            ref_key = self._norm(fk.ref_table)
            if ref_key in target_tables_norm or ref_key in added_tables:
                continue
            if ref_key in source_tables_norm:
                result.add(CheckIssue(
                    level=IssueLevel.WARNING,
                    category=IssueCategory.FOREIGN_KEY,
                    table_name=table_name,
                    object_name=fk.name,
                    message=f'新表 {table_name} 的外键 {fk.name} 引用了目标库不存在的表 {fk.ref_table}',
                    detail=f'该表仅存在于源库，建议同步该表或手动处理外键约束',
                ))
            else:
                result.add(CheckIssue(
                    level=IssueLevel.ERROR,
                    category=IssueCategory.FOREIGN_KEY,
                    table_name=table_name,
                    object_name=fk.name,
                    message=f'新表 {table_name} 的外键 {fk.name} 引用了不存在的表 {fk.ref_table}',
                    detail=f'外键引用目标在源库和目标库均不存在，请检查表定义',
                ))

    def _check_fk_refs_to_modified_columns(self, table_name: str, dropped_cols: Set[str],
                                            modified_cols: Set[str], target_schema: SchemaInfo,
                                            source_schema: SchemaInfo, result: DependencyCheckResult):
        tbl_key = self._norm(table_name)

        for schema in [target_schema, source_schema]:
            is_target = (schema is target_schema)
            for other_name, other_tbl in schema.tables.items():
                if self._norm(other_name) == tbl_key:
                    continue
                for fk in other_tbl.foreign_keys.values():
                    if self._norm(fk.ref_table) != tbl_key:
                        continue
                    for ref_col in fk.ref_columns:
                        ref_col_key = self._norm(ref_col)
                        if ref_col_key in dropped_cols:
                            level = IssueLevel.ERROR if is_target else IssueLevel.WARNING
                            result.add(CheckIssue(
                                level=level,
                                category=IssueCategory.FOREIGN_KEY,
                                table_name=other_name,
                                object_name=fk.name,
                                message=f'{("目标库" if is_target else "源库")}表 {other_name} 的外键 {fk.name} '
                                        f'引用了 {table_name} 待删除列 {ref_col}',
                                detail=f'外键列: {fk.columns} -> {table_name}.{fk.ref_columns}',
                            ))
                        elif ref_col_key in modified_cols:
                            level = IssueLevel.WARNING
                            result.add(CheckIssue(
                                level=level,
                                category=IssueCategory.FOREIGN_KEY,
                                table_name=other_name,
                                object_name=fk.name,
                                message=f'{("目标库" if is_target else "源库")}表 {other_name} 的外键 {fk.name} '
                                        f'引用了 {table_name} 被修改的列 {ref_col}，需验证类型兼容性',
                                detail=f'外键列: {fk.columns} -> {table_name}.{fk.ref_columns}',
                            ))

    def _check_partitions(self, source_schema: SchemaInfo, target_schema: SchemaInfo,
                          schema_diff: SchemaDiff, diff_tables: Dict[str, Dict[str, Any]],
                          result: DependencyCheckResult):
        self.logger.debug('开始检查分区依赖...', step='DEPENDENCY_CHECK')

        for tbl_key, info in diff_tables.items():
            td: TableDiff = info['table_diff']
            src_tbl = td.source_table or source_schema.tables.get(td.table_name)
            tgt_tbl = td.target_table or target_schema.tables.get(td.table_name)

            if info['diff_type'] == DiffType.TABLE_DROP and tgt_tbl and tgt_tbl.is_partitioned:
                result.add(CheckIssue(
                    level=IssueLevel.ERROR,
                    category=IssueCategory.PARTITION,
                    table_name=td.table_name,
                    object_name=td.table_name,
                    message=f'分区父表 {td.table_name} 待删除，请先删除所有子分区表',
                    detail=f'子分区: {[p.name for p in tgt_tbl.partitions]}',
                ))

            if tgt_tbl and tgt_tbl.parent_table:
                parent_key = self._norm(tgt_tbl.parent_table)
                if parent_key in diff_tables and diff_tables[parent_key]['diff_type'] == DiffType.TABLE_DROP:
                    result.add(CheckIssue(
                        level=IssueLevel.ERROR,
                        category=IssueCategory.PARTITION,
                        table_name=td.table_name,
                        object_name=td.table_name,
                        message=f'分区子表 {td.table_name} 依赖的父表 {tgt_tbl.parent_table} 待删除',
                        detail='请调整同步顺序或同时处理父子分区表',
                    ))

            if info['diff_type'] in (DiffType.TABLE_MODIFY, DiffType.TABLE_ADD) and src_tbl:
                if src_tbl.is_partitioned or (tgt_tbl and tgt_tbl.is_partitioned):
                    dropped_cols = info['dropped_columns']
                    modified_cols = info['modified_columns']
                    all_tbl = tgt_tbl or src_tbl
                    partition_cols = set()
                    for p in all_tbl.partitions:
                        expr = (p.partition_expression or '').lower()
                        for col in all_tbl.columns:
                            if col.lower() in expr:
                                partition_cols.add(self._norm(col))
                    for pc in partition_cols:
                        if pc in dropped_cols:
                            result.add(CheckIssue(
                                level=IssueLevel.ERROR,
                                category=IssueCategory.PARTITION,
                                table_name=td.table_name,
                                object_name=td.table_name,
                                message=f'分区表 {td.table_name} 的分区键列被删除，将破坏分区结构',
                                detail=f'分区表达式涉及列: {list(partition_cols)}',
                            ))
                        elif pc in modified_cols:
                            result.add(CheckIssue(
                                level=IssueLevel.WARNING,
                                category=IssueCategory.PARTITION,
                                table_name=td.table_name,
                                object_name=td.table_name,
                                message=f'分区表 {td.table_name} 的分区键列被修改，请验证分区策略兼容性',
                                detail=f'分区表达式涉及列: {list(partition_cols)}',
                            ))

    def _check_triggers(self, source_schema: SchemaInfo, target_schema: SchemaInfo,
                        schema_diff: SchemaDiff, diff_tables: Dict[str, Dict[str, Any]],
                        result: DependencyCheckResult):
        self.logger.debug('开始检查触发器依赖...', step='DEPENDENCY_CHECK')

        for tbl_key, info in diff_tables.items():
            td: TableDiff = info['table_diff']
            src_tbl = td.source_table or source_schema.tables.get(td.table_name)
            tgt_tbl = td.target_table or target_schema.tables.get(td.table_name)

            if info['diff_type'] == DiffType.TABLE_DROP:
                if tgt_tbl and tgt_tbl.triggers:
                    result.add(CheckIssue(
                        level=IssueLevel.WARNING,
                        category=IssueCategory.TRIGGER,
                        table_name=td.table_name,
                        object_name=','.join(tgt_tbl.triggers.keys()),
                        message=f'待删除表 {td.table_name} 上存在 {len(tgt_tbl.triggers)} 个触发器，将一并删除',
                        detail=f'触发器列表: {list(tgt_tbl.triggers.keys())}',
                    ))
                continue

            dropped_cols = info['dropped_columns']
            for tbl in [tgt_tbl, src_tbl]:
                if not tbl:
                    continue
                is_target = (tbl is tgt_tbl)
                for tr_name, trigger in tbl.triggers.items():
                    stmt = (trigger.action_statement or '').lower()
                    for col_key in dropped_cols:
                        col_name = col_key
                        for orig_col in tbl.columns:
                            if self._norm(orig_col) == col_key:
                                col_name = orig_col
                                break
                        if col_name.lower() in stmt:
                            level = IssueLevel.ERROR if is_target else IssueLevel.WARNING
                            result.add(CheckIssue(
                                level=level,
                                category=IssueCategory.TRIGGER,
                                table_name=tbl.name,
                                object_name=tr_name,
                                message=f'{("目标库" if is_target else "源库")}触发器 {tr_name} 的逻辑中引用了待删除列 {col_name}',
                                detail=f'触发器事件: {trigger.action_timing} {trigger.event_manipulation}',
                            ))

            if src_tbl and td.diff_type in (DiffType.TABLE_ADD, DiffType.TABLE_MODIFY):
                for tr_name, trigger in src_tbl.triggers.items():
                    stmt = (trigger.action_statement or '').lower()
                    referenced_tables = set()
                    for other_name in source_schema.tables:
                        if self._norm(other_name) != self._norm(src_tbl.name) and other_name.lower() in stmt:
                            referenced_tables.add(other_name)
                    for ref_tbl in referenced_tables:
                        ref_key = self._norm(ref_tbl)
                        tgt_exists = ref_key in {self._norm(n) for n in target_schema.tables}
                        will_add = ref_key in diff_tables and diff_tables[ref_key]['diff_type'] == DiffType.TABLE_ADD
                        if not tgt_exists and not will_add:
                            result.add(CheckIssue(
                                level=IssueLevel.WARNING,
                                category=IssueCategory.TRIGGER,
                                table_name=src_tbl.name,
                                object_name=tr_name,
                                message=f'新触发器 {tr_name} 引用的表 {ref_tbl} 目标库不存在且不在本次同步范围内',
                                detail='建议手动确认触发器逻辑在目标库的兼容性',
                            ))
