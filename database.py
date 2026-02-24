"""
database.py - SQLite DB初期化・接続管理
AI Trading System v2.0
"""

import sqlite3
import logging
from pathlib import Path

DB_PATH = Path(__file__).parent / "trading_log.db"

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """スレッドセーフなDB接続を返す"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """テーブルを初期化する（存在しなければ作成）"""
    conn = get_connection()
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
            prompt_json      TEXT
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

        conn.commit()
        logger.info("✅ DB初期化完了: %s", DB_PATH)
    finally:
        conn.close()
