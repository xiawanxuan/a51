import pymysql
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from typing import Optional, List, Tuple, Any, Dict
from abc import ABC, abstractmethod


class DBConnection:
    def __init__(self, db_type: str, host: str, port: int, user: str,
                 password: str, database: str, charset: str = 'utf8mb4',
                 schema: str = 'public'):
        self.db_type = db_type
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.schema = schema
        self._conn = None
        self._in_transaction = False

    def connect(self):
        raise NotImplementedError

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._in_transaction = False

    @property
    def connection(self):
        return self._conn

    @property
    def is_connected(self) -> bool:
        if self._conn is None:
            return False
        try:
            if self._conn.open:
                return True
        except Exception:
            return False
        return False

    def get_connection(self):
        if not self.is_connected:
            self.connect()
        return self._conn

    @contextmanager
    def cursor(self, dict_cursor: bool = False):
        conn = self.get_connection()
        cur = None
        try:
            if dict_cursor:
                cur = conn.cursor(pymysql.cursors.DictCursor) if self.db_type == 'mysql' else conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) if self.db_type == 'postgresql' else conn.cursor()
            else:
                cur = conn.cursor()
            yield cur
        finally:
            if cur:
                cur.close()

    def begin_transaction(self):
        conn = self.get_connection()
        if not self._in_transaction:
            conn.begin()
            self._in_transaction = True

    def commit(self):
        if self._conn and self._in_transaction:
            self._conn.commit()
            self._in_transaction = False

    def rollback(self):
        if self._conn and self._in_transaction:
            self._conn.rollback()
            self._in_transaction = False

    @contextmanager
    def transaction(self):
        self.begin_transaction()
        try:
            yield self
            self.commit()
        except Exception:
            self.rollback()
            raise

    def execute(self, sql: str, params: Optional[Tuple] = None, fetch: bool = False,
                fetch_one: bool = False) -> Any:
        with self.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch_one:
                return cur.fetchone()
            if fetch:
                return cur.fetchall()
            return cur.rowcount

    def execute_many(self, sql: str, params_list: List[Tuple]) -> int:
        with self.cursor() as cur:
            cur.executemany(sql, params_list)
            return cur.rowcount

    def execute_script(self, sql_script: str) -> None:
        statements = [s.strip() for s in sql_script.split(';') if s.strip()]
        with self.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)

    def quote_identifier(self, identifier: str) -> str:
        if self.db_type == 'mysql':
            return f'`{identifier}`'
        else:
            return f'"{identifier}"'

    def quote_string(self, value: str) -> str:
        if self.db_type == 'mysql':
            return f"'{value.replace(chr(39), chr(39) + chr(39))}'"
        else:
            return f"'{value.replace(chr(39), chr(39) + chr(39))}'"


class MySQLConnection(DBConnection):
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, charset: str = 'utf8mb4', **kwargs):
        super().__init__('mysql', host, port, user, password, database, charset)

    def connect(self):
        self.close()
        self._conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset=self.charset,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._in_transaction = False


class PostgreSQLConnection(DBConnection):
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, schema: str = 'public', **kwargs):
        super().__init__('postgresql', host, port, user, password, database, schema=schema)

    def connect(self):
        self.close()
        dsn = (
            f"host={self.host} port={self.port} user={self.user} "
            f"password={self.password} dbname={self.database}"
        )
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = False
        self._in_transaction = False


class DBAdapter:
    @staticmethod
    def create(db_type: str, **kwargs) -> DBConnection:
        db_type_lower = db_type.lower()
        if db_type_lower == 'mysql':
            return MySQLConnection(**kwargs)
        elif db_type_lower in ('postgresql', 'postgres', 'pgsql'):
            return PostgreSQLConnection(**kwargs)
        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")

    @staticmethod
    def from_config(config) -> DBConnection:
        return DBAdapter.create(
            db_type=config.db_type,
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset=getattr(config, 'charset', 'utf8mb4'),
            schema=getattr(config, 'schema', 'public'),
        )
