"""
test_market_hours.py - is_weekend() の週末判定テスト
"""

from datetime import datetime, timezone
from unittest.mock import patch
import pytest
import market_hours


def _make_utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestIsWeekend:
    """is_weekend() が正しい週末クローズ判定を返すことを確認する"""

    @pytest.mark.parametrize("dt, expected, label", [
        # 月曜 — 開場中
        (_make_utc(2026, 3, 23, 0, 0),  False, "月曜 00:00 UTC → 開場"),
        (_make_utc(2026, 3, 23, 0, 5),  False, "月曜 00:05 UTC → 開場"),
        (_make_utc(2026, 3, 23, 12, 0), False, "月曜 12:00 UTC → 開場"),
        # 金曜 — 22:00 UTC でクローズ
        (_make_utc(2026, 3, 20, 21, 59), False, "金曜 21:59 UTC → 開場"),
        (_make_utc(2026, 3, 20, 22, 0),  True,  "金曜 22:00 UTC → クローズ"),
        (_make_utc(2026, 3, 20, 22, 1),  True,  "金曜 22:01 UTC → クローズ"),
        (_make_utc(2026, 3, 20, 23, 59), True,  "金曜 23:59 UTC → クローズ"),
        # 土曜 — 全日クローズ
        (_make_utc(2026, 3, 21, 0, 0),  True, "土曜 00:00 UTC → クローズ"),
        (_make_utc(2026, 3, 21, 12, 0), True, "土曜 12:00 UTC → クローズ"),
        # 日曜 — 22:00 UTC に開場
        (_make_utc(2026, 3, 22, 0, 0),  True,  "日曜 00:00 UTC → クローズ"),
        (_make_utc(2026, 3, 22, 21, 59), True,  "日曜 21:59 UTC → クローズ"),
        (_make_utc(2026, 3, 22, 22, 0),  False, "日曜 22:00 UTC → 開場"),
        (_make_utc(2026, 3, 22, 23, 0),  False, "日曜 23:00 UTC → 開場"),
        # 平日（火〜木）— 開場中
        (_make_utc(2026, 3, 24, 10, 0), False, "火曜 10:00 UTC → 開場"),
        (_make_utc(2026, 3, 25, 15, 0), False, "水曜 15:00 UTC → 開場"),
        (_make_utc(2026, 3, 26, 20, 0), False, "木曜 20:00 UTC → 開場"),
    ])
    def test_is_weekend(self, dt, expected, label):
        with patch.object(market_hours, "_utc_now", return_value=dt):
            assert market_hours.is_weekend() is expected, label
