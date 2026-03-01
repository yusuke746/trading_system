"""
tests/test_db_maintenance.py - DbMaintenance のユニットテスト
AI Trading System v3.5

テスト対象:
  - run() による古いレコードの削除
  - run() による大容量カラムの NULL 化
  - 保持期間内のレコードは削除・NULL化されないこと
  - run() 戻り値の構造確認
  - _vacuum() の動作確認
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_maintenance import DbMaintenance


# ──────────────────────────────────────────────────────────
# テスト用 DB ヘルパー
# ──────────────────────────────────────────────────────────

def _setup_test_db(path: str) -> None:
    """最低限のテーブルを持つ一時 DB を作成する"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS system_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event      TEXT NOT NULL,
            detail     TEXT,
            level      TEXT DEFAULT 'INFO'
        );
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            symbol      TEXT NOT NULL DEFAULT 'GOLD',
            raw_json    TEXT,
            processed   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS scoring_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT NOT NULL,
            signal_direction TEXT,
            total_score      REAL,
            decision         TEXT
        );
        CREATE TABLE IF NOT EXISTS wait_history (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at     TEXT NOT NULL,
            ai_decision_id INTEGER,
            wait_scope     TEXT
        );
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT NOT NULL,
            decision     TEXT,
            prompt_json  TEXT,
            context_json TEXT
        );
    """)
    conn.commit()
    conn.close()


def _dt_ago(days: int) -> str:
    """現在時刻から days 日前のISO8601文字列（UTC）を返す"""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _count(conn: sqlite3.Connection, table: str, condition: str = "1=1") -> int:
    cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}")
    return cur.fetchone()[0]


# ──────────────────────────────────────────────────────────
# テストクラス
# ──────────────────────────────────────────────────────────

class TestDeleteOldRows(unittest.TestCase):
    """保持期間超のレコードが削除されること"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = self.tmpfile.name
        _setup_test_db(self.db_path)
        self.maint = DbMaintenance(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_old_system_events_deleted(self):
        """system_events: 91日以上前の行は削除される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO system_events(created_at, event) VALUES(?, 'x')",
                     (_dt_ago(91),))
        conn.execute("INSERT INTO system_events(created_at, event) VALUES(?, 'x')",
                     (_dt_ago(1),))  # 保持期間内
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(_count(conn, "system_events"), 1,
                         "91日以上前の行だけが残り1件になるべき")
        conn.close()

    def test_old_signals_deleted(self):
        """signals: 181日以上前の行は削除される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO signals(received_at, symbol) VALUES(?, 'GOLD')",
                     (_dt_ago(181),))
        conn.execute("INSERT INTO signals(received_at, symbol) VALUES(?, 'GOLD')",
                     (_dt_ago(10),))  # 保持期間内
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(_count(conn, "signals"), 1)
        conn.close()

    def test_old_scoring_history_deleted(self):
        """scoring_history: 91日以上前の行は削除される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO scoring_history(created_at, decision) VALUES(?, 'reject')",
                     (_dt_ago(91),))
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(_count(conn, "scoring_history"), 0)
        conn.close()

    def test_old_wait_history_deleted(self):
        """wait_history: 181日以上前の行は削除される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO wait_history(created_at) VALUES(?)",
                     (_dt_ago(181),))
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(_count(conn, "wait_history"), 0)
        conn.close()


class TestNullifyColumns(unittest.TestCase):
    """保持期間超の ai_decisions カラムが NULL 化されること"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = self.tmpfile.name
        _setup_test_db(self.db_path)
        self.maint = DbMaintenance(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_prompt_json_nullified_after_90days(self):
        """ai_decisions.prompt_json: 91日以上前の行は NULL 化される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO ai_decisions(created_at, decision, prompt_json, context_json) "
            "VALUES(?, 'approve', '{\"x\":1}', '{\"y\":2}')",
            (_dt_ago(91),)
        )
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT prompt_json, context_json FROM ai_decisions").fetchone()
        self.assertIsNone(row[0], "prompt_json は NULL になるべき")
        # context_json は 180日ポリシーなので不変（91日前の行はまだ保持）
        conn.close()

    def test_context_json_nullified_after_180days(self):
        """ai_decisions.context_json: 181日以上前の行は NULL 化される"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO ai_decisions(created_at, decision, prompt_json, context_json) "
            "VALUES(?, 'approve', '{\"p\":1}', '{\"c\":2}')",
            (_dt_ago(181),)
        )
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT prompt_json, context_json FROM ai_decisions").fetchone()
        self.assertIsNone(row[0], "prompt_json は NULL になるべき（91日以上のため）")
        self.assertIsNone(row[1], "context_json は NULL になるべき（181日以上のため）")
        conn.close()

    def test_recent_ai_decisions_not_modified(self):
        """ai_decisions: 保持期間内の行は変更されない"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO ai_decisions(created_at, decision, prompt_json, context_json) "
            "VALUES(?, 'approve', '{\"p\":1}', '{\"c\":2}')",
            (_dt_ago(10),)
        )
        conn.commit()
        conn.close()

        self.maint.run()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT prompt_json, context_json FROM ai_decisions").fetchone()
        self.assertIsNotNone(row[0], "prompt_json は保持期間内のため変更されない")
        self.assertIsNotNone(row[1], "context_json は保持期間内のため変更されない")
        conn.close()


class TestRunReturnValue(unittest.TestCase):
    """run() の戻り値構造を確認する"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = self.tmpfile.name
        _setup_test_db(self.db_path)
        self.maint = DbMaintenance(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_run_returns_required_keys(self):
        """run() の戻り値に必須キーが含まれる"""
        result = self.maint.run()
        self.assertIn("deleted",    result)
        self.assertIn("nulled",     result)
        self.assertIn("vacuum",     result)
        self.assertIn("db_size_mb", result)

    def test_run_empty_db_no_error(self):
        """空のDBで run() がエラーなく完了する"""
        result = self.maint.run()
        self.assertIsInstance(result["deleted"], dict)
        self.assertIsInstance(result["nulled"],  dict)
        self.assertIsInstance(result["vacuum"],  bool)


class TestVacuum(unittest.TestCase):
    """_vacuum() の動作確認"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = self.tmpfile.name
        _setup_test_db(self.db_path)
        self.maint = DbMaintenance(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_vacuum_returns_true_on_valid_db(self):
        """正常な DB に対して _vacuum() が True を返す"""
        ok = self.maint._vacuum()
        self.assertTrue(ok)

    def test_vacuum_invalid_path_returns_false(self):
        """存在しないパスに対して _vacuum() が False を返す（例外を外に出さない）"""
        bad_maint = DbMaintenance(db_path="/nonexistent/path/to.db")
        ok = bad_maint._vacuum()
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
