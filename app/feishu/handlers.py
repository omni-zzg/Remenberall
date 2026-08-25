"""飞书交互的业务逻辑：录入 / 命令 / 卡片回调 / 主动推送。

所有函数是同步的；含 DeepSeek 调用的部分由调用方用 asyncio.to_thread 包装，
避免阻塞事件循环。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from .. import db, due, sm2
from . import cards as card_builder

logger = logging.getLogger(__name__)

_SPOT_CHECK_N = 5           # /抽查 随机数量
_RATING_DEDUP_SECONDS = 5   # 卡片回调防重复


# ---------- 用户识别 ----------

def ensure_user(conn: sqlite3.Connection, open_id: str) -> str:
    """记录用户 open_id，供主动推送使用。返回当前生效的 open_id。"""
    if open_id:
        db.config_set(conn, "user_open_id", open_id)
    return db.config_get(conn, "user_open_id", "") or open_id


def current_user(conn: sqlite3.Connection) -> str:
    from ..config import settings

    return db.config_get(conn, "user_open_id", "") or settings().feishu_user_open_id


# ---------- 录入 ----------

def ingest_text(conn: sqlite3.Connection, feishu, open_id: str, text: str) -> None:
    """用户发来普通文本 → 一条消息一张卡，内容原封不动 → 推送一张待确认卡。"""
    text = text.strip()
    if not text:
        return
    logger.info("录入内容，长度=%d", len(text))

    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO entries(source_text, source_type, status) VALUES(?, 'text', 'processed')",
            (text,),
        )
        entry_id = cur.lastrowid
        conn.execute(
            "INSERT INTO cards(entry_id, question, answer, summary, mode, status) "
            "VALUES(?, ?, '', '', 'qa', 'draft')",
            (entry_id, text),
        )

    drafts = _pending_drafts(conn, entry_id=entry_id)
    for i, c in enumerate(drafts, start=1):
        feishu.send_card(open_id, card_builder.draft_card(c, i, len(drafts)))


def _pending_drafts(conn: sqlite3.Connection, entry_id: int | None = None) -> list[dict]:
    if entry_id is not None:
        rows = conn.execute(
            "SELECT * FROM cards WHERE status='draft' AND entry_id=? ORDER BY id ASC", (entry_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards WHERE status='draft' ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def push_drafts(conn: sqlite3.Connection, feishu, open_id: str) -> None:
    drafts = _pending_drafts(conn)
    if not drafts:
        feishu.send_text(open_id, "没有待确认的草稿。")
        return
    total = len(drafts)
    for i, c in enumerate(drafts, start=1):
        feishu.send_card(open_id, card_builder.draft_card(c, i, total))


# ---------- 卡片回调 ----------

def handle_card_action(conn: sqlite3.Connection, feishu, open_id: str, value: dict) -> str:
    """处理卡片按钮回调。状态变化通过发新消息呈现，返回 toast 文案。"""
    action = (value or {}).get("a")
    card_id = (value or {}).get("id")
    if not action or card_id is None:
        return "未知操作"

    # 取当前卡，校验归属
    c = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if c is None:
        feishu.send_card(open_id, card_builder.text_card(f"卡片 #{card_id} 不存在。"))
        return "卡片不存在"
    c = dict(c)

    if action == "confirm":
        return _do_confirm(conn, feishu, open_id, card_id)
    if action == "discard":
        return _do_discard(conn, feishu, open_id, card_id)
    if action in sm2.RATING_QUALITY:
        if _recently_rated(conn, card_id, action):
            return "已记录过，请勿重复点击"
        updated = due.apply_rating(conn, card_id, action)
        feishu.send_card(open_id, card_builder.review_feedback_card(updated, action, updated["interval_days"]))
        return "已记录"
    return "未知操作"


def _recently_rated(conn: sqlite3.Connection, card_id: int, rating: str) -> bool:
    row = conn.execute(
        "SELECT reviewed_at, rating FROM review_log WHERE card_id=? ORDER BY id DESC LIMIT 1",
        (card_id,),
    ).fetchone()
    if row is None or row["rating"] != rating:
        return False
    t = datetime.strptime(row["reviewed_at"], "%Y-%m-%d %H:%M:%S")
    return datetime.now() - t < timedelta(seconds=_RATING_DEDUP_SECONDS)


def _do_confirm(conn: sqlite3.Connection, feishu, open_id: str, card_id: int) -> str:
    try:
        due.confirm_card(conn, card_id, mode="qa")
    except KeyError as e:
        feishu.send_card(open_id, card_builder.text_card(str(e), template="orange"))
        return "处理失败"
    feishu.send_card(open_id, card_builder.draft_done_card(confirmed=1, mode="qa"))
    return "已确认，进入复习队列"


def _do_discard(conn: sqlite3.Connection, feishu, open_id: str, card_id: int) -> str:
    with db.transaction(conn):
        conn.execute(
            "UPDATE cards SET status='archived', updated_at=datetime('now','localtime') WHERE id=?",
            (card_id,),
        )
    feishu.send_card(open_id, card_builder.draft_discarded_card())
    return "已丢弃"


# ---------- 命令 ----------

def handle_command(conn: sqlite3.Connection, feishu, open_id: str, cmd: str, rest: str) -> None:
    cmd = cmd.strip().lower()
    if cmd in ("复习", "review"):
        push_review(conn, feishu, open_id)
    elif cmd in ("草稿", "draft", "drafts"):
        push_drafts(conn, feishu, open_id)
    elif cmd in ("列表", "list"):
        _cmd_list(conn, feishu, open_id)
    elif cmd in ("统计", "stats"):
        feishu.send_card(open_id, card_builder.stats_card(build_stats(conn)))
    elif cmd in ("抽查", "spot"):
        _cmd_spot(conn, feishu, open_id)
    elif cmd in ("仅重读", "readonly"):
        _cmd_readonly(conn, feishu, open_id, rest)
    elif cmd in ("删除", "delete", "del"):
        _cmd_delete(conn, feishu, open_id, rest)
    elif cmd in ("编辑", "edit"):
        feishu.send_text(open_id, "v1 暂不支持编辑，可用 `/删除 <id>` 删除后重新录入。")
    elif cmd in ("帮助", "help", "?"):
        feishu.send_card(open_id, card_builder.help_card())
    else:
        feishu.send_card(open_id, card_builder.text_card(f"未知命令 `/{cmd}`，发 `/帮助` 查看说明。"))


def _cmd_list(conn: sqlite3.Connection, feishu, open_id: str) -> None:
    rows = conn.execute(
        """
        SELECT id, mode, status, question, due_at FROM cards
        WHERE status IN ('active','draft') ORDER BY status, id LIMIT 30
        """
    ).fetchall()
    if not rows:
        feishu.send_text(open_id, "还没有卡片。发一段文字试试。")
        return
    lines = []
    for r in rows:
        tag = {"active": "✅", "draft": "📝"}[r["status"]]
        mode = "仅重读" if r["mode"] == "readonly" else ""
        q = r["question"][:28]
        lines.append(f"{tag} #{r['id']} {mode} {q}  ·  到期 {r['due_at'] or '—'}")
    feishu.send_card(open_id, card_builder.text_card("\n".join(lines), header=f"卡片清单（{len(rows)}）"))


def _cmd_spot(conn: sqlite3.Connection, feishu, open_id: str) -> None:
    rows = conn.execute(
        "SELECT * FROM cards WHERE status='active' AND mode='qa' ORDER BY RANDOM() LIMIT ?",
        (_SPOT_CHECK_N,),
    ).fetchall()
    if not rows:
        feishu.send_text(open_id, "没有可抽查的卡片。")
        return
    feishu.send_text(open_id, f"随机抽查 {len(rows)} 张：")
    for r in rows:
        feishu.send_card(open_id, card_builder.review_card(dict(r)))


def _cmd_readonly(conn: sqlite3.Connection, feishu, open_id: str, rest: str) -> None:
    cid = _parse_id(rest)
    if cid is None:
        feishu.send_text(open_id, "用法：`/仅重读 <id>`，id 用 `/列表` 查看。")
        return
    with db.transaction(conn):
        conn.execute(
            "UPDATE cards SET mode='readonly', updated_at=datetime('now','localtime') "
            "WHERE id=? AND status='active'",
            (cid,),
        )
    feishu.send_text(open_id, f"卡片 #{cid} 已改为仅重读（不再出题）。")


def _cmd_delete(conn: sqlite3.Connection, feishu, open_id: str, rest: str) -> None:
    cid = _parse_id(rest)
    if cid is None:
        feishu.send_text(open_id, "用法：`/删除 <id>`，id 用 `/列表` 查看。")
        return
    with db.transaction(conn):
        conn.execute(
            "UPDATE cards SET status='archived', updated_at=datetime('now','localtime') WHERE id=?", (cid,)
        )
    feishu.send_text(open_id, f"卡片 #{cid} 已删除。")


def _parse_id(rest: str) -> int | None:
    token = (rest or "").strip().split()[0] if (rest or "").strip() else ""
    if token.isdigit():
        return int(token)
    return None


# ---------- 主动推送 ----------

def push_review(conn: sqlite3.Connection, feishu, open_id: str, limit: int | None = None) -> None:
    """把到期卡推成复习卡。limit 为空则用配置的 DAILY_CAP。"""
    from ..config import settings

    cap = limit or settings().daily_cap
    due_cards = due.get_due_cards(conn, limit=cap)
    if not due_cards:
        feishu.send_text(open_id, "现在没有到期卡，休息一下 ☕")
        return

    cards_to_send = [card_builder.review_card(c) for c in due_cards]
    feishu.push_cards(open_id, cards_to_send)
    # 不推「完成」卡：用户没答完之前说"完成"很误导。
    # 答完与否由复习卡按钮的反馈自然体现。


def push_spot_check(conn: sqlite3.Connection, feishu, open_id: str) -> None:
    _cmd_spot(conn, feishu, open_id)


# ---------- 统计 ----------

def build_stats(conn: sqlite3.Connection) -> dict:
    now = due.now_str()
    week_ago = (due.now_dt() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "total": db.row_to_dict(
            conn.execute("SELECT COUNT(*) AS n FROM cards WHERE status='active'").fetchone()
        )["n"],
        "due": due.due_count(conn, now),
        "mastered": db.row_to_dict(
            conn.execute(
                "SELECT COUNT(*) AS n FROM cards WHERE status='active' AND interval_days >= 21"
            ).fetchone()
        )["n"],
        "reviewed_7d": db.row_to_dict(
            conn.execute("SELECT COUNT(*) AS n FROM review_log WHERE reviewed_at >= ?", (week_ago,)).fetchone()
        )["n"],
        "drafts": db.row_to_dict(
            conn.execute("SELECT COUNT(*) AS n FROM cards WHERE status='draft'").fetchone()
        )["n"],
    }
