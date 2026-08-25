"""数据备份：导出 SQLite → 固定飞书在线文档（或聊天文件）。

主路径：追加写入固定 docx（首次自动创建，文档 ID 存 app_config）。
兜底路径：上传为飞书消息文件（聊天里可下载）。
无论哪种，本地 data/backups/ 都留一份。
备份内容只含知识库（entries/cards/review_log），不含任何密钥/配置。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx

from . import db
from .config import settings

logger = logging.getLogger(__name__)

_OPEN_API = "https://open.feishu.cn/open-apis"


def export_json(conn: sqlite3.Connection) -> str:
    """把所有业务表导出成 JSON 字符串。"""
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entries": [dict(r) for r in conn.execute("SELECT * FROM entries ORDER BY id").fetchall()],
        "cards": [dict(r) for r in conn.execute("SELECT * FROM cards ORDER BY id").fetchall()],
        "review_log": [
            dict(r) for r in conn.execute("SELECT * FROM review_log ORDER BY id").fetchall()
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _write_local(data: str, data_dir: Path) -> Path:
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    fname = datetime.now().strftime("backup-%Y%m%d-%H%M%S.json")
    path = backup_dir / fname
    path.write_text(data, encoding="utf-8")
    # 只保留最近 30 份本地备份
    files = sorted(backup_dir.glob("backup-*.json"))
    for old in files[:-30]:
        old.unlink(missing_ok=True)
    return path


def ensure_backup_doc(conn: sqlite3.Connection, token: str) -> str:
    """返回备份文档 ID。没有则自动创建一个。"""
    doc_id = db.config_get(conn, "backup_doc_id") or settings().backup_doc_id
    if doc_id:
        return doc_id
    resp = httpx.post(
        f"{_OPEN_API}/docx/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "remenberall 记忆库备份"},
        timeout=20,
    )
    data = resp.json()
    if data.get("code") == 0:
        doc_id = data["data"]["document"]["document_id"]
        db.config_set(conn, "backup_doc_id", doc_id)
        logger.info("已创建备份文档: %s", doc_id)
        return doc_id
    raise RuntimeError(f"创建备份文档失败: code={data.get('code')} msg={data.get('msg')}")


def _append_to_doc(token: str, doc_id: str, text: str) -> bool:
    """把一段文本追加到文档末尾。失败返回 False。"""
    # 找到文档的根 block
    resp = httpx.get(
        f"{_OPEN_API}/docx/v1/documents/{doc_id}/blocks",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    blocks = resp.json().get("data", {}).get("items", [])
    if not blocks:
        return False
    # 根 block：parent_id 为空的那个；追加到它下面
    root = next((b for b in blocks if not b.get("parent_id")), blocks[-1])
    block_id = root["block_id"]

    lines = text.splitlines()
    children = [
        {
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": line}}]},
        }
        for line in lines
        if line
    ]
    append = httpx.post(
        f"{_OPEN_API}/docx/v1/documents/{doc_id}/blocks/{block_id}/children",
        headers={"Authorization": f"Bearer {token}"},
        json={"children": children, "index": -1},
        timeout=30,
    )
    return append.json().get("code") == 0


def _upload_as_file(token: str, feishu, open_id: str, data: str) -> bool:
    """兜底：把备份作为文件消息发给用户。"""
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{_OPEN_API}/im/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                data={"file_type": "stream", "file_name": "remenberall-backup.json"},
                files={"file": ("backup.json", data.encode("utf-8"), "application/json")},
            )
        file_key = resp.json().get("data", {}).get("file_key")
        if not file_key:
            return False
        return feishu.send_card(
            open_id,
            {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "今日备份"}},
                "elements": [{"tag": "markdown", "content": "记忆库每日备份，见上方文件。"}],
            },
        ) or feishu.send_text(open_id, "备份已生成为消息文件（见上方附件）。")
    except Exception as e:  # noqa: BLE001
        logger.warning("上传备份文件失败: %s", e)
        return False


def run_backup(conn: sqlite3.Connection, feishu, open_id: str) -> dict:
    """执行一次完整备份。返回摘要。"""
    from .feishu import client as fs

    token = feishu.tenant_access_token()
    data = export_json(conn)
    local = _write_local(data, settings().data_dir)
    logger.info("本地备份完成: %s", local)

    uploaded = False
    # 主路径：固定文档
    try:
        doc_id = ensure_backup_doc(conn, token)
        if _append_to_doc(token, doc_id, f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n{data}"):
            uploaded = True
            logger.info("备份已追加到文档 %s", doc_id)
        else:
            logger.warning("文档追加失败，走文件兜底")
    except Exception as e:  # noqa: BLE001
        logger.warning("文档备份失败: %s", e)

    if not uploaded:
        uploaded = _upload_as_file(token, feishu, open_id, data)

    return {"local": str(local), "feishu": uploaded}
