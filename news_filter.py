"""
news_filter.py - ニュースフィルター（v2追加）
AI Trading System v2.0

MT5カレンダーAPIを使用して重要経済指標の前後30分をブロックする。
サーバータイム基準で動作し、サマータイムを自動吸収する。
"""

import logging
from datetime import datetime, timezone, timedelta

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config import SYSTEM_CONFIG
from logger_module import log_event

logger = logging.getLogger(__name__)

BLOCK_BEFORE_MIN     = SYSTEM_CONFIG["news_block_before_min"]     # 30
BLOCK_AFTER_MIN      = SYSTEM_CONFIG["news_block_after_min"]      # 30
TARGET_CURRENCIES    = set(SYSTEM_CONFIG["news_target_currencies"])  # USD, EUR
MIN_IMPORTANCE       = SYSTEM_CONFIG["news_min_importance"]        # 2
NEWS_FILTER_ENABLED  = SYSTEM_CONFIG["news_filter_enabled"]


def check_news_filter(symbol: str = "XAUUSD") -> dict:
    """
    ニュースフィルターを実行する。

    Returns:
        {
            "blocked":    bool,
            "reason":     str,
            "resumes_at": str | None   # ISO8601 UTC
        }
    """
    if not NEWS_FILTER_ENABLED:
        return {"blocked": False, "reason": "ニュースフィルター無効", "resumes_at": None}

    if not MT5_AVAILABLE:
        logger.warning("MT5未インストール - ニュースフィルタースキップ")
        return {"blocked": False, "reason": "MT5未インストール", "resumes_at": None}

    now = datetime.now(timezone.utc)
    look_ahead = now + timedelta(hours=2)

    try:
        events = mt5.calendar_event_get(now, look_ahead)
    except Exception as e:
        msg = f"MT5カレンダーAPI取得失敗: {e}"
        logger.warning(msg)
        log_event("news_filter_api_error", msg, level="WARNING")
        # 取得失敗はエントリーを許可（過剰ブロック防止）
        return {"blocked": False, "reason": msg, "resumes_at": None}

    if events is None:
        return {"blocked": False, "reason": "カレンダーイベントなし", "resumes_at": None}

    for event in events:
        # 通貨フィルター
        currency = getattr(event, "currency", None)
        if currency not in TARGET_CURRENCIES:
            continue

        # 重要度フィルター（2以上）
        importance = getattr(event, "importance", 0)
        if importance < MIN_IMPORTANCE:
            continue

        # 発表時刻
        event_time_ts = getattr(event, "time", None)
        if event_time_ts is None:
            continue
        try:
            event_dt = datetime.fromtimestamp(event_time_ts, tz=timezone.utc)
        except Exception:
            continue

        # ± 30分チェック（diff_min: 正=発表前、負=発表後）
        diff_min = (event_dt - now).total_seconds() / 60.0

        if -BLOCK_AFTER_MIN <= diff_min <= BLOCK_BEFORE_MIN:
            # 発表後の場合は resumes_at = event_dt + 30min
            if diff_min < 0:
                resumes_at = (event_dt + timedelta(minutes=BLOCK_AFTER_MIN)).isoformat()
            else:
                resumes_at = (event_dt + timedelta(minutes=BLOCK_AFTER_MIN)).isoformat()

            event_name = getattr(event, "name", "不明")
            side = "発表前" if diff_min >= 0 else "発表後"
            abs_min = int(abs(diff_min))
            reason = f"指標ブロック: {event_name} ({side}{abs_min}分)"

            log_event("news_filter_block", detail=reason)
            logger.info("🚫 %s → エントリー拒否 / 再開予定: %s", reason, resumes_at)

            return {
                "blocked":    True,
                "reason":     reason,
                "resumes_at": resumes_at,
            }

    return {"blocked": False, "reason": "ニュースフィルター通過", "resumes_at": None}
