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
MAX_DAILY_LOSS_PCT           = SYSTEM_CONFIG.get("max_daily_loss_percent",       -5.0)
MAX_CONSECUTIVE_LOSS         = SYSTEM_CONFIG.get("max_consecutive_losses",        3)
CONSECUTIVE_LOSS_RESET_HOURS = SYSTEM_CONFIG.get("consecutive_loss_reset_hours", 24)
GAP_BLOCK_THRESHOLD          = SYSTEM_CONFIG.get("gap_block_threshold_usd",      15.0)
SYMBOL                       = SYSTEM_CONFIG.get("symbol", "XAUUSD")
FALLBACK_BALANCE             = SYSTEM_CONFIG.get("fallback_balance",             10000.0)


def _get_balance() -> float:
    """MT5から口座残高を取得する。取得失敗時は config の fallback_balance を使用"""
    if MT5_AVAILABLE:
        try:
            acc = mt5.account_info()
            if acc and acc.balance > 0:
                return acc.balance
        except Exception:
            pass
    return FALLBACK_BALANCE


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

    複数ポジション同時決済に対応：
    closed_at が ±10秒以内の sl_hit は「1回の連続損失」としてまとめてカウントする。

    時間ベースリセット：
    CONSECUTIVE_LOSS_RESET_HOURS > 0 の場合、その時間より古いトレードは
    連続カウントから除外する（例: 24時間前より古い損失は引き継がない）。

    Returns:
        {
            "blocked":           bool,
            "consecutive_count": int,
            "reason":            str,
            "reset_hours":       int,   # 適用中のリセット時間（0=無効）
        }
    """
    from datetime import timedelta

    n           = MAX_CONSECUTIVE_LOSS
    reset_hours = CONSECUTIVE_LOSS_RESET_HOURS

    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT outcome, closed_at
            FROM   trade_results
            ORDER  BY id DESC
            LIMIT  50
            """,
        ).fetchall()
    except Exception as exc:
        logger.error("check_consecutive_losses DB error: %s", exc)
        return {"blocked": False, "consecutive_count": 0,
                "reason": "db_error", "reset_hours": reset_hours}

    if not rows:
        return {"blocked": False, "consecutive_count": 0,
                "reason": "insufficient_data", "reset_hours": reset_hours}

    def _parse_dt(s):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            # タイムゾーン情報がない場合は UTC として扱う
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    # ── 時間ベースリセット：cutoff より古いトレードを除外 ──────
    if reset_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=reset_hours)
        rows = [
            r for r in rows
            if (_parse_dt(r["closed_at"]) or datetime.min.replace(tzinfo=timezone.utc))
               >= cutoff
        ]

    if not rows:
        # cutoff によって全件が除外された → 連続損失リセット済み
        logger.info(
            "連続損失カウント: 直近 %d 時間以内のトレードなし → カウントリセット",
            reset_hours,
        )
        return {"blocked": False, "consecutive_count": 0,
                "reason": "reset_by_time", "reset_hours": reset_hours}

    # ── 同時決済グループにまとめる（±10秒以内のsl_hitを1グループ）──
    grouped = []   # [{"outcome": str, "closed_at": datetime}, ...]
    for row in rows:
        outcome   = row["outcome"]
        closed_at = _parse_dt(row["closed_at"])

        if not grouped:
            grouped.append({"outcome": outcome, "closed_at": closed_at})
            continue

        last = grouped[-1]
        # 直前グループと同じ outcome かつ 0秒超〜10秒以内 → 同時決済とみなしスキップ
        # （差分が0秒＝完全同一タイムスタンプは別ロスとしてカウント）
        if (
            outcome == "sl_hit"
            and last["outcome"] == "sl_hit"
            and last["closed_at"] is not None
            and closed_at is not None
            and 0 < abs((closed_at - last["closed_at"]).total_seconds()) <= 10
        ):
            continue  # 同時決済とみなし重複カウントしない

        grouped.append({"outcome": outcome, "closed_at": closed_at})

    # ── グループ化後の直近 n 件で連続損失を判定 ──
    recent = grouped[:n]

    if len(recent) < n:
        # まだ充分なデータがない
        count = sum(1 for g in recent if g["outcome"] == "sl_hit")
        return {"blocked": False, "consecutive_count": count,
                "reason": "insufficient_data", "reset_hours": reset_hours}

    all_sl = all(g["outcome"] == "sl_hit" for g in recent)
    if all_sl:
        reason = f"直近 {n} 件が連続SL被弾 → 取引停止"
        logger.warning("RISK: %s", reason)
        return {"blocked": True, "consecutive_count": n,
                "reason": reason, "reset_hours": reset_hours}

    # 実際の連続損失数（直近から数える）
    count = 0
    for g in grouped:
        if g["outcome"] == "sl_hit":
            count += 1
        else:
            break

    return {"blocked": False, "consecutive_count": count,
            "reason": "ok", "reset_hours": reset_hours}


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

def reset_daily_stats(delete_records: bool = False) -> dict:
    """
    日次ストップ・連続損失カウントを手動リセットする（デモ用）。

    Args:
        delete_records: True → 当日の trade_results を物理削除
                        False → pnl_usd=0 の補正レコードを挿入（履歴保持）

    Returns:
        {"ok": bool, "message": str, "daily_pnl_before": float, "consecutive_before": int}
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    daily_before = 0.0
    consec_before = 0

    try:
        conn = get_connection()

        # 現在の状態を記録
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0.0) AS total FROM trade_results WHERE closed_at LIKE ?",
            (today_str + "%",),
        ).fetchone()
        daily_before = float(row["total"])

        rows = conn.execute(
            "SELECT outcome FROM trade_results ORDER BY id DESC LIMIT ?",
            (MAX_CONSECUTIVE_LOSS,),
        ).fetchall()
        for r in rows:
            if r["outcome"] == "sl_hit":
                consec_before += 1
            else:
                break

        if delete_records:
            # 当日レコードを物理削除
            deleted = conn.execute(
                "DELETE FROM trade_results WHERE closed_at LIKE ?",
                (today_str + "%",),
            ).rowcount
            conn.commit()
            message = f"当日 trade_results を {deleted} 件削除しました"
        else:
            # 補正レコードを挿入（daily_pnl を 0 に戻す）
            offset = -daily_before  # 損失分を相殺
            conn.execute(
                """
                INSERT INTO trade_results
                    (execution_id, mt5_ticket, outcome, pnl_usd, pnl_pips, duration_min, closed_at)
                VALUES (NULL, 0, 'manual', ?, 0, 0, ?)
                """,
                (offset, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            message = f"補正レコード挿入: offset={offset:.2f} (当日PnLを0にリセット)"

        logger.warning("⚠️ リスクリセット実行: %s", message)
        return {
            "ok": True,
            "message": message,
            "daily_pnl_before": daily_before,
            "consecutive_before": consec_before,
        }

    except Exception as exc:
        logger.error("reset_daily_stats error: %s", exc)
        return {"ok": False, "message": str(exc), "daily_pnl_before": 0.0, "consecutive_before": 0}


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


# ────────────────────────────────────────────────────────────
# 4. 高インパクト経済指標時間帯チェック
# ────────────────────────────────────────────────────────────

def is_high_impact_period() -> bool:
    """
    config.HIGH_IMPACT_UTC_TIMESに基づき、現在が高インパクト時間帯かを返す。
    Trueの場合、新規エントリーをスキップすること。
    """
    from datetime import datetime, timezone
    from config import HIGH_IMPACT_UTC_TIMES
    now = datetime.now(timezone.utc)
    for period in HIGH_IMPACT_UTC_TIMES:
        if period["weekday"] is not None and now.weekday() != period["weekday"]:
            continue
        if period["hour_start"] <= now.hour < period["hour_end"]:
            return True
    return False
