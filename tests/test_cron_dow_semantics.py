"""Cron day-of-week 语义转换测试。

APScheduler 3.x 的 ``CronTrigger.from_crontab`` 把 day-of-week 数字按 Python
weekday 约定解析（0=Monday），而标准 crontab 约定 0/7=Sunday。cron_sched.py
里的 ``_convert_crontab_dow`` 负责把标准 crontab 语义转换为 APScheduler 语义。
本测试锁定该转换的正确性，防止回归。
"""

import datetime

import pytest
from apscheduler.triggers.cron import CronTrigger

from seed.integrations.cron_sched import _convert_crontab_dow

# 基准时刻：2026-08-06（周四）14:00 Asia/Shanghai
_BASE = datetime.datetime(2026, 8, 6, 14, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))


def _next_fire(expr: str) -> datetime.datetime:
    conv = _convert_crontab_dow(expr)
    trigger = CronTrigger.from_crontab(conv, timezone="Asia/Shanghai")
    return trigger.get_next_fire_time(None, _BASE)


@pytest.mark.parametrize(
    "expr, expected",
    [
        # 数字语义：标准 crontab 0=Sunday
        ("0 9 * * 0", "2026-08-09T09:00:00+08:00"),  # 周日
        ("0 9 * * 1", "2026-08-10T09:00:00+08:00"),  # 周一
        ("0 9 * * 6", "2026-08-08T09:00:00+08:00"),  # 周六
        ("0 9 * * 7", "2026-08-09T09:00:00+08:00"),  # 7=周日（兼容写法）
        # 范围 / 列表
        ("0 9 * * 1-5", "2026-08-07T09:00:00+08:00"),  # 周一~周五 → 次日周五
        ("0 9 * * 0,6", "2026-08-08T09:00:00+08:00"),  # 周日+周六 → 次日周六
        ("0 9 * * 0-6", "2026-08-07T09:00:00+08:00"),  # 整周 → 次日
        # 步进
        ("0 9 * * 1-5/2", "2026-08-07T09:00:00+08:00"),  # 周一三五 → 次日周五
        ("0 9 * * */2", "2026-08-08T09:00:00+08:00"),  # 周日二四六 → 次日周六
        # 文本名（两种语义一致，保持原样）
        ("0 9 * * sun", "2026-08-09T09:00:00+08:00"),
        ("0 9 * * mon-fri", "2026-08-07T09:00:00+08:00"),
        # 不含 day-of-week 的表达式不受影响
        ("0 8 * * *", "2026-08-07T08:00:00+08:00"),
    ],
)
def test_crontab_dow_semantics(expr: str, expected: str):
    nxt = _next_fire(expr)
    assert nxt.isoformat() == expected
    # 额外断言：标准 crontab 的 "0" 必须落在周日，而不是周一
    if expr.endswith("* * 0") or expr.endswith("* * 7"):
        assert nxt.strftime("%A") == "Sunday"


def test_convert_keeps_other_fields_untouched():
    """只有 day-of-week 字段会被改写，其余字段必须原样保留。"""
    assert _convert_crontab_dow("30 2 15 * 0") == "30 2 15 * 6"
    assert _convert_crontab_dow("0 9 * * *") == "0 9 * * *"
    assert _convert_crontab_dow("5 4 * * sun") == "5 4 * * sun"


def test_convert_short_expr_unchanged():
    """字段数不是 5 时原样返回（交给 from_crontab 报错）。"""
    assert _convert_crontab_dow("0 9 * *") == "0 9 * *"
