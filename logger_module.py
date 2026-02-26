"""
logger_module.py - ログ書き込み（SQLite連携）
AI Trading System v2.0
"""

import logging
import json
from datetime import datetime, timezone
from database import get_connection

# コンソールロガー設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("trading_system")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── signals ──────────────────────
def log_signal(signal: dict) -> int:
    """受信シグナルをDBに記録し、IDを返す"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals
        (received_at, symbol, source, signal_type, event,
         direction, price, tf, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal.get("received_at", now_utc()),
        signal.get("symbol"),
        signal.get("source"),
        signal.get("signal_type"),
        signal.get("event"),
        signal.get("direction"),
        signal.get("price"),
        signal.get("tf"),
        json.dumps(signal, ensure_ascii=False),
    ))
    conn.commit()
    return c.lastrowid


# ─────────────────────────── ai_decisions ─────────────────
def log_ai_decision(signal_ids: list, ai_result: dict,
                    context: dict = None, prompt: dict = None) -> int:
    """AI判定をDBに記録し、IDを返す"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO ai_decisions
        (created_at, signal_ids, decision, confidence, ev_score,
         order_type, limit_price, limit_expiry, reason, risk_note,
         wait_scope, wait_condition, context_json, prompt_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_utc(),
        json.dumps(signal_ids),
        ai_result.get("decision"),
        ai_result.get("confidence"),
        ai_result.get("ev_score"),
        ai_result.get("order_type"),
        ai_result.get("limit_price"),
        ai_result.get("limit_expiry"),
        ai_result.get("reason"),
        ai_result.get("risk_note"),
        ai_result.get("wait_scope"),
        ai_result.get("wait_condition"),
        json.dumps(context, ensure_ascii=False) if context else None,
        json.dumps(prompt,   ensure_ascii=False) if prompt   else None,
    ))
    conn.commit()
    return c.lastrowid


# ─────────────────────────── executions ───────────────────
def log_execution(ai_decision_id: int, params: dict,
                  ticket: int, success: bool, error_msg: str = None) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO executions
        (created_at, ai_decision_id, symbol, direction, order_type,
         lot_size, entry_price, sl_price, tp_price, mt5_ticket,
         success, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_utc(),
        ai_decision_id,
        params.get("symbol"),
        params.get("direction"),
        params.get("order_type"),
        params.get("lot_size"),
        params.get("entry_price"),
        params.get("sl_price"),
        params.get("tp_price"),
        ticket,
        int(success),
        error_msg,
    ))
    conn.commit()
    return c.lastrowid


# ─────────────────────────── trade_results ────────────────
def log_trade_result(execution_id: int, ticket: int,
                     outcome: str, pnl_usd: float, pnl_pips: float,
                     duration_min: float, partial_close_pnl: float = None) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trade_results
        (closed_at, execution_id, mt5_ticket, outcome,
         pnl_usd, pnl_pips, duration_min, partial_close_pnl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_utc(), execution_id, ticket,
        outcome, pnl_usd, pnl_pips, duration_min, partial_close_pnl,
    ))
    conn.commit()
    return c.lastrowid


def update_trade_result_loss_analysis(result_id: int, loss_reason: str,
                                      missed_context: str, prompt_hint: str):
    conn = get_connection()
    conn.execute("""
        UPDATE trade_results
        SET loss_reason=?, missed_context=?, prompt_hint=?
        WHERE id=?
    """, (loss_reason, missed_context, prompt_hint, result_id))
    conn.commit()


# ─────────────────────────── wait_history ─────────────────
def log_wait(ai_decision_id: int, wait_scope: str,
             wait_condition: str) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO wait_history
        (created_at, ai_decision_id, wait_scope, wait_condition)
        VALUES (?, ?, ?, ?)
    """, (now_utc(), ai_decision_id, wait_scope, wait_condition))
    conn.commit()
    return c.lastrowid


def update_wait_history(wait_id: int, reeval_count: int,
                        final_status: str):
    conn = get_connection()
    conn.execute("""
        UPDATE wait_history
        SET reeval_count=?, final_status=?, resolved_at=?
        WHERE id=?
    """, (reeval_count, final_status, now_utc(), wait_id))
    conn.commit()


# ─────────────────────────── system_events ────────────────
def log_event(event: str, detail: str = None, level: str = "INFO"):
    conn = get_connection()
    conn.execute("""
        INSERT INTO system_events (created_at, event, detail, level)
        VALUES (?, ?, ?, ?)
    """, (now_utc(), event, detail, level))
    conn.commit()
    # コンソールにも出力
    getattr(logger, level.lower(), logger.info)(
        "[%s] %s", event, detail or ""
    )
