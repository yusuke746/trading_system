"""
tests/test_logger_module.py - logger_module.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - log_scoring_history() が int（lastrowid）を返すこと
  - log_execution() に scoring_history_id を渡すと
    executions.ai_decision_id に保存されること
  - update_scoring_history_outcome() を呼ぶと
    scoring_history の outcome・pnl_usd が更新されること
"""

import sys
import os
import sqlite3
import unittest
from unittest.mock import patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_in_memory_conn() -> sqlite3.Connection:
    """テスト用インメモリ DB を作成してスキーマを初期化する"""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scoring_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT DEFAULT (datetime('now')),
            signal_direction TEXT,
            regime           TEXT,
            session          TEXT,
            total_score      REAL,
            decision         TEXT,
            breakdown_json   TEXT,
            fvg_aligned      INTEGER DEFAULT 0,
            zone_aligned     INTEGER DEFAULT 0,
            bos_confirmed    INTEGER DEFAULT 0,
            ob_aligned       INTEGER DEFAULT 0,
            choch_confirmed  INTEGER DEFAULT 0,
            outcome          TEXT DEFAULT NULL,
            pnl_usd          REAL DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS executions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL,
            ai_decision_id  INTEGER,
            symbol          TEXT,
            direction       TEXT,
            order_type      TEXT,
            lot_size        REAL,
            entry_price     REAL,
            sl_price        REAL,
            tp_price        REAL,
            mt5_ticket      INTEGER,
            success         INTEGER,
            error_msg       TEXT
        );
    """)
    conn.commit()
    return conn


class TestLogScoringHistory(unittest.TestCase):
    """log_scoring_history() が int（lastrowid）を返すことを検証"""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_returns_int(self):
        """log_scoring_history() は int を返す"""
        import logger_module

        alert  = {"direction": "buy", "regime": "TREND", "session": "london"}
        result = {"score": 0.75, "decision": "approve", "score_breakdown": {}}

        with patch("logger_module.get_connection", return_value=self.conn):
            rowid = logger_module.log_scoring_history(alert, result)

        self.assertIsInstance(rowid, int)
        self.assertGreater(rowid, 0)

    def test_sequential_ids_increment(self):
        """2回呼び出すと ID が連番でインクリメントされる"""
        import logger_module

        alert  = {"direction": "buy", "regime": "TREND", "session": "london"}
        result = {"score": 0.75, "decision": "approve", "score_breakdown": {}}

        with patch("logger_module.get_connection", return_value=self.conn):
            id1 = logger_module.log_scoring_history(alert, result)
            id2 = logger_module.log_scoring_history(alert, result)

        self.assertEqual(id2, id1 + 1)

    def test_record_is_persisted(self):
        """INSERT 後に SELECT で同一レコードが取得できる"""
        import logger_module

        alert  = {"direction": "sell", "regime": "BREAKOUT", "session": "ny",
                  "fvg_aligned": True}
        result = {"score": 0.55, "decision": "wait",
                  "score_breakdown": {"fvg_aligned": 0.3}}

        with patch("logger_module.get_connection", return_value=self.conn):
            rowid = logger_module.log_scoring_history(alert, result)

        row = self.conn.execute(
            "SELECT * FROM scoring_history WHERE id = ?", (rowid,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["signal_direction"], "sell")
        self.assertEqual(row["decision"], "wait")
        self.assertEqual(row["fvg_aligned"], 1)


class TestLogExecution(unittest.TestCase):
    """log_execution() に scoring_history_id を渡すと
    executions.ai_decision_id に保存されることを検証"""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def _make_params(self, **overrides) -> dict:
        base = {
            "symbol":      "XAUUSD",
            "direction":   "buy",
            "order_type":  "market",
            "lot_size":    0.1,
            "entry_price": 2000.0,
            "sl_price":    1990.0,
            "tp_price":    2020.0,
        }
        base.update(overrides)
        return base

    def test_ai_decision_id_saved(self):
        """scoring_history_id を渡すと executions.ai_decision_id に保存される"""
        import logger_module

        scoring_history_id = 42
        params = self._make_params()

        with patch("logger_module.get_connection", return_value=self.conn):
            exec_id = logger_module.log_execution(
                ai_decision_id=scoring_history_id,
                params=params,
                ticket=12345,
                success=True,
            )

        row = self.conn.execute(
            "SELECT ai_decision_id FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["ai_decision_id"], scoring_history_id)

    def test_ai_decision_id_none_when_not_passed(self):
        """ai_decision_id=None を渡すと executions.ai_decision_id も NULL になる"""
        import logger_module

        params = self._make_params()

        with patch("logger_module.get_connection", return_value=self.conn):
            exec_id = logger_module.log_execution(
                ai_decision_id=None,
                params=params,
                ticket=99999,
                success=True,
            )

        row = self.conn.execute(
            "SELECT ai_decision_id FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["ai_decision_id"])

    def test_full_flow_scoring_to_execution(self):
        """log_scoring_history() → log_execution() の連携フロー検証"""
        import logger_module

        alert  = {"direction": "buy", "regime": "TREND", "session": "london"}
        result = {"score": 0.80, "decision": "approve", "score_breakdown": {}}

        with patch("logger_module.get_connection", return_value=self.conn):
            scoring_id = logger_module.log_scoring_history(alert, result)
            exec_id    = logger_module.log_execution(
                ai_decision_id=scoring_id,
                params=self._make_params(),
                ticket=55555,
                success=True,
            )

        row = self.conn.execute(
            "SELECT ai_decision_id FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
        self.assertEqual(row["ai_decision_id"], scoring_id)


class TestUpdateScoringHistoryOutcome(unittest.TestCase):
    """update_scoring_history_outcome() が scoring_history の
    outcome・pnl_usd を正しく更新することを検証"""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def _insert_scoring_history(self) -> int:
        """テスト用 scoring_history レコードを INSERT して id を返す"""
        import logger_module

        alert  = {"direction": "buy", "regime": "TREND", "session": "london"}
        result = {"score": 0.70, "decision": "approve", "score_breakdown": {}}

        with patch("logger_module.get_connection", return_value=self.conn):
            return logger_module.log_scoring_history(alert, result)

    def test_win_outcome_updated(self):
        """pnl_usd > 0 → outcome='win' で更新される"""
        import logger_module

        scoring_id = self._insert_scoring_history()

        with patch("logger_module.get_connection", return_value=self.conn):
            logger_module.update_scoring_history_outcome(
                scoring_history_id=scoring_id,
                outcome="win",
                pnl_usd=150.0,
            )

        row = self.conn.execute(
            "SELECT outcome, pnl_usd FROM scoring_history WHERE id = ?",
            (scoring_id,)
        ).fetchone()
        self.assertEqual(row["outcome"], "win")
        self.assertAlmostEqual(row["pnl_usd"], 150.0)

    def test_loss_outcome_updated(self):
        """loss outcome と負の pnl_usd が保存される"""
        import logger_module

        scoring_id = self._insert_scoring_history()

        with patch("logger_module.get_connection", return_value=self.conn):
            logger_module.update_scoring_history_outcome(
                scoring_history_id=scoring_id,
                outcome="loss",
                pnl_usd=-80.5,
            )

        row = self.conn.execute(
            "SELECT outcome, pnl_usd FROM scoring_history WHERE id = ?",
            (scoring_id,)
        ).fetchone()
        self.assertEqual(row["outcome"], "loss")
        self.assertAlmostEqual(row["pnl_usd"], -80.5)

    def test_breakeven_outcome_updated(self):
        """breakeven outcome と 0.0 pnl_usd が保存される"""
        import logger_module

        scoring_id = self._insert_scoring_history()

        with patch("logger_module.get_connection", return_value=self.conn):
            logger_module.update_scoring_history_outcome(
                scoring_history_id=scoring_id,
                outcome="breakeven",
                pnl_usd=0.0,
            )

        row = self.conn.execute(
            "SELECT outcome, pnl_usd FROM scoring_history WHERE id = ?",
            (scoring_id,)
        ).fetchone()
        self.assertEqual(row["outcome"], "breakeven")
        self.assertAlmostEqual(row["pnl_usd"], 0.0)

    def test_outcome_initially_null(self):
        """INSERT 直後は outcome と pnl_usd が NULL である"""
        scoring_id = self._insert_scoring_history()

        row = self.conn.execute(
            "SELECT outcome, pnl_usd FROM scoring_history WHERE id = ?",
            (scoring_id,)
        ).fetchone()
        self.assertIsNone(row["outcome"])
        self.assertIsNone(row["pnl_usd"])

    def test_overwrite_existing_outcome(self):
        """既に outcome が設定されている場合も上書きされる"""
        import logger_module

        scoring_id = self._insert_scoring_history()

        with patch("logger_module.get_connection", return_value=self.conn):
            logger_module.update_scoring_history_outcome(scoring_id, "win", 100.0)
            logger_module.update_scoring_history_outcome(scoring_id, "loss", -50.0)

        row = self.conn.execute(
            "SELECT outcome, pnl_usd FROM scoring_history WHERE id = ?",
            (scoring_id,)
        ).fetchone()
        self.assertEqual(row["outcome"], "loss")
        self.assertAlmostEqual(row["pnl_usd"], -50.0)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
