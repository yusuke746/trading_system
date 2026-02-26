"""
config.py - 全設定の一元管理
AI Trading System v2.0
"""

SYSTEM_CONFIG = {
    # ── 取引設定 ────────────────────────────────
    "symbol":               "GOLD",   # XMTrading のシンボル名（XAUUSD ではない）
    "max_positions":          5,       # 同時保有可能な最大ポジション数
    "max_total_risk_percent": 0.10,    # 口座残高の10%を上限とする合計リスクエクスポージャー上限（max_positions=5対応）
    "min_free_margin":        500.0,

    # ── リスク管理 ───────────────────────────────
    "risk_percent":         2.0,
    # GOLD 5200ドル水準でATR15m≈ 8〜15ドル。SL=ATR×2.0で15〜20ドルの雑音耐性を確保
    "atr_sl_multiplier":    2.0,
    "atr_tp_multiplier":    3.0,   # RR=1.5維持
    "max_sl_pips":          80.0,  # dollar単位
    "min_sl_pips":          8.0,   # dollar単位
    "pip_points":           10,    # GOLD: 1pip = 10point = $0.10
    # ATRボラティリティフィルター（異常値でエントリー禁止）
    "atr_volatility_max":   30.0,  # 30ドル超: 指標・重要イベント直後等
    "atr_volatility_min":   3.0,   # 3ドル未満: 値動きなし（スプレッド費用対効果悪化）
    # 追加リスク管理（risk_manager.py）
    # 1日の確定損失上限: 口座残高に対するパーセンテージ（負値）
    # risk_percent=2.0%なら細4負け相当で停止。一般的なリスク管理基準（5%）に準拠。
    "max_daily_loss_percent":   -5.0,    # 1日の確定損失上限（残高比率%）
    "max_consecutive_losses":   3,       # 連続SL被弾で自動停止
    "gap_block_threshold_usd":  15.0,    # 週明けギャップブロック閾値（ドル）    "fallback_balance":         10000.0, # MT5未接続時のフォールバック残高（ドル）
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
        "zone_retrace_touch":  60 * 15,         # 15分
        "fvg_touch":           60 * 15,         # 15分
        "liquidity_sweep":     60 * 30,         # 30分
        "prediction_signal":   60 * 60 * 4,    # 新規追加：4時間（Q-trend環境認識）
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
    "news_min_importance":      2,     # MT5重要度2以上    "news_filter_fail_safe":    True,  # True=取得失敗時はエントリーブロック / False=許可（安全側をデフォルト）
    # ── ポジション管理 v2追加 ──────────────────
    "partial_close_ratio":      0.5,   # 第1TPで50%決済
    "partial_tp_atr_mult":      2.0,   # SL乗数に合わせて設定
    "be_trigger_atr_mult":      1.0,   # BE発動 = ATR×1.0含み益（早めに発動）
    "be_buffer_pips":           2.0,   # BEの予裕幅
    "trailing_step_atr_mult":   1.5,   # トレーリング幅（ATR基準）
    "pm_check_interval_sec":    10,

    # ── 逆張り自動昇格設定 ────────────────────────
    "reversal_auto_trigger_enabled": True,   # 逆張り自動昇格の有効/無効
    "reversal_cooldown_sec":         60 * 5, # 同一方向の連続昇格を防ぐクールダウン（5分）

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

# ── セッション別 SL/TP 乗数補正テーブル ─────────────
# 各セッションの atr_sl_multiplier / atr_tp_multiplier に掛ける係数
# 1.0 = 変更なし
SESSION_SLTP_ADJUST = {
    # Asia: 低ボラ・レンジ → SL/TP を絞って費用対効果を改善
    "Asia":       {"sl_mult": 0.75, "tp_mult": 0.75},
    # London: 通常
    "London":     {"sl_mult": 1.00, "tp_mult": 1.00},
    # London_NY: 最高ボラ → SL/TP を広げてノイズ耐性と利幅を確保
    "London_NY":  {"sl_mult": 1.30, "tp_mult": 1.30},
    # NY: 通常
    "NY":         {"sl_mult": 1.00, "tp_mult": 1.00},
    # Off_hours: 低ボラ → Asiaと同様に絞る
    "Off_hours":  {"sl_mult": 0.75, "tp_mult": 0.75},
}
