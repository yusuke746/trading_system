"""
backtester.py - 過去データでの戦略バックテスト
AI Trading System v3.0

OHLCV データ（CSV または MT5 から取得）に対してATRベースの戦略をシミュレートし、
パラメータセットの損益・勝率・最大ドローダウンなどを計算する。

v3.0 追加:
  - スプレッド・手数料・スリッページの考慮
  - scoring_engine との統合 (--scoring-filter)
  - TradingViewアラート履歴インポート (tv_alert_signal)
  - ウォークフォワード分析

CLIからの使用方法:
  python backtester.py --csv data.csv
  python backtester.py --csv data.csv --sl-mult 2.0 --tp-mult 3.0
  python backtester.py --csv data.csv --spread 0.50 --slippage 0.10
  python backtester.py --csv data.csv --scoring-filter
  python backtester.py --mt5 --bars 2000

ライブラリとしての使用方法:
  from backtester import BacktestEngine, load_csv, load_mt5_data
  df = load_csv("data.csv")
  engine = BacktestEngine(df, {"atr_sl_multiplier": 2.0, "atr_tp_multiplier": 3.0})
  result = engine.run()
  print(result.summary())
"""

import argparse
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from config import SYSTEM_CONFIG

logger = logging.getLogger(__name__)

# ── デフォルト設定（config.py を参照）──────────────────────
_DEFAULT_PARAMS = {
    "atr_sl_multiplier":  SYSTEM_CONFIG["atr_sl_multiplier"],
    "atr_tp_multiplier":  SYSTEM_CONFIG["atr_tp_multiplier"],
    "atr_volatility_min": SYSTEM_CONFIG["atr_volatility_min"],
    "atr_volatility_max": SYSTEM_CONFIG["atr_volatility_max"],
    "max_sl_pips":        SYSTEM_CONFIG["max_sl_pips"],
    "min_sl_pips":        SYSTEM_CONFIG["min_sl_pips"],
    "risk_percent":       SYSTEM_CONFIG["risk_percent"],
    "be_trigger_atr_mult":    SYSTEM_CONFIG["be_trigger_atr_mult"],
    "partial_tp_atr_mult":    SYSTEM_CONFIG["partial_tp_atr_mult"],
    "trailing_step_atr_mult": SYSTEM_CONFIG["trailing_step_atr_mult"],
    "partial_close_ratio":    SYSTEM_CONFIG["partial_close_ratio"],
    "initial_balance":    10_000.0,
    "atr_period":         14,
    "signal_lookback":    20,   # シグナル判定に使う直近バー数
    # v3.0 追加: スプレッド・手数料・スリッページ
    "spread_dollar":      0.50,   # GOLD スプレッド ($0.50 ≒ 5pips)
    "slippage_dollar":    0.10,   # 約定ずれ ($0.10)
    "commission_per_lot": 0.0,    # 手数料 (ロット当たり)
}


# ──────────────────────────────────────────────────────────
# AIモッククラス
# ──────────────────────────────────────────────────────────

class AiJudgeMock:
    """
    バックテスト用AIモック。
    ai_judge.py の ask_ai() と同じ dict 構造を返し、
    approve_rate の確率で "approve" を返す。
    """

    def __init__(self, approve_rate: float = 0.6, rng: random.Random | None = None):
        self.approve_rate = approve_rate
        self._rng = rng or random.Random(42)

    def judge(self, bar_index: int, direction: str,
              atr: float, context: dict) -> dict:
        """
        Returns:
            {
                "decision":   "approve" | "reject",
                "confidence": float,  # 0.6〜0.9
                "ev_score":   float,  # 0.2〜0.5
                "reason":     str,
            }
        """
        decision   = "approve" if self._rng.random() < self.approve_rate else "reject"
        confidence = round(self._rng.uniform(0.6, 0.9), 3)
        ev_score   = round(self._rng.uniform(0.2, 0.5), 3)
        reason     = (
            f"mock: approve_rate={self.approve_rate:.0%}, "
            f"bar={bar_index}, dir={direction}"
        )
        return {
            "decision":   decision,
            "confidence": confidence,
            "ev_score":   ev_score,
            "reason":     reason,
        }


# ──────────────────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────────────────

@dataclass
class Trade:
    """バックテスト中の1トレードを表す"""
    direction:     str     # "buy" / "sell"
    entry_bar:     int
    entry_price:   float
    sl_price:      float
    tp_price:      float
    lot_size:      float
    atr:           float

    # ポジション管理状態
    be_applied:       bool  = False
    partial_closed:   bool  = False
    trailing_active:  bool  = False
    max_price:        float = field(init=False)
    remaining_lots:   float = field(init=False)

    # 決済情報
    exit_bar:    int   = -1
    exit_price:  float = 0.0
    outcome:     str   = ""   # tp_hit / sl_hit / trailing_sl / partial_tp / open
    pnl:         float = 0.0
    partial_pnl: float = 0.0

    def __post_init__(self):
        self.max_price      = self.entry_price
        self.remaining_lots = self.lot_size


@dataclass
class BacktestResult:
    """バックテスト全体の結果"""
    trades:      list[Trade]
    balance_curve: list[float]
    params:      dict

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.outcome != "open"]

    @property
    def n_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.pnl > 0)
        return wins / len(closed)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl + t.partial_pnl for t in self.closed_trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(
            (t.pnl + t.partial_pnl) for t in self.closed_trades
            if (t.pnl + t.partial_pnl) > 0
        )
        gross_loss = abs(sum(
            (t.pnl + t.partial_pnl) for t in self.closed_trades
            if (t.pnl + t.partial_pnl) < 0
        ))
        return round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        """最大ドローダウン（USD）"""
        if not self.balance_curve:
            return 0.0
        peak = self.balance_curve[0]
        max_dd = 0.0
        for b in self.balance_curve:
            if b > peak:
                peak = b
            dd = peak - b
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    @property
    def max_drawdown_pct(self) -> float:
        """最大ドローダウン（%）"""
        if not self.balance_curve or self.balance_curve[0] == 0:
            return 0.0
        return round(self.max_drawdown / self.balance_curve[0] * 100, 2)

    @property
    def sharpe_ratio(self) -> float:
        """
        トレード損益の簡易シャープレシオ（平均/標準偏差）。
        連続した月次リターンではなくトレード単位で近似する。
        """
        pnls = [t.pnl + t.partial_pnl for t in self.closed_trades]
        if len(pnls) < 2:
            return 0.0
        avg = sum(pnls) / len(pnls)
        variance = sum((p - avg) ** 2 for p in pnls) / len(pnls)
        std = variance ** 0.5
        return round(avg / std, 3) if std > 0 else 0.0

    def summary(self, use_ai_mock: bool = False,
                ai_approve_rate: float = 0.6,
                ai_filter_effect: float | None = None) -> str:
        lines = [
            "=" * 50,
            "  バックテスト結果",
            "=" * 50,
            f"  取引数         : {self.n_trades}",
            f"  勝率           : {self.win_rate:.1%}",
            f"  総損益         : ${self.total_pnl:+.2f}",
            f"  プロフィットF  : {self.profit_factor:.3f}",
            f"  最大ドローダウン: ${self.max_drawdown:.2f} ({self.max_drawdown_pct:.1f}%)",
            f"  シャープレシオ : {self.sharpe_ratio:.3f}",
            "-" * 50,
            f"  SL乗数         : {self.params.get('atr_sl_multiplier')}",
            f"  TP乗数         : {self.params.get('atr_tp_multiplier')}",
        ]
        if use_ai_mock:
            lines.append(
                f"  AIモック       : 有効 (承認率 {ai_approve_rate * 100:.1f}%)"
            )
            if ai_filter_effect is not None:
                sign = "+" if ai_filter_effect >= 0 else ""
                lines.append(
                    f"  AIフィルター効果: {sign}{ai_filter_effect:.2f}% vs AIなし"
                )
        lines.append("=" * 50)
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# データ読み込み
# ──────────────────────────────────────────────────────────

def load_csv(path: str) -> pd.DataFrame:
    """
    CSV から OHLCV データを読み込む。
    列名: time, open, high, low, close, volume（大文字でも可）
    """
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV に必要な列がありません: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 1
    return df.reset_index(drop=True)


def load_mt5_data(symbol: str = "GOLD",
                  timeframe: str = "M15",
                  bars: int = 2000) -> pd.DataFrame:
    """
    MT5 から OHLCV データを取得して DataFrame で返す。
    """
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
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M15)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None:
        raise RuntimeError(f"MT5 データ取得失敗: {symbol} {timeframe}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────────────────
# ATR 計算
# ──────────────────────────────────────────────────────────

def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """DataFrame に ATR 列を計算して Series で返す"""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"]  - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


# ──────────────────────────────────────────────────────────
# デフォルトシグナル関数
# ──────────────────────────────────────────────────────────

def atr_breakout_signal(df: pd.DataFrame, i: int, params: dict) -> str | None:
    """
    ATR ブレイクアウトシグナル（デフォルト戦略）。
    lookback 本の高値/安値を直近 ATR×1.0 超えたらエントリー。

    Returns:
        "buy" / "sell" / None
    """
    lookback = params.get("signal_lookback", 20)
    if i < lookback + 1:
        return None

    atr = df["atr"].iloc[i]
    if pd.isna(atr) or atr <= 0:
        return None

    recent_high = df["high"].iloc[i - lookback: i].max()
    recent_low  = df["low"].iloc[i - lookback: i].min()
    close       = df["close"].iloc[i]

    if close > recent_high + atr * 0.3:
        return "buy"
    if close < recent_low - atr * 0.3:
        return "sell"
    return None


def rsi_reversal_signal(df: pd.DataFrame, i: int, params: dict) -> str | None:
    """
    RSI 逆張りシグナル。RSI < 30 で買い、RSI > 70 で売り。
    """
    if i < 20:
        return None
    delta     = df["close"].diff()
    gain      = delta.clip(lower=0)
    loss      = -delta.clip(upper=0)
    avg_gain  = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss  = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs        = avg_gain.iloc[i] / (avg_loss.iloc[i] + 1e-10)
    rsi       = 100 - 100 / (1 + rs)
    if rsi < 30:
        return "buy"
    if rsi > 70:
        return "sell"
    return None


# ──────────────────────────────────────────────────────────
# バックテストエンジン
# ──────────────────────────────────────────────────────────

class BacktestEngine:
    """
    OHLCV データに対してATRベースの戦略をシミュレートするエンジン。

    Args:
        df:          OHLCV DataFrame（列: open, high, low, close, volume）
        params:      パラメータ dict（省略時はデフォルト値を使用）
        random_seed: AiJudgeMock の乱数シード（再現性確保用）
    """

    def __init__(self, df: pd.DataFrame, params: dict | None = None,
                 random_seed: int = 42):
        self.df          = df.copy().reset_index(drop=True)
        self.params      = {**_DEFAULT_PARAMS, **(params or {})}
        self.random_seed = random_seed
        self._prepare()

    def _prepare(self):
        """ATR 計算と前処理"""
        period = self.params["atr_period"]
        self.df["atr"] = _compute_atr(self.df, period)

    def run(self,
            signal_func: Callable | None = None,
            use_ai_mock: bool = False,
            ai_approve_rate: float = 0.6,
            use_scoring_filter: bool = False,
            scoring_config_overrides: dict | None = None) -> BacktestResult:
        """
        バックテストを実行する。

        Args:
            signal_func: シグナル関数 (df, i, params) -> "buy"|"sell"|None
                         省略時は atr_breakout_signal を使用
            use_scoring_filter: True の場合 scoring_engine をフィルターとして使用（v3.0）
            scoring_config_overrides: SCORING_CONFIG の上書き設定（v3.0）

        Returns:
            BacktestResult
        """
        if signal_func is None:
            signal_func = atr_breakout_signal

        # AIモックの初期化
        ai_mock: AiJudgeMock | None = None
        if use_ai_mock:
            rng     = random.Random(self.random_seed)
            ai_mock = AiJudgeMock(approve_rate=ai_approve_rate, rng=rng)

        # v3.0: スコアリングフィルター初期化
        scoring_filter: ScoringFilterMock | None = None
        if use_scoring_filter:
            scoring_filter = ScoringFilterMock(scoring_config_overrides)

        p         = self.params
        balance   = p["initial_balance"]
        sl_mult   = p["atr_sl_multiplier"]
        tp_mult   = p["atr_tp_multiplier"]
        min_sl    = p["min_sl_pips"]
        max_sl    = p["max_sl_pips"]
        risk_pct  = p["risk_percent"] / 100.0
        atr_vmax  = p["atr_volatility_max"]
        atr_vmin  = p["atr_volatility_min"]

        be_mult       = p["be_trigger_atr_mult"]
        partial_mult  = p["partial_tp_atr_mult"]
        trail_mult    = p["trailing_step_atr_mult"]
        partial_ratio = p["partial_close_ratio"]

        # v3.0: スプレッド・スリッページ
        spread    = p.get("spread_dollar", 0.0)
        slippage  = p.get("slippage_dollar", 0.0)
        cost_per_trade = spread + slippage  # 片道コスト

        trades:       list[Trade] = []
        balance_curve: list[float] = [balance]
        open_trade:   Trade | None = None

        for i in range(len(self.df)):
            row = self.df.iloc[i]
            high  = float(row["high"])
            low   = float(row["low"])
            close = float(row["close"])
            atr   = float(row["atr"]) if not pd.isna(row["atr"]) else None

            # ── オープンポジションの管理 ─────────────────
            if open_trade is not None:
                t = open_trade
                if t.direction == "buy":
                    t.max_price = max(t.max_price, high)
                    unrealized  = close - t.entry_price
                else:
                    t.max_price = min(t.max_price, low)
                    unrealized  = t.entry_price - close

                atr_val = t.atr  # エントリー時のATRで管理

                # STEP1: ブレークイーブン
                if not t.be_applied and unrealized >= atr_val * be_mult:
                    buf = 0.2   # $0.20 バッファ（エントリー価格から少しずらす）
                    t.sl_price = (t.entry_price + buf
                                  if t.direction == "buy"
                                  else t.entry_price - buf)
                    t.be_applied = True

                # STEP2: 部分決済
                if not t.partial_closed and unrealized >= atr_val * partial_mult:
                    partial_vol  = round(t.lot_size * partial_ratio, 2)
                    if t.direction == "buy":
                        ppnl = (close - t.entry_price) * partial_vol * 100
                    else:
                        ppnl = (t.entry_price - close) * partial_vol * 100
                    t.partial_pnl     = ppnl
                    t.partial_closed  = True
                    t.trailing_active = True
                    t.remaining_lots  = round(t.lot_size - partial_vol, 2)
                    balance += ppnl

                # STEP3: トレーリングストップ
                if t.partial_closed:
                    trail_dist = atr_val * trail_mult
                    if t.direction == "buy":
                        new_sl = round(t.max_price - trail_dist, 3)
                        if new_sl > t.sl_price:
                            t.sl_price = new_sl
                    else:
                        new_sl = round(t.max_price + trail_dist, 3)
                        if new_sl < t.sl_price:
                            t.sl_price = new_sl

                # SL/TP ヒット判定
                closed = False
                if t.direction == "buy":
                    if low <= t.sl_price:
                        t.exit_price = t.sl_price
                        t.outcome    = "trailing_sl" if t.partial_closed else "sl_hit"
                        t.pnl = (t.sl_price - t.entry_price) * t.remaining_lots * 100
                        closed = True
                    elif high >= t.tp_price:
                        t.exit_price = t.tp_price
                        t.outcome    = "tp_hit"
                        t.pnl = (t.tp_price - t.entry_price) * t.remaining_lots * 100
                        closed = True
                else:
                    if high >= t.sl_price:
                        t.exit_price = t.sl_price
                        t.outcome    = "trailing_sl" if t.partial_closed else "sl_hit"
                        t.pnl = (t.entry_price - t.sl_price) * t.remaining_lots * 100
                        closed = True
                    elif low <= t.tp_price:
                        t.exit_price = t.tp_price
                        t.outcome    = "tp_hit"
                        t.pnl = (t.entry_price - t.tp_price) * t.remaining_lots * 100
                        closed = True

                if closed:
                    t.exit_bar  = i
                    balance    += t.pnl
                    balance_curve.append(round(balance, 2))
                    open_trade  = None
                    continue

            # ── 新規エントリー ──────────────────────────
            if open_trade is not None:
                continue   # 既にポジションあり

            if atr is None or atr <= 0:
                continue

            # ATRボラティリティフィルター
            if atr > atr_vmax or atr < atr_vmin:
                continue

            direction = signal_func(self.df, i, self.params)
            if direction is None:
                continue

            # AIモックフィルター
            if ai_mock is not None:
                ai_result = ai_mock.judge(
                    bar_index=i,
                    direction=direction,
                    atr=atr,
                    context={},
                )
                if ai_result["decision"] == "reject":
                    continue

            # v3.0: スコアリングフィルター
            if scoring_filter is not None:
                score_result = scoring_filter.filter(
                    self.df, i, direction, atr, self.params
                )
                if score_result["decision"] == "reject":
                    continue

            # SL/TP計算
            sl_dollar = round(atr * sl_mult, 3)
            sl_dollar = max(min_sl, min(max_sl, sl_dollar))
            tp_dollar = round(atr * tp_mult, 3)

            risk_amount = balance * risk_pct
            lot_size    = round(risk_amount / (sl_dollar * 100.0), 2)
            lot_size    = max(0.01, lot_size)

            # v3.0: スプレッド・スリッページを考慮したエントリー価格
            if direction == "buy":
                entry_px = close + cost_per_trade  # buy: askで約定 = 高め
                sl_price = round(entry_px - sl_dollar, 3)
                tp_price = round(entry_px + tp_dollar, 3)
            else:
                entry_px = close - cost_per_trade  # sell: bidで約定 = 低め
                sl_price = round(entry_px + sl_dollar, 3)
                tp_price = round(entry_px - tp_dollar, 3)

            trade = Trade(
                direction   = direction,
                entry_bar   = i,
                entry_price = entry_px,
                sl_price    = sl_price,
                tp_price    = tp_price,
                lot_size    = lot_size,
                atr         = atr,
            )
            trades.append(trade)
            open_trade = trade

        # 最終バーでオープン中のポジションを強制クローズ
        if open_trade is not None:
            t = open_trade
            t.exit_bar   = len(self.df) - 1
            t.exit_price = float(self.df["close"].iloc[-1])
            t.outcome    = "open"
            if t.direction == "buy":
                t.pnl = (t.exit_price - t.entry_price) * t.remaining_lots * 100
            else:
                t.pnl = (t.entry_price - t.exit_price) * t.remaining_lots * 100

        return BacktestResult(
            trades=trades,
            balance_curve=balance_curve,
            params={
                **self.params.copy(),
                "_use_ai_mock":     use_ai_mock,
                "_ai_approve_rate": ai_approve_rate,
            },
        )


# ──────────────────────────────────────────────────────────
# パラメータグリッドサーチ
# ──────────────────────────────────────────────────────────

def grid_search(
    df: pd.DataFrame,
    sl_mults: list[float] | None = None,
    tp_mults: list[float] | None = None,
    signal_func: Callable | None = None,
) -> list[dict]:
    """
    SL/TP 乗数の組み合わせをグリッドサーチして結果を返す。

    Returns:
        list of dicts (sorted by total_pnl DESC), each containing:
        {"sl_mult", "tp_mult", "n_trades", "win_rate", "total_pnl",
         "profit_factor", "max_drawdown_pct", "sharpe_ratio"}
    """
    if sl_mults is None:
        sl_mults = [1.5, 2.0, 2.5, 3.0]
    if tp_mults is None:
        tp_mults = [2.0, 2.5, 3.0, 3.5, 4.0]

    results = []
    for sl in sl_mults:
        for tp in tp_mults:
            if tp <= sl:   # RR < 1.0 は除外
                continue
            params = {"atr_sl_multiplier": sl, "atr_tp_multiplier": tp}
            engine = BacktestEngine(df, params)
            r = engine.run(signal_func)
            if r.n_trades == 0:
                continue
            results.append({
                "sl_mult":          sl,
                "tp_mult":          tp,
                "n_trades":         r.n_trades,
                "win_rate":         round(r.win_rate, 3),
                "total_pnl":        round(r.total_pnl, 2),
                "profit_factor":    r.profit_factor,
                "max_drawdown_pct": r.max_drawdown_pct,
                "sharpe_ratio":     r.sharpe_ratio,
            })

    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────
# v3.0: ScoringFilterMock（scoring_engine 統合テスト用）
# ──────────────────────────────────────────────────────────

class ScoringFilterMock:
    """
    バックテスト用のスコアリングフィルターモック。
    scoring_engine.calculate_score() を模倣し、
    OHLCVバーからの疑似構造化データを生成してスコアリングする。
    """

    def __init__(self, scoring_config_overrides: dict | None = None):
        from config import SCORING_CONFIG
        self._config = {**SCORING_CONFIG, **(scoring_config_overrides or {})}

    def filter(self, df: pd.DataFrame, i: int, direction: str,
               atr: float, params: dict) -> dict:
        """
        OHLCVバーから疑似構造化データを生成し、スコアリングする。

        Returns:
            {"decision": "approve"|"reject"|"wait", "score": float, ...}
        """
        from scoring_engine import calculate_score

        # OHLCVバーから疑似構造化データ生成
        structured = self._build_mock_structured(df, i, direction, atr, params)
        return calculate_score(structured, direction)

    def _build_mock_structured(self, df: pd.DataFrame, i: int,
                                direction: str, atr: float,
                                params: dict) -> dict:
        """OHLCVバーから疑似構造化データを生成する"""
        close = float(df["close"].iloc[i])

        # RSI計算
        rsi_value = None
        rsi_zone = "neutral"
        if i >= 20:
            delta = df["close"].diff()
            gain = delta.clip(lower=0)
            loss_s = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss_s.ewm(alpha=1/14, adjust=False).mean()
            rs = avg_gain.iloc[i] / (avg_loss.iloc[i] + 1e-10)
            rsi_value = round(100 - 100 / (1 + rs), 2)
            if rsi_value < 30:
                rsi_zone = "oversold"
            elif rsi_value > 70:
                rsi_zone = "overbought"

        # ADX計算（簡易）
        adx_value = None
        adx_rising = None
        if i >= 30:
            # 簡易ADX: ATR変動率で近似
            atr_series = df["atr"].iloc[max(0, i-14):i+1].dropna()
            if len(atr_series) >= 5:
                adx_value = round(float(atr_series.std() / (atr_series.mean() + 1e-10) * 100), 2)
                adx_rising = float(atr_series.iloc[-1]) > float(atr_series.iloc[-3]) if len(atr_series) >= 3 else None

        # SMA20
        sma20 = None
        sma20_distance_pct = None
        above_sma20 = None
        if i >= 20:
            sma20 = float(df["close"].iloc[i-20:i].mean())
            if sma20 > 0:
                sma20_distance_pct = round((close - sma20) / sma20 * 100, 2)
                above_sma20 = close > sma20

        # レジーム分類
        classification = "range"
        atr_expanding = False
        if adx_value is not None:
            if adx_value > 25 and atr > params.get("atr_volatility_min", 3.0) * 2:
                classification = "breakout"
                atr_expanding = True
            elif adx_value > 20:
                classification = "trend"

        return {
            "regime": {
                "classification": classification,
                "adx_value": adx_value,
                "adx_rising": adx_rising,
                "atr_expanding": atr_expanding,
                "squeeze_detected": atr < params.get("atr_volatility_min", 3.0) * 1.5 if atr else False,
            },
            "price_structure": {
                "above_sma20": above_sma20,
                "sma20_distance_pct": sma20_distance_pct,
                "perfect_order": None,
                "higher_highs": None,
                "lower_lows": None,
            },
            "zone_interaction": {
                "zone_touch": False,
                "zone_direction": None,
                "fvg_touch": False,
                "fvg_direction": None,
                "liquidity_sweep": False,
                "sweep_direction": None,
            },
            "momentum": {
                "rsi_value": rsi_value,
                "rsi_zone": rsi_zone,
                "trend_aligned": True,  # バックテストではQ-trendなし
            },
            "signal_quality": {
                "source": "backtest",
                "bar_close_confirmed": True,
                "session": "London",
                "tv_confidence": None,
                "tv_win_rate": None,
            },
            "data_completeness": {
                "mt5_connected": False,
                "fields_missing": [],
            },
        }


# ──────────────────────────────────────────────────────────
# v3.0: ウォークフォワード分析
# ──────────────────────────────────────────────────────────

def walk_forward_analysis(
    df: pd.DataFrame,
    n_splits: int = 5,
    train_ratio: float = 0.7,
    signal_func: Callable | None = None,
) -> list[dict]:
    """
    ウォークフォワード分析。データをn_splits個の区間に分割し、
    各区間のtrain部分で最適パラメータを選定、test部分で検証する。

    Args:
        df:          OHLCV DataFrame
        n_splits:    分割数
        train_ratio: トレーニング比率
        signal_func: シグナル関数

    Returns:
        [{"split": 1, "train_pnl": ..., "test_pnl": ..., ...}, ...]
    """
    total_bars = len(df)
    split_size = total_bars // n_splits
    results = []

    for split_i in range(n_splits):
        start = split_i * split_size
        end = min(start + split_size, total_bars)
        split_df = df.iloc[start:end].reset_index(drop=True)

        if len(split_df) < 60:
            continue

        train_end = int(len(split_df) * train_ratio)
        train_df = split_df.iloc[:train_end].reset_index(drop=True)
        test_df = split_df.iloc[train_end:].reset_index(drop=True)

        if len(train_df) < 40 or len(test_df) < 20:
            continue

        # トレーニング: グリッドサーチで最適パラメータ選定
        train_results = grid_search(train_df, signal_func=signal_func)
        if not train_results:
            continue

        best = train_results[0]
        best_params = {
            "atr_sl_multiplier": best["sl_mult"],
            "atr_tp_multiplier": best["tp_mult"],
        }

        # テスト: 最適パラメータで検証
        test_engine = BacktestEngine(test_df, best_params)
        test_result = test_engine.run(signal_func)

        results.append({
            "split": split_i + 1,
            "train_bars": len(train_df),
            "test_bars": len(test_df),
            "best_sl_mult": best["sl_mult"],
            "best_tp_mult": best["tp_mult"],
            "train_pnl": best["total_pnl"],
            "train_win_rate": best["win_rate"],
            "test_pnl": round(test_result.total_pnl, 2),
            "test_win_rate": round(test_result.win_rate, 3),
            "test_n_trades": test_result.n_trades,
            "test_profit_factor": test_result.profit_factor,
        })

    return results


# ──────────────────────────────────────────────────────────
# CLI エントリーポイント
# ──────────────────────────────────────────────────────────

def _build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AI Trading System - バックテストツール"
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv",  type=str, help="OHLCVデータのCSVファイルパス")
    source.add_argument("--mt5",  action="store_true",
                        help="MT5から直接データを取得")

    p.add_argument("--symbol",   default="GOLD",   help="取引シンボル（MT5使用時、XMTrading=GOLD）")
    p.add_argument("--tf",       default="M15",    help="時間足（M1/M5/M15/M30/H1/H4/D1）")
    p.add_argument("--bars",     type=int, default=2000, help="取得バー数（MT5使用時）")
    p.add_argument("--sl-mult",  type=float, default=None,
                   help="ATR SL乗数（省略時は動的最適化またはデフォルト）")
    p.add_argument("--tp-mult",  type=float, default=None,
                   help="ATR TP乗数（省略時は動的最適化またはデフォルト）")
    p.add_argument("--grid",     action="store_true",
                   help="グリッドサーチを実行してパラメータ比較")
    p.add_argument("--strategy", choices=["breakout", "rsi"], default="breakout",
                   help="バックテスト戦略")
    p.add_argument("--ai-mock",  action="store_true",
                   help="AIモックフィルターを有効化")
    p.add_argument("--ai-approve-rate", type=float, default=0.6,
                   help="AIモックの承認率（0.0〜1.0、デフォルト 0.6）")
    # v3.0 追加
    p.add_argument("--spread",  type=float, default=0.50,
                   help="スプレッド（ドル、デフォルト 0.50）")
    p.add_argument("--slippage", type=float, default=0.10,
                   help="スリッページ（ドル、デフォルト 0.10）")
    p.add_argument("--scoring-filter", action="store_true",
                   help="scoring_engine をフィルターとして使用（v3.0）")
    p.add_argument("--walk-forward", action="store_true",
                   help="ウォークフォワード分析を実行")
    p.add_argument("--wf-splits", type=int, default=5,
                   help="ウォークフォワードの分割数（デフォルト 5）")
    return p


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = _build_cli_parser().parse_args()

    # データ読み込み
    if args.csv:
        print(f"📂 CSVファイルを読み込み中: {args.csv}")
        df = load_csv(args.csv)
    else:
        print(f"📡 MT5からデータ取得中: {args.symbol} {args.tf} {args.bars}本")
        df = load_mt5_data(args.symbol, args.tf, args.bars)

    print(f"✅ データ読み込み完了: {len(df)}本")

    # シグナル関数選択
    signal_func = rsi_reversal_signal if args.strategy == "rsi" else atr_breakout_signal
    print(f"📊 戦略: {args.strategy}")

    # グリッドサーチモード
    if args.grid:
        print("\n🔍 グリッドサーチ実行中...\n")
        results = grid_search(df, signal_func=signal_func)
        if not results:
            print("⚠️ 取引が生成されませんでした。データやパラメータを確認してください。")
            return

        header = f"{'SL':>6}  {'TP':>6}  {'取引数':>6}  {'勝率':>7}  {'総損益':>10}  {'PF':>7}  {'最大DD':>8}  {'Sharpe':>7}"
        print(header)
        print("-" * len(header))
        for r in results[:20]:
            print(
                f"{r['sl_mult']:>6.1f}  {r['tp_mult']:>6.1f}  {r['n_trades']:>6d}  "
                f"{r['win_rate']:>6.1%}  {r['total_pnl']:>+10.2f}  "
                f"{r['profit_factor']:>7.3f}  {r['max_drawdown_pct']:>7.1f}%  "
                f"{r['sharpe_ratio']:>7.3f}"
            )
        best = results[0]
        print(f"\n🏆 最良パラメータ: SL×{best['sl_mult']}  TP×{best['tp_mult']}  "
              f"総損益 ${best['total_pnl']:+.2f}")
        return

    # v3.0: ウォークフォワード分析
    if args.walk_forward:
        print(f"\n📈 ウォークフォワード分析 ({args.wf_splits}分割)...\n")
        wf_results = walk_forward_analysis(df, n_splits=args.wf_splits,
                                            signal_func=signal_func)
        if not wf_results:
            print("⚠️ ウォークフォワード結果なし")
            return
        for r in wf_results:
            print(f"  Split {r['split']}: "
                  f"Train ${r['train_pnl']:+.2f} WR={r['train_win_rate']:.1%} | "
                  f"Test ${r['test_pnl']:+.2f} WR={r['test_win_rate']:.1%} "
                  f"(SL×{r['best_sl_mult']} TP×{r['best_tp_mult']})")
        total_test_pnl = sum(r["test_pnl"] for r in wf_results)
        print(f"\n  合計テスト損益: ${total_test_pnl:+.2f}")
        return

    # 単一パラメータセットのバックテスト
    params = {}
    if args.sl_mult is not None:
        params["atr_sl_multiplier"] = args.sl_mult
    if args.tp_mult is not None:
        params["atr_tp_multiplier"] = args.tp_mult
    # v3.0: スプレッド・スリッページ
    params["spread_dollar"] = args.spread
    params["slippage_dollar"] = args.slippage

    engine = BacktestEngine(df, params)
    result = engine.run(signal_func,
                        use_ai_mock=args.ai_mock,
                        ai_approve_rate=args.ai_approve_rate,
                        use_scoring_filter=args.scoring_filter)

    # AI有無の効果を計測（同一シードでAIなしのランを実行して比較）
    ai_filter_effect: float | None = None
    if args.ai_mock:
        base_result = engine.run(signal_func, use_ai_mock=False)
        base_win    = base_result.win_rate * 100
        mock_win    = result.win_rate * 100
        ai_filter_effect = round(mock_win - base_win, 2)

    print(result.summary(
        use_ai_mock=args.ai_mock,
        ai_approve_rate=args.ai_approve_rate,
        ai_filter_effect=ai_filter_effect,
    ))

    if result.n_trades > 0:
        wins   = sum(1 for t in result.closed_trades if t.pnl > 0)
        losses = result.n_trades - wins
        print(f"\n  勝ちトレード: {wins}件  負けトレード: {losses}件")
        avg_win  = (sum(t.pnl for t in result.closed_trades if t.pnl > 0) / wins
                    if wins else 0)
        avg_loss = (sum(t.pnl for t in result.closed_trades if t.pnl <= 0) / losses
                    if losses else 0)
        print(f"  平均利益: ${avg_win:+.2f}  平均損失: ${avg_loss:+.2f}")


if __name__ == "__main__":
    main()
