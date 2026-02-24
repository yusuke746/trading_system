"""
validation.py - シグナルバリデーション・正規化
AI Trading System v2.0
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

VALID_SIGNAL_TYPES = {"entry_trigger", "structure"}
VALID_EVENTS = {
    "prediction_signal",
    "zone_retrace_touch",
    "new_zone_confirmed",
    "fvg_touch",
    "liquidity_sweep",
}
VALID_DIRECTIONS = {"buy", "sell"}

# GOLD銘柄の別名マッピング（XMTrading のシンボル名は GOLD）
SYMBOL_MAP = {
    "GOLD":   "GOLD",
    "XAUUSD": "GOLD",
    "gold":   "GOLD",
    "xauusd": "GOLD",
}


def normalize_symbol(raw: str) -> str:
    return SYMBOL_MAP.get(raw, raw.upper())


def validate_and_normalize(raw: dict) -> dict | None:
    """
    受信シグナルをバリデーション・正規化する。
    不正な場合は None を返す。
    """
    # ── 必須フィールド ──
    required = ["signal_type", "event", "price"]
    for field in required:
        if field not in raw:
            logger.warning("シグナル欠損フィールド: %s | raw=%s", field, raw)
            return None

    # ── signal_type バリデーション ──
    signal_type = raw.get("signal_type", "").strip().lower()
    if signal_type not in VALID_SIGNAL_TYPES:
        logger.warning("不明なsignal_type: %s", signal_type)
        return None

    # ── event バリデーション ──
    event = raw.get("event", "").strip().lower()
    if event not in VALID_EVENTS:
        logger.warning("不明なevent: %s", event)
        return None

    # ── direction 正規化（side / action → direction）──
    direction = (raw.get("direction") or raw.get("side") or
                 raw.get("action") or "").strip().lower()
    if signal_type == "entry_trigger" and direction not in VALID_DIRECTIONS:
        logger.warning("不明なdirection: %s", direction)
        return None

    # ── 数値変換 ──
    try:
        price = float(raw["price"])
    except (ValueError, TypeError):
        logger.warning("price変換失敗: %s", raw.get("price"))
        return None

    try:
        tf = int(raw["tf"]) if "tf" in raw else None
    except (ValueError, TypeError):
        tf = None

    # ── 正規化シグナル構築 ──
    normalized = {
        "symbol":      normalize_symbol(raw.get("symbol", "GOLD")),
        "price":       price,
        "tf":          tf,
        "direction":   direction,
        "signal_type": signal_type,
        "event":       event,
        "source":      raw.get("source", ""),
        "strength":    raw.get("strength", ""),
        "comment":     raw.get("comment", ""),
        "confirmed":   raw.get("confirmed", ""),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    # time フィールドが存在すれば保持
    if "time" in raw:
        normalized["time"] = raw["time"]

    return normalized
