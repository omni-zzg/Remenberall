"""飞书消息卡片构建（纯函数，card schema 2.0）。

按钮 value 统一用 {"a": action, "id": card_id}，回调据此路由。
"""
from __future__ import annotations

from .. import sm2

# ---------- 通用构件 ----------

def _button(text: str, action: str, card_id: int, *, primary: bool = False, danger: bool = False) -> dict:
    btn_type = "danger" if danger else ("primary" if primary else "default")
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": {"a": action, "id": card_id},
    }


def _markdown(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _note(content: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def _card(header_text: str, elements: list[dict], template: str = "blue") -> dict:
    # 用 v1 卡片格式（无 schema 字段）。实测该飞书租户对 schema 2.0 卡片
    # 返回 200621 解析错误，v1 格式稳定可用。
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": template,
        },
        "elements": elements,
    }


def _markdown_escape(text: str) -> str:
    """飞书 markdown 里特殊字符转义，避免破坏卡片排版。"""
    return (text or "").replace("|", "\\|")


# ---------- 草稿确认卡 ----------

def draft_card(c: dict, index: int, total: int) -> dict:
    cid = c["id"]
    elements = [
        _markdown(f"**Q{index}** · {_markdown_escape(c['question'])}"),
        _markdown(f"**答案** {_markdown_escape(c['answer'])}"),
    ]
    if c.get("summary"):
        elements.append(_markdown(f"**摘要** {_markdown_escape(c['summary'])}"))
    elements.append(
        {
            "tag": "action",
            "actions": [
                _button("确认", "confirm", cid, primary=True),
                _button("仅重读", "readonly", cid),
                _button("丢弃", "discard", cid, danger=True),
                _button("重写", "rewrite", cid),
            ],
        }
    )
    return _card(f"待确认草稿 · {index}/{total}", elements, template="grey")


def draft_done_card(confirmed: int, mode: str) -> dict:
    txt = "已确认，进入复习队列" if mode == "qa" else "已确认，仅重读不出题"
    return _card("已入库", [_markdown(txt)], template="green")


def draft_discarded_card() -> dict:
    return _card("已丢弃", [_markdown("这条草稿已丢弃。")], template="grey")


def drafts_remain_card(remaining: int) -> dict:
    return _card(
        "还有草稿待处理",
        [_markdown(f"还有 {remaining} 条草稿未确认。发 `/草稿` 查看全部。")],
        template="orange",
    )


# ---------- 复习卡（两阶段） ----------

def review_question_card(c: dict) -> dict:
    """第一阶段：只显示问题。"""
    elements = [
        _markdown(f"**{_markdown_escape(c['question'])}**"),
        _note("先在脑子里想答案，再点「显示答案」"),
        {
            "tag": "action",
            "actions": [_button("显示答案", "reveal", c["id"], primary=True)],
        },
    ]
    return _card("复习 · 先想再答", elements, template="blue")


def review_answer_card(c: dict) -> dict:
    """第二阶段：答案 + 摘要 + 反馈按钮。"""
    elements = [
        _markdown(f"**{_markdown_escape(c['question'])}**"),
        _markdown(f"**答案** {_markdown_escape(c['answer'])}"),
    ]
    if c.get("summary"):
        elements.append(_markdown(f"**摘要** {_markdown_escape(c['summary'])}"))
    elements.append(
        {
            "tag": "action",
            "actions": [
                _button("记住了", "remembered", c["id"], primary=True),
                _button("模糊", "fuzzy", c["id"]),
                _button("忘了", "forgot", c["id"], danger=True),
            ],
        }
    )
    return _card("复习 · 对答案", elements, template="indigo")


def review_feedback_card(c: dict, rating: str, next_interval: float) -> dict:
    label = {"remembered": "记住了", "fuzzy": "模糊", "forgot": "忘了"}[rating]
    tpl = "green" if rating == "remembered" else ("orange" if rating == "fuzzy" else "red")
    return _card(
        "已记录",
        [
            _markdown(f"反馈：**{label}**"),
            _markdown(f"下次复习：**{sm2.human_interval(next_interval)}**"),
        ],
        template=tpl,
    )


def review_done_card(total: int, remaining: int) -> dict:
    if remaining > 0:
        body = f"本轮 {total} 张复习完，还剩 {remaining} 张未处理，下一时段继续。"
    else:
        body = f"本轮 {total} 张全部复习完 🎉"
    return _card("本轮复习完成", [_markdown(body)], template="green")


# ---------- 其他 ----------

def readonly_card(c: dict) -> dict:
    """仅重读条目的复习卡：只推摘要，不出题。"""
    elements = [_markdown(f"**{_markdown_escape(c['question'])}**")]
    if c.get("summary"):
        elements.append(_markdown(_markdown_escape(c["summary"])))
    elements.append(
        {
            "tag": "action",
            "actions": [
                _button("再看一遍了", "remembered", c["id"], primary=True),
                _button("模糊", "fuzzy", c["id"]),
                _button("忘了", "forgot", c["id"], danger=True),
            ],
        }
    )
    return _card("重读 · 加深印象", elements, template="turquoise")


def stats_card(stats: dict) -> dict:
    lines = [
        f"**累计收录** {stats['total']} 张",
        f"**今天到期** {stats['due']} 张",
        f"**熟练** ≥21 天间隔 {stats['mastered']} 张",
        f"**近期复习** 7 天 {stats['reviewed_7d']} 次",
        f"**草稿待处理** {stats['drafts']} 张",
    ]
    return _card("统计概览", [_markdown("\n".join(lines))], template="blue")


def help_card() -> dict:
    body = (
        "**给机器人发消息就是操作它**\n\n"
        "· 普通文字/文章 → 提炼成复习卡\n"
        "· 草稿卡按钮 → 确认/仅重读/丢弃/重写\n"
        "· 复习卡按钮 → 记住了/模糊/忘了\n\n"
        "**命令（`/` 开头）**\n"
        "`/复习` 现在来一轮 · `/草稿` 查看待确认草稿\n"
        "`/列表` 卡片清单 · `/统计` 数据概览\n"
        "`/抽查` 随机抽测 · `/仅重读 <id>` 改仅重读\n"
        "`/删除 <id>` 删卡 · `/编辑 <id>` 编辑（v1 暂用删除重录）\n"
        "`/帮助` 本说明"
    )
    return _card("remenberall · 使用说明", [_markdown(body)], template="blue")


def text_card(text: str, header: str = "remenberall", template: str = "blue") -> dict:
    return _card(header, [_markdown(text)], template=template)


def simple_text(text: str) -> str:
    return text
