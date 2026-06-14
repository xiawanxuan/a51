from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from .db_adapter import DBConnection
from .logger import SyncLogger
from .config_manager import ConfigManager


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    column_type: str = ''
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    auto_increment: bool = False
    comment: str = ''
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    ordinal_position: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'data_type': self.data_type,
            'column_type': self.column_type,
            'nullable': self.nullable,
            'default': self.default,
            'primary_key': self.primary_key,
            'auto_increment': self.auto_increment,
            'comment': self.comment,
            'character_maximum_length': self.character_maximum_length,
            'numeric_precision': self.numeric_precision,
            'numeric_scale': self.numeric_scale,
            'ordinal_position': self.ordinal_position,
        }


@dataclass
class IndexInfo:
    name: str
    columns: List[str] = field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False
    index_type: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'columns': self.columns,
            'is_unique': self.is_unique,
            'is_primary': self.is_primary,
            'index_type': self.index_type,
        }


@dataclass
class ForeignKeyInfo:
    name: str
    columns: List[str] = field(default_factory=list)
    ref_table: str = ''
    ref_columns: List[str] = field(default_factory=list)
    on_delete: str = ''
    on_update: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'columns': self.columns,
            'ref_table': self.ref_table,
            'ref_columns': self.ref_columns,
            'on_delete': self.on_delete,
            'on_update': self.on_update,
        }


@dataclass
class PartitionInfo:
    name: str
    partition_type: str = ''
    partition_expression: str = ''
    parent_table: str = ''
    partition_values: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'partition_type': self.partition_type,
            'partition_expression': self.partition_expression,
            'parent_table': self.parent_table,
            'partition_values': self.partition_values,
        }


@dataclass
class TriggerInfo:
    name: str
    event_manipulation: str = ''
    action_timing: str = ''
    action_statement: str = ''
    action_orientation: str = ''
    referenced_trigger: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'event_manipulation': self.event_manipulation,
            'action_timing': self.action_timing,
            'action_statement': self.action_statement,
            'action_orientation': self.action_orientation,
            'referenced_trigger': self.referenced_trigger,
        }


@dataclass
class TableInfo:
    name: str
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)
    indexes: Dict[str, IndexInfo] = field(default_factory=dict)
    foreign_keys: Dict[str, ForeignKeyInfo] = field(default_factory=dict)
    partitions: List[PartitionInfo] = field(default_factory=list)
    triggers: Dict[str, TriggerInfo] = field(default_factory=dict)
    primary_key_columns: List[str] = field(default_factory=list)
    comment: str = ''
    is_partitioned: bool = False
    parent_table: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'columns': {k: v.to_dict() for k, v in self.columns.items()},
            'indexes': {k: v.to_dict() for k, v in self.indexes.items()},
            'foreign_keys': {k: v.to_dict() for k, v in self.foreign_keys.items()},
            'partitions': [p.to_dict() for p in self.partitions],
            'triggers': {k: v.to_dict() for k, v in self.triggers.items()},
            'primary_key_columns': self.primary_key_columns,
            'comment': self.comment,
            'is_partitioned': self.is_partitioned,
            'parent_table': self.parent_table,
        }


@dataclass
class SchemaInfo:
    db_type: str
    tables: Dict[str, TableInfo] = field(default_factory=dict)
    all_foreign_keys: Dict[str, ForeignKeyInfo] = field(default_factory=dict)
    all_triggers: Dict[str, TriggerInfo] = field(default_factory=dict)
    partition_tables: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'db_type': self.db_type,
            'tables': {k: v.to_dict() for k, v in self.tables.items()},
            'all_foreign_keys': {k: v.to_dict() for k, v in self.all_foreign_keys.items()},
            'all_triggers': {k: v.to_dict() for k, v in self.all_triggers.items()},
            'partition_tables': self.partition_tables,
        }


class MySQLMetadataCollector:
    def __init__(self, db: DBConnection, config: ConfigManager, logger: SyncLogger):
        self.db = db
        self.config = config
        self.logger = logger

    def collect(self) -> SchemaInfo:
        schema = SchemaInfo(db_type='mysql')
        self.logger.step_start('MYSQL_COLLECT_TABLES')
        tables = self._collect_tables()
        self.logger.info(f'采集到 {len(tables)} 个表', step='MYSQL_COLLECT_TABLES')
        self.logger.step_success('MYSQL_COLLECT_TABLES')

        for table_name in tables:
            if not self.config.is_table_allowed(table_name):
                self.logger.debug(f'跳过表(过滤): {table_name}', step='MYSQL_COLLECT')
                continue
            self.logger.step_start(f'MYSQL_COLLECT_TABLE_{table_name}')
            try:
                table_info = TableInfo(name=table_name)
                table_info.columns = self._collect_columns(table_name)
                table_info.indexes = self._collect_indexes(table_name)
                table_info.foreign_keys = self._collect_foreign_keys(table_name)
                table_info.partitions = self._collect_partitions(table_name)
                table_info.triggers = self._collect_triggers(table_name)
                table_info.primary_key_columns = self._get_primary_key_columns(table_info)
                table_info.comment = self._collect_table_comment(table_name)
                if table_info.partitions:
                    table_info.is_partitioned = True
                schema.tables[table_name] = table_info
                for fk_name, fk in table_info.foreign_keys.items():
                    schema.all_foreign_keys[f'{table_name}.{fk_name}'] = fk
                for tr_name, tr in table_info.triggers.items():
                    schema.all_triggers[f'{table_name}.{tr_name}'] = tr
                self.logger.info(
                    f'表 {table_name}: {len(table_info.columns)} 字段, {len(table_info.indexes)} 索引, '
                    f'{len(table_info.foreign_keys)} 外键, {len(table_info.partitions)} 分区, '
                    f'{len(table_info.triggers)} 触发器',
                    step=f'MYSQL_COLLECT_TABLE_{table_name}'
                )
                self.logger.step_success(f'MYSQL_COLLECT_TABLE_{table_name}')
            except Exception as e:
                self.logger.step_failed(f'MYSQL_COLLECT_TABLE_{table_name}', e)
                raise

        self._build_partition_tree(schema)
        return schema

    def _build_partition_tree(self, schema: SchemaInfo):
        for tbl in schema.tables.values():
            if tbl.is_partitioned:
                children = [p.name for p in tbl.partitions if p.name]
                if children:
                    schema.partition_tables[tbl.name] = children

    def _collect_tables(self) -> List[str]:
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
        rows = self.db.execute(sql, fetch=True)
        return [r[0] for r in rows]

    def _collect_columns(self, table_name: str) -> Dict[str, ColumnInfo]:
        sql = """
            SELECT column_name, data_type, column_type, is_nullable, column_default,
                   extra, column_comment, character_maximum_length,
                   numeric_precision, numeric_scale, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ordinal_position
        """
        rows = self.db.execute(sql, (table_name,), fetch=True)
        columns = {}
        for r in rows:
            col_name = r[0]
            if not self.config.is_column_allowed(col_name):
                continue
            col = ColumnInfo(
                name=col_name,
                data_type=r[1],
                column_type=r[2],
                nullable=(r[3] == 'YES'),
                default=r[4],
                auto_increment=('auto_increment' in (r[5] or '')),
                comment=r[6] or '',
                character_maximum_length=r[7],
                numeric_precision=r[8],
                numeric_scale=r[9],
                ordinal_position=r[10],
            )
            columns[col_name] = col
        return columns

    def _collect_indexes(self, table_name: str) -> Dict[str, IndexInfo]:
        sql = """
            SELECT index_name, column_name, non_unique, index_type, seq_in_index
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY index_name, seq_in_index
        """
        rows = self.db.execute(sql, (table_name,), fetch=True)
        indexes = {}
        for r in rows:
            idx_name = r[0]
            col_name = r[1]
            if not self.config.is_index_allowed(idx_name):
                continue
            if idx_name not in indexes:
                indexes[idx_name] = IndexInfo(
                    name=idx_name,
                    is_unique=(r[2] == 0),
                    is_primary=(idx_name == 'PRIMARY'),
                    index_type=r[3],
                    columns=[],
                )
            indexes[idx_name].columns.append(col_name)
        return indexes

    def _get_primary_key_columns(self, table_info: TableInfo) -> List[str]:
        for idx in table_info.indexes.values():
            if idx.is_primary:
                for col_name in idx.columns:
                    if col_name in table_info.columns:
                        table_info.columns[col_name].primary_key = True
                return idx.columns
        return []

    def _collect_table_comment(self, table_name: str) -> str:
        sql = "SELECT table_comment FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s"
        row = self.db.execute(sql, (table_name,), fetch_one=True)
        return row[0] if row else ''

    def _collect_foreign_keys(self, table_name: str) -> Dict[str, ForeignKeyInfo]:
        sql = """
            SELECT kc.constraint_name, kc.column_name,
                   kc.referenced_table_name, kc.referenced_column_name,
                   rc.update_rule, rc.delete_rule, kc.ordinal_position
            FROM information_schema.key_column_usage kc
            JOIN information_schema.referential_constraints rc
              ON kc.constraint_name = rc.constraint_name
             AND kc.table_schema = rc.constraint_schema
            WHERE kc.table_schema = DATABASE()
              AND kc.table_name = %s
              AND kc.referenced_table_name IS NOT NULL
            ORDER BY kc.constraint_name, kc.ordinal_position
        """
        rows = self.db.execute(sql, (table_name,), fetch=True)
        fks: Dict[str, ForeignKeyInfo] = {}
        for r in rows:
            fk_name = r[0]
            col_name = r[1]
            ref_table = r[2]
            ref_col = r[3]
            on_update = r[4] or ''
            on_delete = r[5] or ''
            if fk_name not in fks:
                fks[fk_name] = ForeignKeyInfo(
                    name=fk_name,
                    columns=[],
                    ref_table=ref_table,
                    ref_columns=[],
                    on_update=on_update,
                    on_delete=on_delete,
                )
            fks[fk_name].columns.append(col_name)
            fks[fk_name].ref_columns.append(ref_col)
        return fks

    def _collect_partitions(self, table_name: str) -> List[PartitionInfo]:
        sql = """
            SELECT partition_name, partition_method, partition_expression,
                   partition_description
            FROM information_schema.partitions
            WHERE table_schema = DATABASE() AND table_name = %s
              AND partition_name IS NOT NULL
            ORDER BY partition_ordinal_position
        """
        rows = self.db.execute(sql, (table_name,), fetch=True)
        partitions = []
        for r in rows:
            partitions.append(PartitionInfo(
                name=r[0] or '',
                partition_type=r[1] or '',
                partition_expression=r[2] or '',
                parent_table=table_name,
                partition_values=r[3] or '',
            ))
        return partitions

    def _collect_triggers(self, table_name: str) -> Dict[str, TriggerInfo]:
        sql = """
            SELECT trigger_name, event_manipulation, action_timing,
                   action_statement, action_orientation
            FROM information_schema.triggers
            WHERE trigger_schema = DATABASE() AND event_object_table = %s
            ORDER BY trigger_name
        """
        rows = self.db.execute(sql, (table_name,), fetch=True)
        triggers = {}
        for r in rows:
            triggers[r[0]] = TriggerInfo(
                name=r[0],
                event_manipulation=r[1] or '',
                action_timing=r[2] or '',
                action_statement=r[3] or '',
                action_orientation=r[4] or '',
            )
        return triggers


class PostgreSQLMetadataCollector:
    def __init__(self, db: DBConnection, config: ConfigManager, logger: SyncLogger):
        self.db = db
        self.config = config
        self.logger = logger
        self.schema = db.schema

    def collect(self) -> SchemaInfo:
        schema = SchemaInfo(db_type='postgresql')
        self.logger.step_start('PGSQL_COLLECT_TABLES')
        tables = self._collect_tables()
        self.logger.info(f'采集到 {len(tables)} 个表', step='PGSQL_COLLECT_TABLES')
        self.logger.step_success('PGSQL_COLLECT_TABLES')

        for table_name in tables:
            if not self.config.is_table_allowed(table_name):
                self.logger.debug(f'跳过表(过滤): {table_name}', step='PGSQL_COLLECT')
                continue
            self.logger.step_start(f'PGSQL_COLLECT_TABLE_{table_name}')
            try:
                table_info = TableInfo(name=table_name)
                table_info.columns = self._collect_columns(table_name)
                table_info.indexes = self._collect_indexes(table_name)
                table_info.foreign_keys = self._collect_foreign_keys(table_name)
                table_info.partitions = self._collect_partitions(table_name)
                table_info.triggers = self._collect_triggers(table_name)
                table_info.primary_key_columns = self._get_primary_key_columns(table_info)
                table_info.comment = self._collect_table_comment(table_name)
                if table_info.partitions:
                    table_info.is_partitioned = True
                schema.tables[table_name] = table_info
                for fk_name, fk in table_info.foreign_keys.items():
                    schema.all_foreign_keys[f'{table_name}.{fk_name}'] = fk
                for tr_name, tr in table_info.triggers.items():
                    schema.all_triggers[f'{table_name}.{tr_name}'] = tr
                self.logger.info(
                    f'表 {table_name}: {len(table_info.columns)} 字段, {len(table_info.indexes)} 索引, '
                    f'{len(table_info.foreign_keys)} 外键, {len(table_info.partitions)} 分区, '
                    f'{len(table_info.triggers)} 触发器',
                    step=f'PGSQL_COLLECT_TABLE_{table_name}'
                )
                self.logger.step_success(f'PGSQL_COLLECT_TABLE_{table_name}')
            except Exception as e:
                self.logger.step_failed(f'PGSQL_COLLECT_TABLE_{table_name}', e)
                raise

        self._build_partition_tree(schema)
        return schema

    def _build_partition_tree(self, schema: SchemaInfo):
        for tbl in schema.tables.values():
            if tbl.is_partitioned:
                children = [p.name for p in tbl.partitions if p.name]
                if children:
                    schema.partition_tables[tbl.name] = children

    def _collect_tables(self) -> List[str]:
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        rows = self.db.execute(sql, (self.schema,), fetch=True)
        return [r[0] for r in rows]

    def _collect_columns(self, table_name: str) -> Dict[str, ColumnInfo]:
        sql = """
            SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable, c.column_default,
                   c.character_maximum_length, c.numeric_precision, c.numeric_scale,
                   c.ordinal_position,
                   pg_catalog.col_description(pc.oid, c.ordinal_position) as column_comment
            FROM information_schema.columns c
            JOIN pg_catalog.pg_class pc ON pc.relname = c.table_name
            JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace AND pn.nspname = c.table_schema
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position
        """
        rows = self.db.execute(sql, (self.schema, table_name), fetch=True)
        columns = {}
        for r in rows:
            col_name = r[0]
            if not self.config.is_column_allowed(col_name):
                continue
            default_val = r[4]
            auto_inc = False
            if default_val and ('nextval' in default_val):
                auto_inc = True
            col = ColumnInfo(
                name=col_name,
                data_type=r[1],
                column_type=r[2],
                nullable=(r[3] == 'YES'),
                default=default_val,
                auto_increment=auto_inc,
                comment=r[9] or '',
                character_maximum_length=r[5],
                numeric_precision=r[6],
                numeric_scale=r[7],
                ordinal_position=r[8] if r[8] is not None else 0,
            )
            columns[col_name] = col
        return columns

    def _collect_indexes(self, table_name: str) -> Dict[str, IndexInfo]:
        sql = """
            SELECT
                idx.relname AS index_name,
                a.attname AS column_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                am.amname AS index_type,
                array_position(ix.indkey, a.attnum) AS seq_in_index
            FROM pg_catalog.pg_class t
            JOIN pg_catalog.pg_index ix ON t.oid = ix.indrelid
            JOIN pg_catalog.pg_class idx ON idx.oid = ix.indexrelid
            JOIN pg_catalog.pg_am am ON idx.relam = am.oid
            JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = %s AND t.relname = %s
            ORDER BY index_name, seq_in_index
        """
        rows = self.db.execute(sql, (self.schema, table_name), fetch=True)
        indexes = {}
        for r in rows:
            idx_name = r[0]
            col_name = r[1]
            if not self.config.is_index_allowed(idx_name):
                continue
            if idx_name not in indexes:
                indexes[idx_name] = IndexInfo(
                    name=idx_name,
                    columns=[],
                    is_unique=bool(r[2]),
                    is_primary=bool(r[3]),
                    index_type=r[4],
                )
            indexes[idx_name].columns.append(col_name)
        return indexes

    def _get_primary_key_columns(self, table_info: TableInfo) -> List[str]:
        for idx in table_info.indexes.values():
            if idx.is_primary:
                for col_name in idx.columns:
                    if col_name in table_info.columns:
                        table_info.columns[col_name].primary_key = True
                return idx.columns
        return []

    def _collect_table_comment(self, table_name: str) -> str:
        sql = """
            SELECT obj_description(pc.oid, 'pg_class')
            FROM pg_catalog.pg_class pc
            JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace
            WHERE pn.nspname = %s AND pc.relname = %s
        """
        row = self.db.execute(sql, (self.schema, table_name), fetch_one=True)
        return row[0] if row and row[0] else ''

    def _collect_foreign_keys(self, table_name: str) -> Dict[str, ForeignKeyInfo]:
        sql = """
            SELECT tc.constraint_name, kcu.column_name,
                   ccu.table_name AS referenced_table_name,
                   ccu.column_name AS referenced_column_name,
                   rc.update_rule, rc.delete_rule,
                   kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.table_schema = rc.constraint_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.constraint_name, kcu.ordinal_position
        """
        rows = self.db.execute(sql, (self.schema, table_name), fetch=True)
        fks: Dict[str, ForeignKeyInfo] = {}
        for r in rows:
            fk_name = r[0]
            col_name = r[1]
            ref_table = r[2]
            ref_col = r[3]
            on_update = r[4] or ''
            on_delete = r[5] or ''
            if fk_name not in fks:
                fks[fk_name] = ForeignKeyInfo(
                    name=fk_name,
                    columns=[],
                    ref_table=ref_table,
                    ref_columns=[],
                    on_update=on_update,
                    on_delete=on_delete,
                )
            fks[fk_name].columns.append(col_name)
            fks[fk_name].ref_columns.append(ref_col)
        return fks

    def _collect_partitions(self, table_name: str) -> List[PartitionInfo]:
        sql = """
            SELECT
                child.relname AS partition_name,
                pg_get_expr(child.relpartbound, child.oid) AS partition_bound,
                parent.relkind AS parent_kind,
                CASE
                    WHEN parent.relkind = 'p' THEN 'declarative'
                    ELSE 'inheritance'
                END AS partition_method,
                pg_get_partkeydef(parent.oid) AS partition_expr
            FROM pg_catalog.pg_inherits pi
            JOIN pg_catalog.pg_class child ON child.oid = pi.inhrelid
            JOIN pg_catalog.pg_class parent ON parent.oid = pi.inhparent
            JOIN pg_catalog.pg_namespace pn ON pn.oid = parent.relnamespace
            WHERE pn.nspname = %s AND parent.relname = %s
            ORDER BY child.relname
        """
        rows = self.db.execute(sql, (self.schema, table_name), fetch=True)
        partitions = []
        for r in rows:
            partitions.append(PartitionInfo(
                name=r[0] or '',
                partition_type=r[3] or '',
                partition_expression=r[4] or '',
                parent_table=table_name,
                partition_values=r[1] or '',
            ))
        return partitions

    def _collect_triggers(self, table_name: str) -> Dict[str, TriggerInfo]:
        sql = """
            SELECT trigger_name, event_manipulation, action_timing,
                   action_statement, action_orientation
            FROM information_schema.triggers
            WHERE trigger_schema = %s AND event_object_table = %s
              AND trigger_name NOT LIKE 'pg_%'
            ORDER BY trigger_name
        """
        rows = self.db.execute(sql, (self.schema, table_name), fetch=True)
        triggers = {}
        for r in rows:
            triggers[r[0]] = TriggerInfo(
                name=r[0],
                event_manipulation=r[1] or '',
                action_timing=r[2] or '',
                action_statement=r[3] or '',
                action_orientation=r[4] or '',
            )
        return triggers


class MetadataCollector:
    def __init__(self, db: DBConnection, config: ConfigManager, logger: SyncLogger):
        self.db = db
        self.config = config
        self.logger = logger
        if db.db_type == 'mysql':
            self._collector = MySQLMetadataCollector(db, config, logger)
        elif db.db_type == 'postgresql':
            self._collector = PostgreSQLMetadataCollector(db, config, logger)
        else:
            raise ValueError(f"不支持的数据库类型: {db.db_type}")

    def collect(self) -> SchemaInfo:
        return self._collector.collect()
