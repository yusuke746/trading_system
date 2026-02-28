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

    # tv_confidence: TradingViewのLorentzianが計算したsmooth_confidence（0.0〜1.0）
    try:
        tv_confidence = float(raw["confidence"]) if "confidence" in raw else None
        if tv_confidence is not None and not (0.0 <= tv_confidence <= 1.0):
            logger.warning("tv_confidence範囲外: %.4f → Noneにクランプ", tv_confidence)
            tv_confidence = None
    except (ValueError, TypeError):
        tv_confidence = None

    # tv_win_rate: 旧フィールド。Lorentzian v2では送信されなくなったため
    # 後方互換のためフィールドが存在すれば受け取るが、なければNone
    try:
        tv_win_rate_raw = float(raw["win_rate"]) if "win_rate" in raw else None
        if tv_win_rate_raw is not None and not (0.0 <= tv_win_rate_raw <= 100.0):
            logger.warning("tv_win_rate範囲外: %.2f → Noneにクランプ", tv_win_rate_raw)
            tv_win_rate_raw = None
        tv_win_rate = round(tv_win_rate_raw / 100.0, 3) if tv_win_rate_raw is not None else None
    except (ValueError, TypeError):
        tv_win_rate = None

    # pattern_similarity: Lorentzian v2で追加。avg_distanceの反転正規化値（0.0〜1.0）
    # 高いほど過去パターンと高類似 = 予測の信頼性が高い
    try:
        pattern_similarity = float(raw["pattern_similarity"]) if "pattern_similarity" in raw else None
        if pattern_similarity is not None and not (0.0 <= pattern_similarity <= 1.0):
            logger.warning("pattern_similarity範囲外: %.4f → Noneにクランプ", pattern_similarity)
            pattern_similarity = None
    except (ValueError, TypeError):
        pattern_similarity = None

    # avg_distance: Lorentzianの生の平均ローレンツィアン距離（ログ記録用）
    try:
        avg_distance = float(raw["avg_distance"]) if "avg_distance" in raw else None
    except (ValueError, TypeError):
        avg_distance = None

    normalized = {
        "symbol":        normalize_symbol(raw.get("symbol", "GOLD")),
        "price":         price,
        "tf":            tf,
        "direction":     direction,
        "signal_type":   signal_type,
        "event":         event,
        "source":        raw.get("source", ""),
        "strength":      raw.get("strength", ""),
        "comment":       raw.get("comment", ""),
        "confirmed":     raw.get("confirmed", ""),
        "tv_confidence":     tv_confidence,
        "tv_win_rate":       tv_win_rate,
        "pattern_similarity": pattern_similarity,
        "avg_distance":      avg_distance,
        "received_at":       datetime.now(timezone.utc).isoformat(),
    }

    # time フィールドが存在すれば保持
    if "time" in raw:
        normalized["time"] = raw["time"]

    return normalized
