"""
ai_judge.py - AI判定パイプライン（v3.0）
AI Trading System v3.0

v2.0: GPT-4o-mini に直接 approve/reject 判定をさせていた
v3.0: LLM構造化 → スコアリングエンジン のパイプラインで判定

後方互換のため、既存の返却形式（decision/confidence/ev_score）を維持する。
"""

import json
import logging
from config import SYSTEM_CONFIG
from data_structurer import structurize
from scoring_engine import calculate_score

logger = logging.getLogger(__name__)


def ask_ai(messages: list[dict], context: dict | None = None,
           signal_direction: str | None = None) -> dict:
    """
    後方互換wrapper。
    内部では llm_structurer → scoring_engine のパイプラインを実行し、
    既存形式の dict を返す。

    Args:
        messages: 旧形式のプロンプトメッセージ（v3.0では使用しないが互換性維持）
        context: context_builder.py のコンテキスト（v3.0で追加）
        signal_direction: エントリー方向（v3.0で追加）

    Returns:
        AI判定dict（decision / confidence / ev_score / ...）
        エラー時は {"decision": "reject", "reason": "エラー内容", ...}
    """
    try:
        # v3.0: context が渡された場合は新パイプラインを使用
        if context is not None:
            structured = structurize(context)
            direction = signal_direction or _extract_direction(context)
            alert_dict = _structured_to_alert_dict(structured, direction)
            score_result = calculate_score(alert_dict)

            result = {
                "market_regime": structured.get("regime", {}).get("classification", "range"),
                "regime_reason": _build_regime_reason(structured),
                "decision": score_result["decision"],
                "confidence": _score_to_confidence(score_result["score"]),
                "ev_score": score_result["score"],
                "reason": _build_reason(score_result),
                "risk_note": _build_risk_note(score_result),
                "wait_condition": score_result.get("wait_condition"),
                "wait_scope": _determine_wait_scope(score_result),
                "score_breakdown": score_result["score_breakdown"],
                "structured_data": structured,
            }

            logger.info(
                "🤖 AI判定(v3): decision=%s score=%.2f regime=%s",
                result["decision"],
                result["ev_score"],
                result["market_regime"],
            )
            return result

        # v2.0 フォールバック: context が None の場合は旧式API呼び出し
        # （revaluator.py 等の互換性のため）
        return _legacy_ask_ai(messages)

    except Exception as e:
        logger.error("AI判定エラー: %s", e, exc_info=True)
        from notifier import notify_ai_api_error
        notify_ai_api_error()
        return {
            "market_regime": "range",
            "regime_reason": "AI API エラー",
            "decision": "reject",
            "confidence": 0.0,
            "ev_score": 0.0,
            "reason": f"AI判定エラー: {e}",
            "risk_note": None,
            "wait_condition": None,
            "score_breakdown": {},
        }


def _legacy_ask_ai(messages: list[dict]) -> dict:
    """
    v2.0互換のレガシーAPI呼び出し。
    revaluator.py が build_prompt() + ask_ai(messages) を呼ぶケース用。

    v3.0ではLLM構造化 + スコアリングを実行する。
    messagesからcontextを復元してパイプライン処理する。
    """
    import os
    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv()

    try:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未設定")

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
        )
        content = response.choices[0].message.content
        result = json.loads(content)

        # LLM出力を構造化データとして解釈し、スコアリング
        # （revaluator経由の場合、LLM出力は構造化フォーマットで返る）
        if "regime" in result and "zone_interaction" in result:
            # v3.0 構造化出力 → スコアリングエンジンで判定
            from scoring_engine import calculate_score
            direction = _extract_direction_from_messages(messages)
            alert_dict = _structured_to_alert_dict(result, direction)
            score_result = calculate_score(alert_dict)
            return {
                "market_regime": result.get("regime", {}).get("classification", "range"),
                "regime_reason": _build_regime_reason(result),
                "decision": score_result["decision"],
                "confidence": _score_to_confidence(score_result["score"]),
                "ev_score": score_result["score"],
                "reason": _build_reason(score_result),
                "risk_note": _build_risk_note(score_result),
                "wait_condition": score_result.get("wait_condition"),
                "wait_scope": _determine_wait_scope(score_result),
                "score_breakdown": score_result["score_breakdown"],
            }

        # 旧形式のLLM出力の場合はそのまま返す
        logger.info(
            "🤖 AI判定(legacy): decision=%s confidence=%.2f ev_score=%.2f",
            result.get("decision"),
            result.get("confidence", 0),
            result.get("ev_score", 0),
        )
        return result

    except Exception as e:
        logger.error("Legacy AI API呼び出しエラー: %s", e, exc_info=True)
        from notifier import notify_ai_api_error
        notify_ai_api_error()
        return {
            "market_regime": "range",
            "regime_reason": "AI API エラー",
            "decision": "reject",
            "confidence": 0.0,
            "ev_score": 0.0,
            "reason": f"AI API エラー: {e}",
            "risk_note": None,
            "wait_condition": None,
        }


def should_execute(ai_result: dict) -> bool:
    """
    執行判定（v3.0）:
    - decision == 'approve'
    - confidence >= min_confidence
    - ev_score >= min_ev_score
    """
    return (
        ai_result.get("decision") == "approve"
        and ai_result.get("confidence", 0) >= SYSTEM_CONFIG["min_confidence"]
        and ai_result.get("ev_score", 0) >= SYSTEM_CONFIG["min_ev_score"]
    )


def _score_to_confidence(score: float) -> float:
    """スコアをconfidence（0.0〜1.0）にマッピングする"""
    if score <= 0:
        return 0.0
    if score >= 1.0:
        return 1.0
    # スコア0.30でconfidence 0.70、スコア0.60でconfidence 0.90
    confidence = 0.5 + score * 0.667
    return round(min(1.0, max(0.0, confidence)), 3)


def _build_reason(score_result: dict) -> str:
    """スコア結果から判定理由文を生成する"""
    decision = score_result["decision"]
    score = score_result["score"]
    breakdown = score_result.get("score_breakdown", {})

    if decision == "reject":
        reasons = score_result.get("reject_reasons", [])
        if reasons:
            return f"スコア不足 ({score:.2f}): " + "; ".join(reasons[:3])
        return f"スコア不足 ({score:.2f})"

    if decision == "wait":
        condition = score_result.get("wait_condition", "追加確認待ち")
        return f"条件部分一致 (score={score:.2f}): {condition}"

    # approve
    top_factors = sorted(
        [(k, v) for k, v in breakdown.items() if v > 0],
        key=lambda x: x[1],
        reverse=True,
    )[:3]
    factor_text = ", ".join(f"{k}({v:+.2f})" for k, v in top_factors)
    return f"スコア承認 ({score:.2f}): {factor_text}"


def _build_risk_note(score_result: dict) -> str | None:
    """リスクノートを生成する"""
    breakdown = score_result.get("score_breakdown", {})
    negatives = {k: v for k, v in breakdown.items() if v < 0}
    if not negatives:
        return None
    items = [f"{k}({v:+.2f})" for k, v in negatives.items()]
    return "リスク要因: " + ", ".join(items)


def _build_regime_reason(structured: dict) -> str:
    """レジーム判定理由を生成する"""
    regime = structured.get("regime", {})
    classification = regime.get("classification", "unknown")
    adx = regime.get("adx_value")
    atr_expanding = regime.get("atr_expanding", False)

    parts = [f"regime={classification}"]
    if adx is not None:
        parts.append(f"ADX={adx:.1f}")
    if atr_expanding:
        parts.append("ATR拡大中")
    return " ".join(parts)


def _determine_wait_scope(score_result: dict) -> str | None:
    """wait判定時のスコープを決定する"""
    if score_result["decision"] != "wait":
        return None
    condition = score_result.get("wait_condition", "")
    if "structure" in condition:
        return "structure_needed"
    if "next_bar" in condition:
        return "next_bar"
    return "cooldown"


def _extract_direction(context: dict) -> str:
    """contextからシグナル方向を抽出する"""
    entry_signals = context.get("entry_signals", [])
    if entry_signals:
        return entry_signals[0].get("direction", "buy")
    return "buy"


def _structured_to_alert_dict(structured: dict, direction: str) -> dict:
    """
    旧 structured 形式（llm_structurer 出力）を
    Pine Script フラット JSON 形式へ変換する（ai_judge.py レガシーパス用）。

    h1_adx は旧フォーマットに存在しないため、
    adx_value を下限 25 でクリップして代用する。
    """
    regime_map = {"trend": "TREND", "breakout": "BREAKOUT",
                  "range": "RANGE", "reversal": "REVERSAL"}
    regime_raw = structured.get("regime", {}).get("classification", "range")
    regime     = regime_map.get(regime_raw, "RANGE")

    adx    = float(structured.get("regime", {}).get("adx_value") or 25.0)
    zone   = structured.get("zone_interaction", {})
    mom    = structured.get("momentum", {})
    sq     = structured.get("signal_quality", {})

    zone_dir   = zone.get("zone_direction", "")
    fvg_dir    = zone.get("fvg_direction", "")
    zone_touch = zone.get("zone_touch", False)
    fvg_touch  = zone.get("fvg_touch", False)

    zone_aligned = zone_touch and (
        (zone_dir == "demand" and direction == "buy") or
        (zone_dir == "supply" and direction == "sell")
    )
    fvg_aligned = fvg_touch and (
        (fvg_dir == "bullish" and direction == "buy") or
        (fvg_dir == "bearish" and direction == "sell")
    )

    session_map = {
        "London_NY": "london_ny", "London": "london",
        "NY": "ny", "Tokyo": "tokyo", "off_hours": "off",
    }
    session = session_map.get(sq.get("session", ""), "london")

    return {
        "regime":            regime,
        "direction":         direction,
        "h1_direction":      "bull" if direction == "buy" else "bear",
        "h1_adx":            max(adx, 25.0),   # 旧パスは h1_adx 非保有のため下限 25 で代用
        "m15_adx":           adx,
        "m15_adx_drop":      0.0,
        "atr_ratio":         1.2,
        "choch_confirmed":   sq.get("bar_close_confirmed", True),
        "fvg_hit":           fvg_touch,
        "fvg_aligned":       fvg_aligned,
        "zone_hit":          zone_touch,
        "zone_aligned":      zone_aligned,
        "rsi_divergence":    False,
        "sweep_detected":    zone.get("liquidity_sweep", False),
        "candle_pattern":    "none",
        "session":           session,
        "rsi_trend_aligned": mom.get("trend_aligned", True),
        "rsi_value":         float(mom.get("rsi_value") or 50.0),
        "news_nearby":       False,
    }


def _extract_direction_from_messages(messages: list[dict]) -> str:
    """messagesからシグナル方向を抽出する（レガシー互換）"""
    for msg in messages:
        content = msg.get("content", "")
        if "direction" in content:
            if '"buy"' in content or "'buy'" in content or "direction: buy" in content.lower():
                return "buy"
            if '"sell"' in content or "'sell'" in content or "direction: sell" in content.lower():
                return "sell"
    return "buy"
