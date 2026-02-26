"""
tests/test_database.py - database.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - ConnectionPool.get_connection() のスレッドローカル動作
  - ConnectionPool.close_all() 後の再接続
"""

import sys
import os
import sqlite3
import threading
import unittest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import ConnectionPool


class TestConnectionPool(unittest.TestCase):
    """ConnectionPool のスレッドローカル動作テスト"""

    def setUp(self):
        """各テスト用にインメモリ DB のプールを作成"""
        self.pool = ConnectionPool(":memory:")

    def tearDown(self):
        self.pool.close_all()

    # ── 同一スレッドで同じオブジェクトが返る ──────────────────

    def test_same_thread_returns_same_connection(self):
        """同一スレッドで get_connection() を2回呼ぶと同じオブジェクトが返る"""
        conn1 = self.pool.get_connection()
        conn2 = self.pool.get_connection()
        self.assertIs(conn1, conn2,
                      "同一スレッドでは同じ接続オブジェクトが返るべき")

    def test_same_thread_multiple_calls(self):
        """5回呼んでも全て同じオブジェクト"""
        conns = [self.pool.get_connection() for _ in range(5)]
        for c in conns:
            self.assertIs(c, conns[0])

    # ── 別スレッドから呼ぶと別オブジェクトが返る ─────────────

    def test_different_threads_return_different_connections(self):
        """別スレッドから呼ぶと別オブジェクトが返る"""
        results: dict[str, sqlite3.Connection] = {}

        def worker(name: str):
            results[name] = self.pool.get_connection()

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertIn("t1", results)
        self.assertIn("t2", results)
        self.assertIsNot(results["t1"], results["t2"],
                         "異なるスレッドでは別の接続オブジェクトが返るべき")

    # ── close_all() 後に新しい接続が取れる ───────────────────

    def test_get_connection_after_close_all(self):
        """close_all() 後に get_connection() を呼んでも新しい接続が取れる"""
        conn1 = self.pool.get_connection()
        self.pool.close_all()

        # close_all 後は新しい接続が返るべき（古い接続は閉じられている）
        conn2 = self.pool.get_connection()
        self.assertIsNotNone(conn2)
        # 動作確認: SELECT 1 が通る
        row = conn2.execute("SELECT 1").fetchone()
        self.assertEqual(row[0], 1)

    def test_connection_is_usable(self):
        """取得した接続でクエリが実行できる"""
        conn = self.pool.get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS _test (id INTEGER)")
        conn.execute("INSERT INTO _test VALUES (42)")
        conn.commit()
        row = conn.execute("SELECT id FROM _test").fetchone()
        self.assertEqual(row[0], 42)

    def test_row_factory_set(self):
        """接続の row_factory が sqlite3.Row に設定されている"""
        conn = self.pool.get_connection()
        self.assertIs(conn.row_factory, sqlite3.Row)


# ──────────────────────────────────────────────────────────
# モジュールレベルの get_connection() テスト
# ──────────────────────────────────────────────────────────

class TestModuleGetConnection(unittest.TestCase):
    """database.get_connection() がプールを経由していることを確認"""

    def test_module_get_connection_same_thread(self):
        """モジュールの get_connection() も同一スレッドでは同じオブジェクト"""
        import database
        conn1 = database.get_connection()
        conn2 = database.get_connection()
        self.assertIs(conn1, conn2)

    def test_module_get_connection_is_usable(self):
        """モジュールの get_connection() で SELECT 1 が通る"""
        import database
        conn = database.get_connection()
        row  = conn.execute("SELECT 1").fetchone()
        self.assertEqual(row[0], 1)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
