"""端到端流程测试（伪造飞书与 AI，不触网）。

覆盖：录入 → AI 提炼 → 草稿卡 → 确认 → 到期 → 复习反馈。
"""
import sqlite3

import pytest

from app import db
from app.feishu import handlers as h
from app.feishu import cards as cb


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


FAKE_CARDS = [
    {"question": "什么是 SM-2？", "answer": "SuperMemo 的间隔重复算法", "summary": "SM-2 是经典 SRS 算法"},
    {"question": "遗忘曲线由谁提出？", "answer": "艾宾浩斯", "summary": "艾宾浩斯遗忘曲线"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.init_db(conn)
    feishu = FakeFeishu()

    # handlers 里是 `from ..ai.extract import extract_cards`（本地绑定），
    # 所以要 patch handlers.extract_cards 而不是 app.ai.extract
    monkeypatch.setattr(h, "extract_cards", lambda text, instruction=None: list(FAKE_CARDS))
    return conn, feishu


def test_full_flow(env):
    conn, feishu = env
    open_id = "ou_test"

    # 1. 录入 → 草稿
    h.ingest_text(conn, feishu, open_id, "这是一段关于记忆的文章……")
    drafts = conn.execute("SELECT * FROM cards WHERE status='draft'").fetchall()
    assert len(drafts) == 2
    assert sum(1 for k, _ in feishu.sent if k == "card") == 2

    # 2. 确认第一张
    cid = drafts[0]["id"]
    toast = h.handle_card_action(conn, feishu, open_id, {"a": "confirm", "id": cid})
    assert "确认" in toast
    card = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    assert card["status"] == "active"
    assert card["mode"] == "qa"

    # 3. 到期后可复习（确认后的卡 due_at=now，应能被 /复习 推出）
    feishu.sent.clear()
    h.push_review(conn, feishu, open_id, limit=5)
    sent_cards = [p for k, p in feishu.sent if k == "card"]
    assert len(sent_cards) >= 1
    assert any(c.get("header", {}).get("title", {}).get("content", "").startswith("复习") for c in sent_cards)

    # 4. 两阶段：显示答案 → 反馈
    # 找到刚推送的复习卡里 id（answer 卡没到，先构造 reveal）
    active = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    h.handle_card_action(conn, feishu, open_id, {"a": "reveal", "id": active["id"]})
    h.handle_card_action(conn, feishu, open_id, {"a": "remembered", "id": active["id"]})

    log = conn.execute("SELECT * FROM review_log WHERE card_id=?", (cid,)).fetchall()
    assert len(log) == 1
    assert log[0]["rating"] == "remembered"
    updated = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    assert updated["repetitions"] == 1
    assert updated["interval_days"] == 1.0

    # 5. 重复点击防抖
    before = len(conn.execute("SELECT * FROM review_log").fetchall())
    h.handle_card_action(conn, feishu, open_id, {"a": "remembered", "id": active["id"]})
    after = len(conn.execute("SELECT * FROM review_log").fetchall())
    assert before == after


def test_discard_and_readonly(env):
    conn, feishu = env
    h.ingest_text(conn, feishu, "ou_t", "内容……")
    drafts = conn.execute("SELECT * FROM cards WHERE status='draft'").fetchall()

    # 丢弃第一张
    h.handle_card_action(conn, feishu, "ou_t", {"a": "discard", "id": drafts[0]["id"]})
    assert conn.execute("SELECT status FROM cards WHERE id=?", (drafts[0]["id"],)).fetchone()["status"] == "archived"

    # 第二张仅重读
    h.handle_card_action(conn, feishu, "ou_t", {"a": "readonly", "id": drafts[1]["id"]})
    card = conn.execute("SELECT * FROM cards WHERE id=?", (drafts[1]["id"],)).fetchone()
    assert card["mode"] == "readonly"
    assert card["status"] == "active"


def test_command_review_empty(env):
    conn, feishu = env
    h.push_review(conn, feishu, "ou_t", limit=5)
    assert any(k == "text" for k, _ in feishu.sent)
