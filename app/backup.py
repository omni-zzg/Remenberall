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
import lark_oapi as lark
from lark_oapi.api.docx.v1.model.block import Block
from lark_oapi.api.docx.v1.model.create_document_block_children_request import (
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
)
from lark_oapi.api.docx.v1.model.text import Text
from lark_oapi.api.docx.v1.model.text_element import TextElement
from lark_oapi.api.docx.v1.model.text_run import TextRun

from . import db
from .config import settings

logger = logging.getLogger(__name__)

_OPEN_API = "https://open.feishu.cn/open-apis"

_lark_client_obj: lark.Client | None = None


def _get_lark_client() -> lark.Client:
    global _lark_client_obj
    if _lark_client_obj is None:
        s = settings()
        _lark_client_obj = (
            lark.Client.builder().app_id(s.feishu_app_id).app_secret(s.feishu_app_secret).build()
        )
    return _lark_client_obj


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


def _resolve_wiki_node(token: str, wiki_token: str) -> str:
    """把 wiki 页面节点解析成底层 docx document_id。"""
    resp = httpx.get(
        f"{_OPEN_API}/wiki/v2/spaces/get_node",
        params={"token": wiki_token},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"解析 wiki 节点失败: code={data.get('code')} msg={data.get('msg')}")
    node = data.get("data", {}).get("node", {})
    if node.get("obj_type") != "docx":
        raise RuntimeError(f"wiki 节点不是文档(docx): {node.get('obj_type')}")
    return node["obj_token"]


def ensure_backup_doc(conn: sqlite3.Connection, token: str) -> str:
    """返回备份文档 ID。优先级：已缓存 > wiki 页面 > 自动创建。"""
    doc_id = db.config_get(conn, "backup_doc_id") or settings().backup_doc_id
    if doc_id:
        return doc_id

    wiki = settings().backup_wiki_token or db.config_get(conn, "backup_wiki_token")
    if wiki:
        doc_id = _resolve_wiki_node(token, wiki)
        db.config_set(conn, "backup_doc_id", doc_id)
        logger.info("备份目标: wiki 页面 → docx %s", doc_id)
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


_MAX_TEXT_RUN = 1500  # 飞书 text_run content 上限 2000，留余量
_BATCH_SIZE = 20      # 单次请求 children 数量上限（实测 314 块一次会校验失败）


def _append_to_doc(doc_id: str, text: str) -> bool:
    """把一段文本按行追加到文档末尾。用 SDK 构造块，分批发送。"""
    children = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # 超长行（如文章全文）切成多块，避免 text_run 长度超限
        for start in range(0, len(line), _MAX_TEXT_RUN):
            children.append(
                Block.builder()
                .block_type(2)
                .text(
                    Text.builder()
                    .elements(
                        [
                            TextElement.builder()
                            .text_run(TextRun.builder().content(line[start : start + _MAX_TEXT_RUN]).build())
                            .build()
                        ]
                    )
                    .build()
                )
                .build()
            )
    if not children:
        return True

    client = _get_lark_client()
    ok = True
    for i in range(0, len(children), _BATCH_SIZE):
        batch = children[i : i + _BATCH_SIZE]
        body = CreateDocumentBlockChildrenRequestBody.builder().children(batch).index(-1).build()
        req = (
            CreateDocumentBlockChildrenRequest.builder()
            .document_id(doc_id)
            .block_id(doc_id)  # 根 block id 即文档 id
            .request_body(body)
            .build()
        )
        resp = client.docx.v1.document_block_children.create(req)
        if not resp.success():
            logger.warning("文档追加被拒(第%d批): code=%s msg=%s", i // _BATCH_SIZE + 1, resp.code, resp.msg)
            ok = False
    return ok


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
                "config": {"wide_screen_mode": True},
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
        if _append_to_doc(doc_id, f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n{data}"):
            uploaded = True
            logger.info("备份已追加到文档 %s", doc_id)
        else:
            logger.warning("文档追加失败，走文件兜底")
    except Exception as e:  # noqa: BLE001
        logger.warning("文档备份失败: %s", e)

    if not uploaded:
        uploaded = _upload_as_file(token, feishu, open_id, data)

    return {"local": str(local), "feishu": uploaded}
