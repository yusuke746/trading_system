"""
tests/test_risk_manager.py - risk_manager.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - check_daily_loss_limit()
  - check_consecutive_losses()
  - check_gap_risk()
  - run_all_risk_checks()
"""

import sqlite3
import sys
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import risk_manager


# ──────────────────────────────────────────────────────────
# テスト用インメモリ DB ファクトリ
# ──────────────────────────────────────────────────────────

def _make_db(trade_rows: list[dict] | None = None) -> sqlite3.Connection:
    """
    trade_results テーブルを持つインメモリ DB を作成する。
    trade_rows: [{"closed_at": str, "outcome": str, "pnl_usd": float}, ...]
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE trade_results (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            closed_at TEXT    NOT NULL,
            outcome   TEXT,
            pnl_usd   REAL
        )
    """)
    conn.commit()
    for row in (trade_rows or []):
        conn.execute(
            "INSERT INTO trade_results (closed_at, outcome, pnl_usd) VALUES (?, ?, ?)",
            (row["closed_at"], row["outcome"], row["pnl_usd"]),
        )
    conn.commit()
    return conn


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────
# check_daily_loss_limit のテスト
# ──────────────────────────────────────────────────────────

class TestCheckDailyLossLimit(unittest.TestCase):

    def _run(self, rows):
        conn = _make_db(rows)
        # balance=4000 / max_daily_loss_percent=-5% → limit = 4000×(-5/100) = -200
        with patch("risk_manager.get_connection", return_value=conn), \
             patch("risk_manager._get_balance", return_value=4000.0):
            return risk_manager.check_daily_loss_limit()

    def test_no_trades_not_blocked(self):
        """当日トレードなし → ブロックしない"""
        result = self._run([])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["daily_pnl_usd"], 0.0)

    def test_profit_not_blocked(self):
        """当日利益 (+100) → ブロックしない"""
        rows = [{"closed_at": _today() + "T10:00:00", "outcome": "tp_hit", "pnl_usd": 100.0}]
        result = self._run(rows)
        self.assertFalse(result["blocked"])
        self.assertAlmostEqual(result["daily_pnl_usd"], 100.0)

    def test_loss_within_limit_not_blocked(self):
        """当日損失 -100（上限 balance$4000×5%=-200 以内）→ ブロックしない"""
        rows = [{"closed_at": _today() + "T10:00:00", "outcome": "sl_hit", "pnl_usd": -100.0}]
        result = self._run(rows)
        self.assertFalse(result["blocked"])

    def test_loss_at_exactly_limit_not_blocked(self):
        """当日損失が上限と同値 (-200) → ブロックしない（超過ではない）"""
        rows = [
            {"closed_at": _today() + "T09:00:00", "outcome": "sl_hit", "pnl_usd": -100.0},
            {"closed_at": _today() + "T10:00:00", "outcome": "sl_hit", "pnl_usd": -100.0},
        ]
        result = self._run(rows)
        self.assertFalse(result["blocked"])

    def test_loss_exceeds_limit_blocked(self):
        """当日損失 -250（上限 -200 超過）→ ブロックする"""
        rows = [
            {"closed_at": _today() + "T09:00:00", "outcome": "sl_hit", "pnl_usd": -150.0},
            {"closed_at": _today() + "T10:00:00", "outcome": "sl_hit", "pnl_usd": -100.0},
        ]
        result = self._run(rows)
        self.assertTrue(result["blocked"])
        self.assertAlmostEqual(result["daily_pnl_usd"], -250.0)
        self.assertIn("超過", result["reason"])

    def test_yesterday_trades_not_counted(self):
        """昨日のトレードは当日集計に含まれない"""
        rows = [{"closed_at": "2000-01-01T10:00:00", "outcome": "sl_hit", "pnl_usd": -999.0}]
        result = self._run(rows)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["daily_pnl_usd"], 0.0)

    def test_db_error_not_blocked(self):
        """DB エラー → ブロックしない（取引を止めない）"""
        def bad_connection():
            raise RuntimeError("db down")
        with patch("risk_manager.get_connection", side_effect=bad_connection):
            result = risk_manager.check_daily_loss_limit()
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "db_error")


# ──────────────────────────────────────────────────────────
# check_consecutive_losses のテスト
# ──────────────────────────────────────────────────────────

class TestCheckConsecutiveLosses(unittest.TestCase):

    def _run(self, outcomes: list[str]):
        """
        outcomes[0] が最新になるよう逆順で DB に挿入する。
        (ORDER BY id DESC で outcomes[0] が先頭に来るようにするため)
        """
        rows = [
            {"closed_at": _today() + "T10:00:00", "outcome": o, "pnl_usd": -50.0}
            for o in reversed(outcomes)
        ]
        conn = _make_db(rows)
        with patch("risk_manager.get_connection", return_value=conn):
            return risk_manager.check_consecutive_losses()

    def _run_with_old_losses(self, recent_outcomes: list[str],
                              old_outcomes: list[str],
                              old_hours_ago: float = 25.0):
        """
        recent_outcomes: 現在時刻のトレード（リセット内に収まる）
        old_outcomes:    cutoffより古いトレード（除外される）
        old_hours_ago:   古いトレードが何時間前か
        """
        from datetime import timedelta
        old_ts = (datetime.now(timezone.utc)
                  - timedelta(hours=old_hours_ago)).isoformat()
        rows = []
        # 古いトレード（id小 = 古い）から挿入
        for o in old_outcomes:
            rows.append({"closed_at": old_ts, "outcome": o, "pnl_usd": -50.0})
        # 新しいトレード（id大 = 新しい）
        for o in recent_outcomes:
            rows.append({"closed_at": _today() + "T10:00:00",
                         "outcome": o, "pnl_usd": -50.0})
        conn = _make_db(rows)
        with patch("risk_manager.get_connection", return_value=conn):
            return risk_manager.check_consecutive_losses()

    def test_no_trades_not_blocked(self):
        """トレードなし（データ不足）→ ブロックしない"""
        result = self._run([])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "insufficient_data")

    def test_fewer_than_threshold_not_blocked(self):
        """直近2件が SL_HIT でもしきい値 3 未満 → ブロックしない"""
        result = self._run(["sl_hit", "sl_hit"])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "insufficient_data")

    def test_three_consecutive_sl_hit_blocked(self):
        """直近 3 件がすべて sl_hit → ブロックする"""
        result = self._run(["sl_hit", "sl_hit", "sl_hit"])
        self.assertTrue(result["blocked"])
        self.assertIn("取引停止", result["reason"])

    def test_mixed_outcomes_not_blocked(self):
        """直近 3 件が sl_hit / tp_hit / sl_hit → ブロックしない"""
        result = self._run(["sl_hit", "tp_hit", "sl_hit"])
        self.assertFalse(result["blocked"])

    def test_consecutive_count_returned(self):
        """連続損失数が正しく返る（3件中 2 連敗）"""
        result = self._run(["sl_hit", "sl_hit", "tp_hit"])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["consecutive_count"], 2)

    def test_more_than_threshold_blocked(self):
        """4 件すべて sl_hit （しきい値 3 超過）→ ブロックする"""
        result = self._run(["sl_hit", "sl_hit", "sl_hit", "sl_hit"])
        self.assertTrue(result["blocked"])

    def test_db_error_not_blocked(self):
        """DB エラー → ブロックしない"""
        def bad_connection():
            raise RuntimeError("db down")
        with patch("risk_manager.get_connection", side_effect=bad_connection):
            result = risk_manager.check_consecutive_losses()
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "db_error")

    # ── 時間ベースリセットのテスト ────────────────────────

    def test_old_losses_outside_reset_window_not_counted(self):
        """cutoff より古い 3 連敗は除外されてブロックしない"""
        # 古い 3 連敗(25h前)は除外、直近トレードなし → リセット済み
        result = self._run_with_old_losses(
            recent_outcomes=[],
            old_outcomes=["sl_hit", "sl_hit", "sl_hit"],
            old_hours_ago=25.0,
        )
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "reset_by_time")
        self.assertEqual(result["consecutive_count"], 0)

    def test_mixed_old_and_recent_does_not_block(self):
        """25h前に 2 連敗、直近に 1 連敗 → 合算しないため閾値(3)未満"""
        result = self._run_with_old_losses(
            recent_outcomes=["sl_hit"],
            old_outcomes=["sl_hit", "sl_hit"],
            old_hours_ago=25.0,
        )
        self.assertFalse(result["blocked"])
        # 直近 1 件しかカウントされない
        self.assertEqual(result["consecutive_count"], 1)

    def test_recent_three_losses_still_blocked(self):
        """25h前の負け + 直近 3 連敗 → 直近 3 件でブロック"""
        result = self._run_with_old_losses(
            recent_outcomes=["sl_hit", "sl_hit", "sl_hit"],
            old_outcomes=["sl_hit"],
            old_hours_ago=25.0,
        )
        self.assertTrue(result["blocked"])

    def test_reset_hours_zero_disables_time_filter(self):
        """CONSECUTIVE_LOSS_RESET_HOURS=0 のとき時間フィルターなし（3連敗でブロック）"""
        rows = [
            {"closed_at": "2000-01-01T10:00:00+00:00", "outcome": "sl_hit", "pnl_usd": -50.0},
            {"closed_at": "2000-01-02T10:00:00+00:00", "outcome": "sl_hit", "pnl_usd": -50.0},
            {"closed_at": "2000-01-03T10:00:00+00:00", "outcome": "sl_hit", "pnl_usd": -50.0},
        ]
        conn = _make_db(rows)
        with patch("risk_manager.get_connection", return_value=conn), \
             patch("risk_manager.CONSECUTIVE_LOSS_RESET_HOURS", 0):
            result = risk_manager.check_consecutive_losses()
        # 時間フィルターを無効化したので古い3連敗がブロックに繋がる
        self.assertTrue(result["blocked"])

    def test_result_contains_reset_hours_key(self):
        """返り値に reset_hours キーが存在する"""
        result = self._run(["tp_hit"])
        self.assertIn("reset_hours", result)


# ──────────────────────────────────────────────────────────
# check_gap_risk のテスト
# ──────────────────────────────────────────────────────────

class TestCheckGapRisk(unittest.TestCase):

    def _monday_utc_2am(self) -> datetime:
        """月曜日 02:00 UTC を返す（weekday=0, hour=2）"""
        # 固定の月曜日を返す
        return datetime(2026, 2, 23, 2, 0, 0, tzinfo=timezone.utc)  # 2026-02-23は月曜

    def test_not_monday_not_blocked(self):
        """月曜日以外 → ブロックしない"""
        # 火曜日 (weekday=1)
        tuesday = datetime(2026, 2, 24, 2, 0, 0, tzinfo=timezone.utc)
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = tuesday
            mock_dt.now.side_effect = None
            with patch("risk_manager.MT5_AVAILABLE", False):
                result = risk_manager.check_gap_risk("XAUUSD", 5200.0)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "not_gap_window")

    def test_monday_outside_window_not_blocked(self):
        """月曜日でも 03:00 以降はウィンドウ外 → ブロックしない"""
        monday_4am = datetime(2026, 2, 23, 4, 0, 0, tzinfo=timezone.utc)
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = monday_4am
            result = risk_manager.check_gap_risk("XAUUSD", 5200.0)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "not_gap_window")

    def test_monday_mt5_unavailable_not_blocked(self):
        """月曜 01-03 UTC でも MT5 なし → ブロックしない"""
        monday = self._monday_utc_2am()
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            with patch("risk_manager.MT5_AVAILABLE", False):
                result = risk_manager.check_gap_risk("XAUUSD", 5200.0)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "mt5_unavailable")

    def test_monday_small_gap_not_blocked(self):
        """月曜 02:00 UTC、ギャップ 3 USD（閾値 15 以内）→ ブロックしない"""
        monday = self._monday_utc_2am()
        # rates[0]=月曜, rates[1]=金曜終値 5205 → gap = |5202-5205| = 3 < 15
        fake_rates = [{"close": 5200.0}, {"close": 5205.0}]
        with patch("risk_manager.datetime") as mock_dt, \
             patch("risk_manager.MT5_AVAILABLE", True), \
             patch("risk_manager.mt5") as mock_mt5:
            mock_dt.now.return_value = monday
            mock_mt5.copy_rates_from_pos.return_value = fake_rates
            mock_mt5.TIMEFRAME_D1 = 1
            result = risk_manager.check_gap_risk("XAUUSD", 5202.0)
        self.assertFalse(result["blocked"])

    def test_monday_large_gap_blocked(self):
        """月曜 02:00 UTC、ギャップ 20 USD（閾値 15 超過）→ ブロックする"""
        monday = self._monday_utc_2am()
        # rates[0]=月曜, rates[1]=金曜終値 5180 → gap = |5200-5180| = 20 >= 15
        fake_rates = [{"close": 5200.0}, {"close": 5180.0}]
        with patch("risk_manager.datetime") as mock_dt, \
             patch("risk_manager.MT5_AVAILABLE", True), \
             patch("risk_manager.mt5") as mock_mt5:
            mock_dt.now.return_value = monday
            mock_mt5.copy_rates_from_pos.return_value = fake_rates
            mock_mt5.TIMEFRAME_D1 = 1
            result = risk_manager.check_gap_risk("XAUUSD", 5200.0)
        self.assertTrue(result["blocked"])
        self.assertAlmostEqual(result["gap_usd"], 20.0)
        self.assertIn("ギャップ", result["reason"])


# ──────────────────────────────────────────────────────────
# run_all_risk_checks のテスト
# ──────────────────────────────────────────────────────────

class TestRunAllRiskChecks(unittest.TestCase):

    def _patch_all(self, daily_blocked=False, consec_blocked=False, gap_blocked=False):
        """3つの個別チェックをまとめてパッチする"""
        daily_result = {
            "blocked": daily_blocked,
            "daily_pnl_usd": -250.0 if daily_blocked else 0.0,
            "reason": "daily_loss_exceeded" if daily_blocked else "ok",
        }
        consec_result = {
            "blocked": consec_blocked,
            "consecutive_count": 3 if consec_blocked else 0,
            "reason": "consecutive_loss" if consec_blocked else "ok",
        }
        gap_result = {
            "blocked": gap_blocked,
            "gap_usd": 20.0 if gap_blocked else 0.0,
            "reason": "gap_risk" if gap_blocked else "ok",
        }
        return (
            patch("risk_manager.check_daily_loss_limit",  return_value=daily_result),
            patch("risk_manager.check_consecutive_losses", return_value=consec_result),
            patch("risk_manager.check_gap_risk",           return_value=gap_result),
        )

    def test_all_clear_not_blocked(self):
        """3つのチェックがすべて通過 → blocked=False"""
        p1, p2, p3 = self._patch_all()
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["reason"], "ok")
        self.assertIn("details", result)

    def test_daily_loss_blocks(self):
        """当日損失超過 → blocked=True、理由が daily_loss"""
        p1, p2, p3 = self._patch_all(daily_blocked=True)
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "daily_loss_exceeded")

    def test_consecutive_loss_blocks(self):
        """連続損失 → blocked=True"""
        p1, p2, p3 = self._patch_all(consec_blocked=True)
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "consecutive_loss")

    def test_gap_risk_blocks(self):
        """ギャップリスク → blocked=True"""
        p1, p2, p3 = self._patch_all(gap_blocked=True)
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "gap_risk")

    def test_daily_loss_takes_priority_over_consecutive(self):
        """当日損失と連続損失が両方ブロック → 当日損失の理由が優先"""
        p1, p2, p3 = self._patch_all(daily_blocked=True, consec_blocked=True)
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "daily_loss_exceeded")

    def test_details_always_present(self):
        """details キーに3つのサブ結果が常に含まれる"""
        p1, p2, p3 = self._patch_all()
        with p1, p2, p3:
            result = risk_manager.run_all_risk_checks("XAUUSD", 5200.0)
        self.assertIn("daily_loss",  result["details"])
        self.assertIn("consecutive", result["details"])
        self.assertIn("gap",         result["details"])


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
