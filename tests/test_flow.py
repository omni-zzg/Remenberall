"""端到端流程测试（伪造飞书，不触网）。

新规则：一条消息 = 一张知识卡，内容原封不动，无 AI 抽取。
覆盖：录入 → 待确认卡 → 确认 → 到期 → 复习反馈。
"""
import sqlite3

import pytest

from app import db
from app.feishu import handlers as h


class FakeFeishu:
    def __init__(self):
        self.sent = []  # [(kind, payload)]

    def send_card(self, open_id, card):
        self.sent.append(("card", card))
        return True

    def send_text(self, open_id, text):
        self.sent.append(("text", text))
        return True

    def push_cards(self, open_id, cards):
        for c in cards:
            self.sent.append(("card", c))
        return len(cards), len(cards)


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.init_db(conn)
    return conn, FakeFeishu()


def test_full_flow(env):
    conn, feishu = env
    open_id = "ou_test"

    # 1. 录入 → 一条消息一张卡，内容原封不动
    text = "艾宾浩斯遗忘曲线指出信息在学习后很快就会遗忘。"
    h.ingest_text(conn, feishu, open_id, text)
    drafts = conn.execute("SELECT * FROM cards WHERE status='draft'").fetchall()
    assert len(drafts) == 1
    assert drafts[0]["question"] == text
    assert sum(1 for k, _ in feishu.sent if k == "card") == 1

    # 2. 确认
    cid = drafts[0]["id"]
    toast = h.handle_card_action(conn, feishu, open_id, {"a": "confirm", "id": cid})
    assert "确认" in toast
    card = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    assert card["status"] == "active"

    # 3. 到期后可复习（确认后 due_at=now，应能被 /复习 推出）
    feishu.sent.clear()
    h.push_review(conn, feishu, open_id, limit=5)
    sent_cards = [p for k, p in feishu.sent if k == "card"]
    assert len(sent_cards) >= 1
    assert any(c.get("header", {}).get("title", {}).get("content", "").startswith("复习") for c in sent_cards)

    # 4. 反馈
    h.handle_card_action(conn, feishu, open_id, {"a": "remembered", "id": cid})
    log = conn.execute("SELECT * FROM review_log WHERE card_id=?", (cid,)).fetchall()
    assert len(log) == 1
    assert log[0]["rating"] == "remembered"
    updated = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    assert updated["repetitions"] == 1
    assert updated["interval_days"] == 1.0

    # 5. 重复点击防抖
    before = len(conn.execute("SELECT * FROM review_log").fetchall())
    h.handle_card_action(conn, feishu, open_id, {"a": "remembered", "id": cid})
    after = len(conn.execute("SELECT * FROM review_log").fetchall())
    assert before == after


def test_discard_then_confirm(env):
    conn, feishu = env
    # 第一条 → 丢弃
    h.ingest_text(conn, feishu, "ou_t", "内容一……")
    draft = conn.execute("SELECT * FROM cards WHERE status='draft'").fetchone()
    h.handle_card_action(conn, feishu, "ou_t", {"a": "discard", "id": draft["id"]})
    assert conn.execute("SELECT status FROM cards WHERE id=?", (draft["id"],)).fetchone()["status"] == "archived"

    # 第二条 → 确认
    h.ingest_text(conn, feishu, "ou_t", "内容二……")
    draft2 = conn.execute("SELECT * FROM cards WHERE status='draft' ORDER BY id DESC").fetchone()
    h.handle_card_action(conn, feishu, "ou_t", {"a": "confirm", "id": draft2["id"]})
    card = conn.execute("SELECT * FROM cards WHERE id=?", (draft2["id"],)).fetchone()
    assert card["status"] == "active"


def test_command_review_empty(env):
    conn, feishu = env
    h.push_review(conn, feishu, "ou_t", limit=5)
    assert any(k == "text" for k, _ in feishu.sent)
