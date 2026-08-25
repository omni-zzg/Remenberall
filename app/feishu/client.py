"""飞书 Open API 客户端（发消息为主）。

发送走原生 HTTP：获取 tenant_access_token → POST im/v1/messages。
密钥只来自环境变量；请求/日志不输出 app_secret。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_OPEN_API = "https://open.feishu.cn/open-apis"
_TOKEN_URL = f"{_OPEN_API}/auth/v3/tenant_access_token/internal"
_MESSAGE_URL = f"{_OPEN_API}/im/v1/messages"


class FeishuError(Exception):
    pass


class FeishuClient:
    """线程安全：token 带锁缓存；发送是同步 HTTP，可在任意线程调用。"""

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expire: float = 0.0
        self._lock = __import__("threading").Lock()

    # ---------- token ----------

    def tenant_access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expire - 60:
                return self._token
            s = settings()
            resp = httpx.post(
                _TOKEN_URL,
                json={"app_id": s.feishu_app_id, "app_secret": s.feishu_app_secret},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(f"获取 tenant_access_token 失败: code={data.get('code')} msg={data.get('msg')}")
            self._token = data["tenant_access_token"]
            self._token_expire = time.time() + float(data.get("expire", 7200))
            return self._token

    # ---------- 发送 ----------

    def send_card(self, open_id: str, card: dict) -> bool:
        return self._send(open_id, "interactive", json.dumps(card, ensure_ascii=False))

    def send_text(self, open_id: str, text: str) -> bool:
        return self._send(open_id, "text", json.dumps({"text": text}, ensure_ascii=False))

    def _send(self, open_id: str, msg_type: str, content: str) -> bool:
        if not open_id:
            logger.warning("未配置 open_id，无法发送")
            return False
        try:
            resp = httpx.post(
                _MESSAGE_URL,
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {self.tenant_access_token()}"},
                json={"receive_id": open_id, "msg_type": msg_type, "content": content},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("发送失败: code=%s msg=%s", data.get("code"), data.get("msg"))
                return False
            return True
        except Exception as e:  # noqa: BLE001 - 发送失败不致命
            logger.warning("发送异常: %s", e)
            return False

    def push_cards(self, open_id: str, cards: list[dict]) -> tuple[int, int]:
        """连发多张卡，返回 (成功, 总数)。"""
        ok = 0
        for card in cards:
            if self.send_card(open_id, card):
                ok += 1
        return ok, len(cards)

    # ---------- 测试 ----------

    def echo(self, open_id: str) -> bool:
        return self.send_text(open_id, "✅ remenberall 推送链路正常")
