"""常驻守护进程。

职责：
1. 启动飞书 WebSocket 长连接（后台线程）
2. 定时检查推送时段（默认 07:00 / 18:00），到期卡推成复习卡
3. 每天按备份时段把数据备份到固定飞书文档
4. 启动时补推：当天已过且未推送的时段，自动补一次

推送时段、每日上限、备份时段都在配置里可调（env 或 rmem config）。
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import backup, db
from .config import settings
from .feishu import events
from .feishu.client import FeishuClient
from .feishu.handlers import current_user, push_review

logger = logging.getLogger(__name__)

_TICK_SECONDS = 20


def _tz() -> ZoneInfo:
    return ZoneInfo(settings().timezone)


def _now() -> datetime:
    return datetime.now(_tz())


def _today_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _slot_datetime(slot: str) -> datetime:
    """今天的某个时段（HH:MM）的本地 datetime。"""
    hh, mm = (int(x) for x in slot.split(":"))
    return _now().replace(hour=hh, minute=mm, second=0, microsecond=0)


def _last_done(conn: sqlite3.Connection, key: str) -> str:
    return db.config_get(conn, key, "")


def _mark_done(conn: sqlite3.Connection, key: str, date: str) -> None:
    db.config_set(conn, key, date)


def _should_fire(conn: sqlite3.Connection, key: str, slot: str) -> bool:
    """该时段今天是否应该触发（到点且今天还没触发过）。"""
    return _now() >= _slot_datetime(slot) and _last_done(conn, key) != _today_str()


async def _tick(conn: sqlite3.Connection, feishu: FeishuClient) -> None:
    open_id = current_user(conn)
    if not open_id:
        logger.info("尚未获得用户 open_id，跳过推送")
        return

    # 推送时段
    for slot in settings().push_slots:
        key = f"last_push:{slot}"
        if _should_fire(conn, key, slot):
            logger.info("推送时段 %s 触发", slot)
            try:
                await asyncio.to_thread(push_review, conn, feishu, open_id)
            except Exception:  # noqa: BLE001
                logger.exception("推送失败")
            _mark_done(conn, key, _today_str())

    # 备份时段
    bkey = "last_backup"
    if _should_fire(conn, bkey, settings().backup_time):
        logger.info("备份时段触发")
        try:
            result = await asyncio.to_thread(backup.run_backup, conn, feishu, open_id)
            logger.info("备份完成: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("备份失败")
        _mark_done(conn, bkey, _today_str())


async def run() -> None:
    s = settings()
    ok, missing = s.is_complete
    if not ok:
        logger.error("配置不完整，缺失: %s", ", ".join(missing))
        logger.error("请参照 .env.example 配置 .env 后重试")
        return

    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(s.db_path)
    db.init_db(conn)

    feishu = FeishuClient()
    events.start(conn, feishu)

    logger.info("remenberall 启动 · 推送时段=%s · 每日上限=%d · 备份=%s",
                s.push_slots, s.daily_cap, s.backup_time)

    stop = asyncio.Event()

    def _on_signal(_sig, _frame):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass  # Windows 上部分信号不可用

    while not stop.is_set():
        try:
            await _tick(conn, feishu)
        except Exception:  # noqa: BLE001
            logger.exception("tick 异常")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass

    logger.info("收到退出信号，关闭连接")
    conn.close()
