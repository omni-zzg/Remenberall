"""到期队列与复习反馈。

时间统一存本地时间（Asia/Shanghai，随配置）的无时区 ISO 字符串，字符串比较即时间比较。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, sm2

# SM-2 复习状态机：learning 优先于 reviewing
_STATE_ORDER = """CASE WHEN state = 'learning' THEN 0 ELSE 1 END"""


def _tz() -> ZoneInfo:
    from .config import settings

    return ZoneInfo(settings().timezone)


def now_str() -> str:
    """当前本地时间，格式 2026-08-25 07:00:00。"""
    return datetime.now(_tz()).strftime("%Y-%m-%d %H:%M:%S")


def now_dt() -> datetime:
    return datetime.now(_tz())


def add_days(days: float) -> str:
    return (now_dt() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 到期查询 ----------

def get_due_cards(conn: sqlite3.Connection, limit: int, now: str | None = None) -> list[dict]:
    """到期卡，按 学习态优先 → 最久未复习 → 越难越前 排序。"""
    now = now or now_str()
    rows = conn.execute(
        f"""
        SELECT * FROM cards
        WHERE status = 'active' AND due_at IS NOT NULL AND due_at <= ?
        ORDER BY {_STATE_ORDER}, due_at ASC, ease_factor ASC, id ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def due_count(conn: sqlite3.Connection, now: str | None = None) -> int:
    now = now or now_str()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM cards WHERE status='active' AND due_at IS NOT NULL AND due_at <= ?",
        (now,),
    ).fetchone()
    return int(row["n"])


def active_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE status='active'").fetchone()
    return int(row["n"])


# ---------- 复习反馈 ----------

def apply_rating(conn: sqlite3.Connection, card_id: int, rating: str, now: str | None = None) -> dict:
    """用户点「忘了/模糊/记住了」后更新卡片状态与历史。返回更新后的卡。"""
    if rating not in sm2.RATING_QUALITY:
        raise ValueError(f"未知反馈: {rating}")
    now = now or now_str()
    quality = sm2.RATING_QUALITY[rating]

    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        raise KeyError(f"卡片不存在: {card_id}")
    card = dict(card)

    reps, ease, interval = sm2.schedule(
        card["repetitions"], card["ease_factor"], card["interval_days"], quality
    )
    due = (datetime.strptime(now, "%Y-%m-%d %H:%M:%S") + timedelta(days=interval)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with db.transaction(conn):
        conn.execute(
            """
            INSERT INTO review_log(card_id, reviewed_at, rating, quality, interval_days, ease_factor, due_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (card_id, now, sm2.QUALITY_RATING[quality], quality, interval, ease, due),
        )
        conn.execute(
            """
            UPDATE cards SET
                repetitions = ?, ease_factor = ?, interval_days = ?, due_at = ?,
                state = CASE WHEN ? = 0 THEN 'learning' ELSE 'reviewing' END,
                updated_at = ?
            WHERE id = ?
            """,
            (reps, ease, interval, due, reps, now, card_id),
        )

    updated = dict(conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())
    return updated


# ---------- 确认入库 ----------

def confirm_card(conn: sqlite3.Connection, card_id: int, mode: str = "qa") -> dict:
    """把草稿卡（status=draft）转成 active，并安排首次复习。"""
    if mode not in ("qa", "readonly"):
        raise ValueError(f"未知模式: {mode}")
    now = now_str()
    card = conn.execute("SELECT * FROM cards WHERE id = ? AND status = 'draft'", (card_id,)).fetchone()
    if card is None:
        raise KeyError(f"草稿卡不存在或已处理: {card_id}")

    interval = sm2.first_interval_days()
    due = (datetime.strptime(now, "%Y-%m-%d %H:%M:%S") + timedelta(days=interval)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with db.transaction(conn):
        conn.execute(
            """
            UPDATE cards SET mode = ?, status = 'active', state = 'new',
                repetitions = 0, ease_factor = 2.5, interval_days = ?, due_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (mode, interval, due, now, card_id),
        )
    return dict(conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())
