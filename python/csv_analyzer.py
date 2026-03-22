"""
csv_analyzer.py - CSVシグナルデータのスコアリング分析ツール
AI Trading System v4.0

TradingViewからエクスポートしたCSVを読み込み、
scoring_engine.calculate_score() を用いてバックテスト的なスコア分析を行う。

使用方法:
    py python/csv_analyzer.py data/OANDA_XAUUSD_5.csv
"""

import sys
import os
import logging
from pathlib import Path

import pandas as pd
from unittest.mock import patch

# python/ ディレクトリと プロジェクトルートをパスに追加（scoring_engine 等のインポート用）
_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT_DIR   = _SCRIPT_DIR.parent
for _p in [str(_SCRIPT_DIR), str(_ROOT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("csv_analyzer")


# ── CSVデコードマップ ────────────────────────────────────────────
_REGIME_MAP = {
    "1": "TREND",
    "2": "BREAKOUT",
    "3": "RANGE",
    "4": "REVERSAL",
}
_DIR_MAP = {
    "1":  "buy",
    "-1": "sell",
    "0":  "none",
}
_SESS_MAP = {
    "5": "london_ny",
    "4": "london",
    "3": "ny",
    "2": "tokyo",
    "1": "off",
}
_H1_MAP = {
    "1":  "bull",
    "-1": "bear",
    "0":  "none",
}


def csv_row_to_alert(row: dict) -> dict:
    """CSVの1行をscoring_engine.calculate_score()が受け取れる
    alert dict形式に変換する"""
    return {
        "regime":          _REGIME_MAP.get(str(row.get("regime", "3")), "RANGE"),
        "direction":       _DIR_MAP.get(str(row.get("direction", "0")), "none"),
        "h1_direction":    _H1_MAP.get(str(row.get("h1_direction", "0")), "none"),
        "h1_adx":          float(row.get("h1_adx", 0)),
        "m15_adx":         float(row.get("m15_adx", 0)),
        "atr_ratio":       float(row.get("atr_ratio", 1.0)),
        "choch_confirmed": str(row.get("choch", "0")) == "1",
        "fvg_aligned":     str(row.get("fvg", "0")) == "1",
        "zone_aligned":    str(row.get("zone", "0")) == "1",
        "bos_confirmed":   str(row.get("bos", "0")) == "1",
        "ob_aligned":      str(row.get("ob", "0")) == "1",
        "sweep_detected":  str(row.get("sweep", "0")) == "1",
        "session":         _SESS_MAP.get(str(row.get("session", "1")), "off"),
        "news_nearby":     False,   # オフライン分析では常にFalse
        "rsi_divergence":  False,
    }


def _calculate(alert: dict) -> dict:
    """news_filter をモックして calculate_score() を呼び出す"""
    from scoring_engine import calculate_score
    with patch("news_filter.is_news_blackout", return_value=False):
        return calculate_score(alert)


def analyze(csv_path: str) -> None:
    """CSVを読み込み、閾値シミュレーション結果を表示する"""
    df = pd.read_csv(csv_path)
    total = len(df)
    print(f"\n=== CSV分析: {csv_path} ({total}行) ===\n")

    # 全行のスコアと結果を事前計算
    scores    = []
    decisions = []
    pnl_col   = "pnl" if "pnl" in df.columns else None

    for _, row in df.iterrows():
        alert  = csv_row_to_alert(row.to_dict())
        result = _calculate(alert)
        scores.append(result["score"])
        decisions.append(result["decision"])

    df = df.copy()
    df["_score"]    = scores
    df["_decision"] = decisions

    # ── 閾値シミュレーション ──────────────────────────────────────
    thresholds = [i / 100 for i in range(-20, 71, 5)]  # -0.20 〜 0.70
    trading_days = max(1, total // (24 * 12))  # 5分足: 1日≒288本

    header = (
        f"{'閾値':>6}  {'通過件数':>8}  {'1日平均':>8}"
    )
    if pnl_col:
        header += f"  {'勝率':>6}  {'PF':>6}  {'期待値':>8}"
    print(header)
    print("-" * len(header))

    for thr in thresholds:
        mask  = (df["_score"] >= thr) & (df["_decision"] != "reject")
        count = mask.sum()
        per_day = count / trading_days

        row_str = f"{thr:>6.2f}  {count:>8d}  {per_day:>8.2f}"

        if pnl_col:
            subset = df.loc[mask, pnl_col].dropna()
            if len(subset) > 0:
                wins  = (subset > 0).sum()
                win_r = wins / len(subset)
                gross_p = subset[subset > 0].sum()
                gross_l = abs(subset[subset < 0].sum())
                pf  = gross_p / gross_l if gross_l > 0 else float("inf")
                ev  = subset.mean()
                row_str += f"  {win_r:>6.1%}  {pf:>6.2f}  {ev:>8.2f}"
            else:
                row_str += f"  {'—':>6}  {'—':>6}  {'—':>8}"

        print(row_str)

    # ── 決定分布サマリー ─────────────────────────────────────────
    approve_n = (df["_decision"] == "approve").sum()
    wait_n    = (df["_decision"] == "wait").sum()
    reject_n  = (df["_decision"] == "reject").sum()

    print(f"\n--- 判定分布 (scoring_engine デフォルト閾値) ---")
    print(f"  approve : {approve_n:>6d} ({approve_n/total:>6.1%})")
    print(f"  wait    : {wait_n:>6d} ({wait_n/total:>6.1%})")
    print(f"  reject  : {reject_n:>6d} ({reject_n/total:>6.1%})")
    print(f"  total   : {total:>6d}")

    if pnl_col:
        approved = df[df["_decision"] == "approve"][pnl_col].dropna()
        if len(approved) > 0:
            gross_p = approved[approved > 0].sum()
            gross_l = abs(approved[approved < 0].sum())
            pf = gross_p / gross_l if gross_l > 0 else float("inf")
            print(f"\n--- approve のみ集計 ---")
            print(f"  取引数 : {len(approved)}")
            print(f"  勝率   : {(approved>0).sum()/len(approved):.1%}")
            print(f"  PF     : {pf:.3f}")
            print(f"  期待値 : {approved.mean():.2f}")

    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"使用方法: py {sys.argv[0]} <csvファイルパス>")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"エラー: ファイルが見つかりません: {csv_path}")
        sys.exit(1)

    analyze(csv_path)
