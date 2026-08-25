"""把用户发来的文本提炼成候选知识卡（草稿）。

流程：文本 → DeepSeek → 结构化 {question, answer, summary} 列表。
"""
from __future__ import annotations

import logging

from .client import AIError, chat_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是知识提炼助手。用户会把想记住的内容发给你，你把它提炼成若干条「问题→答案」的复习卡片。\n"
    "要求：\n"
    "1. 只提炼真正的知识要点，去掉客套、流水账和无关细节。\n"
    "2. 问题要具体、可自测（能用来判断自己是否真的记住）。\n"
    "3. 答案简洁准确，一句话到几句话即可，基于原文，不要编造。\n"
    "4. summary 是对该条要点的一句话总结，用于被动重读。\n"
    "5. 数量控制在 3~10 条；如果内容很短，少于 3 条也可以。\n"
    "6. 只输出 JSON，不要任何额外说明。"
)


def build_messages(text: str, instruction: str | None = None) -> list[dict]:
    user = f"内容：\n{text}"
    if instruction:
        user = f"{instruction}\n\n{user}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extract_cards(text: str, instruction: str | None = None) -> list[dict]:
    """抽取候选卡片。返回 [{question, answer, summary}]，已清洗校验。"""
    text = (text or "").strip()
    if not text:
        return []

    data = chat_json(build_messages(text, instruction))
    raw = data.get("cards")
    if not isinstance(raw, list):
        # 部分模型会直接给数组顶层
        raw = data if isinstance(data, list) else []

    cards = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        q = _clean(item.get("question"))
        a = _clean(item.get("answer"))
        s = _clean(item.get("summary"))
        if not q or not a:
            continue
        cards.append({"question": q, "answer": a, "summary": s})
    return cards[:10]


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_interface():
    """预留：不同来源（txt / pdf / 网页）的解析入口。

    v1 默认只接收纯文本。后续在此扩展各 source_type 的解析器。
    当前实现直接透传文本。
    """
    raise NotImplementedError("v1 仅支持文本，其他解析接口待后续补充")


__all__ = ["extract_cards", "parse_interface", "AIError"]
