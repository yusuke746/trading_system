"""
config.py - 全設定の一元管理
AI Trading System v2.0
"""

SYSTEM_CONFIG = {
    # ── 取引設定 ────────────────────────────────
    "symbol":               "GOLD",   # XMTrading のシンボル名（XAUUSD ではない）
    "max_positions":        1,
    "min_free_margin":      500.0,

    # ── リスク管理 ───────────────────────────────
    "risk_percent":         2.0,
    # GOLD 5200ドル水準でATR15m≈ 8〜15ドル。SL=ATR×2.0で15〜20ドルの雑音耐性を確保
    "atr_sl_multiplier":    2.0,   # 1.5 → 2.0
    "atr_tp_multiplier":    3.0,   # 2.5 → 3.0（RR=1.5維持）
    "max_sl_pips":          80.0,  # 50 → 80（dollar単位）
    "min_sl_pips":          8.0,   # 5 → 8（dollar単位）
    "pip_points":           10,    # GOLD: 1pip = 10point = $0.10
    # ATRボラティリティフィルター（異常値でエントリー禁止）
    "atr_volatility_max":   30.0,  # 30ドル超: 指標・重要イベント直後等
    "atr_volatility_min":   3.0,   # 3ドル未満: 値動きなし（スプレッド費用対効果悪化）
    # 追加リスク管理（risk_manager.py）
    # 1日の確定損失上限: 口座残高に対するパーセンテージ（負値）
    # 例: -5.0 → 残高の5%を超えて負けたら自動停止（risk_percent=1.0%なら約5負け相当）
    "max_daily_loss_percent":   -10.0,    # 1日の確定損失上限（残高比率%）
    "max_consecutive_losses":   3,       # 連続SL被弾で自動停止
    "gap_block_threshold_usd":  15.0,    # 週明けギャップブロック閾値（ドル）

    # ── 注文設定 ─────────────────────────────────
    "deviation":            20,
    "magic_number":         20260223,
    "order_comment":        "AI_Flask_v2",

    # ── AI判定閾値 ───────────────────────────────
    "min_confidence":       0.70,
    "min_ev_score":         0.20,   # 0.30 → 0.20（逆張り: liquidity+zone で +0.3 だがmacro逆で-0.3が相殺されやすいため緩和）

    # ── シグナル収集 ─────────────────────────────
    "collection_window_ms": 500,
    "signal_buffer_size":   50,

    # ── コンテキスト時間窓（秒）─────────────────
    "time_windows": {
        "new_zone_confirmed":  60 * 60 * 12,   # 12時間
        "zone_retrace_touch":  60 * 15,          # 15分
        "fvg_touch":           60 * 15,          # 15分
        "liquidity_sweep":     60 * 30,          # 30分
    },

    # ── wait設定（秒）────────────────────────────
    "wait_expiry": {
        "next_bar":         60 * 6,    # 6分
        "structure_needed": 60 * 15,   # 15分
        "cooldown":         60 * 3,    # 3分
    },
    "max_reeval_count":     3,

    # ── ニュースフィルター v2追加 ──────────────
    "news_filter_enabled":      True,
    "news_block_before_min":    30,    # 発表前30分ブロック
    "news_block_after_min":     30,    # 発表後30分ブロック
    "news_target_currencies":   ["USD", "EUR"],
    "news_min_importance":      2,     # MT5重要度2以上

    # ── ポジション管理 v2追加 ──────────────────
    "partial_close_ratio":      0.5,   # 第1TPで50%決済
    "partial_tp_atr_mult":      2.0,   # 1.5 → 2.0（SL乗数に合わせて修正）
    "be_trigger_atr_mult":      1.0,   # BE発動 = ATR×1.0含み益（早めに発動）
    "be_buffer_pips":           2.0,   # BEの余裕幅
    "trailing_step_atr_mult":   1.5,   # 1.0 → 1.5（トレーリング幅を拡大）
    "pm_check_interval_sec":    10,

    # ── 監視設定 ─────────────────────────────────
    "health_check_interval_sec":   60,
    "position_check_interval_sec": 10,
    "loss_alert_usd":              -100.0,

    # ── XMサーバータイム（クローズ判定）────────────
    "daily_break_start_h": 23,
    "daily_break_start_m": 45,
    "daily_break_end_h":   1,
    # 未約定指値の自動キャンセル開始時刻（仕様: 23:30から）
    "limit_cancel_start_h": 23,
    "limit_cancel_start_m": 30,
    # デイリーブレイク前の警告マージン（分）
    # daily_break_start_m(45) - limit_cancel_warn_m(15) = 23:30 からキャンセル警戒
    "limit_cancel_warn_m":  15,
    # デイリーブレイク前の全ポジション強制クローズ時刻（UTC）
    # daily_break(23:45)の15分前 = 23:30
    "eod_close_h": 23,
    "eod_close_m": 30,
}

# ── ATRベースのSL/TP計算定数 ───────────────────────
ATR_SL_MULT = SYSTEM_CONFIG["atr_sl_multiplier"]
ATR_TP_MULT = SYSTEM_CONFIG["atr_tp_multiplier"]
MAX_SL_PIPS = SYSTEM_CONFIG["max_sl_pips"]
MIN_SL_PIPS = SYSTEM_CONFIG["min_sl_pips"]
PIP_POINTS  = SYSTEM_CONFIG["pip_points"]
