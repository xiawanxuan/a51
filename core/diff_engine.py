from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from .metadata_collector import SchemaInfo, TableInfo, ColumnInfo, IndexInfo
from .config_manager import ConfigManager
from .logger import SyncLogger


class DiffType(Enum):
    TABLE_ADD = 'table_add'
    TABLE_DROP = 'table_drop'
    TABLE_MODIFY = 'table_modify'
    COLUMN_ADD = 'column_add'
    COLUMN_DROP = 'column_drop'
    COLUMN_MODIFY = 'column_modify'
    INDEX_ADD = 'index_add'
    INDEX_DROP = 'index_drop'
    INDEX_MODIFY = 'index_modify'
    PK_CHANGE = 'pk_change'


@dataclass
class ColumnDiff:
    diff_type: DiffType
    table_name: str
    column_name: str
    source_column: Optional[ColumnInfo] = None
    target_column: Optional[ColumnInfo] = None
    changed_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'diff_type': self.diff_type.value,
            'table_name': self.table_name,
            'column_name': self.column_name,
            'source_column': self.source_column.to_dict() if self.source_column else None,
            'target_column': self.target_column.to_dict() if self.target_column else None,
            'changed_fields': self.changed_fields,
        }


@dataclass
class IndexDiff:
    diff_type: DiffType
    table_name: str
    index_name: str
    source_index: Optional[IndexInfo] = None
    target_index: Optional[IndexInfo] = None
    changed_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'diff_type': self.diff_type.value,
            'table_name': self.table_name,
            'index_name': self.index_name,
            'source_index': self.source_index.to_dict() if self.source_index else None,
            'target_index': self.target_index.to_dict() if self.target_index else None,
            'changed_fields': self.changed_fields,
        }


@dataclass
class TableDiff:
    diff_type: DiffType
    table_name: str
    source_table: Optional[TableInfo] = None
    target_table: Optional[TableInfo] = None
    column_diffs: List[ColumnDiff] = field(default_factory=list)
    index_diffs: List[IndexDiff] = field(default_factory=list)
    pk_changed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'diff_type': self.diff_type.value,
            'table_name': self.table_name,
            'column_diffs': [cd.to_dict() for cd in self.column_diffs],
            'index_diffs': [id_.to_dict() for id_ in self.index_diffs],
            'pk_changed': self.pk_changed,
        }


@dataclass
class SchemaDiff:
    source_db_type: str
    target_db_type: str
    table_diffs: List[TableDiff] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        s = {
            '新增表': 0,
            '删除表': 0,
            '变更表': 0,
            '新增字段': 0,
            '删除字段': 0,
            '修改字段': 0,
            '新增索引': 0,
            '删除索引': 0,
            '修改索引': 0,
            '主键变更': 0,
        }
        for td in self.table_diffs:
            if td.diff_type == DiffType.TABLE_ADD:
                s['新增表'] += 1
            elif td.diff_type == DiffType.TABLE_DROP:
                s['删除表'] += 1
            else:
                s['变更表'] += 1
            for cd in td.column_diffs:
                if cd.diff_type == DiffType.COLUMN_ADD:
                    s['新增字段'] += 1
                elif cd.diff_type == DiffType.COLUMN_DROP:
                    s['删除字段'] += 1
                elif cd.diff_type == DiffType.COLUMN_MODIFY:
                    s['修改字段'] += 1
            for id_ in td.index_diffs:
                if id_.diff_type == DiffType.INDEX_ADD:
                    s['新增索引'] += 1
                elif id_.diff_type == DiffType.INDEX_DROP:
                    s['删除索引'] += 1
                elif id_.diff_type == DiffType.INDEX_MODIFY:
                    s['修改索引'] += 1
            if td.pk_changed:
                s['主键变更'] += 1
        return s

    def to_dict(self) -> Dict[str, Any]:
        return {
            'source_db_type': self.source_db_type,
            'target_db_type': self.target_db_type,
            'summary': self.summary(),
            'table_diffs': [td.to_dict() for td in self.table_diffs],
        }


class DiffEngine:
    def __init__(self, config: ConfigManager, logger: SyncLogger):
        self.config = config
        self.logger = logger
        self.case_sensitive = self.config.rules.get('rules', {}).get('case_sensitive', False)

    def _normalize_name(self, name: str) -> str:
        return name if self.case_sensitive else name.lower()

    def _match_name(self, a: str, b: str) -> bool:
        return self._normalize_name(a) == self._normalize_name(b)

    def _normalize_data_type(self, db_type: str, data_type: str) -> str:
        mapping = self.config.get_type_mapping(db_type)
        dt_lower = data_type.lower()
        if dt_lower in mapping:
            return mapping[dt_lower].lower()
        return dt_lower

    def compare(self, source: SchemaInfo, target: SchemaInfo) -> SchemaDiff:
        self.logger.step_start('DIFF_COMPARE')
        self._source_db_type = source.db_type
        self._target_db_type = target.db_type
        diff = SchemaDiff(source_db_type=source.db_type, target_db_type=target.db_type)

        source_tables = {self._normalize_name(n): t for n, t in source.tables.items()}
        target_tables = {self._normalize_name(n): t for n, t in target.tables.items()}

        all_table_names = set(source_tables.keys()) | set(target_tables.keys())

        for norm_name in sorted(all_table_names):
            src_tbl = source_tables.get(norm_name)
            tgt_tbl = target_tables.get(norm_name)

            if src_tbl and not tgt_tbl:
                self.logger.info(f'发现新增表: {src_tbl.name}', step='DIFF_COMPARE')
                diff.table_diffs.append(TableDiff(
                    diff_type=DiffType.TABLE_ADD,
                    table_name=src_tbl.name,
                    source_table=src_tbl,
                ))
            elif tgt_tbl and not src_tbl:
                if self.config.should_drop_missing_tables():
                    self.logger.info(f'发现待删除表: {tgt_tbl.name}', step='DIFF_COMPARE')
                    diff.table_diffs.append(TableDiff(
                        diff_type=DiffType.TABLE_DROP,
                        table_name=tgt_tbl.name,
                        target_table=tgt_tbl,
                    ))
                else:
                    self.logger.debug(f'跳过删除表(配置禁用): {tgt_tbl.name}', step='DIFF_COMPARE')
            else:
                table_diff = self._compare_table(src_tbl, tgt_tbl)
                if table_diff.column_diffs or table_diff.index_diffs or table_diff.pk_changed:
                    self.logger.info(
                        f'表 {src_tbl.name} 有差异: {len(table_diff.column_diffs)} 字段差异, '
                        f'{len(table_diff.index_diffs)} 索引差异, PK变更={table_diff.pk_changed}',
                        step='DIFF_COMPARE'
                    )
                    diff.table_diffs.append(table_diff)

        self.logger.diff_summary(diff.summary(), step='DIFF_COMPARE')
        self.logger.step_success('DIFF_COMPARE')
        return diff

    def _compare_table(self, source: TableInfo, target: TableInfo) -> TableDiff:
        table_diff = TableDiff(
            diff_type=DiffType.TABLE_MODIFY,
            table_name=source.name,
            source_table=source,
            target_table=target,
        )

        src_cols = {self._normalize_name(n): c for n, c in source.columns.items()}
        tgt_cols = {self._normalize_name(n): c for n, c in target.columns.items()}
        all_col_names = set(src_cols.keys()) | set(tgt_cols.keys())

        for norm_name in all_col_names:
            src_col = src_cols.get(norm_name)
            tgt_col = tgt_cols.get(norm_name)
            if src_col and not tgt_col:
                self.logger.debug(f'新增字段: {source.name}.{src_col.name}', step='DIFF_COMPARE')
                table_diff.column_diffs.append(ColumnDiff(
                    diff_type=DiffType.COLUMN_ADD,
                    table_name=source.name,
                    column_name=src_col.name,
                    source_column=src_col,
                ))
            elif tgt_col and not src_col:
                if self.config.should_drop_missing_columns():
                    self.logger.debug(f'删除字段: {source.name}.{tgt_col.name}', step='DIFF_COMPARE')
                    table_diff.column_diffs.append(ColumnDiff(
                        diff_type=DiffType.COLUMN_DROP,
                        table_name=source.name,
                        column_name=tgt_col.name,
                        target_column=tgt_col,
                    ))
            else:
                col_diff = self._compare_column(source.name, src_col, tgt_col)
                if col_diff:
                    table_diff.column_diffs.append(col_diff)

        src_idx = {self._normalize_name(n): i for n, i in source.indexes.items()}
        tgt_idx = {self._normalize_name(n): i for n, i in target.indexes.items()}
        all_idx_names = set(src_idx.keys()) | set(tgt_idx.keys())

        for norm_name in all_idx_names:
            src_i = src_idx.get(norm_name)
            tgt_i = tgt_idx.get(norm_name)

            if src_i and src_i.is_primary:
                continue
            if tgt_i and tgt_i.is_primary:
                if not (src_i and src_i.is_primary):
                    if self.config.should_drop_missing_indexes():
                        self.logger.debug(
                            f'跳过主键索引(由pk_changed处理): {source.name}.{tgt_i.name}',
                            step='DIFF_COMPARE'
                        )
                    continue

            if src_i and not tgt_i:
                src_cols_key = tuple(sorted([self._normalize_name(c) for c in src_i.columns]))
                found_same_content = False
                for t_norm, t_existing in tgt_idx.items():
                    if t_existing.is_primary:
                        continue
                    t_cols_key = tuple(sorted([self._normalize_name(c) for c in t_existing.columns]))
                    if t_cols_key == src_cols_key and t_existing.is_unique == src_i.is_unique:
                        found_same_content = True
                        self.logger.debug(
                            f'索引重命名检测: 源[{src_i.name}] 目标[{t_existing.name}], '
                            f'列和唯一性一致,判定为修改',
                            step='DIFF_COMPARE'
                        )
                        idx_diff = self._compare_index(source.name, src_i, t_existing)
                        if idx_diff is None:
                            idx_diff = IndexDiff(
                                diff_type=DiffType.INDEX_MODIFY,
                                table_name=source.name,
                                index_name=src_i.name,
                                source_index=src_i,
                                target_index=t_existing,
                                changed_fields=['name'],
                            )
                        table_diff.index_diffs.append(idx_diff)
                        del tgt_idx[t_norm]
                        break
                if not found_same_content:
                    self.logger.debug(f'新增索引: {source.name}.{src_i.name}', step='DIFF_COMPARE')
                    table_diff.index_diffs.append(IndexDiff(
                        diff_type=DiffType.INDEX_ADD,
                        table_name=source.name,
                        index_name=src_i.name,
                        source_index=src_i,
                    ))
            elif tgt_i and not src_i:
                tgt_cols_key = tuple(sorted([self._normalize_name(c) for c in tgt_i.columns]))
                matched_by_content = False
                for s_norm, s_existing in src_idx.items():
                    if s_existing.is_primary:
                        continue
                    s_cols_key = tuple(sorted([self._normalize_name(c) for c in s_existing.columns]))
                    if s_cols_key == tgt_cols_key and s_existing.is_unique == tgt_i.is_unique:
                        matched_by_content = True
                        break
                if matched_by_content:
                    continue
                if self.config.should_drop_missing_indexes():
                    self.logger.debug(f'删除索引: {source.name}.{tgt_i.name}', step='DIFF_COMPARE')
                    table_diff.index_diffs.append(IndexDiff(
                        diff_type=DiffType.INDEX_DROP,
                        table_name=source.name,
                        index_name=tgt_i.name,
                        target_index=tgt_i,
                    ))
                else:
                    self.logger.debug(
                        f'跳过删除索引(配置禁用): {source.name}.{tgt_i.name}',
                        step='DIFF_COMPARE'
                    )
            else:
                idx_diff = self._compare_index(source.name, src_i, tgt_i)
                if idx_diff:
                    table_diff.index_diffs.append(idx_diff)

        src_pk = [self._normalize_name(c) for c in source.primary_key_columns]
        tgt_pk = [self._normalize_name(c) for c in target.primary_key_columns]
        if src_pk != tgt_pk:
            self.logger.debug(f'主键变更: {source.name}', step='DIFF_COMPARE')
            table_diff.pk_changed = True

        return table_diff

    def _compare_column(self, table_name: str, source: ColumnInfo, target: ColumnInfo) -> Optional[ColumnDiff]:
        changed_fields = []

        src_type = self._normalize_data_type(self._source_db_type, source.data_type)
        tgt_type = self._normalize_data_type(self._target_db_type, target.data_type)
        if self.config.should_modify_column_type() and src_type != tgt_type:
            changed_fields.append('data_type')

        if source.nullable != target.nullable:
            changed_fields.append('nullable')

        src_default = source.default or ''
        tgt_default = target.default or ''
        if src_default != tgt_default and 'nextval' not in src_default and 'nextval' not in tgt_default:
            changed_fields.append('default')

        if source.character_maximum_length != target.character_maximum_length:
            changed_fields.append('character_maximum_length')

        if source.numeric_precision != target.numeric_precision:
            changed_fields.append('numeric_precision')

        if source.numeric_scale != target.numeric_scale:
            changed_fields.append('numeric_scale')

        if changed_fields:
            self.logger.debug(
                f'字段变更: {table_name}.{source.name}, 变更字段: {changed_fields}',
                step='DIFF_COMPARE'
            )
            return ColumnDiff(
                diff_type=DiffType.COLUMN_MODIFY,
                table_name=table_name,
                column_name=source.name,
                source_column=source,
                target_column=target,
                changed_fields=changed_fields,
            )
        return None

    def _compare_index(self, table_name: str, source: IndexInfo, target: IndexInfo) -> Optional[IndexDiff]:
        changed_fields = []

        src_cols = [self._normalize_name(c) for c in source.columns]
        tgt_cols = [self._normalize_name(c) for c in target.columns]
        if src_cols != tgt_cols:
            changed_fields.append('columns')
        else:
            for i, sc in enumerate(source.columns):
                if not self._match_name(sc, target.columns[i]):
                    changed_fields.append('columns')
                    break

        if source.is_unique != target.is_unique:
            changed_fields.append('is_unique')

        if source.is_primary != target.is_primary:
            changed_fields.append('is_primary')

        src_idx_type = (source.index_type or '').lower()
        tgt_idx_type = (target.index_type or '').lower()
        if src_idx_type and tgt_idx_type and src_idx_type != tgt_idx_type:
            compatible_types = {
                ('btree', ''), ('', 'btree'),
                ('btree', 'b-tree'), ('b-tree', 'btree'),
            }
            if (src_idx_type, tgt_idx_type) not in compatible_types:
                changed_fields.append('index_type')

        if changed_fields:
            self.logger.debug(
                f'索引变更: {table_name}.{source.name}, 变更字段: {changed_fields}',
                step='DIFF_COMPARE'
            )
            return IndexDiff(
                diff_type=DiffType.INDEX_MODIFY,
                table_name=table_name,
                index_name=source.name,
                source_index=source,
                target_index=target,
                changed_fields=changed_fields,
            )
        return None
