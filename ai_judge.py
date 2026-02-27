"""
ai_judge.py - GPT-4o-mini API呼び出し
AI Trading System v2.0
"""

import json
import logging
import os
from openai import OpenAI
from dotenv import load_dotenv
from config import SYSTEM_CONFIG

load_dotenv()

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未設定")
        _client = OpenAI(api_key=api_key)
    return _client


def ask_ai(messages: list[dict]) -> dict:
    """
    GPT-4o-miniにメッセージを送りAI判定結果を返す。
    
    Returns:
        AI判定dict（decision / confidence / ev_score / ...）
        エラー時は {"decision": "reject", "reason": "エラー内容", ...}
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2048,
        )
        content = response.choices[0].message.content
        result  = json.loads(content)
        logger.info(
            "🤖 AI判定: decision=%s confidence=%.2f ev_score=%.2f",
            result.get("decision"),
            result.get("confidence", 0),
            result.get("ev_score",   0),
        )
        return result

    except Exception as e:
        logger.error("AI API呼び出しエラー: %s", e, exc_info=True)
        from notifier import notify_ai_api_error
        notify_ai_api_error()
        return {
            "market_regime":  "Range",
            "regime_reason":  "AI API エラー",
            "decision":       "reject",
            "confidence":     0.0,
            "ev_score":       0.0,
            "reason":         f"AI API エラー: {e}",
            "risk_note":      None,
            "wait_condition": None,
        }


def should_execute(ai_result: dict) -> bool:
    """
    執行判定（2軸）:
    - decision == 'approve'
    - confidence >= 0.70
    - ev_score  >= 0.30
    """
    return (
        ai_result.get("decision")             == "approve"
        and ai_result.get("confidence",   0)  >= SYSTEM_CONFIG["min_confidence"]
        and ai_result.get("ev_score",     0)  >= SYSTEM_CONFIG["min_ev_score"]
    )
