"""
scoring_engine.py - 数値ルールベースのスコアリングエンジン
AI Trading System v3.0

llm_structurer.py の出力を受け取り、純粋な数値ルールで approve/reject/wait を判定する。
全ての判定ロジックは Python の if/else で書く。LLM は一切関与しない。
全ての閾値は config.py の SCORING_CONFIG で管理し、バックテストで最適化可能。
"""

import logging
from config import SCORING_CONFIG

logger = logging.getLogger(__name__)

def calculate_score(structured_data: dict, signal_direction: str,
                    q_trend_available: bool = True) -> dict:
    """
    構造化データからスコアを計算し、判定結果を返す。

    Args:
        structured_data: llm_structurer.py の出力（正規化された構造データ）
        signal_direction: エントリー方向 ("buy" / "sell")

    Returns:
        {
            "decision": "approve" | "reject" | "wait",
            "score": float,
            "score_breakdown": {条件名: 加点値, ...},
            "reject_reasons": [str],
            "wait_condition": str | None,
        }
    """
    # config を毎回読み直す（meta_optimizer による動的更新に対応）
    from config import SCORING_CONFIG as _cfg
    approve_threshold = _cfg["approve_threshold"]
    wait_threshold    = _cfg["wait_threshold"]

    # 即rejectチェック
    reject_reasons = _check_instant_reject(structured_data, signal_direction,
                                           q_trend_available)
    if reject_reasons:
        return {
            "decision": "reject",
            "score": -999.0,
            "score_breakdown": {"instant_reject": -999},
            "reject_reasons": reject_reasons,
            "wait_condition": None,
        }

    total_score = 0.0
    breakdown: dict[str, float] = {}

    # レジームスコア
    regime = structured_data.get("regime", {})
    regime_score, regime_detail = _calculate_regime_score(regime, signal_direction)
    total_score += regime_score
    if regime_detail:
        breakdown[regime_detail] = regime_score

    # 構造スコア
    zone_interaction = structured_data.get("zone_interaction", {})
    # momentum を先に取得して trend_aligned を structure_score に渡す
    momentum = structured_data.get("momentum", {})
    trend_aligned = momentum.get("trend_aligned", True)
    structure_score, structure_breakdown = _calculate_structure_score(
        zone_interaction,
        signal_direction,
        trend_aligned=trend_aligned,
    )
    total_score += structure_score
    breakdown.update(structure_breakdown)

    # モメンタムスコア（momentumはすでに取得済みのものを使う）
    has_sweep = zone_interaction.get("liquidity_sweep", False)
    momentum_score, momentum_breakdown = _calculate_momentum_score(
        momentum, signal_direction, has_sweep=has_sweep
    )
    total_score += momentum_score
    breakdown.update(momentum_breakdown)

    # シグナル品質スコア
    signal_quality = structured_data.get("signal_quality", {})
    quality_score, quality_breakdown = _calculate_signal_quality_score(signal_quality)
    total_score += quality_score
    breakdown.update(quality_breakdown)

    total_score = round(total_score, 4)

    # 判定
    if total_score >= approve_threshold:
        decision = "approve"
        wait_condition = None
    elif total_score >= wait_threshold:
        decision = "wait"
        wait_condition = _determine_wait_condition(structured_data, breakdown)
    else:
        decision = "reject"
        wait_condition = None

    return {
        "decision": decision,
        "score": total_score,
        "score_breakdown": breakdown,
        "reject_reasons": [] if decision != "reject" else _build_reject_reasons(breakdown),
        "wait_condition": wait_condition,
    }


def _check_instant_reject(structured_data: dict,
                           signal_direction: str,
                           q_trend_available: bool = True) -> list[str]:
    """即rejectパターンのチェック。該当する理由のリストを返す。"""
    reasons: list[str] = []

    # データ不足チェック
    data_completeness = structured_data.get("data_completeness", {})
    fields_missing = data_completeness.get("fields_missing", [])
    critical_fields = {"rsi_value", "adx_value", "atr_expanding"}
    missing_critical = set(fields_missing) & critical_fields
    if len(fields_missing) >= 3 or missing_critical:
        reasons.append(f"重要データ欠損: {fields_missing}")

    # レンジ中央での順張りチェック
    regime = structured_data.get("regime", {})
    price_structure = structured_data.get("price_structure", {})

    if regime.get("classification") == "range":
        sma20_distance = price_structure.get("sma20_distance_pct")
        if sma20_distance is not None and abs(sma20_distance) <= 0.3:
            # レンジ中央での順張り
            zone_interaction = structured_data.get("zone_interaction", {})
            zone_touch = zone_interaction.get("zone_touch", False)
            fvg_touch = zone_interaction.get("fvg_touch", False)
            if not zone_touch and not fvg_touch:
                reasons.append("レンジ中央での順張り（SMA20乖離±0.3%以内、ゾーン/FVGタッチなし）")

    # Gate 2: Q-trend不一致 かつ bar_close未確認 → 即reject
    # q_trend_availableがFalseの場合（Q-trendデータ未受信）はスキップする
    if q_trend_available:
        momentum = structured_data.get("momentum", {})
        signal_quality = structured_data.get("signal_quality", {})
        trend_aligned = momentum.get("trend_aligned", True)
        bar_close_confirmed = signal_quality.get("bar_close_confirmed", True)
        if not trend_aligned and not bar_close_confirmed:
            reasons.append("Gate2: Q-trend不一致かつbar_close未確認")

    return reasons


def _calculate_regime_score(regime: dict, signal_direction: str) -> tuple[float, str]:
    """レジーム別の基礎スコア計算"""
    classification = regime.get("classification", "range")

    if classification == "trend":
        return SCORING_CONFIG["regime_trend_base"], "regime_trend_base"
    elif classification == "breakout":
        return SCORING_CONFIG["regime_breakout_base"], "regime_breakout_base"
    elif classification == "range":
        return SCORING_CONFIG["regime_range_base"], "regime_range_base"
    else:
        return 0.0, ""


def _calculate_structure_score(
    zone_interaction: dict,
    signal_direction: str,
    trend_aligned: bool = True,
) -> tuple[float, dict]:
    """ゾーン・構造要素のスコア計算"""
    score = 0.0
    breakdown: dict[str, float] = {}

    zone_touch = zone_interaction.get("zone_touch", False)
    zone_direction = zone_interaction.get("zone_direction")
    fvg_touch = zone_interaction.get("fvg_touch", False)
    fvg_direction = zone_interaction.get("fvg_direction")
    liquidity_sweep = zone_interaction.get("liquidity_sweep", False)
    sweep_direction = zone_interaction.get("sweep_direction")

    # ゾーンタッチ（方向一致）: Q-trend整合性で重みを分岐
    if zone_touch and _is_direction_aligned(zone_direction, signal_direction):
        if trend_aligned:
            val = SCORING_CONFIG.get("zone_touch_aligned_with_trend",
                                     SCORING_CONFIG["zone_touch_aligned"])
            breakdown["zone_touch_aligned_with_trend"] = val
        else:
            val = SCORING_CONFIG.get("zone_touch_counter_trend", 0.08)
            breakdown["zone_touch_counter_trend"] = val
        score += val

    # FVGタッチ（方向一致）: Q-trend整合性で重みを分岐
    if fvg_touch and _is_direction_aligned(fvg_direction, signal_direction):
        if trend_aligned:
            val = SCORING_CONFIG.get("fvg_touch_aligned_with_trend",
                                     SCORING_CONFIG["fvg_touch_aligned"])
            breakdown["fvg_touch_aligned_with_trend"] = val
        else:
            val = SCORING_CONFIG.get("fvg_touch_counter_trend", 0.06)
            breakdown["fvg_touch_counter_trend"] = val
        score += val

    # リクイディティスイープ
    if liquidity_sweep and _is_sweep_aligned(sweep_direction, signal_direction):
        val = SCORING_CONFIG["liquidity_sweep"]
        score += val
        breakdown["liquidity_sweep"] = val

        # スイープ + ゾーンタッチのコンボボーナス
        if zone_touch and _is_direction_aligned(zone_direction, signal_direction):
            val = SCORING_CONFIG["sweep_plus_zone"]
            score += val
            breakdown["sweep_plus_zone"] = val

    return score, breakdown


def _calculate_momentum_score(
    momentum: dict, signal_direction: str, has_sweep: bool = False
) -> tuple[float, dict]:
    """モメンタムスコア計算"""
    score = 0.0
    breakdown: dict[str, float] = {}

    # Q-trendとシグナル方向の一致
    trend_aligned = momentum.get("trend_aligned", False)
    if trend_aligned:
        val = SCORING_CONFIG["trend_aligned"]
        score += val
        breakdown["trend_aligned"] = val

    # RSI確認
    rsi_value = momentum.get("rsi_value")
    rsi_zone = momentum.get("rsi_zone", "neutral")
    if rsi_value is not None:
        if signal_direction == "buy" and rsi_zone == "oversold":
            val = SCORING_CONFIG["rsi_confirmation"]
            score += val
            breakdown["rsi_confirmation"] = val
        elif signal_direction == "sell" and rsi_zone == "overbought":
            val = SCORING_CONFIG["rsi_confirmation"]
            score += val
            breakdown["rsi_confirmation"] = val
        elif signal_direction == "buy" and rsi_zone == "overbought":
            val = SCORING_CONFIG["rsi_divergence"]
            score += val
            breakdown["rsi_divergence"] = val
        elif signal_direction == "sell" and rsi_zone == "oversold":
            val = SCORING_CONFIG["rsi_divergence"]
            score += val
            breakdown["rsi_divergence"] = val

    # トレンド逆行チェック（スイープなし）
    # trend_alignedが明示的にFalseかつスイープによる逆張り根拠がない場合のみ減点
    if not trend_aligned and momentum.get("trend_aligned") is not None and not has_sweep:
        val = SCORING_CONFIG["counter_trend_no_sweep"]
        score += val
        breakdown["counter_trend_no_sweep"] = val

    return score, breakdown


def _calculate_signal_quality_score(signal_quality: dict) -> tuple[float, dict]:
    """シグナル品質スコア計算"""
    score = 0.0
    breakdown: dict[str, float] = {}

    # バークローズ確認
    if signal_quality.get("bar_close_confirmed", False):
        val = SCORING_CONFIG["bar_close_confirmed"]
        score += val
        breakdown["bar_close_confirmed"] = val

    # セッション
    session = signal_quality.get("session", "")
    if session == "London_NY":
        val = SCORING_CONFIG["session_london_ny"]
        score += val
        breakdown["session_london_ny"] = val
    elif session == "Tokyo":
        val = SCORING_CONFIG.get("session_tokyo", 0.0)
        if val != 0.0:
            score += val
            breakdown["session_tokyo"] = val
    elif session == "off_hours":
        val = SCORING_CONFIG["session_off_hours"]
        score += val
        breakdown["session_off_hours"] = val

    # TV confidence
    tv_confidence = signal_quality.get("tv_confidence")
    if tv_confidence is not None:
        if tv_confidence > 0.7:
            val = SCORING_CONFIG["tv_confidence_high"]
            score += val
            breakdown["tv_confidence_high"] = val
        elif tv_confidence < 0.3:
            val = SCORING_CONFIG["tv_confidence_low"]
            score += val
            breakdown["tv_confidence_low"] = val

    # pattern_similarity: Lorentzian v2の新フィールド
    # None の場合は旧バージョンのアラート（win_rateのみ）→ 加減点なし
    pattern_similarity = signal_quality.get("pattern_similarity")
    if pattern_similarity is not None:
        if pattern_similarity > 0.70:
            val = SCORING_CONFIG["pattern_similarity_high"]
            score += val
            breakdown["pattern_similarity_high"] = val
        elif pattern_similarity < 0.30:
            val = SCORING_CONFIG["pattern_similarity_low"]
            score += val
            breakdown["pattern_similarity_low"] = val
    # else: 0.30〜0.70の中間域は加減点なし（ニュートラル）
    # Noneの場合（旧バージョンアラート）も加減点なし

    # duplicate_warning: 同方向シグナルが30分以内に3件以上発火した場合のペナルティ
    # NOTE: このフィールドはzone_touch cooldown実装後（バッチ3）に実際に発火する
    if signal_quality.get("duplicate_warning", False):
        val = -0.15
        score += val
        breakdown["duplicate_warning"] = val

    return score, breakdown


def _is_direction_aligned(zone_direction: str | None, signal_direction: str) -> bool:
    """ゾーン方向がシグナル方向と一致するかチェック"""
    if zone_direction is None:
        return False
    # demand zone → buy が一致、supply zone → sell が一致
    if zone_direction == "demand" and signal_direction == "buy":
        return True
    if zone_direction == "supply" and signal_direction == "sell":
        return True
    # bullish fvg → buy、bearish fvg → sell
    if zone_direction == "bullish" and signal_direction == "buy":
        return True
    if zone_direction == "bearish" and signal_direction == "sell":
        return True
    return False


def _is_sweep_aligned(sweep_direction: str | None, signal_direction: str) -> bool:
    """スイープ方向がシグナル方向と逆張り（反転エントリー）として一致するかチェック"""
    if sweep_direction is None:
        return False
    # sell_side sweep（売り側の流動性を狩った）→ 売り圧力が解消 → buy方向が正しい逆張り
    # buy_side sweep（買い側の流動性を狩った）→ 買い圧力が解消 → sell方向が正しい逆張り
    if sweep_direction == "sell_side" and signal_direction == "buy":
        return True
    if sweep_direction == "buy_side" and signal_direction == "sell":
        return True
    return False


def _determine_wait_condition(structured_data: dict, breakdown: dict) -> str:
    """wait判定時の昇格条件を決定する"""
    regime = structured_data.get("regime", {})
    zone_interaction = structured_data.get("zone_interaction", {})

    if not zone_interaction.get("zone_touch") and not zone_interaction.get("fvg_touch"):
        return "structure_needed: ゾーンまたはFVGタッチを待つ"

    if not structured_data.get("signal_quality", {}).get("bar_close_confirmed"):
        return "next_bar: バークローズ確認を待つ"

    return "cooldown: 追加確認を待つ"


def _build_reject_reasons(breakdown: dict) -> list[str]:
    """breakdownからreject理由を生成する"""
    reasons = []
    negative_items = {k: v for k, v in breakdown.items() if v < 0}
    if negative_items:
        for key, val in negative_items.items():
            reasons.append(f"{key}: {val:+.2f}")
    if not reasons:
        reasons.append("スコア不足（閾値未達）")
    return reasons
