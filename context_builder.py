"""
context_builder.py - AIコンテキスト組み立て（MT5指標含む）
AI Trading System v2.0
"""

import logging
import time
from datetime import datetime, timezone, timedelta

try:
    import MetaTrader5 as mt5
    import pandas as pd
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from database import get_connection
from config import SYSTEM_CONFIG
from market_hours import get_current_session

logger = logging.getLogger(__name__)

TIME_WINDOWS = SYSTEM_CONFIG["time_windows"]
SYMBOL       = SYSTEM_CONFIG["symbol"]


# ─────────────────────────── MT5指標取得 ──────────────────

def _ensure_symbol_selected(symbol: str, retries: int = 3, delay: float = 1.0) -> bool:
    """シンボルをMarketWatchに追加し、データが利用可能か確認する（リトライあり）"""
    if not MT5_AVAILABLE:
        return False
    for attempt in range(1, retries + 1):
        # terminal_info で接続・準備状態を確認
        info = mt5.terminal_info()
        if info is None:
            logger.error("[%d/%d] terminal_info取得失敗: last_error=%s", attempt, retries, mt5.last_error())
            time.sleep(delay)
            continue
        if not getattr(info, "connected", False):
            logger.error("[%d/%d] MT5未接続(connected=False): last_error=%s", attempt, retries, mt5.last_error())
            time.sleep(delay)
            continue
        # symbol_select: MarketWatchに追加 & データ購読を有効化
        if mt5.symbol_select(symbol, True):
            sym_info = mt5.symbol_info(symbol)
            if sym_info is not None:
                return True
            logger.warning("[%d/%d] symbol_info=None: symbol=%s, last_error=%s", attempt, retries, symbol, mt5.last_error())
        else:
            err = mt5.last_error()
            logger.warning("[%d/%d] symbol_select失敗: symbol=%s, last_error=%s", attempt, retries, symbol, err)
        if attempt < retries:
            time.sleep(delay)
    logger.error("symbol_select最終失敗: symbol=%s (%d回試行)", symbol, retries)
    return False


def _get_mt5_indicators(symbol: str, tf_mt5: int, tf_label: str,
                         smas: list, extra: bool = False) -> dict:
    """指定時間足のテクニカル指標を返す"""
    if not MT5_AVAILABLE:
        return {"error": "MT5未インストール"}
    if not _ensure_symbol_selected(symbol):
        return {"error": "MT5接続/シンボル選択失敗"}
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf_mt5, 0, 300)
        if rates is None:
            err = mt5.last_error()
            logger.error("copy_rates_from_pos(%s %s) → None: last_error=%s", symbol, tf_label, err)
            return {"error": f"取得失敗({err})"}
        if len(rates) < 50:
            logger.warning("copy_rates_from_pos(%s %s) → バー数不足: %d本 (最低50本必要)",
                           symbol, tf_label, len(rates))
            return {"error": f"データ不足({len(rates)}本)"}
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)

        result = {}
        for sma_period in smas:
            df[f"sma{sma_period}"] = df["close"].rolling(
                window=sma_period,
                min_periods=sma_period,
            ).mean()
            result[f"sma{sma_period}"] = round(float(df[f"sma{sma_period}"].iloc[-1]), 3)

        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        df["rsi14"] = 100 - (100 / (1 + rs))

        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["atr14"] = tr.rolling(window=14, min_periods=14).mean()

        result["rsi14"] = round(float(df["rsi14"].iloc[-1]), 2)
        result["atr14"] = round(float(df["atr14"].iloc[-1]), 3)
        result["close"] = round(float(df["close"].iloc[-1]), 3)

        if extra:
            result["ema20"] = round(float(df["ema20"].iloc[-1]), 3)
            sma20_val = df["sma20"].iloc[-1] if "sma20" in df.columns else None
            if sma20_val:
                diff = df["close"].iloc[-1] - float(sma20_val)
                result["price_vs_sma20"] = round(diff, 3)

        return result
    except Exception as e:
        logger.error("MT5指標取得エラー(%s %s): %s", symbol, tf_label, e)
        return {"error": str(e)}


def get_mt5_context(symbol: str = SYMBOL) -> dict:
    """
    5分・15分・1時間足のテクニカル指標と口座情報を返す。
    """
    if not MT5_AVAILABLE:
        return {"error": "MT5未インストール"}

    TF_5M  = mt5.TIMEFRAME_M5
    TF_15M = mt5.TIMEFRAME_M15
    TF_1H  = mt5.TIMEFRAME_H1

    ctx = {
        "indicators_5m":  _get_mt5_indicators(symbol, TF_5M,  "5m",  [20], extra=True),
        "indicators_15m": _get_mt5_indicators(symbol, TF_15M, "15m", [20]),
        "indicators_1h":  _get_mt5_indicators(symbol, TF_1H,  "1h",  [50, 200], extra=False),
    }

    # 1時間足のprice_vs_sma200
    try:
        close_1h  = ctx["indicators_1h"].get("close", 0)
        sma200_1h = ctx["indicators_1h"].get("sma200", 0)
        if sma200_1h:
            ctx["indicators_1h"]["price_vs_sma200"] = round(close_1h - sma200_1h, 3)
    except Exception:
        pass

    # 口座情報
    try:
        acc = mt5.account_info()
        if acc:
            ctx["account"] = {
                "balance":        acc.balance,
                "equity":         acc.equity,
                "margin_free":    acc.margin_free,
                "open_positions": len(mt5.positions_get() or []),
            }
    except Exception as e:
        ctx["account"] = {"error": str(e)}

    return ctx


# ─────────────────────────── 統計コンテキスト ─────────────

def _get_atr_percentile(symbol: str, tf_mt5: int, lookback: int = 100) -> int:
    """
    現在のATR14を過去lookback本のATRと比較し、パーセンタイル順位（0-100）を返す。
    高い値ほど現在ボラが大きい（90超 = 直近100本中で最もボラが高い10%以内）。
    """
    if not MT5_AVAILABLE:
        return 50  # テスト用デフォルト
    if not _ensure_symbol_selected(symbol):
        return 50
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf_mt5, 0, lookback + 20)
        if rates is None:
            logger.error("_get_atr_percentile copy_rates失敗: last_error=%s", mt5.last_error())
            return 50
        if len(rates) < lookback:
            logger.warning("_get_atr_percentile バー数不足: %d本", len(rates))
            return 50
        df = pd.DataFrame(rates)
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(window=14, min_periods=14).mean().dropna()
        if len(atr_series) < 2:
            return 50
        current_atr = float(atr_series.iloc[-1])
        historical   = atr_series.iloc[-lookback:-1]
        rank = int((historical < current_atr).sum() / len(historical) * 100)
        return rank
    except Exception as e:
        logger.error("_get_atr_percentile error: %s", e)
        return 50


def _get_trading_stats(recent_n: int = 20) -> dict:
    """
    直近 recent_n 件のトレード結果からWR・平均損益・連続損失数を返す。

    Returns:
        {
            "win_rate": float (0.0-1.0),
            "avg_pnl_usd": float,
            "consecutive_losses": int,
            "trade_count": int
        }
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT outcome, pnl_usd
            FROM   trade_results
            ORDER  BY id DESC
            LIMIT  ?
            """,
            (recent_n,),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.error("_get_trading_stats DB error: %s", e)
        return {"win_rate": 0.5, "avg_pnl_usd": 0.0, "consecutive_losses": 0, "trade_count": 0}

    if not rows:
        return {"win_rate": 0.5, "avg_pnl_usd": 0.0, "consecutive_losses": 0, "trade_count": 0}

    outcomes = [r["outcome"] for r in rows]
    pnls     = [float(r["pnl_usd"]) for r in rows]

    wins     = sum(1 for o in outcomes if o in ("tp_hit", "partial_tp", "trailing_sl", "manual"))
    win_rate = wins / len(outcomes)
    avg_pnl  = sum(pnls) / len(pnls)

    # 直近からの連続損失数
    consecutive = 0
    for o in outcomes:  # DESC順なので最新から
        if o == "sl_hit":
            consecutive += 1
        else:
            break

    return {
        "win_rate":           round(win_rate, 3),
        "avg_pnl_usd":        round(avg_pnl, 2),
        "consecutive_losses": consecutive,
        "trade_count":        len(rows),
    }


def _get_market_regime(symbol: str) -> dict:
    """
    現在のマーケットレジームを判定する。

    Returns:
        {
            "atr_percentile_15m": int,   # 0-100
            "rsi_zscore_5m":      float, # RSI14の過去50本Zスコア
            "trend_strength":     str    # "strong_bull" / "bull" / "range" / "bear" / "strong_bear"
        }
    """
    if not MT5_AVAILABLE:
        return {
            "atr_percentile_15m": 50,
            "rsi_zscore_5m":      0.0,
            "trend_strength":     "range",
        }

    atr_pct = _get_atr_percentile(symbol, mt5.TIMEFRAME_M15, lookback=100)

    # RSI Zスコア（5分足）
    rsi_zscore = 0.0
    try:
        rates5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 70)
        if rates5 is not None and len(rates5) >= 50:
            df5 = pd.DataFrame(rates5)
            delta = df5["close"].diff()
            gain  = delta.clip(lower=0)
            loss  = -delta.clip(upper=0)
            avg_g = gain.ewm(alpha=1 / 14, adjust=False).mean()
            avg_l = loss.ewm(alpha=1 / 14, adjust=False).mean()
            rs    = avg_g / avg_l.replace(0, pd.NA)
            rsi   = 100 - (100 / (1 + rs))
            rsi_clean = rsi.dropna().iloc[-50:]
            if len(rsi_clean) >= 10:
                mean = rsi_clean.mean()
                std  = rsi_clean.std()
                if std > 0:
                    rsi_zscore = round(float((rsi_clean.iloc[-1] - mean) / std), 2)
    except Exception as e:
        logger.error("RSI Zスコア計算エラー: %s", e)

    # トレンド強度（1時間足 SMA50 vs SMA200）
    trend_strength = "range"
    try:
        rates1h = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 210)
        if rates1h is not None and len(rates1h) >= 200:
            df1h    = pd.DataFrame(rates1h)
            sma50   = df1h["close"].rolling(50).mean().iloc[-1]
            sma200  = df1h["close"].rolling(200).mean().iloc[-1]
            close1h = df1h["close"].iloc[-1]
            diff_pct = (sma50 - sma200) / sma200 * 100
            if diff_pct > 1.0 and close1h > sma50:
                trend_strength = "strong_bull"
            elif diff_pct > 0.2:
                trend_strength = "bull"
            elif diff_pct < -1.0 and close1h < sma50:
                trend_strength = "strong_bear"
            elif diff_pct < -0.2:
                trend_strength = "bear"
            else:
                trend_strength = "range"
    except Exception as e:
        logger.error("トレンド強度計算エラー: %s", e)

    return {
        "atr_percentile_15m": atr_pct,
        "rsi_zscore_5m":      rsi_zscore,
        "trend_strength":     trend_strength,
    }


# ─────────────────────────── structureシグナル取得 ─────────

def _fetch_structure_signals(event: str, window_sec: int) -> list:
    since = (
        datetime.now(timezone.utc) - timedelta(seconds=window_sec)
    ).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE event = ?
              AND received_at >= ?
            ORDER BY received_at DESC
        """, (event, since)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _fetch_macro_zones_with_direction(window_sec: int, current_price: float) -> list:
    """
    new_zone_confirmed シグナルを取得し、現在価格との比較で direction を動的に判定する。

    - zone価格 < 現在価格 → Demand Zone（支持帯）→ direction="buy"
    - zone価格 > 現在価格 → Supply Zone（抵抗帯）→ direction="sell"

    TradingView側が送信する direction は価格変動により古くなるため、
    現在価格を基準に必ず上書きする。
    """
    signals = _fetch_structure_signals("new_zone_confirmed", window_sec)
    result = []
    for s in signals:
        try:
            zone_price = float(s["price"])
        except (TypeError, ValueError):
            result.append(s)
            continue

        s = dict(s)  # イミュータブルなコピーを作成
        if zone_price < current_price:
            s["direction"] = "buy"   # Demand Zone（価格の下にある → 支持帯）
        elif zone_price > current_price:
            s["direction"] = "sell"  # Supply Zone（価格の上にある → 抵抗帯）
        else:
            s["direction"] = "none"  # 現在価格と履んでいる（調整なし）
        result.append(s)
    return result


def build_context_for_ai(entry_signals: list) -> dict:
    """
    entry_triggerシグナル群を受け取り、AIに渡すコンテキストを組み立てる。
    """
    symbol = entry_signals[0]["symbol"] if entry_signals else SYMBOL

    # 現在価格：MT5ティックを最優先、取得失敗時にentry_signals価格を使用
    # （TV送信時前の価格よりもMT5リアルタイムティックの方が常に最新であるため）
    current_price: float | None = None
    if MT5_AVAILABLE:
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                current_price = float((tick.bid + tick.ask) / 2)
        except Exception:
            pass
    if current_price is None:
        try:
            current_price = float(entry_signals[0]["price"]) if entry_signals else None
        except (TypeError, ValueError):
            current_price = None

    context = {
        "entry_signals": entry_signals,
        "mt5_context":   get_mt5_context(symbol),
        "structure": {
            # 12時間窓：現在価格と比較してDemand/Supplyを動的判定
            "macro_zones": (
                _fetch_macro_zones_with_direction(
                    TIME_WINDOWS["new_zone_confirmed"], current_price)
                if current_price is not None
                else _fetch_structure_signals(
                    "new_zone_confirmed", TIME_WINDOWS["new_zone_confirmed"])
            ),
            # 15分窓
            "zone_retrace": _fetch_structure_signals(
                "zone_retrace_touch", TIME_WINDOWS["zone_retrace_touch"]),
            # 15分窓
            "fvg_touch": _fetch_structure_signals(
                "fvg_touch", TIME_WINDOWS["fvg_touch"]),
            # 30分窓
            "liquidity_sweep": _fetch_structure_signals(
                "liquidity_sweep", TIME_WINDOWS["liquidity_sweep"]),
        },
        "statistical_context": {
            "market_regime":  _get_market_regime(symbol),
            "trading_stats":  _get_trading_stats(recent_n=20),
            "session_info":   get_current_session(),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return context
