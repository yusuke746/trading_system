"""
risk_manager.py - トレードリスク管理
AI Trading System v2.0

チェック項目:
  1. 当日確定損失が上限を超えていないか
  2. 連続SL被弾が上限に達していないか
  3. 週明けギャップリスクがないか（月曜 01:00-03:00 UTC）
"""

import logging
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None          # type: ignore[assignment]
    MT5_AVAILABLE = False

from config import SYSTEM_CONFIG
from database import get_connection

logger = logging.getLogger(__name__)

# ── 設定値 ──────────────────────────────────────────────────
MAX_DAILY_LOSS_PCT    = SYSTEM_CONFIG.get("max_daily_loss_percent",  -5.0)  # 残高比率%
MAX_CONSECUTIVE_LOSS  = SYSTEM_CONFIG.get("max_consecutive_losses",   3)
GAP_BLOCK_THRESHOLD   = SYSTEM_CONFIG.get("gap_block_threshold_usd",  15.0)
SYMBOL                = SYSTEM_CONFIG.get("symbol", "XAUUSD")


def _get_balance() -> float:
    """MT5から口座残高を取得する。取得失敗時は10,000ドルをフォールバックとして使用"""
    if MT5_AVAILABLE:
        try:
            acc = mt5.account_info()
            if acc and acc.balance > 0:
                return acc.balance
        except Exception:
            pass
    return 10_000.0


# ────────────────────────────────────────────────────────────
# 1. 当日確定損失チェック
# ────────────────────────────────────────────────────────────

def check_daily_loss_limit() -> dict:
    """
    当日（サーバー時刻の日付）に確定したPnLの合計がMAX_DAILY_LOSS_USDを
    下回っていたら取引停止フラグを返す。

    Returns:
        {
            "blocked": bool,
            "daily_pnl_usd": float,
            "reason": str
        }
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT COALESCE(SUM(pnl_usd), 0.0) AS total_pnl
            FROM   trade_results
            WHERE  closed_at LIKE ?
            """,
            (today_str + "%",),
        ).fetchone()
        conn.close()
        daily_pnl = float(rows["total_pnl"])
    except Exception as exc:
        logger.error("check_daily_loss_limit DB error: %s", exc)
        return {"blocked": False, "daily_pnl_usd": 0.0, "reason": "db_error"}

    # 残高ベースで上限額を動的計算
    balance   = _get_balance()
    limit_usd = balance * (MAX_DAILY_LOSS_PCT / 100.0)   # 例: 10000 × (-5/100) = -500

    if daily_pnl < limit_usd:
        reason = (
            f"当日損失 {daily_pnl:.1f} USD が上限 {limit_usd:.1f} USD"
            f"（残高 {balance:.0f} USD × {MAX_DAILY_LOSS_PCT:.1f}%）を超過"
        )
        logger.warning("RISK: %s", reason)
        return {"blocked": True, "daily_pnl_usd": daily_pnl, "reason": reason}

    return {"blocked": False, "daily_pnl_usd": daily_pnl, "reason": "ok"}


# ────────────────────────────────────────────────────────────
# 2. 連続損失チェック
# ────────────────────────────────────────────────────────────

def check_consecutive_losses() -> dict:
    """
    直近 MAX_CONSECUTIVE_LOSS 件のトレード結果を確認し、
    全件が 'sl_hit' であれば取引停止。

    Returns:
        {
            "blocked": bool,
            "consecutive_count": int,
            "reason": str
        }
    """
    n = MAX_CONSECUTIVE_LOSS
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT outcome
            FROM   trade_results
            ORDER  BY id DESC
            LIMIT  ?
            """,
            (n,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("check_consecutive_losses DB error: %s", exc)
        return {"blocked": False, "consecutive_count": 0, "reason": "db_error"}

    if len(rows) < n:
        # まだ充分なデータがない
        return {"blocked": False, "consecutive_count": len(rows), "reason": "insufficient_data"}

    all_sl = all(r["outcome"] == "sl_hit" for r in rows)
    if all_sl:
        reason = f"直近 {n} 件が連続SL被弾 → 取引停止"
        logger.warning("RISK: %s", reason)
        return {"blocked": True, "consecutive_count": n, "reason": reason}

    # 実際の連続損失数（直近から数える）
    count = 0
    for r in rows:
        if r["outcome"] == "sl_hit":
            count += 1
        else:
            break

    return {"blocked": False, "consecutive_count": count, "reason": "ok"}


# ────────────────────────────────────────────────────────────
# 3. 週明けギャップリスクチェック
# ────────────────────────────────────────────────────────────

def check_gap_risk(symbol: str, entry_price: float) -> dict:
    """
    月曜 01:00〜03:00 UTC（XMサーバー時刻ではなくUTC）のみ判定。
    前週金曜のD1終値 vs 月曜始値（現在レート）のギャップが
    GAP_BLOCK_THRESHOLD ドル以上なら取引停止。

    Args:
        symbol:      取引シンボル（"XAUUSD"）
        entry_price: 現在の想定エントリー価格

    Returns:
        {
            "blocked": bool,
            "gap_usd": float,
            "reason": str
        }
    """
    now_utc = datetime.now(timezone.utc)
    # 月曜 01:00-03:00 UTC のみ確認
    if now_utc.weekday() != 0 or not (1 <= now_utc.hour < 3):
        return {"blocked": False, "gap_usd": 0.0, "reason": "not_gap_window"}

    if not MT5_AVAILABLE:
        return {"blocked": False, "gap_usd": 0.0, "reason": "mt5_unavailable"}

    try:
        # 直近2本のD1足を取得
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 2)
    except Exception as exc:
        logger.error("check_gap_risk mt5 error: %s", exc)
        return {"blocked": False, "gap_usd": 0.0, "reason": "mt5_error"}

    if rates is None or len(rates) < 2:
        return {"blocked": False, "gap_usd": 0.0, "reason": "no_rates"}

    # rates[0] = 最新（月曜のD1）/ rates[1] = 前営業日（金曜）
    # ただし D1 はサーバー時刻に依存するため金曜の終値は rates[1].close
    friday_close = float(rates[1]["close"])
    gap_usd = abs(entry_price - friday_close)

    if gap_usd >= GAP_BLOCK_THRESHOLD:
        reason = (
            f"週明けギャップ {gap_usd:.1f} USD ≥ 閾値 {GAP_BLOCK_THRESHOLD:.1f} USD"
        )
        logger.warning("RISK: %s", reason)
        return {"blocked": True, "gap_usd": gap_usd, "reason": reason}

    return {"blocked": False, "gap_usd": gap_usd, "reason": "ok"}


# ────────────────────────────────────────────────────────────
# 統合チェック（executor.pyから呼び出す便利関数）
# ────────────────────────────────────────────────────────────

def run_all_risk_checks(symbol: str, entry_price: float = 0.0) -> dict:
    """
    3つのリスクチェックをまとめて実行。
    いずれか1つでもブロックされた場合は blocked=True を返す。

    Returns:
        {
            "blocked": bool,
            "reason": str,
            "details": {
                "daily_loss": dict,
                "consecutive": dict,
                "gap": dict
            }
        }
    """
    daily   = check_daily_loss_limit()
    consec  = check_consecutive_losses()
    gap     = check_gap_risk(symbol, entry_price)

    blocked = daily["blocked"] or consec["blocked"] or gap["blocked"]

    if daily["blocked"]:
        reason = daily["reason"]
    elif consec["blocked"]:
        reason = consec["reason"]
    elif gap["blocked"]:
        reason = gap["reason"]
    else:
        reason = "ok"

    return {
        "blocked": blocked,
        "reason": reason,
        "details": {
            "daily_loss":  daily,
            "consecutive": consec,
            "gap":         gap,
        },
    }
