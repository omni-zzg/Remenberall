"""SM-2 算法单元测试。"""
from app import sm2


def test_first_success():
    reps, ease, interval = sm2.schedule(0, 2.5, 0.0, 5)
    assert reps == 1
    assert interval == 1.0
    assert ease == 2.6  # 2.5 + 0.1


def test_second_success():
    reps, ease, interval = sm2.schedule(1, 2.6, 1.0, 5)
    assert reps == 2
    assert interval == 6.0


def test_third_success_multiplies_by_ease():
    reps, ease, interval = sm2.schedule(2, 2.7, 6.0, 5)
    assert reps == 3
    assert interval == 16.2  # 6 * 2.7


def test_fuzzy_is_pass_but_lower_ease():
    reps, ease, interval = sm2.schedule(2, 2.7, 6.0, 3)
    assert reps == 3
    assert interval == 16.2
    assert ease == round(2.7 + 0.1 - (2 * (0.08 + 2 * 0.02)), 2)  # 2.7 + 0.1 - 0.24 = 2.56


def test_failure_resets():
    reps, ease, interval = sm2.schedule(5, 2.5, 30.0, 0)
    assert reps == 0
    assert interval == 1.0
    assert ease == 1.7  # 2.5 + (0.1 - 0.8)


def test_ease_floor():
    reps, ease, interval = sm2.schedule(3, 1.3, 10.0, 0)
    assert ease == 1.3  # 1.3 + (0.1 - 0.8) = 0.6 → clamp 1.3


def test_rating_quality_map():
    assert sm2.RATING_QUALITY == {"forgot": 0, "fuzzy": 3, "remembered": 5}
    assert sm2.QUALITY_RATING[5] == "remembered"
