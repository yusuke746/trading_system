"""
scoring_engine.py - 数値ルールベースのスコアリングエンジン
AI Trading System v4.0（マルチレジーム対応）

Pine Script アラート JSON を直接受け取り、純粋な数値ルールで
approve / reject / wait を判定する。LLM は一切関与しない。
全ての閾値は config.py の SCORING_CONFIG で管理し、バックテストで最適化可能。

v4.1: TREND / BREAKOUT スコアリングを完全分離。
      _score_trend()    … 既存ロジックをそのまま TREND 専用に移植
      _score_breakout() … BREAKOUT 専用の新スコアテーブル
"""

import logging

logger = logging.getLogger(__name__)


def calculate_score(alert: dict) -> dict:
    """
    Pine Script アラート JSON を受け取り、スコアリング結果を返す。

    Args:
        alert: Pine Script が送信するフラット JSON dict

    Returns:
        {
            "decision":        "approve" | "wait" | "reject",
            "score":           float,
            "score_breakdown": {条件名: 加点値, ...},
            "reject_reasons":  [str],
        }
    """
    # config を毎回読み直す（meta_optimizer による動的更新に対応）
    from config import SCORING_CONFIG as _cfg
    approve_threshold = _cfg["approve_threshold"]
    wait_threshold    = _cfg["wait_threshold"]

    # ── 必須ゲートチェック ──────────────────────────────────────
    # ゲートをひとつでも通過しなければ即 reject（スコア計算スキップ）
    reject_reasons = _check_gates(alert)
    if reject_reasons:
        logger.info(
            "🚫 ゲート不通過 reject: reasons=%s", reject_reasons
        )
        return {
            "decision":        "reject",
            "score":           -999.0,
            "score_breakdown": {"gate_reject": -999},
            "reject_reasons":  reject_reasons,
        }

    # ── レジーム別スコア計算 ─────────────────────────────────────
    # RANGE / REVERSAL / direction=none はゲートで弾済みなので
    # ここに到達するのは TREND か BREAKOUT のみ
    regime = alert.get("regime", "RANGE")
    if regime == "BREAKOUT":
        breakdown = _score_breakout(alert, _cfg)
    else:
        breakdown = _score_trend(alert, _cfg)

    total_score = round(sum(breakdown.values()), 4)

    # ── 判定 ────────────────────────────────────────────────
    if total_score >= approve_threshold:
        decision       = "approve"
        reject_reasons = []
    elif total_score >= wait_threshold:
        decision       = "wait"
        reject_reasons = []
    else:
        decision       = "reject"
        reject_reasons = ["スコア不足（閾値未達）"]

    logger.info(
        "🧮 スコアリング: decision=%s score=%.4f breakdown=%s",
        decision, total_score, breakdown,
    )

    return {
        "decision":        decision,
        "score":           total_score,
        "score_breakdown": breakdown,
        "reject_reasons":  reject_reasons,
    }


# ── 必須ゲートチェック ──────────────────────────────────────────

def _check_gates(alert: dict) -> list[str]:
    """
    必須ゲートを全てチェックする。
    戻り値: 不通過の理由リスト（空リスト = 全ゲート通過）
    """
    reasons:   list[str] = []
    regime    = alert.get("regime", "RANGE")
    h1_adx    = float(alert.get("h1_adx", 0) or 0)
    choch     = bool(alert.get("choch_confirmed", False))
    fvg_al    = bool(alert.get("fvg_aligned", False))
    zone_al   = bool(alert.get("zone_aligned", False))
    sweep     = bool(alert.get("sweep_detected", False))

    # 共通ゲート ①: h1_adx >= 25
    # BREAKOUTレジームは免除（ブレイク直後はADXが遅行するため）
    # 代替フィルター: 15M足のATR急拡大+直前ADX低下でレジーム判定済み
    if h1_adx < 25 and regime != "BREAKOUT":
        reasons.append(f"Gate1: h1_adx={h1_adx:.1f} < 25")

    # 共通ゲート ②: RANGE / REVERSAL は即 reject
    if regime == "RANGE":
        reasons.append("Gate2: regime=RANGE → reject")
        return reasons
    if regime == "REVERSAL":
        reasons.append("Gate2: regime=REVERSAL → reject (廃止レジーム)")
        return reasons

    # 共通ゲート ③: direction が未確定の場合は即 reject
    direction = alert.get("direction", "none")
    if direction == "none":
        reasons.append("Gate3: direction=none → reject (方向未確定)")
        return reasons

    # レジーム別追加ゲート
    if regime == "TREND":
        bos_al = bool(alert.get("bos_confirmed", False))
        ob_al  = bool(alert.get("ob_aligned",    False))
        # SMC条件のいずれか1つあれば通過（AND→OR緩和）
        # 複数条件の組み合わせ評価はスコアリングで行う
        smc_any = choch or fvg_al or zone_al or bos_al or ob_al
        if not smc_any:
            reasons.append(
                "Gate3(TREND): SMC条件未充足"
                "(choch/fvg/zone/bos/ob が全てfalse)"
            )

    elif regime == "REVERSAL":
        if not choch:
            reasons.append("Gate3(REVERSAL): choch_confirmed=false")
        if not sweep:
            reasons.append("Gate3(REVERSAL): sweep_detected=false")

    # BREAKOUT: Gate3 条件なし（スコアテーブルで評価する）

    return reasons


# ── レジーム別スコア計算 ────────────────────────────────────────

def _score_trend(alert: dict, cfg: dict) -> dict:
    """
    TRENDレジーム専用スコア計算。
    既存の共通スコアリング関数をそのまま流用する。
    戻り値: breakdown dict
    """
    breakdown: dict[str, float] = {}
    _apply_choch_score(alert, cfg, breakdown)
    _apply_rsi_divergence(alert, cfg, breakdown)
    _apply_fvg_zone_overlap(alert, cfg, breakdown)
    _apply_fvg_zone_penalty(alert, cfg, breakdown)
    _apply_adx_score(alert, cfg, breakdown)
    _apply_h1_direction_score(alert, cfg, breakdown)
    _apply_session_score(alert, cfg, breakdown)
    _apply_atr_ratio_score(alert, cfg, breakdown)
    _apply_news_penalty(alert, cfg, breakdown)
    _apply_bos_ob_score(alert, cfg, breakdown)
    return breakdown


def _score_breakout(alert: dict, cfg: dict) -> dict:
    """
    BREAKOUTレジーム専用スコア計算。
    TREND とは独立したスコアテーブルを使用する。
    戻り値: breakdown dict
    """
    breakdown: dict[str, float] = {}

    # h1_adx が極端に低い場合はペナルティ
    # h1_adx<20 = H1レベルでトレンドが存在しない → ダマシブレイクになりやすい
    # h1_adx=None（未設定）は判定不能のためフェイルセーフで免除
    _h1_adx_raw = alert.get("h1_adx", None)
    if _h1_adx_raw is not None:
        h1_adx = float(_h1_adx_raw)
        if h1_adx < 20:
            breakdown["breakout_low_adx_penalty"] = cfg.get("breakout_low_adx_penalty", -0.30)

    # ベーススコア（ブレイク確認済み）
    breakdown["breakout_base"] = cfg.get("breakout_base", 0.30)

    # SMCフラグ全なし = 相場の根拠ゼロ → ペナルティ
    # choch/fvg/zone/bos/ob/sweep いずれも未確認
    no_smc = not any([
        bool(alert.get("choch_confirmed", False)),
        bool(alert.get("fvg_aligned",     False)),
        bool(alert.get("zone_aligned",    False)),
        bool(alert.get("bos_confirmed",   False)),
        bool(alert.get("ob_aligned",      False)),
        bool(alert.get("sweep_detected",  False)),
    ])
    if no_smc:
        breakdown["breakout_no_smc_penalty"] = cfg.get("breakout_no_smc_penalty", -0.20)

    # FVGリテスト確認ボーナス
    if bool(alert.get("fvg_aligned", False)):
        breakdown["breakout_fvg_retest"] = cfg.get("breakout_fvg_retest", 0.15)

    # Zoneリテスト確認ボーナス
    if bool(alert.get("zone_aligned", False)):
        breakdown["breakout_zone_retest"] = cfg.get("breakout_zone_retest", 0.10)

    # H1方向一致ボーナス
    h1_dir = alert.get("h1_direction", "")
    dir_   = alert.get("direction", "none")
    if (h1_dir == "bull" and dir_ == "buy") or (h1_dir == "bear" and dir_ == "sell"):
        breakdown["breakout_h1_aligned"] = cfg.get("breakout_h1_aligned", 0.10)

    # ATR ratio（ボラティリティ確認）
    atr_r = float(alert.get("atr_ratio", 1.0))
    if atr_r >= 1.5:
        breakdown["breakout_atr_surge"] = cfg.get("breakout_atr_surge", 0.10)
    elif atr_r < 0.8:
        breakdown["breakout_atr_low"] = cfg.get("breakout_atr_low", -0.10)

    # セッション（BREAKOUT専用・Londonを強めにペナルティ）
    session_breakout_map = {
        "london_ny": "breakout_session_london_ny",
        "london":    "breakout_session_london",
        "ny":        "breakout_session_ny",
        "tokyo":     "breakout_session_tokyo",
        "off":       "breakout_session_off",
    }
    key = session_breakout_map.get(alert.get("session", ""))
    if key:
        val = cfg.get(key, 0)
        if val != 0:
            breakdown[key] = val

    # ニュースペナルティ（TRENDと共通ロジック）
    _apply_news_penalty(alert, cfg, breakdown)

    return breakdown


# ── 個別スコア評価関数（TREND専用） ────────────────────────────

def _apply_choch_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """CHoCH確認済み → 加点"""
    if bool(alert.get("choch_confirmed", False)):
        breakdown["choch_strong"] = cfg["choch_strong"]


def _apply_rsi_divergence(alert: dict, cfg: dict, breakdown: dict) -> None:
    """RSIダイバージェンス一致 → 加点"""
    if bool(alert.get("rsi_divergence", False)):
        breakdown["rsi_divergence"] = cfg["rsi_divergence"]


def _apply_fvg_zone_overlap(alert: dict, cfg: dict, breakdown: dict) -> None:
    """FVG と Zone の両方にアライン → 重複ヒットボーナス"""
    if bool(alert.get("fvg_aligned", False)) and bool(alert.get("zone_aligned", False)):
        breakdown["fvg_and_zone_overlap"] = cfg["fvg_and_zone_overlap"]


def _apply_fvg_zone_penalty(alert: dict, cfg: dict, breakdown: dict) -> None:
    """FVG単独またはZone単独の場合はペナルティ（bos/obがない場合）"""
    fvg  = bool(alert.get("fvg_aligned",  False))
    zone = bool(alert.get("zone_aligned", False))
    bos  = bool(alert.get("bos_confirmed", False))
    ob   = bool(alert.get("ob_aligned",   False))

    # BOSもOBも伴わないFVG単独はペナルティ
    if fvg and not zone and not bos and not ob:
        val = cfg.get("fvg_only_penalty", -0.20)
        if val != 0.0:
            breakdown["fvg_only_penalty"] = val

    # BOSもOBも伴わないZone単独はペナルティ
    if zone and not fvg and not bos and not ob:
        val = cfg.get("zone_only_penalty", -0.20)
        if val != 0.0:
            breakdown["zone_only_penalty"] = val


def _apply_adx_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """15M ADX による加点・減点"""
    m15_adx = float(alert.get("m15_adx", 0))
    regime  = alert.get("regime", "RANGE")

    if 25 <= m15_adx <= 35:
        breakdown["adx_normal"] = cfg["adx_normal"]

    # REVERSAL で ADX が高すぎる場合はペナルティ
    if regime == "REVERSAL" and m15_adx > 35:
        breakdown["adx_reversal_penalty"] = cfg["adx_reversal_penalty"]


def _apply_h1_direction_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """1H 方向とエントリー方向が一致 → 加点"""
    h1_dir = alert.get("h1_direction", "")
    dir_   = alert.get("direction", "none")
    if (h1_dir == "bull" and dir_ == "buy") or (h1_dir == "bear" and dir_ == "sell"):
        breakdown["h1_direction_aligned"] = cfg["h1_direction_aligned"]


def _apply_session_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """セッション別の加点・減点"""
    session_map = {
        "london_ny": "session_london_ny",
        "london":    "session_london",
        "ny":        "session_ny",
        "tokyo":     "session_tokyo",
        "off":       "session_off",
    }
    key = session_map.get(alert.get("session", ""))
    if key is None:
        return
    val = cfg.get(key, 0.0)
    if val != 0.0:
        breakdown[key] = val


def _apply_atr_ratio_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """ATR ratio（15M ATR / ATR MA20）による加点・減点"""
    atr_ratio = float(alert.get("atr_ratio", 1.0))
    if 0.8 <= atr_ratio <= 1.5:
        breakdown["atr_ratio_normal"] = cfg["atr_ratio_normal"]
    elif atr_ratio > 1.5:
        breakdown["atr_ratio_high"] = cfg["atr_ratio_high"]


def _apply_news_penalty(alert: dict, cfg: dict, breakdown: dict) -> None:
    """高インパクトニュース 30 分前後 → 大幅減点

    Pine Script の news_nearby フラグ、または固定ブラックアウト時刻の
    いずれかが True の場合にペナルティを適用する。
    """
    from news_filter import is_news_blackout

    pine_flag = bool(alert.get("news_nearby", False))
    blackout  = is_news_blackout()

    if pine_flag or blackout:
        breakdown["news_nearby"] = cfg["news_nearby"]


def _apply_bos_ob_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """
    BOS/OBスコア評価。
    bos_confirmed = BOS発生（トレンド継続の構造確認）→ 加点
    ob_aligned    = OBにリテスト済み（実際のエントリーポイント到達）→ 加点
    両方同時にtrueの場合はob_alignedがbos_confirmedを包含するため
    bos_confirmedの加点は省略（二重加点防止）
    """
    bos   = bool(alert.get("bos_confirmed", False))
    ob    = bool(alert.get("ob_aligned", False))
    fvg   = bool(alert.get("fvg_aligned", False))
    sweep = bool(alert.get("sweep_detected", False))
    if ob:
        # OBリテスト成立 = BOSパス完成。ob_alignedのみ加点
        breakdown["ob_aligned"] = cfg.get("ob_aligned", 0.20)
        if fvg:
            breakdown["ob_and_fvg"] = cfg.get("ob_and_fvg", 0.10)
    elif bos:
        # BOSは発生しているがOBリテスト未到達 → 加点を抑える
        breakdown["bos_confirmed"] = cfg.get("bos_confirmed", 0.30)
        # BOS+Sweep同時はボーナス加点（PF=1.48）
        if sweep:
            bonus = cfg.get("bos_and_sweep", 0.20)
            if bonus != 0.0:
                breakdown["bos_and_sweep"] = bonus
