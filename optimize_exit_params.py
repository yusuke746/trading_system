"""
optimize_exit_params.py - TRENDエグジットパラメータのOptuna最適化
対象: atr_sl_multiplier / atr_tp_multiplier / be_trigger_atr_mult
      partial_tp_atr_mult / trailing_step_atr_mult
"""
import math
import optuna
import pandas as pd

# Optunaログを抑制
optuna.logging.set_verbosity(optuna.logging.WARNING)

from backtester_live import LiveBacktestEngine, load_alerts, load_ohlcv_csv

# データ読み込み（load_alerts/load_ohlcv_csv でタイムスタンプを正しく変換）
alerts = load_alerts("alerts_test.csv")
ohlcv  = load_ohlcv_csv("ohlcv_GOLD_5m.csv")


def objective(trial):
    params = {
        # 探索範囲
        "atr_sl_multiplier":      trial.suggest_float("sl_mult",     1.2, 3.0, step=0.1),
        "atr_tp_multiplier":      trial.suggest_float("tp_mult",     2.0, 6.0, step=0.2),
        "be_trigger_atr_mult":    trial.suggest_float("be_trigger",  0.5, 2.0, step=0.1),
        "partial_tp_atr_mult":    trial.suggest_float("partial_tp",  1.5, 4.0, step=0.1),
        "trailing_step_atr_mult": trial.suggest_float("trailing",    1.0, 3.0, step=0.1),
    }

    # 制約: partial_tp > be_trigger（順序の整合性）
    if params["partial_tp_atr_mult"] <= params["be_trigger_atr_mult"]:
        return float("-inf")

    # 制約: tp > partial_tp（TPがpartial_tpより遠い）
    if params["atr_tp_multiplier"] <= params["partial_tp_atr_mult"]:
        return float("-inf")

    engine = LiveBacktestEngine(alerts, ohlcv, params)
    result = engine.run()

    n_trades = len(result.completed_trades)

    # トレード数が30件未満は無効（過学習防止）
    if n_trades < 30:
        return float("-inf")

    # 目的関数: PF × log(トレード数)
    # PFだけを最大化するとトレード数が激減するため、
    # トレード数も考慮したペナルティを加える
    return result.profit_factor * math.log(max(n_trades, 1))


if __name__ == "__main__":
    print("🔍 Optuna最適化開始 (n_trials=300, n_jobs=-1)...")
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=300, n_jobs=-1)

    print("\n=== 最適パラメータ ===")
    print(study.best_params)
    print(f"目的関数値: {study.best_value:.3f}")

    # 上位10件を表示
    df = study.trials_dataframe()
    df_valid = df[df["value"] > float("-inf")].sort_values("value", ascending=False)
    cols = ["params_sl_mult", "params_tp_mult", "params_be_trigger",
            "params_partial_tp", "params_trailing", "value"]
    print("\n=== 上位10件 ===")
    print(df_valid[cols].head(10).to_string(index=False))

    # 上位3セットのバックテスト詳細
    print("\n=== 上位3セット 詳細バックテスト ===")
    for rank, row in enumerate(df_valid.head(3).itertuples(), 1):
        params = {
            "atr_sl_multiplier":      row.params_sl_mult,
            "atr_tp_multiplier":      row.params_tp_mult,
            "be_trigger_atr_mult":    row.params_be_trigger,
            "partial_tp_atr_mult":    row.params_partial_tp,
            "trailing_step_atr_mult": row.params_trailing,
        }
        engine = LiveBacktestEngine(alerts, ohlcv, params)
        result = engine.run()
        done   = result.completed_trades
        wins   = [t for t in done if (t.pnl + t.partial_pnl) > 0]
        days   = 50  # 約50取引日
        tp_hits = [t for t in done if t.outcome == "tp_hit"]
        print(f"\n--- #{rank} ---")
        print(f"  sl={row.params_sl_mult} tp={row.params_tp_mult} "
              f"be_trigger={row.params_be_trigger} "
              f"partial_tp={row.params_partial_tp} trailing={row.params_trailing}")
        print(f"  トレード数: {len(done)} | 1日平均: {len(done)/days:.2f}")
        print(f"  勝率: {len(wins)/len(done)*100:.1f}%")
        print(f"  PF: {result.profit_factor:.3f}")
        print(f"  最大DD: {result.max_drawdown:.2f}%")
        print(f"  TP到達: {len(tp_hits)} / {len(done)} ({len(tp_hits)/len(done)*100:.1f}%)")
        print(f"  総損益: ${sum(t.pnl + t.partial_pnl for t in done):+.2f}")

    # result_p2.csv に最適パラメータ結果を保存
    print("\n📊 最適パラメータでバックテスト実行 → result_p2.csv")
    best = study.best_params
    best_params = {
        "atr_sl_multiplier":      best["sl_mult"],
        "atr_tp_multiplier":      best["tp_mult"],
        "be_trigger_atr_mult":    best["be_trigger"],
        "partial_tp_atr_mult":    best["partial_tp"],
        "trailing_step_atr_mult": best["trailing"],
    }
    engine = LiveBacktestEngine(alerts, ohlcv, best_params)
    result = engine.run()
    import numpy as np
    done = result.completed_trades
    rows = []
    for t in done:
        rows.append({
            "alert_time":     t.alert_time,
            "regime":         t.regime,
            "direction":      t.direction,
            "entry_price":    t.entry_price,
            "sl_price":       t.sl_price,
            "tp_price":       t.tp_price,
            "lot_size":       round(t.lot_size, 4),
            "atr":            round(t.atr, 4),
            "score":          round(t.score, 4),
            "decision":       t.decision,
            "outcome":        t.outcome,
            "exit_price":     t.exit_price,
            "pnl":            round(t.pnl, 2),
            "partial_pnl":    round(t.partial_pnl, 2),
            "net_pnl":        round(t.pnl + t.partial_pnl, 2),
            "pnl_pips":       round(t.pnl_pips, 2),
            "duration_bars":  t.duration_bars,
            "be_applied":     t.be_applied,
            "partial_closed": t.partial_closed,
        })
    pd.DataFrame(rows).to_csv("result_p2.csv", index=False)
    print(f"  保存完了: result_p2.csv ({len(rows)}件)")
