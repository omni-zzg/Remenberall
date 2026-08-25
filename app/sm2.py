"""SM-2 间隔重复算法。

来源：SuperMemo SM-2（Anki 经典算法）。三档按钮映射到 SM-2 quality：
    忘了 → 0，模糊 → 3，记住了 → 5

复习历史全量落库（app.review_log），为二期切换到 FSRS 预留数据。
"""
from __future__ import annotations

MIN_EASE = 1.3

# 按钮 → quality
RATING_QUALITY = {
    "forgot": 0,
    "fuzzy": 3,
    "remembered": 5,
}
# quality → 按钮（反向，用于展示）
QUALITY_RATING = {v: k for k, v in RATING_QUALITY.items()}


def schedule(
    repetitions: int,
    ease_factor: float,
    interval_days: float,
    quality: int,
) -> tuple[int, float, float]:
    """给定复习前的状态和本次质量，返回 (新 repetitions, 新 ease, 新 interval_days)。"""
    if quality < 3:
        # 失败：重置次数，回到 1 天
        repetitions = 0
        interval = 1.0
    else:
        if repetitions == 0:
            interval = 1.0
        elif repetitions == 1:
            interval = 6.0
        else:
            interval = round(interval_days * ease_factor, 1)
        repetitions += 1

    # SM-2 难度修正（经典公式，成功失败都更新）
    ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease = max(MIN_EASE, round(ease, 2))

    return repetitions, ease, interval


def first_interval_days() -> float:
    """确认入库的新卡首次复习间隔（以天计）。

    设为 0：确认后立即到期，会在下一个推送时段（当天 18:00 或次日 07:00）出现。
    """
    return 0.0


def human_interval(interval_days: float) -> str:
    """把间隔天数变成人话：<1 天 / X 天 / X 个月。"""
    if interval_days < 1:
        return f"{int(round(interval_days * 24))} 小时"
    if interval_days < 30:
        return f"{int(round(interval_days))} 天"
    if interval_days < 365:
        return f"{round(interval_days / 30, 1)} 个月"
    return f"{round(interval_days / 365, 1)} 年"
