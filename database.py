"""
database.py - SQLite DB初期化・接続管理
AI Trading System v2.0
"""

import atexit
import sqlite3
import logging
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "trading_log.db"

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# スレッドローカル接続プール
# ──────────────────────────────────────────────────────────

class ConnectionPool:
    """
    threading.local() を使ってスレッドごとに1本の接続を使い回す軽量プール。

    Args:
        db_path:   SQLite DB ファイルパス
        pool_size: 将来の拡張用（現在はスレッド単位で1接続のみ）
    """

    def __init__(self, db_path: str, pool_size: int = 5):
        self._db_path    = db_path
        self._pool_size  = pool_size
        self._local      = threading.local()
        # 全スレッドの接続を追跡（close_all 用）
        self._all_conns: list[sqlite3.Connection] = []
        self._lock       = threading.Lock()

    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._all_conns.append(conn)
        return conn

    def get_connection(self) -> sqlite3.Connection:
        """
        現在のスレッド用の接続を返す。
        初回呼び出し時に接続を作成し、以降は使い回す。
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._make_conn()
            self._local.conn = conn
        return conn

    def close_all(self) -> None:
        """全スレッドの接続をクローズする（シャットダウン用）"""
        with self._lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
        # 現スレッドのローカル接続もリセット
        self._local.conn = None


# モジュールレベルのシングルトンプール
_pool = ConnectionPool(str(DB_PATH))
atexit.register(_pool.close_all)


def get_connection() -> sqlite3.Connection:
    """スレッドセーフなDB接続を返す（スレッドローカルプールから取得）"""
    return _pool.get_connection()


def init_db() -> None:
    """テーブルを初期化する（存在しなければ作成）。初期化専用の独立接続を使用。"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        c = conn.cursor()

        # ── signals ─────────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at  TEXT    NOT NULL,
            symbol       TEXT    NOT NULL,
            source       TEXT,
            signal_type  TEXT,
            event        TEXT,
            direction    TEXT,
            price        REAL,
            tf           INTEGER,
            raw_json     TEXT,
            processed    INTEGER DEFAULT 0
        )""")

        # ── ai_decisions ────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT    NOT NULL,
            signal_ids       TEXT,
            decision         TEXT,
            confidence       REAL,
            ev_score         REAL,
            order_type       TEXT,
            limit_price      REAL,
            limit_expiry     TEXT,
            reason           TEXT,
            risk_note        TEXT,
            wait_scope       TEXT,
            wait_condition   TEXT,
            context_json     TEXT,
            prompt_json      TEXT,
            setup_type       TEXT DEFAULT 'standard',
            q_trend_aligned  INTEGER DEFAULT 0,
            session          TEXT DEFAULT NULL,
            pattern_similarity REAL DEFAULT NULL
        )""")

        # ── executions ──────────────────────────────────────
        c.execute("""
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
        )""")

        # ── trade_results ────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS trade_results (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            closed_at          TEXT    NOT NULL,
            execution_id       INTEGER,
            mt5_ticket         INTEGER,
            outcome            TEXT,   -- tp_hit/sl_hit/trailing_sl/partial_tp/manual
            pnl_usd            REAL,
            pnl_pips           REAL,
            duration_min       REAL,
            partial_close_pnl  REAL,
            loss_reason        TEXT,
            missed_context     TEXT,
            prompt_hint        TEXT
        )""")

        # ── wait_history ──────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS wait_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL,
            ai_decision_id  INTEGER,
            wait_scope      TEXT,
            wait_condition  TEXT,
            reeval_count    INTEGER DEFAULT 0,
            final_status    TEXT,
            resolved_at     TEXT
        )""")

        # ── system_events ─────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS system_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL,
            event       TEXT NOT NULL,
            detail      TEXT,
            level       TEXT DEFAULT 'INFO'
        )""")

        # ── param_history ─────────────────────────────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS param_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            updated_at      TEXT NOT NULL,
            atr_sl_mult     REAL NOT NULL,
            atr_tp_mult     REAL NOT NULL,
            regime          TEXT,
            win_rate        REAL,
            consecutive_losses INTEGER,
            reason          TEXT
        )""")

        # ai_decisions テーブルに新カラムを追加（既存DBへの後方互換マイグレーション）
        for col_def in [
            "market_regime TEXT",
            "regime_reason TEXT",
            "score_breakdown TEXT",      # v3.0: スコア内訳JSON
            "structured_data TEXT",      # v3.0: LLM構造化出力JSON
        ]:
            try:
                c.execute(f"ALTER TABLE ai_decisions ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # カラムが既に存在する場合はスキップ
        # 新規追加カラム（作楥26-02-28）
        _new_columns = [
            "ALTER TABLE ai_decisions ADD COLUMN setup_type TEXT DEFAULT 'standard'",
            "ALTER TABLE ai_decisions ADD COLUMN q_trend_aligned INTEGER DEFAULT 0",
            "ALTER TABLE ai_decisions ADD COLUMN session TEXT DEFAULT NULL",
            "ALTER TABLE ai_decisions ADD COLUMN pattern_similarity REAL DEFAULT NULL",
        ]
        for sql in _new_columns:
            try:
                conn.execute(sql)
            except Exception:
                pass  # カラムが既に存在する場合はスキップ
        conn.commit()
        # ── scoring_history（v3.0 新規テーブル）─────────────
        c.execute("""
        CREATE TABLE IF NOT EXISTS scoring_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT DEFAULT (datetime('now')),
            signal_direction TEXT,
            regime           TEXT,
            total_score      REAL,
            decision         TEXT,
            breakdown_json   TEXT,
            outcome          TEXT DEFAULT NULL,
            pnl_usd          REAL DEFAULT NULL
        )""")

        # ── インデックス（クエリ高速化・保守用）────────────
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_signals_received_at         ON signals(received_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_created_at      ON ai_decisions(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ai_decisions_decision        ON ai_decisions(decision)",
            "CREATE INDEX IF NOT EXISTS idx_executions_created_at        ON executions(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_trade_results_closed_at      ON trade_results(closed_at)",
            "CREATE INDEX IF NOT EXISTS idx_system_events_created_at     ON system_events(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_scoring_history_created_at   ON scoring_history(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_wait_history_created_at      ON wait_history(created_at)",
        ]
        for idx_sql in indexes:
            c.execute(idx_sql)

        conn.commit()
        logger.info("✅ DB初期化完了: %s", DB_PATH)
    finally:
        conn.close()
