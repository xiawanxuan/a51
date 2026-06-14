import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Optional


class SyncLogger:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, log_dir: Optional[str] = None, log_level: int = logging.INFO):
        if self._initialized:
            return
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log_dir = log_dir or os.path.join(base_dir, 'logs')
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_level = log_level
        self.logger = logging.getLogger('DBSync')
        self.logger.setLevel(log_level)
        self.logger.handlers.clear()
        self.logger.propagate = False
        self._setup_console_handler()
        self._setup_file_handler()
        self._setup_error_handler()
        self._initialized = True

    def _setup_console_handler(self):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_format = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(step)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)

    def _setup_file_handler(self):
        timestamp = datetime.now().strftime('%Y%m%d')
        log_file = os.path.join(self.log_dir, f'sync_{timestamp}.log')
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=30,
            encoding='utf-8'
        )
        file_handler.setLevel(self.log_level)
        file_format = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(step)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)

    def _setup_error_handler(self):
        timestamp = datetime.now().strftime('%Y%m%d')
        error_file = os.path.join(self.log_dir, f'error_{timestamp}.log')
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=30,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_format = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(step)s] %(message)s\n%(exc_info)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        error_handler.setFormatter(error_format)
        self.logger.addHandler(error_handler)

    def _extra(self, step: str = 'MAIN') -> dict:
        return {'step': step}

    def info(self, message: str, step: str = 'MAIN'):
        self.logger.info(message, extra=self._extra(step))

    def debug(self, message: str, step: str = 'MAIN'):
        self.logger.debug(message, extra=self._extra(step))

    def warning(self, message: str, step: str = 'MAIN'):
        self.logger.warning(message, extra=self._extra(step))

    def error(self, message: str, step: str = 'MAIN', exc_info: bool = False):
        self.logger.error(message, extra=self._extra(step), exc_info=exc_info)

    def critical(self, message: str, step: str = 'MAIN', exc_info: bool = False):
        self.logger.critical(message, extra=self._extra(step), exc_info=exc_info)

    def step_start(self, step_name: str):
        self.info(f'========== 开始执行步骤: {step_name} ==========', step=step_name)

    def step_success(self, step_name: str):
        self.info(f'========== 步骤执行成功: {step_name} ==========', step=step_name)

    def step_failed(self, step_name: str, error: Exception = None):
        msg = f'========== 步骤执行失败: {step_name} =========='
        if error:
            msg += f' 错误: {str(error)}'
        self.error(msg, step=step_name, exc_info=error is not None)

    def sql(self, sql: str, step: str = 'SQL'):
        self.debug(f'执行SQL: {sql}', step=step)

    def ddl_script(self, ddl: str, step: str = 'DDL'):
        self.info(f'生成DDL:\n{ddl}', step=step)

    def diff_summary(self, summary: dict, step: str = 'DIFF'):
        lines = ['差异汇总:']
        for key, value in summary.items():
            lines.append(f'  {key}: {value}')
        self.info('\n'.join(lines), step=step)
