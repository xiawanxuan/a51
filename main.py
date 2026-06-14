import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config_manager import ConfigManager
from core.logger import SyncLogger
from core.db_adapter import DBAdapter
from core.metadata_collector import MetadataCollector
from core.diff_engine import DiffEngine
from core.dependency_checker import DependencyChecker
from core.ddl_executor import DDLExecutor


class DBSyncRunner:
    def __init__(self, db_config_path: str = None, rules_path: str = None,
                 mode: str = None):
        self.config = ConfigManager(db_config_path, rules_path)
        if mode:
            self.config.rules.setdefault('sync', {})['mode'] = mode
        self.logger = SyncLogger()
        self.source_db = None
        self.target_db = None

    def _connect_databases(self):
        self.logger.step_start('CONNECT_DATABASES')

        src_cfg = self.config.get_source_config()
        if not src_cfg:
            raise RuntimeError('未找到源数据库配置')
        self.logger.info(
            f'连接源库: {src_cfg.db_type}://{src_cfg.host}:{src_cfg.port}/{src_cfg.database}',
            step='CONNECT_DATABASES'
        )
        self.source_db = DBAdapter.from_config(src_cfg)
        self.source_db.connect()

        tgt_cfg = self.config.get_target_config()
        if not tgt_cfg:
            raise RuntimeError('未找到目标数据库配置')
        self.logger.info(
            f'连接目标库: {tgt_cfg.db_type}://{tgt_cfg.host}:{tgt_cfg.port}/{tgt_cfg.database}',
            step='CONNECT_DATABASES'
        )
        self.target_db = DBAdapter.from_config(tgt_cfg)
        self.target_db.connect()

        self.logger.step_success('CONNECT_DATABASES')

    def _collect_metadata(self):
        self.logger.step_start('COLLECT_METADATA')

        src_collector = MetadataCollector(self.source_db, self.config, self.logger)
        source_schema = src_collector.collect()
        self.logger.info(
            f'源库元数据采集完成: {len(source_schema.tables)} 个表',
            step='COLLECT_METADATA'
        )

        tgt_collector = MetadataCollector(self.target_db, self.config, self.logger)
        target_schema = tgt_collector.collect()
        self.logger.info(
            f'目标库元数据采集完成: {len(target_schema.tables)} 个表',
            step='COLLECT_METADATA'
        )

        self.logger.step_success('COLLECT_METADATA')
        return source_schema, target_schema

    def _compare_diff(self, source_schema, target_schema):
        engine = DiffEngine(self.config, self.logger)
        return engine.compare(source_schema, target_schema)

    def _check_dependencies(self, source_schema, target_schema, schema_diff) -> bool:
        checker = DependencyChecker(self.config, self.logger)
        result = checker.run(source_schema, target_schema, schema_diff)
        if not result.passed and self.config.is_dependency_check_blocking():
            self.logger.error(
                f'依赖校验未通过（错误{len(result.errors)}个，警告{len(result.warnings)}个），'
                f'同步任务已终止。可设置 dependency_check.blocking=false 继续执行，'
                f'或修复问题后重试。',
                step='MAIN'
            )
            return False
        if result.warnings:
            self.logger.warning(
                f'依赖校验存在警告（{len(result.warnings)}个），请仔细评估风险后继续',
                step='MAIN'
            )
        return True

    def _generate_and_execute(self, schema_diff, source_db_type):
        executor = DDLExecutor(self.target_db, self.config, self.logger)
        script = executor.generate_script(schema_diff, source_db_type)

        output_dir = self.config.get_output_dir()
        saved_path = executor.save_script(script, output_dir)

        self.logger.info(f'DDL 脚本文件: {saved_path}', step='MAIN')

        success = executor.execute(script)
        return success, saved_path

    def run(self) -> bool:
        self.logger.info('========== 数据库结构同步任务开始 ==========', step='MAIN')
        success = False

        try:
            self._connect_databases()
            source_schema, target_schema = self._collect_metadata()
            schema_diff = self._compare_diff(source_schema, target_schema)
            if not self._check_dependencies(source_schema, target_schema, schema_diff):
                self.logger.error('========== 依赖校验未通过，同步任务中止 ==========', step='MAIN')
                success = False
            else:
                success, _ = self._generate_and_execute(schema_diff, source_schema.db_type)

            if success:
                self.logger.info('========== 数据库结构同步任务完成 ==========', step='MAIN')
            else:
                self.logger.error('========== 数据库结构同步任务失败 ==========', step='MAIN')

        except Exception as e:
            self.logger.critical(f'任务执行异常: {str(e)}', step='MAIN', exc_info=True)
            if self.config.is_auto_rollback() and self.target_db and self.target_db._in_transaction:
                try:
                    self.target_db.rollback()
                    self.logger.warning('异常触发事务回滚', step='MAIN')
                except Exception:
                    pass
            success = False

        finally:
            self._close_connections()

        return success

    def _close_connections(self):
        for db in [self.source_db, self.target_db]:
            if db:
                try:
                    db.close()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description='异构数据库结构同步工具')
    parser.add_argument('--db-config', type=str, default=None,
                        help='数据库连接配置文件路径')
    parser.add_argument('--rules', type=str, default=None,
                        help='同步规则配置文件路径')
    parser.add_argument('--mode', type=str, choices=['preview', 'execute'],
                        default=None, help='运行模式: preview(仅预览), execute(执行变更)')
    args = parser.parse_args()

    runner = DBSyncRunner(
        db_config_path=args.db_config,
        rules_path=args.rules,
        mode=args.mode,
    )
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
