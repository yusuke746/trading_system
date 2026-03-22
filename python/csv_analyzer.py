#!/usr/bin/env python3
"""
csv_analyzer.py
TradingViewからエクスポートしたCSVを読み込み、
SMC条件の組み合わせ・セッション・レジームごとの
勝率・損益・期待値を計算してレポートを出力する。

Usage:
    python python/csv_analyzer.py data/export.csv
"""

import sys
import os
import argparse
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import numpy as np

# scoring_engine はプロジェクトルートにあるため追加
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scoring_engine import calculate_score

# ─── 定数 ──────────────────────────────────────────────────────────────────────
SL_ATR_MULT = 2.7
TP_ATR_MULT = 4.0
MAX_BARS    = 100

REGIME_MAP  = {1: "TREND", 2: "BREAKOUT", 3: "RANGE", 4: "REVERSAL"}
DIR_MAP     = {1: "buy", -1: "sell", 0: "none"}
SESSION_MAP = {5: "london_ny", 4: "london", 3: "ny", 2: "tokyo", 1: "off"}
H1DIR_MAP   = {1: "bull", -1: "bear"}

SMC_COLS    = ["choch", "fvg", "zone", "bos", "ob", "sweep"]
SCORE_THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


# ─── ユーティリティ ────────────────────────────────────────────────────────────
def safe_pf(wins: int, losses: int) -> str:
    """Profit Factor を計算する（losses=0 のときは '∞'）。"""
    if losses == 0:
        return "∞" if wins > 0 else "0.00"
    return f"{(wins * TP_ATR_MULT) / (losses * SL_ATR_MULT):.2f}"


def safe_pf_float(wins: int, losses: int) -> float:
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return (wins * TP_ATR_MULT) / (losses * SL_ATR_MULT)


def win_rate(wins: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{wins / total * 100:.2f}%"


def expected_r(wins: int, losses: int, total: int) -> str:
    """期待値(R単位) = WR×TP_mult − LR×SL_mult"""
    if total == 0:
        return "N/A"
    wr = wins / total
    lr = losses / total
    return f"{wr * TP_ATR_MULT - lr * SL_ATR_MULT:.2f}"


def expected_r_float(wins: int, losses: int, total: int) -> float:
    if total == 0:
        return float("nan")
    wr = wins / total
    lr = losses / total
    return wr * TP_ATR_MULT - lr * SL_ATR_MULT


def print_section(title: str) -> None:
    print(f"\n=== {title} ===")


# ─── CSVロード ─────────────────────────────────────────────────────────────────
def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"[ERROR] ファイルが見つかりません: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_csv(path)
    except Exception as e:
        print(f"[ERROR] CSV読み込みエラー: {e}", file=sys.stderr)
        sys.exit(1)

    # カラム名の正規化（前後スペース除去）
    df.columns = df.columns.str.strip()

    required = ["time", "open", "high", "low", "close",
                "regime", "direction", "h1_adx", "m15_adx",
                "choch", "fvg", "zone", "bos", "ob", "sweep",
                "session", "atr", "atr_ratio", "h1_direction", "alert_fired"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[ERROR] 必須カラムが不足しています: {missing}", file=sys.stderr)
        sys.exit(1)

    # 数値変換
    numeric_cols = ["open", "high", "low", "close",
                    "regime", "direction", "h1_adx", "m15_adx",
                    "choch", "fvg", "zone", "bos", "ob", "sweep",
                    "session", "atr", "atr_ratio", "h1_direction", "alert_fired"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).reset_index(drop=True)
    df = df.sort_values("time").reset_index(drop=True)

    return df


# ─── トレードシミュレーション ──────────────────────────────────────────────────
def simulate_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    alert_fired=1 かつ direction != 0 の行をエントリーとしてシミュレートする。
    結果列 'outcome' を追加: 'win' / 'loss' / 'timeout'
    """
    outcomes  = [""] * len(df)
    exit_bars = [0] * len(df)

    # エントリー対象行のインデックスを取得
    entry_mask = (df["alert_fired"] == 1) & (df["direction"] != 0)
    entry_indices = df.index[entry_mask].tolist()

    total = len(entry_indices)
    print(f"[INFO] シミュレーション対象: {total} 件")

    highs  = df["high"].values
    lows   = df["low"].values

    for i, idx in enumerate(entry_indices):
        if (i + 1) % 500 == 0 or (i + 1) == total:
            print(f"  進捗: {i + 1}/{total}", end="\r")

        row = df.iloc[idx]
        entry_price = row["close"]
        atr_val     = row["atr"]
        direction   = int(row["direction"])

        sl_dist = atr_val * SL_ATR_MULT
        tp_dist = atr_val * TP_ATR_MULT

        if direction == 1:   # buy
            sl = entry_price - sl_dist
            tp = entry_price + tp_dist
        else:                # sell
            sl = entry_price + sl_dist
            tp = entry_price - tp_dist

        result = "timeout"
        end_idx = min(idx + MAX_BARS + 1, len(df))

        for j in range(idx + 1, end_idx):
            bar_high = highs[j]
            bar_low  = lows[j]
            if direction == 1:
                hit_sl = bar_low  <= sl
                hit_tp = bar_high >= tp
            else:
                hit_sl = bar_high >= sl
                hit_tp = bar_low  <= tp

            if hit_sl:
                result = "loss"
                exit_bars[idx] = j - idx
                break
            if hit_tp:
                result = "win"
                exit_bars[idx] = j - idx
                break

        outcomes[idx] = result

    if total > 0:
        print()  # 改行

    df = df.copy()
    df["outcome"]  = outcomes
    df["exit_bar"] = exit_bars
    return df


# ─── CSVの1行をscoring_engineのalert形式に変換 ──────────────────────────────
def csv_row_to_alert(row: dict) -> dict:
    """CSVの1行をscoring_engine.calculate_score()が受け取れる
    alert dict形式に変換する"""

    # regime デコード: 1=TREND, 2=BREAKOUT, 3=RANGE, 4=REVERSAL
    regime_map = {'1': 'TREND', '2': 'BREAKOUT', '3': 'RANGE', '4': 'REVERSAL'}
    # direction デコード: 1=buy, -1=sell, 0=none
    dir_map = {'1': 'buy', '-1': 'sell', '0': 'none'}
    # session デコード: 5=london_ny, 4=london, 3=ny, 2=tokyo, 1=off
    sess_map = {'5': 'london_ny', '4': 'london', '3': 'ny', '2': 'tokyo', '1': 'off'}
    # h1_direction デコード: 1=bull, -1=bear
    h1_map = {'1': 'bull', '-1': 'bear', '0': 'none'}

    return {
        'regime':          regime_map.get(str(row.get('regime', '3')), 'RANGE'),
        'direction':       dir_map.get(str(row.get('direction', '0')), 'none'),
        'h1_direction':    h1_map.get(str(row.get('h1_direction', '0')), 'none'),
        'h1_adx':          float(row.get('h1_adx', 0)),
        'm15_adx':         float(row.get('m15_adx', 0)),
        'atr_ratio':       float(row.get('atr_ratio', 1.0)),
        'choch_confirmed': str(row.get('choch', '0')) == '1',
        'fvg_aligned':     str(row.get('fvg', '0')) == '1',
        'zone_aligned':    str(row.get('zone', '0')) == '1',
        'bos_confirmed':   str(row.get('bos', '0')) == '1',
        'ob_aligned':      str(row.get('ob', '0')) == '1',
        'sweep_detected':  str(row.get('sweep', '0')) == '1',
        'session':         sess_map.get(str(row.get('session', '1')), 'off'),
        'news_nearby':     False,
        'rsi_divergence':  False,
    }


# ─── 集計ヘルパー ──────────────────────────────────────────────────────────────
def summarize(subset: pd.DataFrame) -> dict:
    resolved = subset[subset["outcome"].isin(["win", "loss"])]
    wins     = (resolved["outcome"] == "win").sum()
    losses   = (resolved["outcome"] == "loss").sum()
    total    = len(resolved)
    timeouts = (subset["outcome"] == "timeout").sum()
    return dict(
        n_entries=len(subset),
        n_resolved=total,
        n_timeouts=int(timeouts),
        wins=int(wins),
        losses=int(losses),
    )


def format_row(label: str, s: dict) -> str:
    total = s["n_resolved"]
    w, l  = s["wins"], s["losses"]
    return (
        f"{label:<20} | "
        f"件数:{total:>4} | "
        f"勝率:{win_rate(w, total):>8} | "
        f"PF:{safe_pf(w, l):>6} | "
        f"期待値:{expected_r(w, l, total):>6}R"
    )


# ─── レポート出力 ──────────────────────────────────────────────────────────────
def report(df: pd.DataFrame) -> None:
    # エントリー行のみ抽出
    entries = df[(df["alert_fired"] == 1) & (df["direction"] != 0)].copy()
    resolved = entries[entries["outcome"].isin(["win", "loss"])]

    total_alerts = len(entries)
    total_sim    = len(resolved)
    total_timeout = (entries["outcome"] == "timeout").sum()
    wins_all  = (resolved["outcome"] == "win").sum()
    losses_all = (resolved["outcome"] == "loss").sum()

    # ── 全体サマリー ──
    print_section("全体サマリー")
    print(f"総アラート数      : {total_alerts}")
    print(f"シミュレート可能数: {total_sim}")
    print(f"timeout数         : {total_timeout}")
    print(f"全体勝率          : {win_rate(wins_all, total_sim)}")
    print(f"全体PF            : {safe_pf(wins_all, losses_all)}")
    print(f"平均RR            : {TP_ATR_MULT / SL_ATR_MULT:.2f}  (固定 TP{TP_ATR_MULT}R / SL{SL_ATR_MULT}R)")

    # ── レジーム別 ──
    print_section("レジーム別")
    print(f"{'regime':<20} | {'件数':>4} | {'勝率':>8} | {'PF':>6} | {'期待値':>6}")
    print("-" * 65)
    for code, name in sorted(REGIME_MAP.items()):
        sub = resolved[entries["regime"] == code]
        if len(sub) == 0:
            continue
        w = (sub["outcome"] == "win").sum()
        l = (sub["outcome"] == "loss").sum()
        t = len(sub)
        print(f"{name:<20} | {t:>4} | {win_rate(w, t):>8} | {safe_pf(w, l):>6} | {expected_r(w, l, t):>6}R")

    # ── セッション別 ──
    print_section("セッション別")
    print(f"{'session':<20} | {'件数':>4} | {'勝率':>8} | {'PF':>6} | {'期待値':>6}")
    print("-" * 65)
    for code, name in sorted(SESSION_MAP.items(), reverse=True):
        sub = resolved[entries["session"] == code]
        if len(sub) == 0:
            continue
        w = (sub["outcome"] == "win").sum()
        l = (sub["outcome"] == "loss").sum()
        t = len(sub)
        print(f"{name:<20} | {t:>4} | {win_rate(w, t):>8} | {safe_pf(w, l):>6} | {expected_r(w, l, t):>6}R")

    # ── SMC条件組み合わせ別（上位15位）──
    print_section("SMC条件の組み合わせ別（上位15位）")
    print(f"{'conditions':<35} | {'件数':>4} | {'勝率':>8} | {'PF':>6} | {'期待値':>6}")
    print("-" * 75)

    entries_resolved = entries[entries["outcome"].isin(["win", "loss"])].copy()

    def cond_key(row):
        active = [col for col in SMC_COLS if int(row[col]) == 1]
        return "+".join(active) if active else "(none)"

    entries_resolved["cond_key"] = entries_resolved.apply(cond_key, axis=1)
    cond_groups = entries_resolved.groupby("cond_key")

    cond_rows = []
    for key, grp in cond_groups:
        if len(grp) < 5:
            continue
        w = (grp["outcome"] == "win").sum()
        l = (grp["outcome"] == "loss").sum()
        t = len(grp)
        ev = expected_r_float(w, l, t)
        cond_rows.append((key, t, w, l, ev))

    cond_rows.sort(key=lambda x: x[4], reverse=True)
    for key, t, w, l, ev in cond_rows[:15]:
        print(f"{key:<35} | {t:>4} | {win_rate(w, t):>8} | {safe_pf(w, l):>6} | {ev:>+.2f}R")

    # ── h1_direction × direction 一致/不一致 ──
    print_section("h1_direction × direction 一致/不一致")
    aligned     = entries_resolved[
        ((entries_resolved["h1_direction"] == 1) & (entries_resolved["direction"] == 1)) |
        ((entries_resolved["h1_direction"] == -1) & (entries_resolved["direction"] == -1))
    ]
    not_aligned = entries_resolved[
        ~(
            ((entries_resolved["h1_direction"] == 1) & (entries_resolved["direction"] == 1)) |
            ((entries_resolved["h1_direction"] == -1) & (entries_resolved["direction"] == -1))
        )
    ]

    for label, sub in [("一致（順張り）", aligned), ("不一致（逆張り）", not_aligned)]:
        w = (sub["outcome"] == "win").sum()
        l = (sub["outcome"] == "loss").sum()
        t = len(sub)
        print(f"{label}: 件数={t}  勝率={win_rate(w, t)}  PF={safe_pf(w, l)}  期待値={expected_r(w, l, t)}R")

    # ── スコア閾値シミュレーション ──
    print_section("スコア閾値シミュレーション")

    # scoring_engine.calculate_score() を使ってスコアと判定を算出
    scores = []
    decisions = []
    for _, row in entries.iterrows():
        alert = csv_row_to_alert(row)
        with patch('news_filter.is_news_blackout', return_value=False):
            result = calculate_score(alert)
        scores.append(result['score'])
        decisions.append(result['decision'])

    entries['score']    = scores
    entries['decision'] = decisions
    entries_resolved['score']    = entries.loc[entries_resolved.index, 'score']
    entries_resolved['decision'] = entries.loc[entries_resolved.index, 'decision']

    # CSVの総日数
    date_min = entries["time"].dt.date.min()
    date_max = entries["time"].dt.date.max()
    total_days = max((date_max - date_min).days, 1)

    print(f"{'閾値':>6} | {'通過件数':>8} | {'1日平均':>8} | {'勝率':>8} | {'PF':>6} | {'期待値':>6}")
    print("-" * 70)
    for thresh in SCORE_THRESHOLDS:
        filtered = entries_resolved[
            (entries_resolved["score"] >= thresh) &
            (entries_resolved["decision"] != "reject")
        ]
        t = len(filtered)
        if t == 0:
            print(f"{thresh:>6.2f} | {0:>8} | {'0.00':>8} | {'N/A':>8} | {'N/A':>6} | {'N/A':>6}")
            continue
        w = (filtered["outcome"] == "win").sum()
        l = (filtered["outcome"] == "loss").sum()
        daily_avg = t / total_days
        print(
            f"{thresh:>6.2f} | {t:>8} | {daily_avg:>8.2f} | "
            f"{win_rate(w, t):>8} | {safe_pf(w, l):>6} | {expected_r(w, l, t):>6}R"
        )

    # ── 日別エントリー数の分布 ──
    print_section("日別エントリー数の分布")

    entries_resolved2 = entries[entries["outcome"].isin(["win", "loss"])].copy()
    daily_counts = entries_resolved2.groupby(entries_resolved2["time"].dt.date).size()

    # 全日付を埋める（アラートゼロの日も含める）
    all_dates = pd.date_range(date_min, date_max, freq="D")
    daily_counts = daily_counts.reindex(all_dates.date, fill_value=0)

    bins = {
        "0件": (0, 0),
        "1-5件": (1, 5),
        "6-10件": (6, 10),
        "11-20件": (11, 20),
        "21件以上": (21, 9999),
    }
    for label, (lo, hi) in bins.items():
        n = ((daily_counts >= lo) & (daily_counts <= hi)).sum()
        if n > 0:
            print(f"  {label}: {n}日")

    # ── 月別成績 ──
    print_section("月別成績")
    entries_resolved["month"] = entries_resolved["time"].dt.to_period("M").astype(str)
    monthly = entries_resolved.groupby("month")
    print(f"{'月':>10} | {'件数':>4} | {'勝ち':>4} | {'負け':>4} | {'勝率':>7} | {'PF':>6} | {'月次損益':>10}")
    print("-" * 60)
    for month, grp in monthly:
        w = (grp["outcome"] == "win").sum()
        l = (grp["outcome"] == "loss").sum()
        t = len(grp)
        pnl_r = w * TP_ATR_MULT - l * SL_ATR_MULT
        pnl_str = f"{pnl_r:+.1f}R"
        print(f"{month:>10} | {t:>4} | {w:>4} | {l:>4} | "
              f"{win_rate(w,t):>7} | {safe_pf(w,l):>6} | {pnl_str:>10}")

    # ── 連勝・連敗分析 ──
    print_section("連勝・連敗分析")
    outcomes_seq = entries_resolved["outcome"].tolist()
    max_win_streak = max_loss_streak = 0
    cur_streak = 1
    for i in range(1, len(outcomes_seq)):
        if outcomes_seq[i] == outcomes_seq[i-1]:
            cur_streak += 1
        else:
            cur_streak = 1
        if outcomes_seq[i] == "win":
            max_win_streak = max(max_win_streak, cur_streak)
        elif outcomes_seq[i] == "loss":
            max_loss_streak = max(max_loss_streak, cur_streak)
    if outcomes_seq:
        if outcomes_seq[0] == "win":
            max_win_streak = max(max_win_streak, 1)
        else:
            max_loss_streak = max(max_loss_streak, 1)
    print(f"  最大連勝: {max_win_streak} 連勝")
    print(f"  最大連敗: {max_loss_streak} 連敗")
    # 連敗ごとの頻度
    loss_streaks = []
    cur = 0
    for o in outcomes_seq:
        if o == "loss":
            cur += 1
        else:
            if cur > 0:
                loss_streaks.append(cur)
            cur = 0
    if cur > 0:
        loss_streaks.append(cur)
    from collections import Counter
    streak_counts = Counter(loss_streaks)
    print(f"\n  連敗分布:")
    for k in sorted(streak_counts.keys()):
        print(f"    {k}連敗: {streak_counts[k]}回")

    # ── ドローダウン分析 ──
    print_section("ドローダウン分析（R単位）")
    equity_curve = []
    equity = 0.0
    for o in outcomes_seq:
        if o == "win":
            equity += TP_ATR_MULT
        elif o == "loss":
            equity -= SL_ATR_MULT
        equity_curve.append(equity)
    peak = 0.0
    max_dd = 0.0
    max_dd_start = 0
    max_dd_end = 0
    for i, e in enumerate(equity_curve):
        if e > peak:
            peak = e
            dd_start = i
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
            max_dd_end = i
    print(f"  最終損益:         {equity:+.1f}R")
    print(f"  最大ドローダウン: -{max_dd:.1f}R")
    if max_dd > 0:
        print(f"  リカバリーファクター: {equity/max_dd:.2f}")
    else:
        print(f"  リカバリーファクター: ∞")

    # ── 平均保有時間 ──
    print_section("平均保有時間")
    if "exit_bar" in entries_resolved.columns:
        resolved_with_exit = entries_resolved[entries_resolved["exit_bar"] > 0]
        avg_bars = resolved_with_exit["exit_bar"].mean()
        med_bars = resolved_with_exit["exit_bar"].median()
        print(f"  平均保有バー数: {avg_bars:.1f}本 = 約{avg_bars*5/60:.1f}時間")
        print(f"  中央値:         {med_bars:.0f}本 = 約{med_bars*5/60:.1f}時間")
        win_bars  = resolved_with_exit[resolved_with_exit["outcome"]=="win"]["exit_bar"].mean()
        loss_bars = resolved_with_exit[resolved_with_exit["outcome"]=="loss"]["exit_bar"].mean()
        print(f"  勝ちトレード平均: {win_bars:.1f}本 = 約{win_bars*5/60:.1f}時間")
        print(f"  負けトレード平均: {loss_bars:.1f}本 = 約{loss_bars*5/60:.1f}時間")

    # ── 方向別成績 ──
    print_section("方向別成績（Buy / Sell）")
    for dir_val, dir_name in [(1, "BUY"), (-1, "SELL")]:
        sub = entries_resolved[entries_resolved["direction"] == dir_val]
        if len(sub) == 0:
            continue
        w = (sub["outcome"] == "win").sum()
        l = (sub["outcome"] == "loss").sum()
        t = len(sub)
        pnl = w * TP_ATR_MULT - l * SL_ATR_MULT
        print(f"  {dir_name}: {t}件  勝率={win_rate(w,t)}  PF={safe_pf(w,l)}"
              f"  損益={pnl:+.1f}R")


# ─── CSV出力 ───────────────────────────────────────────────────────────────────
def save_result_csv(df: pd.DataFrame) -> None:
    os.makedirs("data", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"data/analysis_result_{ts}.csv"

    entries = df[(df["alert_fired"] == 1) & (df["direction"] != 0)].copy()
    scores = []
    for _, row in entries.iterrows():
        alert = csv_row_to_alert(row)
        with patch('news_filter.is_news_blackout', return_value=False):
            result = calculate_score(alert)
        scores.append(result['score'])
    entries["score"] = scores

    out_cols = [
        "time", "open", "high", "low", "close",
        "regime", "direction", "h1_direction",
        "h1_adx", "m15_adx", "atr", "atr_ratio",
        "choch", "fvg", "zone", "bos", "ob", "sweep",
        "session", "alert_fired", "score", "outcome",
    ]
    out_cols = [c for c in out_cols if c in entries.columns]
    entries[out_cols].to_csv(out_path, index=False)
    print(f"\n[INFO] 結果CSVを保存しました: {out_path}")


# ─── メイン ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="TradingViewエクスポートCSVのSMC条件分析ツール"
    )
    parser.add_argument("csv_path", help="入力CSVファイルのパス")
    args = parser.parse_args()

    print(f"[INFO] CSVロード中: {args.csv_path}")
    df = load_csv(args.csv_path)
    print(f"[INFO] 行数: {len(df)}  期間: {df['time'].min()} 〜 {df['time'].max()}")

    df = simulate_trades(df)
    report(df)
    save_result_csv(df)


if __name__ == "__main__":
    main()
