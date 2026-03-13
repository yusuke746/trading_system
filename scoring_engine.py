"""
scoring_engine.py - 数値ルールベースのスコアリングエンジン
AI Trading System v4.0（マルチレジーム対応）

Pine Script アラート JSON を直接受け取り、純粋な数値ルールで
approve / reject / wait を判定する。LLM は一切関与しない。
全ての閾値は config.py の SCORING_CONFIG で管理し、バックテストで最適化可能。
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

    # ── 共通スコアテーブル評価 ────────────────────────────────
    breakdown: dict[str, float] = {}
    _apply_choch_score(alert, _cfg, breakdown)
    _apply_rsi_divergence(alert, _cfg, breakdown)
    _apply_fvg_zone_overlap(alert, _cfg, breakdown)
    _apply_adx_score(alert, _cfg, breakdown)
    _apply_h1_direction_score(alert, _cfg, breakdown)
    _apply_session_score(alert, _cfg, breakdown)
    _apply_atr_ratio_score(alert, _cfg, breakdown)
    _apply_news_penalty(alert, _cfg, breakdown)
    _apply_bos_ob_score(alert, _cfg, breakdown)

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
    h1_adx    = float(alert.get("h1_adx", 0))
    choch     = bool(alert.get("choch_confirmed", False))
    fvg_al    = bool(alert.get("fvg_aligned", False))
    zone_al   = bool(alert.get("zone_aligned", False))
    sweep     = bool(alert.get("sweep_detected", False))

    # 共通ゲート ①: h1_adx >= 25
    if h1_adx < 25:
        reasons.append(f"Gate1: h1_adx={h1_adx:.1f} < 25")

    # 共通ゲート ②: RANGE は即 reject（以降のレジーム別チェック不要）
    if regime == "RANGE":
        reasons.append("Gate2: regime=RANGE → reject")
        return reasons

    # レジーム別追加ゲート
    if regime == "TREND":
        if not choch:
            reasons.append("Gate3(TREND): choch_confirmed=false")
        if not (fvg_al or zone_al):
            reasons.append("Gate3(TREND): fvg_aligned=false AND zone_aligned=false")

    elif regime == "REVERSAL":
        if not choch:
            reasons.append("Gate3(REVERSAL): choch_confirmed=false")
        if not sweep:
            reasons.append("Gate3(REVERSAL): sweep_detected=false")

    elif regime == "BREAKOUT":
        # CHoCH 不要（レンジブレイク後のリテスト確認が本質）
        if not (fvg_al or zone_al):
            reasons.append("Gate3(BREAKOUT): fvg_aligned=false AND zone_aligned=false")

    return reasons


# ── 個別スコア評価関数 ──────────────────────────────────────────

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
    """高インパクトニュース 30 分前後 → 大幅減点"""
    if bool(alert.get("news_nearby", False)):
        breakdown["news_nearby"] = cfg["news_nearby"]


def _apply_bos_ob_score(alert: dict, cfg: dict, breakdown: dict) -> None:
    """BOS確認・OBヒット加点（P3追加）"""
    if bool(alert.get("bos_confirmed", False)):
        breakdown["bos_confirmed"] = cfg.get("bos_confirmed", 0.20)
    if bool(alert.get("ob_aligned", False)):
        breakdown["ob_aligned"] = cfg.get("ob_aligned", 0.20)
    # OB+FVG重複ボーナス
    if bool(alert.get("ob_aligned", False)) and \
       bool(alert.get("fvg_aligned", False)):
        breakdown["ob_and_fvg"] = cfg.get("ob_and_fvg", 0.10)
