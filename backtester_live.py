"""
backtester_live.py - ライブパイプラインと同一ロジックによるバックテスト
AI Trading System v3.0

【既存 backtester.py との根本的な違い】
  既存: atr_breakout / rsi_reversal という「別の戦略」でシグナルを生成
  本ファイル: TradingViewが実際に送信したアラート履歴CSVをそのまま入力とし、
              scoring_engine（= ライブと同一コード）でフィルタリングする。

  これにより「バックテストとライブの乖離」を構造的に排除する。

【入力CSVフォーマット】
  TradingViewのアラート履歴をエクスポートしたもの。
  以下の2形式を自動判別する:

  (A) TradingViewアラート履歴エクスポート形式（推奨）:
      列: Alert ID, Ticker, Name, Description, Time, Webhook status
      DescriptionはJSON文字列。以下のキーを解析する:
        time / price / source / side / signal_type / event / confirmed /
        confidence / win_rate / strength / action

  (B) シンプルCSV形式:
      列: timestamp, signal_type, event, direction, price, source
      オプション列: confirmed, tv_confidence, tv_win_rate, strength

【OHLCVデータ】
  OHLCVデータ（MT5から取得 or CSV）と時刻でjoinして
  バー確定時のRSI/ADX/ATRをコンテキストに付与する。

【使い方】
  # TradingViewアラート履歴CSV + MT5データでバックテスト
  python backtester_live.py --alerts tv_alerts.csv --mt5 --symbol GOLD --tf M5

  # アラートCSV + OHLCVのCSVを両方指定
  python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv

  # スコアリング閾値を変えて比較
  python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv \\
      --approve-threshold 0.45 --wait-threshold 0.15

  # Gate2を無効にして効果を測定
  python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv --no-gate2

  # 閾値感度分析
  python backtester_live.py --alerts tv_alerts.csv --ohlcv ohlcv.csv --sensitivity

CLIオプション:
  --alerts              TradingViewアラート履歴CSV（必須）
  --ohlcv               OHLCVデータCSV
  --mt5                 MT5から直接OHLCVを取得
  --symbol              シンボル（デフォルト: GOLD）
  --tf                  時間足（デフォルト: M5）
  --bars                MT5取得バー数（デフォルト: 5000）
  --approve-threshold   approveスコア閾値（デフォルト: config値）
  --wait-threshold      waitスコア閾値（デフォルト: config値）
  --no-gate2            Gate2（Q-trend不一致+bar_close未確認）を無効化
  --sl-mult             ATR SL乗数上書き
  --tp-mult             ATR TP乗数上書き
  --spread              スプレッド$（デフォルト: 0.50）
  --slippage            スリッページ$（デフォルト: 0.10）
  --initial-balance     初期残高（デフォルト: 10000）
  --risk-pct            1トレードのリスク%（デフォルト: config値）
  --sensitivity         approve_threshold感度分析を実行
  --output              結果CSVの出力先
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────────────────

@dataclass
class LiveBacktestTrade:
    """1トレードの記録"""
    alert_time:      str
    direction:       str
    entry_price:     float
    sl_price:        float
    tp_price:        float
    lot_size:        float
    atr:             float
    sl_dollar:       float

    # scoring結果
    score:           float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    decision:        str = "approve"

    # 結果
    exit_price:      float = 0.0
    outcome:         str = "open"   # tp_hit / sl_hit / trailing_sl / open
    pnl:             float = 0.0
    pnl_pips:        float = 0.0
    duration_bars:   int = 0

    # BE/部分決済フラグ
    be_applied:      bool = False
    partial_closed:  bool = False
    partial_pnl:     float = 0.0


@dataclass
class LiveBacktestResult:
    trades:         list
    balance_curve:  list
    params:         dict
    alert_count:    int = 0
    approved_count: int = 0
    rejected_count: int = 0
    waited_count:   int = 0

    @property
    def completed_trades(self) -> list:
        return [t for t in self.trades if t.outcome != "open"]

    @property
    def win_rate(self) -> float:
        done = self.completed_trades
        if not done:
            return 0.0
        return sum(1 for t in done if t.pnl > 0) / len(done)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl + t.partial_pnl for t in self.completed_trades)

    @property
    def profit_factor(self) -> float:
        gross_win  = sum(t.pnl + t.partial_pnl for t in self.completed_trades
                         if (t.pnl + t.partial_pnl) > 0)
        gross_loss = sum(abs(t.pnl + t.partial_pnl) for t in self.completed_trades
                         if (t.pnl + t.partial_pnl) < 0)
        return gross_win / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        if not self.balance_curve:
            return 0.0
        peak = self.balance_curve[0]
        max_dd = 0.0
        for b in self.balance_curve:
            peak = max(peak, b)
            dd = (peak - b) / peak * 100
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def filter_rate(self) -> float:
        if self.alert_count == 0:
            return 0.0
        return (self.rejected_count + self.waited_count) / self.alert_count

    def summary(self) -> str:
        done   = self.completed_trades
        wins   = sum(1 for t in done if t.pnl > 0)
        losses = sum(1 for t in done if t.pnl <= 0)
        lines = [
            "=" * 60,
            "  Live Backtest Result",
            "=" * 60,
            f"  アラート総数      : {self.alert_count}件",
            f"  approve           : {self.approved_count}件",
            f"  reject            : {self.rejected_count}件",
            f"  wait(タイムアウト): {self.waited_count}件",
            f"  フィルター率      : {self.filter_rate:.1%}",
            f"  完了トレード数    : {len(done)}件 (勝: {wins} / 負: {losses})",
            f"  勝率              : {self.win_rate:.1%}",
            f"  総損益            : ${self.total_pnl:+.2f}",
            f"  Profit Factor     : {self.profit_factor:.2f}",
            f"  最大ドローダウン  : {self.max_drawdown:.1f}%",
            f"  最終残高          : ${self.balance_curve[-1]:.2f}" if self.balance_curve else "",
            "=" * 60,
        ]
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# アラートCSV読み込み
# ──────────────────────────────────────────────────────────

def _parse_tv_export(df: pd.DataFrame) -> pd.DataFrame:
    """
    TradingViewアラート履歴エクスポート形式を正規化する。
    列: Alert ID, Ticker, Name, Description, Time, Webhook status
    Description列のJSONを展開して標準列名に変換する。
    """
    rows = []
    for _, row in df.iterrows():
        try:
            desc_raw = str(row.get("Description", "{}"))
            # JSONのダブルクォートエスケープを処理
            desc_raw = desc_raw.replace('""', '"')
            if desc_raw.startswith('"') and desc_raw.endswith('"'):
                desc_raw = desc_raw[1:-1]
            desc = json.loads(desc_raw)
        except (json.JSONDecodeError, TypeError):
            desc = {}

        # Time列（アラート発火時刻）
        ts_raw = row.get("Time") or row.get("time") or desc.get("time")

        # Descriptionから各フィールドを取得
        price_raw = desc.get("price") or row.get("Price")
        source    = desc.get("source", "Lorentzian")
        direction = desc.get("side") or desc.get("action") or desc.get("direction", "buy")
        sig_type  = desc.get("signal_type", "entry_trigger")
        event     = desc.get("event", "prediction_signal")
        confirmed = desc.get("confirmed", "bar_close")
        tv_conf   = desc.get("confidence") or desc.get("tv_confidence")
        tv_wr     = desc.get("win_rate") or desc.get("tv_win_rate")
        strength  = desc.get("strength")

        rows.append({
            "timestamp":     ts_raw,
            "signal_type":   sig_type,
            "event":         event,
            "direction":     str(direction).lower(),
            "price":         price_raw,
            "source":        source,
            "confirmed":     confirmed,
            "tv_confidence": tv_conf,
            "tv_win_rate":   tv_wr,
            "strength":      strength,
        })

    return pd.DataFrame(rows)


def load_alerts(path: str) -> pd.DataFrame:
    """
    TradingViewアラート履歴CSVを読み込む。
    TradingViewエクスポート形式（Description列にJSON）と
    シンプルCSV形式（直接列）の両方を自動判別して処理する。
    """
    df = pd.read_csv(path)

    # フォーマット判別: TradingViewエクスポート形式かどうか
    tv_export_cols = {"Description", "Webhook status", "Alert ID"}
    if tv_export_cols.issubset(set(df.columns)):
        logger.info("TradingViewエクスポート形式を検出。Descriptionを展開します。")
        df = _parse_tv_export(df)
    else:
        # シンプルCSV形式: 列名の正規化
        col_map = {
            "time":      "timestamp",
            "Time":      "timestamp",
            "Timestamp": "timestamp",
            "Price":     "price",
            "Signal":    "signal_type",
            "Event":     "event",
            "Direction": "direction",
            "Source":    "source",
        }
        df = df.rename(columns=col_map)

    required = ["timestamp", "signal_type", "event", "direction", "price"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"アラートCSVに必要な列がありません: {missing}\n"
            f"現在の列: {list(df.columns)}\n"
            f"TradingViewアラート履歴をCSVでエクスポートしてください。"
        )

    # timestamp → datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])

    # オプション列のデフォルト
    for col, default in [
        ("source",        "Lorentzian"),
        ("confirmed",     "bar_close"),
        ("tv_confidence", None),
        ("tv_win_rate",   None),
        ("strength",      None),
    ]:
        if col not in df.columns:
            df[col] = default

    df = df.sort_values("timestamp").reset_index(drop=True)
    logger.info("アラート読み込み: %d件", len(df))
    return df


# ──────────────────────────────────────────────────────────
# OHLCVデータ読み込み
# ──────────────────────────────────────────────────────────

def load_ohlcv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    col_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=col_map)
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            raise ValueError(f"OHLCVに {col} 列がありません")

    # timestamp列の特定と変換
    for col in ["time", "timestamp", "datetime", "date"]:
        if col in df.columns:
            df["timestamp"] = pd.to_datetime(df[col], utc=True, errors="coerce")
            break
    if "timestamp" not in df.columns:
        raise ValueError("OHLCVにtimestamp列が見つかりません")

    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def load_ohlcv_mt5(symbol: str = "GOLD", timeframe: str = "M5",
                   bars: int = 5000) -> pd.DataFrame:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise RuntimeError("MetaTrader5 がインストールされていません。")

    tf_map = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None:
        raise RuntimeError(f"MT5データ取得失敗: {symbol} {timeframe}")
    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


# ──────────────────────────────────────────────────────────
# OHLCV + 指標計算
# ──────────────────────────────────────────────────────────

def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - 100 / (1 + rs)


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """簡易ADX（ATRのCV×100で近似）"""
    atr = _compute_atr(df, period)
    adx_approx = (
        atr.rolling(period).std()
        / (atr.rolling(period).mean() + 1e-10)
        * 100
    )
    return adx_approx


def build_ohlcv_indicators(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """OHLCVにATR/RSI/ADX/SMA20/50/200を追加して返す"""
    df = ohlcv.copy()
    df["atr14"]  = _compute_atr(df, 14)
    df["rsi14"]  = _compute_rsi(df["close"], 14)
    df["adx14"]  = _compute_adx(df, 14)
    df["sma20"]  = df["close"].rolling(20).mean()
    df["sma50"]  = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    return df


def _find_bar_at(ohlcv: pd.DataFrame, ts: pd.Timestamp) -> Optional[pd.Series]:
    """アラート発火時刻 ts 以前の最後のバーを返す（バー確定ベース）。"""
    idx = ohlcv[ohlcv["timestamp"] <= ts].index
    if len(idx) == 0:
        return None
    return ohlcv.loc[idx[-1]]


def _find_bars_after(ohlcv: pd.DataFrame, ts: pd.Timestamp,
                     n: int = 200) -> pd.DataFrame:
    """アラート時刻以降のバーをn本返す"""
    return ohlcv[ohlcv["timestamp"] > ts].head(n).reset_index(drop=True)


# ──────────────────────────────────────────────────────────
# ライブと同一の structured_data を構築
# ──────────────────────────────────────────────────────────

def _build_structured_data(
    alert_row:       pd.Series,
    bar:             pd.Series,
    ohlcv:           pd.DataFrame,
    structure_window: list,
    q_trend_latest:  Optional[dict],
    params:          dict,
) -> tuple:
    """
    アラート1件 + OHLCVバー から scoring_engine が期待する structured_data を構築。
    ライブの _fallback_structurize() と同一ロジック。

    Returns:
        (structured_data, q_trend_available)
    """
    direction = str(alert_row.get("direction", "buy")).lower()
    close     = float(bar["close"])
    atr14     = float(bar["atr14"]) if pd.notna(bar.get("atr14")) else None
    rsi14     = float(bar["rsi14"]) if pd.notna(bar.get("rsi14")) else None
    adx14     = float(bar["adx14"]) if pd.notna(bar.get("adx14")) else None
    sma20     = float(bar["sma20"]) if pd.notna(bar.get("sma20")) else None

    # レジーム分類（ライブの _fallback_structurize と同一ルール）
    bar_idx = bar.name if hasattr(bar, "name") else None

    adx_rising = None
    if adx14 is not None and bar_idx is not None and bar_idx >= 3:
        prev_adx = ohlcv.loc[bar_idx - 3, "adx14"] if bar_idx >= 3 else None
        if prev_adx is not None and pd.notna(prev_adx):
            adx_rising = adx14 > prev_adx

    atr_expanding = False
    if bar_idx is not None and bar_idx >= 3 and "atr14" in ohlcv.columns and atr14 is not None:
        prev_atr = ohlcv.loc[bar_idx - 3, "atr14"]
        if prev_atr is not None and pd.notna(prev_atr):
            atr_expanding = atr14 > prev_atr

    if adx14 is not None and adx14 > 25 and adx_rising and atr_expanding:
        regime_class = "breakout"
    elif adx14 is not None and adx14 > 20:
        regime_class = "trend"
    else:
        regime_class = "range"

    # 価格構造
    above_sma20 = (close > sma20) if sma20 else None
    sma20_dist  = ((close - sma20) / sma20 * 100) if sma20 else None

    sma50  = float(bar["sma50"]) if "sma50" in bar.index and pd.notna(bar.get("sma50")) else None
    sma200 = float(bar["sma200"]) if "sma200" in bar.index and pd.notna(bar.get("sma200")) else None
    perfect_order = None
    if sma20 and sma50 and sma200:
        perfect_order = (sma20 > sma50 > sma200) if direction == "buy" else (sma20 < sma50 < sma200)

    # ゾーン・構造インタラクション（タイムウィンドウ内のシグナルから）
    zone_touch      = False
    zone_direction  = None
    fvg_touch       = False
    fvg_direction   = None
    liq_sweep       = False
    sweep_direction = None

    for s in structure_window:
        ev = s.get("event", "")
        sd = s.get("direction", "")
        if ev == "zone_retrace_touch":
            zone_touch     = True
            zone_direction = sd
        elif ev == "fvg_touch":
            fvg_touch     = True
            fvg_direction = sd
        elif ev == "liquidity_sweep":
            liq_sweep       = True
            sweep_direction = sd

    # モメンタム
    rsi_zone = "neutral"
    if rsi14 is not None:
        if rsi14 < 30:
            rsi_zone = "oversold"
        elif rsi14 > 70:
            rsi_zone = "overbought"

    # Q-trendとの一致
    q_trend_available = q_trend_latest is not None
    q_trend_dir  = q_trend_latest.get("direction") if q_trend_latest else None
    trend_aligned = (q_trend_dir == direction) if q_trend_dir else False

    # シグナル品質
    confirmed          = str(alert_row.get("confirmed", "bar_close"))
    bar_close_confirmed = confirmed == "bar_close"
    tv_confidence      = alert_row.get("tv_confidence")
    tv_win_rate        = alert_row.get("tv_win_rate")

    # セッション（タイムスタンプから判定 UTC）
    ts       = alert_row["timestamp"]
    hour_utc = ts.hour
    if 0 <= hour_utc < 7:
        session = "Tokyo"
    elif 7 <= hour_utc < 12:
        session = "London"
    elif 12 <= hour_utc < 15:
        session = "London_NY"
    elif 15 <= hour_utc < 22:
        session = "NY"
    else:
        session = "off_hours"

    fields_missing = []
    if rsi14 is None: fields_missing.append("rsi_value")
    if adx14 is None: fields_missing.append("adx_value")
    if atr14 is None: fields_missing.append("atr_expanding")

    structured = {
        "regime": {
            "classification":   regime_class,
            "adx_value":        adx14,
            "adx_rising":       adx_rising,
            "atr_expanding":    atr_expanding,
            "squeeze_detected": False,
        },
        "price_structure": {
            "above_sma20":        above_sma20,
            "sma20_distance_pct": round(sma20_dist, 3) if sma20_dist else None,
            "perfect_order":      perfect_order,
            "higher_highs":       None,
            "lower_lows":         None,
        },
        "zone_interaction": {
            "zone_touch":      zone_touch,
            "zone_direction":  zone_direction,
            "fvg_touch":       fvg_touch,
            "fvg_direction":   fvg_direction,
            "liquidity_sweep": liq_sweep,
            "sweep_direction": sweep_direction,
        },
        "momentum": {
            "rsi_value":    rsi14,
            "rsi_zone":     rsi_zone,
            "trend_aligned": trend_aligned,
        },
        "signal_quality": {
            "source":              str(alert_row.get("source", "Lorentzian")),
            "bar_close_confirmed": bar_close_confirmed,
            "session":             session,
            "tv_confidence": (
                float(tv_confidence)
                if tv_confidence is not None and str(tv_confidence) not in ("nan", "None", "")
                else None
            ),
            "tv_win_rate": (
                float(tv_win_rate)
                if tv_win_rate is not None and str(tv_win_rate) not in ("nan", "None", "")
                else None
            ),
        },
        "data_completeness": {
            "mt5_connected":  True,  # バックテストではTrue固定
            "fields_missing": fields_missing,
        },
    }
    return structured, q_trend_available


# ──────────────────────────────────────────────────────────
# ポジション管理シミュレーション（position_manager.py と同一ロジック）
# ──────────────────────────────────────────────────────────

def _simulate_trade(
    trade:       LiveBacktestTrade,
    future_bars: pd.DataFrame,
    params:      dict,
) -> LiveBacktestTrade:
    """
    エントリー後のバーをシミュレートし、BE/部分TP/トレーリング/SL/TPを処理。
    ライブの position_manager.py と同一ステップ（ATR乗数も同じ config 値を使用）。
    """
    atr          = trade.atr
    direction    = trade.direction
    sl           = trade.sl_price
    tp           = trade.tp_price
    entry        = trade.entry_price
    lot          = trade.lot_size
    partial_pnl  = 0.0
    remaining    = lot

    be_trigger_mult = params.get("be_trigger_atr_mult", 1.0)
    partial_tp_mult = params.get("partial_tp_atr_mult", 2.0)
    trailing_mult   = params.get("trailing_step_atr_mult", 1.5)
    partial_ratio   = params.get("partial_close_ratio", 0.5)

    be_applied      = False
    partial_closed  = False
    trailing_active = False
    max_price       = entry   # buy: 最高値追跡 / sell: 最安値追跡

    for i, bar in future_bars.iterrows():
        high = float(bar["high"])
        low  = float(bar["low"])

        # 最高値/最安値追跡
        if direction == "buy":
            max_price = max(max_price, high)
        else:
            max_price = min(max_price, low)

        # STEP1: ブレークイーブン（含み益 ATR×be_trigger_mult 到達時）
        if not be_applied:
            if direction == "buy" and high >= entry + atr * be_trigger_mult:
                sl = entry + params.get("be_buffer_pips", 2.0)
                be_applied = True
            elif direction == "sell" and low <= entry - atr * be_trigger_mult:
                sl = entry - params.get("be_buffer_pips", 2.0)
                be_applied = True

        # STEP2: 部分決済（含み益 ATR×partial_tp_mult 到達時、partial_ratio%確定）
        if not partial_closed:
            partial_hit = (
                (direction == "buy"  and high >= entry + atr * partial_tp_mult) or
                (direction == "sell" and low  <= entry - atr * partial_tp_mult)
            )
            if partial_hit:
                partial_units = lot * partial_ratio
                remaining     = lot * (1 - partial_ratio)
                partial_exit  = entry + atr * partial_tp_mult if direction == "buy" \
                                else entry - atr * partial_tp_mult
                partial_pnl   = (partial_exit - entry) * partial_units if direction == "buy" \
                                else (entry - partial_exit) * partial_units
                partial_closed  = True
                trailing_active = True

        # STEP3: トレーリングストップ更新
        if trailing_active:
            if direction == "buy":
                trail_sl = max_price - atr * trailing_mult
                sl = max(sl, trail_sl)
            else:
                trail_sl = max_price + atr * trailing_mult
                sl = min(sl, trail_sl)

        # SL判定 (buy)
        if direction == "buy" and low <= sl:
            exit_price = sl
            pnl = (exit_price - entry) * remaining
            trade.exit_price    = exit_price
            trade.outcome       = "trailing_sl" if trailing_active else "sl_hit"
            trade.pnl           = pnl
            trade.pnl_pips      = exit_price - entry
            trade.duration_bars = i + 1
            trade.be_applied    = be_applied
            trade.partial_closed = partial_closed
            trade.partial_pnl   = partial_pnl
            return trade

        # SL判定 (sell)
        if direction == "sell" and high >= sl:
            exit_price = sl
            pnl = (entry - exit_price) * remaining
            trade.exit_price    = exit_price
            trade.outcome       = "trailing_sl" if trailing_active else "sl_hit"
            trade.pnl           = pnl
            trade.pnl_pips      = entry - exit_price
            trade.duration_bars = i + 1
            trade.be_applied    = be_applied
            trade.partial_closed = partial_closed
            trade.partial_pnl   = partial_pnl
            return trade

        # TP判定 (buy)
        if direction == "buy" and high >= tp:
            exit_price = tp
            pnl = (exit_price - entry) * remaining
            trade.exit_price    = exit_price
            trade.outcome       = "tp_hit"
            trade.pnl           = pnl
            trade.pnl_pips      = exit_price - entry
            trade.duration_bars = i + 1
            trade.be_applied    = be_applied
            trade.partial_closed = partial_closed
            trade.partial_pnl   = partial_pnl
            return trade

        # TP判定 (sell)
        if direction == "sell" and low <= tp:
            exit_price = tp
            pnl = (entry - exit_price) * remaining
            trade.exit_price    = exit_price
            trade.outcome       = "tp_hit"
            trade.pnl           = pnl
            trade.pnl_pips      = entry - exit_price
            trade.duration_bars = i + 1
            trade.be_applied    = be_applied
            trade.partial_closed = partial_closed
            trade.partial_pnl   = partial_pnl
            return trade

    # 未決済（データ終端）
    trade.outcome        = "open"
    trade.pnl            = 0.0
    trade.partial_pnl    = partial_pnl
    trade.be_applied     = be_applied
    trade.partial_closed = partial_closed
    return trade


# ──────────────────────────────────────────────────────────
# メインエンジン
# ──────────────────────────────────────────────────────────

class LiveBacktestEngine:
    """
    TradingViewアラート履歴 + OHLCVデータ を入力とし、
    ライブと同一の scoring_engine パイプラインでシミュレートするエンジン。
    """

    def __init__(self, alerts: pd.DataFrame, ohlcv: pd.DataFrame,
                 params: dict = None):
        self.alerts = alerts.copy()
        self.ohlcv  = build_ohlcv_indicators(ohlcv)
        self.params = self._build_params(params or {})

    def _build_params(self, overrides: dict) -> dict:
        from config import SYSTEM_CONFIG, SCORING_CONFIG
        p = {
            # SL/TP 乗数
            "atr_sl_multiplier":     SYSTEM_CONFIG["atr_sl_multiplier"],
            "atr_tp_multiplier":     SYSTEM_CONFIG["atr_tp_multiplier"],
            "max_sl_pips":           SYSTEM_CONFIG["max_sl_pips"],
            "min_sl_pips":           SYSTEM_CONFIG["min_sl_pips"],
            # リスク管理
            "risk_percent":          SYSTEM_CONFIG["risk_percent"],
            "initial_balance":       10_000.0,
            # 取引コスト
            "spread_dollar":         0.50,
            "slippage_dollar":       0.10,
            # ポジション管理
            "be_trigger_atr_mult":   SYSTEM_CONFIG["be_trigger_atr_mult"],
            "be_buffer_pips":        SYSTEM_CONFIG["be_buffer_pips"],
            "partial_tp_atr_mult":   SYSTEM_CONFIG["partial_tp_atr_mult"],
            "partial_close_ratio":   SYSTEM_CONFIG["partial_close_ratio"],
            "trailing_step_atr_mult": SYSTEM_CONFIG["trailing_step_atr_mult"],
            # スコアリング閾値
            "approve_threshold":     SCORING_CONFIG["approve_threshold"],
            "wait_threshold":        SCORING_CONFIG["wait_threshold"],
            # Gate2
            "gate2_enabled":         True,
            # 構造ウィンドウ（秒）
            "structure_window_sec":  SYSTEM_CONFIG["time_windows"].get("zone_retrace_touch", 900),
            "q_trend_window_sec":    SYSTEM_CONFIG["time_windows"].get("prediction_signal", 14400),
        }
        p.update(overrides)
        return p

    def run(self) -> LiveBacktestResult:
        """バックテストを実行してLiveBacktestResultを返す。"""
        from scoring_engine import calculate_score

        params         = self.params
        ohlcv          = self.ohlcv
        alerts         = self.alerts
        balance        = params["initial_balance"]
        balance_curve  = [balance]
        trades         = []

        alert_count    = 0
        approved_count = 0
        rejected_count = 0
        waited_count   = 0

        sl_mult = params["atr_sl_multiplier"]
        tp_mult = params["atr_tp_multiplier"]
        spread  = params["spread_dollar"]
        slip    = params["slippage_dollar"]
        cost    = spread + slip

        # entry_trigger のみをトレード対象にする
        entry_alerts = alerts[alerts["signal_type"] == "entry_trigger"].copy()
        alert_count  = len(entry_alerts)

        for _, alert_row in entry_alerts.iterrows():
            ts    = alert_row["timestamp"]
            price = float(alert_row["price"])
            direction = str(alert_row.get("direction", "buy")).lower()

            # アラート時刻に対応するOHLCVバーを取得
            bar = _find_bar_at(ohlcv, ts)
            if bar is None:
                logger.debug("バー未発見: %s", ts)
                waited_count += 1
                continue

            # 構造コンテキスト収集
            structure_window = self._get_structure_window(ts)
            q_trend_latest   = self._get_q_trend(ts)

            # Gate2無効化
            if not params.get("gate2_enabled", True):
                q_trend_latest = None

            # structured_data 構築
            structured, q_trend_avail = _build_structured_data(
                alert_row, bar, ohlcv, structure_window, q_trend_latest, params
            )

            # スコアリング（ライブと同一コード）
            result = calculate_score(structured, direction, q_trend_available=q_trend_avail)
            decision = result["decision"]
            score    = result["score"]

            # 閾値上書き
            approve_thr = params.get("approve_threshold")
            wait_thr    = params.get("wait_threshold")
            if decision != "reject" and approve_thr is not None:
                if score >= approve_thr:
                    decision = "approve"
                elif score >= wait_thr:
                    decision = "wait"
                else:
                    decision = "reject"

            if decision == "reject":
                rejected_count += 1
                continue
            if decision == "wait":
                waited_count += 1
                continue

            approved_count += 1

            # ATR取得
            atr14 = float(bar["atr14"]) if pd.notna(bar.get("atr14")) else None
            if atr14 is None or atr14 <= 0:
                logger.debug("ATR未計算: %s", ts)
                waited_count += 1
                approved_count -= 1
                continue

            # SL/TP 計算
            sl_dist = atr14 * sl_mult
            tp_dist = atr14 * tp_mult
            sl_dist = max(params["min_sl_pips"], min(params["max_sl_pips"], sl_dist))

            if direction == "buy":
                entry_price = price + cost
                sl_price    = entry_price - sl_dist
                tp_price    = entry_price + tp_dist
            else:
                entry_price = price - cost
                sl_price    = entry_price + sl_dist
                tp_price    = entry_price - tp_dist

            # ロットサイズ（リスク%ベース）
            risk_dollar = balance * (params["risk_percent"] / 100)
            lot_size    = risk_dollar / (sl_dist + 1e-10)

            trade = LiveBacktestTrade(
                alert_time      = str(ts),
                direction       = direction,
                entry_price     = entry_price,
                sl_price        = sl_price,
                tp_price        = tp_price,
                lot_size        = lot_size,
                atr             = atr14,
                sl_dollar       = sl_dist,
                score           = score,
                score_breakdown = result.get("score_breakdown", {}),
                decision        = decision,
            )

            # トレードシミュレーション
            future_bars = _find_bars_after(ohlcv, ts, n=200)
            trade = _simulate_trade(trade, future_bars, params)

            net_pnl = trade.pnl + trade.partial_pnl
            balance += net_pnl
            balance_curve.append(balance)
            trades.append(trade)

        return LiveBacktestResult(
            trades         = trades,
            balance_curve  = balance_curve,
            params         = params,
            alert_count    = alert_count,
            approved_count = approved_count,
            rejected_count = rejected_count,
            waited_count   = waited_count,
        )

    def _get_structure_window(self, ts: pd.Timestamp) -> list:
        """
        ts より前の structure シグナルを window 内で収集する。
        zone_retrace_touch / fvg_touch / liquidity_sweep を対象とする。
        """
        window_sec = self.params.get("structure_window_sec", 900)
        window_start = ts - pd.Timedelta(seconds=window_sec)
        structure_events = {"zone_retrace_touch", "fvg_touch", "liquidity_sweep"}
        mask = (
            (self.alerts["timestamp"] >= window_start) &
            (self.alerts["timestamp"] < ts) &
            (self.alerts["event"].isin(structure_events))
        )
        subset = self.alerts[mask]
        result = []
        for _, row in subset.iterrows():
            result.append({
                "event":     row.get("event"),
                "direction": row.get("direction"),
                "timestamp": row.get("timestamp"),
            })
        return result

    def _get_q_trend(self, ts: pd.Timestamp) -> Optional[dict]:
        """
        ts 直前の Q-trend シグナルを取得する。
        q_trend_window_sec（デフォルト4時間）以内の最新をQ-trendとして返す。
        """
        window_sec   = self.params.get("q_trend_window_sec", 14400)
        window_start = ts - pd.Timedelta(seconds=window_sec)
        mask = (
            (self.alerts["source"] == "Q-trend") &
            (self.alerts["timestamp"] >= window_start) &
            (self.alerts["timestamp"] < ts)
        )
        subset = self.alerts[mask].sort_values("timestamp")
        if subset.empty:
            return None
        latest = subset.iloc[-1]
        return {
            "direction": str(latest.get("direction", "")).lower(),
            "strength":  latest.get("strength"),
            "timestamp": latest["timestamp"],
        }


# ──────────────────────────────────────────────────────────
# 閾値感度分析
# ──────────────────────────────────────────────────────────

def threshold_sensitivity(alerts: pd.DataFrame, ohlcv: pd.DataFrame,
                           thresholds: list = None) -> list:
    """
    approve_threshold を変えながら複数回バックテストし、比較表を返す。
    デモ開始後にスコア分布が蓄積されたら、最適な閾値の目安を得るために使う。
    """
    if thresholds is None:
        thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

    results = []
    for th in thresholds:
        engine = LiveBacktestEngine(alerts, ohlcv, {"approve_threshold": th})
        r      = engine.run()
        done   = r.completed_trades
        results.append({
            "approve_threshold": th,
            "approved":          r.approved_count,
            "rejected":          r.rejected_count,
            "filter_rate":       f"{r.filter_rate:.1%}",
            "trades":            len(done),
            "win_rate":          f"{r.win_rate:.1%}",
            "total_pnl":         f"${r.total_pnl:+.2f}",
            "profit_factor":     f"{r.profit_factor:.2f}",
            "max_drawdown":      f"{r.max_drawdown:.1f}%",
        })
        print(f"  threshold={th:.2f}  approved={r.approved_count:3d}  "
              f"filter={r.filter_rate:.0%}  trades={len(done):3d}  "
              f"wr={r.win_rate:.0%}  pf={r.profit_factor:.2f}  "
              f"pnl=${r.total_pnl:+.2f}")
    return results


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ライブパイプラインと同一ロジックによるバックテスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--alerts", required=True,
                   help="TradingViewアラート履歴CSV（必須）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ohlcv",  help="OHLCVデータCSV")
    g.add_argument("--mt5",    action="store_true", help="MT5から直接OHLCVを取得")
    p.add_argument("--symbol", default="GOLD")
    p.add_argument("--tf",     default="M5")
    p.add_argument("--bars",   type=int, default=5000)
    p.add_argument("--approve-threshold", type=float, default=None)
    p.add_argument("--wait-threshold",    type=float, default=None)
    p.add_argument("--no-gate2",          action="store_true",
                   help="Gate2（Q-trend不一致+bar_close未確認）を無効化")
    p.add_argument("--sl-mult",  type=float, default=None)
    p.add_argument("--tp-mult",  type=float, default=None)
    p.add_argument("--spread",   type=float, default=0.50)
    p.add_argument("--slippage", type=float, default=0.10)
    p.add_argument("--initial-balance", type=float, default=10000.0)
    p.add_argument("--risk-pct", type=float, default=None)
    p.add_argument("--sensitivity", action="store_true",
                   help="approve_threshold感度分析を実行")
    p.add_argument("--output", default=None, help="結果CSVの出力先")
    return p


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = _build_parser().parse_args()

    # アラート読み込み
    print(f"📋 アラート読み込み: {args.alerts}")
    alerts = load_alerts(args.alerts)
    print(f"  → {len(alerts)}件のアラート")

    # OHLCV読み込み
    if args.mt5:
        print(f"📡 MT5からOHLCV取得: {args.symbol} {args.tf} {args.bars}本")
        ohlcv = load_ohlcv_mt5(args.symbol, args.tf, args.bars)
    else:
        print(f"📂 OHLCV読み込み: {args.ohlcv}")
        ohlcv = load_ohlcv_csv(args.ohlcv)
    print(f"  → {len(ohlcv)}本のOHLCV")

    # パラメータ構築
    params = {
        "spread_dollar":   args.spread,
        "slippage_dollar": args.slippage,
        "initial_balance": args.initial_balance,
        "gate2_enabled":   not args.no_gate2,
    }
    if args.approve_threshold is not None:
        params["approve_threshold"] = args.approve_threshold
    if args.wait_threshold is not None:
        params["wait_threshold"] = args.wait_threshold
    if args.sl_mult is not None:
        params["atr_sl_multiplier"] = args.sl_mult
    if args.tp_mult is not None:
        params["atr_tp_multiplier"] = args.tp_mult
    if args.risk_pct is not None:
        params["risk_percent"] = args.risk_pct

    # 感度分析
    if args.sensitivity:
        print("\n🔍 approve_threshold 感度分析を実行中...")
        rows = threshold_sensitivity(alerts, ohlcv)
        if args.output:
            pd.DataFrame(rows).to_csv(args.output, index=False)
            print(f"\n💾 結果を {args.output} に保存しました。")
        return

    # メインバックテスト
    print("\n⚙️  バックテスト実行中...")
    engine = LiveBacktestEngine(alerts, ohlcv, params)
    result = engine.run()
    print(result.summary())

    # CSVエクスポート
    if args.output:
        rows = []
        for t in result.trades:
            rows.append({
                "alert_time":    t.alert_time,
                "direction":     t.direction,
                "entry_price":   t.entry_price,
                "sl_price":      t.sl_price,
                "tp_price":      t.tp_price,
                "lot_size":      round(t.lot_size, 4),
                "atr":           round(t.atr, 4),
                "score":         round(t.score, 4),
                "decision":      t.decision,
                "outcome":       t.outcome,
                "exit_price":    t.exit_price,
                "pnl":           round(t.pnl, 2),
                "partial_pnl":   round(t.partial_pnl, 2),
                "net_pnl":       round(t.pnl + t.partial_pnl, 2),
                "pnl_pips":      round(t.pnl_pips, 2),
                "duration_bars": t.duration_bars,
                "be_applied":    t.be_applied,
                "partial_closed": t.partial_closed,
            })
        pd.DataFrame(rows).to_csv(args.output, index=False)
        print(f"\n💾 結果を {args.output} に保存しました。")


if __name__ == "__main__":
    main()
