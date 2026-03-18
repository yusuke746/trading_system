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
    "atr_sl_multiplier":    2.7,   # Optuna最適化 P2 (2026-03-12) PF1.435  旧: 2.0
    "atr_tp_multiplier":    6.0,   # Optuna最適化 P2 (2026-03-12) PF1.435  旧: 3.0
    "max_sl_pips":          80.0,  # dollar単位
    "min_sl_pips":          8.0,   # dollar単位
    "pip_points":           10,    # GOLD: 1pip = 10point = $0.10
    # ATRボラティリティフィルター（異常値でエントリー禁止）
    "atr_volatility_max":   50.0,  # 50ドル超: 指標直後の異常スパイクのみ排除（旧30.0は5200水準基準で現価格帯に不適）
    "atr_volatility_min":   3.0,   # 3ドル未満: 値動きなし（スプレッド費用対効果悪化）※15M ATR用
    "atr5_volatility_min":  1.5,   # 5M ATR（atr5）用の下限（15M より小さい値動きを許容）
    # 追加リスク管理（risk_manager.py）
    # 1日の確定損失上限: 口座残高に対するパーセンテージ（負値）
    # risk_percent=2.0%なら細4負け相当で停止。一般的なリスク管理基準（5%）に準拠。
    "max_daily_loss_percent":   -5.0,    # 1日の確定損失上限（残高比率%）
    "max_consecutive_losses":   3,       # 連続SL被弾で自動停止
    "gap_block_threshold_usd":  15.0,    # 週明けギャップブロック閃値（ドル）
    "fallback_balance":         10000.0, # MT5未接続時のフォールバック残高（ドル）
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
    "news_min_importance":      5,     # MT5重要度5以上
    "news_filter_fail_safe":    True,  # True=取得失敗時はエントリーブロック / False=許可（安全側をデフォルト）
    # ── ポジション管理 v2追加 ──────────────────
    "partial_close_ratio":      0.5,   # 第1TPで50%決済
    "partial_tp_atr_mult":      3.6,   # Optuna最適化 P2 (2026-03-12) PF1.435  旧: 2.5
    "be_trigger_atr_mult":      1.8,   # Optuna最適化 P2 (2026-03-12) PF1.435  旧: 1.5
    # "be_buffer_pips":         2.0,   # 旧: Forex pips 単位（XAUUSD では不適切）→ be_buffer_atr_mult に移行
    "be_buffer_atr_mult":       0.15,  # BEバッファ = ATR×0.15（dollar価格単位）
    "trailing_step_atr_mult":   1.2,   # Optuna最適化 P2 (2026-03-12) PF1.435  旧: 2.0
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

# ── スコアリングエンジン設定 ──────────────────────────────────
# scoring_engine.py が参照する閾値と加減点ルール。
# バックテストで最適化可能にするため全て外出し。
SCORING_CONFIG = {
    # ── 旧パラメータ (scoring_engine v3.5 以前 / 現在未使用) ─────────────────
    # backtester.py / meta_optimizer.py が参照している可能性があるためコメントアウトで保持。
    # 削除が必要になった場合は参照側モジュールとセットで対応すること。
    #
    # --- 判定閾値 (旧) ---
    # "approve_threshold":              0.45,   # → v4.0 で 0.25 に変更
    # "wait_threshold":                 0.10,   # → v4.0 で 0.00 に変更
    #
    # --- レジーム別基礎点 ---
    # "regime_trend_base":              0.15,
    # "regime_breakout_base":           0.20,
    # "regime_range_base":             -0.10,
    #
    # --- ゾーン・構造要素 ---
    # "zone_touch_aligned":             0.20,
    # "zone_touch_aligned_with_trend":  0.20,
    # "zone_touch_counter_trend":       0.08,
    # "fvg_touch_aligned":              0.15,
    # "fvg_touch_aligned_with_trend":   0.15,
    # "fvg_touch_counter_trend":        0.06,
    # "liquidity_sweep":                0.25,
    # "sweep_plus_zone":                0.10,
    #
    # --- モメンタム ---
    # "trend_aligned":                  0.10,
    # "rsi_confirmation":               0.05,
    # "rsi_divergence":                -0.20,  # 旧: buy+RSIoverbought → 減点
    #                                           # → v4.0 で意味が変わり +0.15 に変更
    #
    # --- シグナル品質 ---
    # "bar_close_confirmed":            0.10,
    # "session_london_ny":              0.05,   # → v4.0 で 0.10 に変更
    # "session_tokyo":                 -0.05,   # → v4.0 で -0.10 に変更
    # "session_off_hours":             -0.15,   # → v4.0 で session_off(-0.20) に統合
    #
    # --- 危険パターン（即reject）---
    # "range_mid_chase":               -999,
    # "data_insufficient":             -999,
    # "counter_trend_no_sweep":        -0.30,
    #
    # --- TradingView 品質 ---
    # "tv_confidence_high":             0.10,
    # "tv_confidence_low":             -0.10,
    # "pattern_similarity_high":        0.10,
    # "pattern_similarity_low":        -0.10,
    # "pattern_similarity_none":        0.00,

    # ── 新スコアテーブル (scoring_engine v4.0 / マルチレジーム対応) ───────────
    # Pine Script フラット JSON を直接受け取る新アーキテクチャ用パラメータ。

    # --- 判定閾値 ---
    "approve_threshold":      0.15,   # 新テーブル向け確定値（旧 0.25 から変更）
    "wait_threshold":         0.00,   # 新テーブル向け確定値（旧 0.10 から変更）

    # --- CHoCH 確認（強い構造転換）---
    "choch_strong":           0.20,   # choch_confirmed == true

    # --- RSI ダイバージェンス ---
    "rsi_divergence":         0.15,   # rsi_divergence == true（価格と RSI の逆行確認）
                                      # ※旧 -0.20（buy+RSI overbought 減点）とは意味が異なる

    # --- FVG × Zone の重複ヒットボーナス ---
    "fvg_and_zone_overlap":   0.15,   # fvg_aligned == true AND zone_aligned == true

    # --- 15M ADX 強度 ---
    "adx_normal":             0.10,   # m15_adx 25〜35（健全なトレンド強度）
    "adx_reversal_penalty":  -0.10,   # REVERSAL かつ m15_adx > 35（ADX 過熱で REVERSAL 信頼性低下）

    # --- 1H 方向一致 ---
    "h1_direction_aligned":   0.10,   # h1_direction == "bull" and direction == "buy"
                                      # h1_direction == "bear" and direction == "sell"

    # --- セッション別 ---
    "session_london_ny":      0.10,   # London/NY オーバーラップ（最高流動性）
    "session_london":         0.05,   # London オープン
    "session_ny":             0.00,   # NY セッション（加減点なし）
    "session_tokyo":         -0.10,   # Tokyo セッション（低流動性）
    "session_off":           -0.20,   # オフアワーズ（最低流動性）

    # --- ATR ratio（15M ATR / ATR MA20）---
    "atr_ratio_normal":       0.05,   # 0.8〜1.5 倍（正常ボラティリティ）
    "atr_ratio_high":        -0.05,   # 1.5 倍超（ボラティリティ過熱）

    # --- ニュースフィルター ---
    "news_nearby":           -0.30,   # 高インパクトニュース 30 分前後

    # --- BOS・Order Block（P3追加）---
    "bos_confirmed":          0.20,   # BOS確認後プルバックでのエントリー
    "ob_aligned":             0.20,   # OBヒット（方向一致）
    "ob_and_fvg":             0.10,   # OB+FVG重複ボーナス
}

# ── 高インパクト経済指標スケジュール（UTC）──────────────────
HIGH_IMPACT_UTC_TIMES = [
    {"weekday": 4, "hour_start": 12, "hour_end": 14, "name": "NFP"},
    {"weekday": 2, "hour_start": 21, "hour_end": 24, "name": "FOMC"},
    {"weekday": 3, "hour_start": 3,  "hour_end": 6,  "name": "BOJ"},
]
