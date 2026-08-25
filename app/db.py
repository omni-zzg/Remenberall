"""SQLite 数据层。

表结构：
- entries    原文条目（一次录入 = 一条 entry）
- cards      知识卡（question/answer/summary），status 区分 draft/active/archived/deleted
- review_log 复习历史（SM-2 全量记录，预留 FSRS 升级）
- app_config 运行时配置（键值）

存储设计说明：SM-2 的当前状态冗余在 cards 表（repetitions/ease_factor/interval_days/due_at），
同时 review_log 保存每一次完整历史。攒够数据后可基于 history 切换到 FSRS。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'text',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    status      TEXT NOT NULL DEFAULT 'raw'          -- raw | processed
);

CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id      INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    mode          TEXT NOT NULL DEFAULT 'qa',        -- qa | readonly（仅重读）
    status        TEXT NOT NULL DEFAULT 'draft',     -- draft | active | archived | deleted
    state         TEXT NOT NULL DEFAULT 'new',       -- new | learning | reviewing
    repetitions   INTEGER NOT NULL DEFAULT 0,
    ease_factor   REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    due_at        TEXT,
    tags          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS review_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reviewed_at   TEXT NOT NULL,
    rating        TEXT NOT NULL,                     -- forgot | fuzzy | remembered
    quality       INTEGER NOT NULL,                  -- SM-2 quality: 0/3/5
    interval_days REAL NOT NULL,
    ease_factor   REAL NOT NULL,
    due_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_due      ON cards(status, due_at);
CREATE INDEX IF NOT EXISTS idx_review_card    ON review_log(card_id);
CREATE INDEX IF NOT EXISTS idx_cards_entry    ON cards(entry_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- app_config ----------

def config_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def config_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_config(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------- 便捷查询 ----------

def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
