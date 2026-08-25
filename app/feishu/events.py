"""飞书事件接入：WebSocket 长连接（lark-oapi ≥1.7）。

用 ws.Client 的 event_handler 同时接收：
- im.message.receive_v1  → P2ImMessageReceiveV1
- card.action.trigger   → P2CardActionTrigger（卡片按钮回调，作为事件订阅）

关键点：卡片回调的返回值是 P2CardActionTriggerResponse（toast），
状态变化通过「发新消息」完成（避免原地更新卡片格式随 SDK 版本漂移）。
SDK 会自动断线重连（auto_reconnect）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTriggerResponse,
)

from ..config import settings
from . import handlers
from .client import FeishuClient

logger = logging.getLogger(__name__)


# ---------- 防御式字段提取 ----------

def _extract_open_id(event) -> str:
    try:
        return str(event.event.sender.sender_id.open_id)
    except Exception:  # noqa: BLE001
        logger.warning("无法解析 sender open_id")
        return ""


def _extract_message(event) -> tuple[str, str]:
    try:
        return str(event.event.message.message_type), str(event.event.message.content)
    except Exception:  # noqa: BLE001
        return "", ""


def _card_open_id(data) -> str:
    try:
        return str(data.event.operator.open_id)
    except Exception:  # noqa: BLE001
        return ""


def _card_value(data) -> dict:
    try:
        v = data.event.action.value
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _text_of(content_json: str) -> str:
    try:
        return json.loads(content_json).get("text", "")
    except Exception:  # noqa: BLE001
        return content_json


def _parse_command(text: str) -> tuple[str | None, str | None]:
    """以 / 开头视为命令，返回 (命令, 参数)；否则 (None, 原文)。"""
    text = text.strip()
    if text.startswith("/") and len(text) > 1:
        parts = text.split(None, 1)
        return parts[0][1:], (parts[1] if len(parts) > 1 else "")
    return None, text


# ---------- WS 构建 ----------

def _new_ws(conn: sqlite3.Connection, feishu: FeishuClient):
    s = settings()

    def on_message(event):
        open_id = _extract_open_id(event)
        if not open_id:
            return
        handlers.ensure_user(conn, open_id)
        open_id = handlers.current_user(conn)

        msg_type, content = _extract_message(event)
        if msg_type == "text":
            cmd, text = _parse_command(_text_of(content))
            if cmd is not None:
                logger.info("命令: /%s %s", cmd, text)
                handlers.handle_command(conn, feishu, open_id, cmd, text)
            else:
                logger.info("收到文本录入，长度=%d", len(text))
                asyncio.create_task(
                    asyncio.to_thread(handlers.ingest_text, conn, feishu, open_id, text)
                )
        elif msg_type == "file":
            feishu.send_text(open_id, "v1 只支持文字内容，文件解析接口已预留，后续版本开放。")
        else:
            logger.info("忽略消息类型: %s", msg_type)

    def on_card_action(data) -> P2CardActionTriggerResponse:
        open_id = _card_open_id(data)
        value = _card_value(data)
        logger.info("卡片回调: %s", value)
        toast = handlers.handle_card_action(conn, feishu, open_id, value)
        # 注意：这两个类只接受「普通 dict」形式的 d 参数，传关键字或已构造对象都会抛错
        return P2CardActionTriggerResponse({
            "toast": {"type": "success", "content": toast or "已处理"},
        })

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )

    return lark.ws.Client(
        s.feishu_app_id,
        s.feishu_app_secret,
        event_handler=event_handler,
        auto_reconnect=True,
        log_level=lark.LogLevel.INFO,
    )


def start(conn: sqlite3.Connection, feishu: FeishuClient) -> threading.Thread:
    """在后台线程启动 WebSocket 长连接（阻塞式 start）。"""
    ws = _new_ws(conn, feishu)

    def _run():
        logger.info("飞书长连接启动")
        try:
            ws.start()
        except Exception:  # noqa: BLE001
            logger.exception("飞书长连接异常退出（SDK 会自动重连则忽略）")

    t = threading.Thread(target=_run, name="feishu-ws", daemon=True)
    t.start()
    return t
