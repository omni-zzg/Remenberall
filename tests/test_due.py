"""到期队列与反馈集成测试（纯本地，无外部依赖）。"""
import sqlite3

from app import db, due


def _conn(tmp_path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "test.sqlite3")
    db.init_db(conn)
    return conn


def _seed_card(conn, *, due_at, status="active", q="Q", a="A", mode="qa"):
    cur = conn.execute(
        """
        INSERT INTO cards(entry_id, question, answer, summary, mode, status, due_at)
        VALUES(NULL, ?, ?, '', ?, ?, ?)
        """,
        (q, a, mode, status, due_at),
    )
    conn.commit()
    return cur.lastrowid


def test_confirm_draft_to_active(tmp_path):
    conn = _conn(tmp_path)
    cid = _seed_card(conn, due_at="2099-01-01 00:00:00", status="draft")
    card = due.confirm_card(conn, cid, mode="qa")
    assert card["status"] == "active"
    assert card["mode"] == "qa"
    # 确认后立即到期，供下一时段推送
    assert card["due_at"] <= due.now_str()


def test_confirm_readonly_mode(tmp_path):
    conn = _conn(tmp_path)
    cid = _seed_card(conn, due_at="2099-01-01 00:00:00", status="draft")
    card = due.confirm_card(conn, cid, mode="readonly")
    assert card["mode"] == "readonly"
    assert card["status"] == "active"


def test_due_only_returns_past(tmp_path):
    conn = _conn(tmp_path)
    _seed_card(conn, due_at="2000-01-01 00:00:00", q="old")
    _seed_card(conn, due_at="2099-01-01 00:00:00", q="future")
    got = due.get_due_cards(conn, limit=10)
    assert [c["question"] for c in got] == ["old"]


def test_due_ordered_by_oldest_first(tmp_path):
    conn = _conn(tmp_path)
    c_new = _seed_card(conn, due_at="2000-01-02 00:00:00", q="newer")
    c_old = _seed_card(conn, due_at="2000-01-01 00:00:00", q="older")
    got = due.get_due_cards(conn, limit=10)
    assert [c["question"] for c in got] == ["older", "newer"]
    assert c_old != c_new


def test_apply_rating_success(tmp_path):
    conn = _conn(tmp_path)
    cid = _seed_card(conn, due_at="2000-01-01 00:00:00")
    updated = due.apply_rating(conn, cid, "remembered")
    assert updated["repetitions"] == 1
    assert updated["interval_days"] == 1.0
    log = conn.execute("SELECT * FROM review_log WHERE card_id = ?", (cid,)).fetchone()
    assert log is not None
    assert log["rating"] == "remembered"
    assert log["quality"] == 5


def test_apply_rating_forget_resets(tmp_path):
    conn = _conn(tmp_path)
    cid = _seed_card(conn, due_at="2000-01-01 00:00:00")
    due.apply_rating(conn, cid, "remembered")
    updated = due.apply_rating(conn, cid, "forgot")
    assert updated["repetitions"] == 0
    assert updated["interval_days"] == 1.0
    assert updated["state"] == "learning"


def test_due_count_and_active_count(tmp_path):
    conn = _conn(tmp_path)
    _seed_card(conn, due_at="2000-01-01 00:00:00")
    _seed_card(conn, due_at="2099-01-01 00:00:00")
    assert due.due_count(conn) == 1
    assert due.active_count(conn) == 2


def test_unknown_rating_raises(tmp_path):
    conn = _conn(tmp_path)
    cid = _seed_card(conn, due_at="2000-01-01 00:00:00")
    try:
        due.apply_rating(conn, cid, "nope")
        raise AssertionError("should have raised")
    except ValueError:
        pass
