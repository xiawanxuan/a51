import os
from datetime import datetime
from typing import List, Optional
from .db_adapter import DBConnection
from .metadata_collector import SchemaInfo, ColumnInfo, IndexInfo, TableInfo
from .diff_engine import SchemaDiff, TableDiff, ColumnDiff, IndexDiff, DiffType
from .config_manager import ConfigManager
from .logger import SyncLogger


class DDLGenerator:
    def __init__(self, target_db_type: str, config: ConfigManager, logger: SyncLogger):
        self.target_db_type = target_db_type
        self.config = config
        self.logger = logger

    def _quote(self, identifier: str) -> str:
        if self.target_db_type == 'mysql':
            return f'`{identifier}`'
        else:
            return f'"{identifier}"'

    def _map_type(self, source_db_type: str, data_type: str) -> str:
        mapping = self.config.get_type_mapping(source_db_type)
        dt_lower = data_type.lower()
        if dt_lower in mapping:
            return mapping[dt_lower]
        return data_type

    def _format_column_type(self, source_db_type: str, col: ColumnInfo) -> str:
        base_type = self._map_type(source_db_type, col.data_type)
        base_type_lower = base_type.lower()

        if base_type_lower in ('varchar', 'character varying', 'char'):
            if col.character_maximum_length:
                return f'{base_type}({col.character_maximum_length})'
            return base_type
        elif base_type_lower in ('decimal', 'numeric'):
            if col.numeric_precision and col.numeric_scale is not None:
                return f'{base_type}({col.numeric_precision},{col.numeric_scale})'
            elif col.numeric_precision:
                return f'{base_type}({col.numeric_precision})'
            return base_type
        return base_type

    def _format_column_default(self, col: ColumnInfo) -> Optional[str]:
        if col.default is None:
            return None
        default = str(col.default).strip()
        if self.target_db_type == 'mysql':
            if col.auto_increment:
                return None
        else:
            if col.auto_increment or 'nextval' in default:
                return None
        return default

    def _format_column_definition(self, source_db_type: str, col: ColumnInfo,
                                   include_default: bool = True) -> str:
        parts = [self._quote(col.name), self._format_column_type(source_db_type, col)]

        if not col.nullable:
            parts.append('NOT NULL')

        default = self._format_column_default(col) if include_default else None
        if default and not col.auto_increment:
            if self.target_db_type == 'mysql':
                parts.append(f'DEFAULT {default}')
            else:
                parts.append(f'DEFAULT {default}')

        if self.target_db_type == 'mysql' and col.auto_increment:
            parts.append('AUTO_INCREMENT')

        return ' '.join(parts)

    def generate_create_table(self, source_db_type: str, table: TableInfo) -> str:
        lines = []
        col_defs = []

        sorted_cols = sorted(table.columns.values(), key=lambda c: c.ordinal_position)
        for col in sorted_cols:
            col_defs.append(self._format_column_definition(source_db_type, col))

        pk_cols = [self._quote(c) for c in table.primary_key_columns]
        if pk_cols:
            pk_name = f'pk_{table.name}'
            if self.target_db_type == 'mysql':
                col_defs.append(f'PRIMARY KEY ({", ".join(pk_cols)})')
            else:
                col_defs.append(f'CONSTRAINT {self._quote(pk_name)} PRIMARY KEY ({", ".join(pk_cols)})')

        if self.target_db_type == 'mysql':
            lines.append(f'CREATE TABLE {self._quote(table.name)} (')
            lines.append('  ' + ',\n  '.join(col_defs))
            lines.append(') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;')
        else:
            lines.append(f'CREATE TABLE {self._quote(table.name)} (')
            lines.append('  ' + ',\n  '.join(col_defs))
            lines.append(');')

        return '\n'.join(lines)

    def generate_drop_table(self, table_name: str) -> str:
        return f'DROP TABLE {self._quote(table_name)};'

    def generate_add_column(self, source_db_type: str, table_name: str, col: ColumnInfo) -> str:
        col_def = self._format_column_definition(source_db_type, col)
        if self.target_db_type == 'mysql':
            return f'ALTER TABLE {self._quote(table_name)} ADD COLUMN {col_def};'
        else:
            return f'ALTER TABLE {self._quote(table_name)} ADD COLUMN {col_def};'

    def generate_drop_column(self, table_name: str, column_name: str) -> str:
        return f'ALTER TABLE {self._quote(table_name)} DROP COLUMN {self._quote(column_name)};'

    def generate_modify_column(self, source_db_type: str, table_name: str,
                                src_col: ColumnInfo, tgt_col: ColumnInfo,
                                changed_fields: List[str]) -> List[str]:
        stmts = []
        col_def = self._format_column_definition(source_db_type, src_col)

        if self.target_db_type == 'mysql':
            stmts.append(f'ALTER TABLE {self._quote(table_name)} MODIFY COLUMN {col_def};')
        else:
            for field in changed_fields:
                if field == 'data_type' or field == 'character_maximum_length' or \
                   field == 'numeric_precision' or field == 'numeric_scale':
                    col_type = self._format_column_type(source_db_type, src_col)
                    stmts.append(
                        f'ALTER TABLE {self._quote(table_name)} '
                        f'ALTER COLUMN {self._quote(src_col.name)} TYPE {col_type};'
                    )
                elif field == 'nullable':
                    if src_col.nullable:
                        stmts.append(
                            f'ALTER TABLE {self._quote(table_name)} '
                            f'ALTER COLUMN {self._quote(src_col.name)} DROP NOT NULL;'
                        )
                    else:
                        stmts.append(
                            f'ALTER TABLE {self._quote(table_name)} '
                            f'ALTER COLUMN {self._quote(src_col.name)} SET NOT NULL;'
                        )
                elif field == 'default':
                    default = self._format_column_default(src_col)
                    if default:
                        stmts.append(
                            f'ALTER TABLE {self._quote(table_name)} '
                            f'ALTER COLUMN {self._quote(src_col.name)} SET DEFAULT {default};'
                        )
                    else:
                        stmts.append(
                            f'ALTER TABLE {self._quote(table_name)} '
                            f'ALTER COLUMN {self._quote(src_col.name)} DROP DEFAULT;'
                        )

        return stmts

    def _format_index_columns(self, index: IndexInfo) -> str:
        return ', '.join([self._quote(c) for c in index.columns])

    def generate_create_index(self, table_name: str, index: IndexInfo) -> str:
        cols = self._format_index_columns(index)
        if index.is_primary:
            if self.target_db_type == 'mysql':
                return f'ALTER TABLE {self._quote(table_name)} ADD PRIMARY KEY ({cols});'
            else:
                pk_name = self._quote(f'pk_{table_name}')
                return f'ALTER TABLE {self._quote(table_name)} ADD CONSTRAINT {pk_name} PRIMARY KEY ({cols});'

        idx_name = self._quote(index.name)
        tbl = self._quote(table_name)
        if index.is_unique:
            return f'CREATE UNIQUE INDEX {idx_name} ON {tbl} ({cols});'
        else:
            return f'CREATE INDEX {idx_name} ON {tbl} ({cols});'

    def generate_drop_index(self, table_name: str, index: IndexInfo) -> str:
        if index.is_primary:
            if self.target_db_type == 'mysql':
                return f'ALTER TABLE {self._quote(table_name)} DROP PRIMARY KEY;'
            else:
                pk_name = self._quote(f'pk_{table_name}')
                return f'ALTER TABLE {self._quote(table_name)} DROP CONSTRAINT {pk_name};'

        if self.target_db_type == 'mysql':
            return f'DROP INDEX {self._quote(index.name)} ON {self._quote(table_name)};'
        else:
            return f'DROP INDEX {self._quote(index.name)};'

    def generate_all(self, schema_diff: SchemaDiff, source_db_type: str) -> List[str]:
        ddl_statements = []

        create_tables = []
        drop_tables = []
        alter_statements = []

        for table_diff in schema_diff.table_diffs:
            if table_diff.diff_type == DiffType.TABLE_ADD:
                create_tables.append((table_diff, self.generate_create_table(
                    source_db_type, table_diff.source_table
                )))
                for idx in table_diff.source_table.indexes.values():
                    if not idx.is_primary:
                        alter_statements.append(self.generate_create_index(
                            table_diff.table_name, idx
                        ))
            elif table_diff.diff_type == DiffType.TABLE_DROP:
                drop_tables.append(self.generate_drop_table(table_diff.table_name))
            else:
                if table_diff.pk_changed:
                    if table_diff.target_table.primary_key_columns:
                        alter_statements.append(self.generate_drop_index(
                            table_diff.table_name,
                            IndexInfo(name='PRIMARY', columns=table_diff.target_table.primary_key_columns,
                                       is_primary=True)
                        ))
                    if table_diff.source_table.primary_key_columns:
                        alter_statements.append(self.generate_create_index(
                            table_diff.table_name,
                            IndexInfo(name='PRIMARY', columns=table_diff.source_table.primary_key_columns,
                                       is_primary=True)
                        ))

                for idx_diff in table_diff.index_diffs:
                    if idx_diff.diff_type == DiffType.INDEX_DROP:
                        alter_statements.append(self.generate_drop_index(
                            table_diff.table_name, idx_diff.target_index
                        ))
                    elif idx_diff.diff_type == DiffType.INDEX_ADD:
                        alter_statements.append(self.generate_create_index(
                            table_diff.table_name, idx_diff.source_index
                        ))
                    elif idx_diff.diff_type == DiffType.INDEX_MODIFY:
                        alter_statements.append(self.generate_drop_index(
                            table_diff.table_name, idx_diff.target_index
                        ))
                        alter_statements.append(self.generate_create_index(
                            table_diff.table_name, idx_diff.source_index
                        ))

                for col_diff in table_diff.column_diffs:
                    if col_diff.diff_type == DiffType.COLUMN_ADD:
                        alter_statements.append(self.generate_add_column(
                            source_db_type, table_diff.table_name, col_diff.source_column
                        ))
                    elif col_diff.diff_type == DiffType.COLUMN_DROP:
                        alter_statements.append(self.generate_drop_column(
                            table_diff.table_name, col_diff.column_name
                        ))
                    elif col_diff.diff_type == DiffType.COLUMN_MODIFY:
                        stmts = self.generate_modify_column(
                            source_db_type, table_diff.table_name,
                            col_diff.source_column, col_diff.target_column,
                            col_diff.changed_fields
                        )
                        alter_statements.extend(stmts)

        for _, ddl in create_tables:
            ddl_statements.append(ddl)
        ddl_statements.extend(alter_statements)
        ddl_statements.extend(drop_tables)

        return ddl_statements


class DDLExecutor:
    def __init__(self, target_db: DBConnection, config: ConfigManager, logger: SyncLogger):
        self.target_db = target_db
        self.config = config
        self.logger = logger
        self.generator = DDLGenerator(target_db.db_type, config, logger)

    def generate_script(self, schema_diff: SchemaDiff, source_db_type: str) -> str:
        self.logger.step_start('DDL_GENERATE')
        statements = self.generator.generate_all(schema_diff, source_db_type)

        header_lines = [
            '-- ============================================',
            '-- 数据库结构同步 DDL 脚本',
            f'-- 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'-- 源数据库类型: {source_db_type}',
            f'-- 目标数据库类型: {self.target_db.db_type}',
            f'-- 变更数量: {len(statements)} 条',
            '-- ============================================',
            '',
        ]

        script = '\n'.join(header_lines) + '\n'.join(statements)
        if statements:
            script += '\n'

        self.logger.info(f'生成 {len(statements)} 条 DDL 语句', step='DDL_GENERATE')
        for stmt in statements:
            self.logger.ddl_script(stmt, step='DDL_GENERATE')
        self.logger.step_success('DDL_GENERATE')
        return script

    def save_script(self, script: str, output_dir: str) -> str:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'sync_ddl_{timestamp}.sql'
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(script)
        self.logger.info(f'DDL 脚本已保存到: {filepath}', step='DDL_SAVE')
        return filepath

    def execute(self, script: str) -> bool:
        mode = self.config.get_sync_mode()
        if mode == 'preview':
            self.logger.info('运行模式: preview (仅预览，不执行)', step='DDL_EXECUTE')
            return True

        self.logger.step_start('DDL_EXECUTE')
        statements = [s.strip() for s in script.split(';') if s.strip()]
        if not statements:
            self.logger.info('无 DDL 语句需要执行', step='DDL_EXECUTE')
            self.logger.step_success('DDL_EXECUTE')
            return True

        success = True
        auto_rollback = self.config.is_auto_rollback()

        try:
            self.target_db.begin_transaction()
            self.logger.info(f'开始事务，准备执行 {len(statements)} 条语句', step='DDL_EXECUTE')

            for i, stmt in enumerate(statements, 1):
                try:
                    self.logger.info(f'执行第 {i}/{len(statements)} 条: {stmt[:80]}...', step='DDL_EXECUTE')
                    self.logger.sql(stmt, step='DDL_EXECUTE')
                    self.target_db.execute(stmt)
                except Exception as e:
                    self.logger.error(
                        f'执行第 {i}/{len(statements)} 条失败: {str(e)}',
                        step='DDL_EXECUTE', exc_info=True
                    )
                    if auto_rollback:
                        self.logger.warning('触发自动回滚', step='DDL_EXECUTE')
                        self.target_db.rollback()
                        self.logger.step_failed('DDL_EXECUTE', e)
                        return False
                    else:
                        success = False
                        self.logger.warning('配置禁用自动回滚，继续执行后续语句', step='DDL_EXECUTE')

            if success:
                self.target_db.commit()
                self.logger.info('事务提交成功', step='DDL_EXECUTE')
                self.logger.step_success('DDL_EXECUTE')
            else:
                if auto_rollback:
                    self.target_db.rollback()
                    self.logger.warning('部分语句失败，事务已回滚', step='DDL_EXECUTE')
                else:
                    self.target_db.commit()
                    self.logger.warning('部分语句失败，事务已提交(禁用自动回滚)', step='DDL_EXECUTE')
                self.logger.step_failed('DDL_EXECUTE')

        except Exception as e:
            self.logger.error(f'执行过程发生异常: {str(e)}', step='DDL_EXECUTE', exc_info=True)
            if auto_rollback:
                self.target_db.rollback()
                self.logger.warning('异常触发回滚', step='DDL_EXECUTE')
            self.logger.step_failed('DDL_EXECUTE', e)
            return False

        return success
