"""
param_optimizer.py - ATR乗数の動的最適化
AI Trading System v2.0

市場環境（ATRパーセンタイル・トレンド強度）と最近のトレード成績に基づいて
ATR乗数（atr_sl_multiplier / atr_tp_multiplier）を動的に調整する。

executor.py の build_order_params から get_live_params() を呼び出して使用する。

調整ルール:
  1. 高ボラ環境（ATR%ile >= 80）: SL乗数を拡大（×1.2）
  2. 低ボラ環境（ATR%ile <= 20）: SL乗数を縮小（×0.8）
  3. 低勝率（< 40%、直近20件）  : SL乗数を拡大（ノイズで狩られている可能性）
  4. 強トレンド                 : TP乗数を拡大（トレンドフォロー）
  5. レンジ相場                 : TP乗数を縮小（早めの利確）

結果は5分間キャッシュされ param_history テーブルに記録される。
"""

import logging
import threading
import time
from datetime import datetime, timezone

from config import SYSTEM_CONFIG
from database import get_connection

logger = logging.getLogger(__name__)

# ── 基準値（config.pyの静的設定）──────────────────────────
_BASE_SL_MULT = SYSTEM_CONFIG["atr_sl_multiplier"]   # 2.0
_BASE_TP_MULT = SYSTEM_CONFIG["atr_tp_multiplier"]   # 3.0

# ── 乗数の上下限 ──────────────────────────────────────────
SL_MULT_MIN = 1.5
SL_MULT_MAX = 3.5
TP_MULT_MIN = 2.0
TP_MULT_MAX = 3.5

# ── キャッシュ設定 ────────────────────────────────────────
_CACHE_TTL_SEC = 300   # 5分

# ── スレッドセーフなキャッシュ ────────────────────────────
_cache_lock      = threading.Lock()
_cached_params:  dict | None = None
_cache_expires:  float = 0.0


# ──────────────────────────────────────────────────────────
# DB ユーティリティ
# ──────────────────────────────────────────────────────────

def _fetch_recent_trades(n: int = 20) -> list[dict]:
    """直近 n 件のトレード結果を返す（新しい順）"""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT outcome, pnl_usd
            FROM   trade_results
            ORDER  BY id DESC
            LIMIT  ?
            """,
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("param_optimizer DB error: %s", exc)
        return []


def _save_param_history(sl_mult: float, tp_mult: float,
                        regime: str, win_rate: float,
                        consecutive_losses: int, reason: str) -> None:
    """調整結果を param_history テーブルに記録する"""
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO param_history
            (updated_at, atr_sl_mult, atr_tp_mult, regime,
             win_rate, consecutive_losses, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                sl_mult, tp_mult, regime,
                win_rate, consecutive_losses, reason,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.error("param_history 保存エラー: %s", exc)


# ──────────────────────────────────────────────────────────
# 最適化ロジック
# ──────────────────────────────────────────────────────────

def _compute_trade_stats(trades: list[dict]) -> dict:
    """
    トレードリストから勝率・平均損益・連続損失数を計算する。
    """
    if not trades:
        return {"win_rate": 0.5, "avg_pnl": 0.0, "consecutive_losses": 0, "n": 0}

    wins = sum(
        1 for t in trades
        if t["outcome"] in ("tp_hit", "partial_tp", "trailing_sl", "manual")
           and t["pnl_usd"] > 0
    )
    win_rate = wins / len(trades)
    avg_pnl  = sum(t["pnl_usd"] for t in trades) / len(trades)

    consecutive_losses = 0
    for t in trades:   # DESC順なので最新から
        if t["outcome"] == "sl_hit":
            consecutive_losses += 1
        else:
            break

    return {
        "win_rate":          round(win_rate, 3),
        "avg_pnl":           round(avg_pnl, 2),
        "consecutive_losses": consecutive_losses,
        "n":                 len(trades),
    }


def _get_atr_percentile() -> int:
    """
    MT5が利用可能な場合、15分足ATR14のパーセンタイル（0-100）を返す。
    利用不可能な場合は中央値 50 を返す。
    """
    try:
        import MetaTrader5 as mt5
        import pandas as pd
        symbol = SYSTEM_CONFIG["symbol"]
        lookback = 100
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, lookback + 20)
        if rates is None or len(rates) < lookback:
            return 50
        df = pd.DataFrame(rates)
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"]  - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(window=14, min_periods=14).mean().dropna()
        if len(atr_series) < 2:
            return 50
        current_atr = float(atr_series.iloc[-1])
        historical  = atr_series.iloc[-lookback:-1]
        return int((historical < current_atr).sum() / len(historical) * 100)
    except Exception:
        return 50


def _get_trend_strength() -> str:
    """
    MT5が利用可能な場合、1時間足 SMA50/SMA200 でトレンド強度を返す。
    利用不可能な場合は 'range' を返す。
    """
    try:
        import MetaTrader5 as mt5
        import pandas as pd
        symbol = SYSTEM_CONFIG["symbol"]
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 210)
        if rates is None or len(rates) < 200:
            return "range"
        df    = pd.DataFrame(rates)
        sma50 = df["close"].rolling(50).mean().iloc[-1]
        sma200= df["close"].rolling(200).mean().iloc[-1]
        close = df["close"].iloc[-1]
        diff_pct = (sma50 - sma200) / sma200 * 100
        if diff_pct > 1.0 and close > sma50:
            return "strong_bull"
        if diff_pct > 0.2:
            return "bull"
        if diff_pct < -1.0 and close < sma50:
            return "strong_bear"
        if diff_pct < -0.2:
            return "bear"
        return "range"
    except Exception:
        return "range"


def compute_optimized_params() -> dict:
    """
    市場環境 + 最近のトレード成績から最適なATR乗数を計算して返す。

    Returns:
        {
            "atr_sl_multiplier": float,
            "atr_tp_multiplier": float,
            "regime":            str,
            "win_rate":          float,
            "consecutive_losses": int,
            "reason":            str,
        }
    """
    trades = _fetch_recent_trades(n=20)
    stats  = _compute_trade_stats(trades)
    atr_pct = _get_atr_percentile()
    trend   = _get_trend_strength()

    sl_mult = _BASE_SL_MULT
    tp_mult = _BASE_TP_MULT
    reasons = []

    # ── ボラティリティ環境による SL 調整 ────────────────
    if atr_pct >= 80:
        sl_mult *= 1.2
        reasons.append(f"高ボラ(ATR%ile={atr_pct})→SL拡大")
    elif atr_pct <= 20:
        sl_mult *= 0.8
        reasons.append(f"低ボラ(ATR%ile={atr_pct})→SL縮小")

    # ── 成績による SL 調整 ───────────────────────────────
    if stats["n"] >= 10:
        if stats["win_rate"] < 0.40:
            sl_mult *= 1.15
            reasons.append(f"低勝率({stats['win_rate']:.1%})→SL拡大")
        elif stats["win_rate"] > 0.65:
            tp_mult *= 1.1
            reasons.append(f"高勝率({stats['win_rate']:.1%})→TP拡大")

    # ── トレンド強度による TP 調整 ───────────────────────
    if trend in ("strong_bull", "strong_bear"):
        tp_mult *= 1.2
        reasons.append(f"強トレンド({trend})→TP拡大")
    elif trend == "range":
        tp_mult *= 0.85
        reasons.append(f"レンジ相場→TP縮小")

    # ── 上下限クランプ ───────────────────────────────────
    sl_mult = round(max(SL_MULT_MIN, min(SL_MULT_MAX, sl_mult)), 3)
    tp_mult = round(max(TP_MULT_MIN, min(TP_MULT_MAX, tp_mult)), 3)

    reason_str = "、".join(reasons) if reasons else "調整なし（デフォルト値使用）"
    regime = trend

    logger.info(
        "📐 動的パラメータ: sl_mult=%.2f tp_mult=%.2f regime=%s win_rate=%.1f%% reason=%s",
        sl_mult, tp_mult, regime,
        stats["win_rate"] * 100,
        reason_str,
    )

    _save_param_history(
        sl_mult=sl_mult, tp_mult=tp_mult,
        regime=regime,
        win_rate=stats["win_rate"],
        consecutive_losses=stats["consecutive_losses"],
        reason=reason_str,
    )

    return {
        "atr_sl_multiplier":  sl_mult,
        "atr_tp_multiplier":  tp_mult,
        "regime":             regime,
        "win_rate":           stats["win_rate"],
        "consecutive_losses": stats["consecutive_losses"],
        "reason":             reason_str,
    }


def get_live_params() -> dict:
    """
    キャッシュ済みの最適化パラメータを返す（TTL=5分）。
    キャッシュ期限切れの場合は再計算する。

    Returns:
        {
            "atr_sl_multiplier": float,
            "atr_tp_multiplier": float,
            ...
        }
    """
    global _cached_params, _cache_expires

    with _cache_lock:
        now = time.monotonic()
        if _cached_params is None or now >= _cache_expires:
            _cached_params  = compute_optimized_params()
            _cache_expires  = now + _CACHE_TTL_SEC

        return _cached_params.copy()


def get_latest_from_db() -> dict | None:
    """
    直近の param_history レコードを返す（ダッシュボード表示用）。
    """
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT * FROM param_history ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("get_latest_from_db エラー: %s", exc)
        return None


def get_history(n: int = 20) -> list[dict]:
    """
    直近 n 件の param_history レコードを返す（新しい順）。
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM param_history ORDER BY id DESC LIMIT ?
            """,
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_history エラー: %s", exc)
        return []
