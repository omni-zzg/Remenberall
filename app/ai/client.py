"""DeepSeek HTTP 客户端。

密钥只从环境变量读取（见 config.py），请求体/日志不输出密钥。
失败时只记状态码与简短原因，不记录请求体。
"""
from __future__ import annotations

import json
import time

import httpx

from ..config import settings

_DEFAULT_TIMEOUT = 60.0
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2


class AIError(Exception):
    """AI 调用失败（网络/鉴权/模型错误）。"""


def _headers() -> dict:
    s = settings()
    return {
        "Authorization": f"Bearer {s.deepseek_api_key}",
        "Content-Type": "application/json",
    }


def chat_json(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    json_mode: bool = True,
) -> dict:
    """调用 DeepSeek，期望返回 JSON 对象。带有限重试。"""
    s = settings()
    payload: dict = {
        "model": s.deepseek_model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        # deepseek-chat 支持 json_object 输出
        payload["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
                resp = client.post(
                    f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
                    headers=_headers(),
                    json=payload,
                )
        except httpx.HTTPError as e:
            last_err = e
            _sleep_backoff(attempt)
            continue

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            try:
                return _parse_json_object(content)
            except ValueError as e:
                raise AIError(f"模型返回不是合法 JSON: {e}") from e

        if resp.status_code in _RETRY_STATUS:
            last_err = AIError(f"上游 {resp.status_code}")
            _sleep_backoff(attempt)
            continue

        # 4xx 鉴权/参数错误，重试无意义
        raise AIError(f"DeepSeek HTTP {resp.status_code}（不重试）")

    raise AIError(f"重试 {_MAX_RETRIES} 次仍失败: {last_err}")


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    # 去掉可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兜底：截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _sleep_backoff(attempt: int) -> None:
    time.sleep(1.5 * (attempt + 1))
