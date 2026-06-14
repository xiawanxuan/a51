import os
import sys
import signal
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from core.config_manager import ConfigManager
from core.logger import SyncLogger
from main import DBSyncRunner


class ScheduledSyncTask:
    def __init__(self):
        self.config = ConfigManager()
        self.logger = SyncLogger()
        self.running = False

    def run_once(self):
        if self.running:
            self.logger.warning('上一次任务尚未完成，跳过本次调度', step='SCHEDULER')
            return
        self.running = True
        self.logger.info(f'定时任务触发: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', step='SCHEDULER')
        try:
            runner = DBSyncRunner()
            runner.run()
        except Exception as e:
            self.logger.error(f'定时任务执行异常: {str(e)}', step='SCHEDULER', exc_info=True)
        finally:
            self.running = False

    def start(self):
        schedule_cfg = self.config.get_schedule_config()
        enabled = schedule_cfg.get('enabled', False)
        if not enabled:
            self.logger.info('定时调度未启用 (sync_rules.yaml -> schedule.enabled: false)', step='SCHEDULER')
            self.logger.info('如需启动定时调度，请修改配置后重启，或使用 --now 参数立即执行一次', step='SCHEDULER')
            return

        cron_expr = schedule_cfg.get('cron', '0 2 * * *')
        self.logger.info(f'定时调度已启用，Cron表达式: {cron_expr}', step='SCHEDULER')
        self.logger.info('按 Ctrl+C 停止调度器', step='SCHEDULER')

        scheduler = BlockingScheduler(timezone='Asia/Shanghai')
        scheduler.add_job(
            self.run_once,
            trigger=CronTrigger.from_crontab(cron_expr, timezone='Asia/Shanghai'),
            id='db_sync_job',
            name='数据库结构同步任务',
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )

        def signal_handler(signum, frame):
            self.logger.info('收到停止信号，正在关闭调度器...', step='SCHEDULER')
            scheduler.shutdown(wait=False)
            self.logger.info('调度器已关闭', step='SCHEDULER')
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description='数据库结构同步定时调度器')
    parser.add_argument('--now', action='store_true', help='立即执行一次同步任务')
    parser.add_argument('--cron', type=str, default=None, help='覆盖配置的Cron表达式，如 "0 2 * * *"')
    args = parser.parse_args()

    task = ScheduledSyncTask()

    if args.now:
        task.logger.info('--now 参数指定，立即执行一次同步任务', step='SCHEDULER')
        task.run_once()
        return

    if args.cron:
        task.config.rules.setdefault('schedule', {})['enabled'] = True
        task.config.rules['schedule']['cron'] = args.cron
        task.logger.info(f'使用命令行指定的Cron表达式: {args.cron}', step='SCHEDULER')

    task.start()


if __name__ == '__main__':
    main()
