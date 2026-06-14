import configparser
import os
import fnmatch
import yaml
from typing import Dict, List, Any, Optional


class DBConfig:
    def __init__(self, section: str, config: configparser.ConfigParser):
        self.section = section
        self.db_type = config.get(section, 'db_type', fallback='mysql')
        self.host = config.get(section, 'host', fallback='127.0.0.1')
        self.port = config.getint(section, 'port', fallback=3306)
        self.user = config.get(section, 'user', fallback='root')
        self.password = config.get(section, 'password', fallback='')
        self.database = config.get(section, 'database', fallback='')
        self.charset = config.get(section, 'charset', fallback='utf8mb4')
        self.schema = config.get(section, 'schema', fallback='public')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'db_type': self.db_type,
            'host': self.host,
            'port': self.port,
            'user': self.user,
            'password': self.password,
            'database': self.database,
            'charset': self.charset,
            'schema': self.schema,
        }


class ConfigManager:
    def __init__(self, db_config_path: str = None, rules_path: str = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_config_path = db_config_path or os.path.join(base_dir, 'config', 'db_config.ini')
        self.rules_path = rules_path or os.path.join(base_dir, 'config', 'sync_rules.yaml')
        self.db_configs: Dict[str, DBConfig] = {}
        self.rules: Dict[str, Any] = {}
        self._load_db_configs()
        self._load_sync_rules()

    def _load_db_configs(self):
        parser = configparser.ConfigParser()
        parser.read(self.db_config_path, encoding='utf-8')
        for section in parser.sections():
            self.db_configs[section] = DBConfig(section, parser)

    def _load_sync_rules(self):
        with open(self.rules_path, 'r', encoding='utf-8') as f:
            self.rules = yaml.safe_load(f) or {}

    def get_db_config(self, name: str) -> Optional[DBConfig]:
        return self.db_configs.get(name)

    def get_source_config(self) -> Optional[DBConfig]:
        source_name = self.rules.get('sync', {}).get('source_conn', '')
        return self.get_db_config(source_name)

    def get_target_config(self) -> Optional[DBConfig]:
        target_name = self.rules.get('sync', {}).get('target_conn', '')
        return self.get_db_config(target_name)

    def get_sync_mode(self) -> str:
        return self.rules.get('sync', {}).get('mode', 'preview')

    def is_auto_rollback(self) -> bool:
        return self.rules.get('sync', {}).get('auto_rollback', True)

    def get_output_dir(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = self.rules.get('sync', {}).get('output_dir', 'output')
        return os.path.join(base_dir, output_dir)

    def _match_pattern(self, name: str, patterns: List[str], case_sensitive: bool = False) -> bool:
        check_name = name if case_sensitive else name.lower()
        for pattern in patterns:
            check_pattern = pattern if case_sensitive else pattern.lower()
            if fnmatch.fnmatch(check_name, check_pattern):
                return True
        return False

    def is_table_allowed(self, table_name: str) -> bool:
        table_filter = self.rules.get('table_filter', {})
        mode = table_filter.get('mode', 'all')
        case_sensitive = self.rules.get('rules', {}).get('case_sensitive', False)

        if mode == 'whitelist':
            whitelist = table_filter.get('whitelist', [])
            if not whitelist:
                return True
            return self._match_pattern(table_name, whitelist, case_sensitive)
        elif mode == 'blacklist':
            blacklist = table_filter.get('blacklist', [])
            if not blacklist:
                return True
            return not self._match_pattern(table_name, blacklist, case_sensitive)
        return True

    def is_column_allowed(self, column_name: str) -> bool:
        ignore_columns = self.rules.get('column_filter', {}).get('ignore_columns', [])
        if not ignore_columns:
            return True
        case_sensitive = self.rules.get('rules', {}).get('case_sensitive', False)
        return not self._match_pattern(column_name, ignore_columns, case_sensitive)

    def is_index_allowed(self, index_name: str) -> bool:
        ignore_indexes = self.rules.get('index_filter', {}).get('ignore_indexes', [])
        if not ignore_indexes:
            return True
        case_sensitive = self.rules.get('rules', {}).get('case_sensitive', False)
        return not self._match_pattern(index_name, ignore_indexes, case_sensitive)

    def should_drop_missing_tables(self) -> bool:
        return self.rules.get('rules', {}).get('drop_missing_tables', False)

    def should_drop_missing_columns(self) -> bool:
        return self.rules.get('rules', {}).get('drop_missing_columns', False)

    def should_drop_missing_indexes(self) -> bool:
        return self.rules.get('rules', {}).get('drop_missing_indexes', False)

    def should_modify_column_type(self) -> bool:
        return self.rules.get('rules', {}).get('modify_column_type', True)

    def get_type_mapping(self, source_db_type: str) -> Dict[str, str]:
        return self.rules.get('rules', {}).get('type_mapping', {}).get(source_db_type, {})

    def get_schedule_config(self) -> Dict[str, Any]:
        return self.rules.get('schedule', {})

    def reload(self):
        self.db_configs.clear()
        self.rules.clear()
        self._load_db_configs()
        self._load_sync_rules()
