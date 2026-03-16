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
NEWS_FILTER_FAIL_SAFE = SYSTEM_CONFIG.get("news_filter_fail_safe", True)

# カレンダーAPI対応チェック（MT5ビルドによっては未実装）
MT5_CALENDAR_AVAILABLE = (
    MT5_AVAILABLE
    and hasattr(mt5, "calendar_value_get")
    and hasattr(mt5, "calendar_event_by_id")
)
if MT5_AVAILABLE and not MT5_CALENDAR_AVAILABLE:
    logger.warning(
        "MetaTrader5 (v%s) にカレンダーAPIが含まれていません。"
        "ニュースフィルターは無効化されます。"
        "MT5端末 5.0.37 以降ビルドへのアップデートを検討してください。",
        getattr(mt5, "__version__", "unknown"),
    )


def check_news_filter(symbol: str = "XAUUSD") -> dict:
    """
    ニュースフィルターを実行する。

    Returns:
        {
            "blocked":           bool,
            "reason":            str,
            "resumes_at":        str | None   # ISO8601 UTC
            "fail_safe_triggered": bool       # フェイルセーフ発動時 True
        }
    """
    if not NEWS_FILTER_ENABLED:
        return {"blocked": False, "reason": "ニュースフィルター無効",
                "resumes_at": None, "fail_safe_triggered": False}

    if not MT5_AVAILABLE:
        logger.warning("MT5未インストール - ニュースフィルター: fail_safe=%s", NEWS_FILTER_FAIL_SAFE)
        if NEWS_FILTER_FAIL_SAFE:
            reason = "MT5未インストール（安全のためブロック）"
            log_event("news_filter_fail_safe", reason, level="WARNING")
            return {"blocked": True, "reason": reason,
                    "resumes_at": None, "fail_safe_triggered": True}
        return {"blocked": False, "reason": "MT5未インストール",
                "resumes_at": None, "fail_safe_triggered": False}

    # カレンダーAPI非対応ビルドはフィルター機能を持たないため通過扱い
    if not MT5_CALENDAR_AVAILABLE:
        return {"blocked": False,
                "reason": "MT5カレンダーAPI非対応ビルド（フィルター無効）",
                "resumes_at": None, "fail_safe_triggered": False}

    now = datetime.now(timezone.utc)
    look_ahead = now + timedelta(hours=2)

    try:
        # calendar_value_get で時間範囲内のイベント値を取得（正しい MT5 API）
        values = mt5.calendar_value_get(now, look_ahead)
    except Exception as e:
        msg = f"MT5カレンダーAPI取得失敗: {e}"
        logger.warning(msg)
        log_event("news_filter_api_error", msg, level="WARNING")
        if NEWS_FILTER_FAIL_SAFE:
            reason = "カレンダー取得失敗（安全のためブロック）"
            log_event("news_filter_fail_safe", reason, level="WARNING")
            return {"blocked": True, "reason": reason,
                    "resumes_at": None, "fail_safe_triggered": True}
        return {"blocked": False, "reason": msg,
                "resumes_at": None, "fail_safe_triggered": False}

    if values is None:
        if NEWS_FILTER_FAIL_SAFE:
            reason = "カレンダー取得失敗（安全のためブロック）"
            log_event("news_filter_fail_safe", reason, level="WARNING")
            return {"blocked": True, "reason": reason,
                    "resumes_at": None, "fail_safe_triggered": True}
        return {"blocked": False, "reason": "カレンダーイベントなし",
                "resumes_at": None, "fail_safe_triggered": False}

    for value in values:
        # 発表時刻
        event_time_ts = getattr(value, "time", None)
        if event_time_ts is None:
            continue
        try:
            event_dt = datetime.fromtimestamp(event_time_ts, tz=timezone.utc)
        except Exception:
            continue

        # ± 30分チェック（diff_min: 正=発表前、負=発表後）
        diff_min = (event_dt - now).total_seconds() / 60.0
        if not (-BLOCK_AFTER_MIN <= diff_min <= BLOCK_BEFORE_MIN):
            continue

        # イベント定義を取得して通貨・重要度を確認
        event_id = getattr(value, "event_id", None)
        if event_id is None:
            continue
        try:
            event_def = mt5.calendar_event_by_id(event_id)
        except Exception:
            continue
        if event_def is None:
            continue

        # 通貨フィルター
        currency = getattr(event_def, "currency", None)
        if currency not in TARGET_CURRENCIES:
            continue

        # 重要度フィルター（2以上）
        importance = getattr(event_def, "importance", 0)
        if importance < MIN_IMPORTANCE:
            continue

        resumes_at = (event_dt + timedelta(minutes=BLOCK_AFTER_MIN)).isoformat()

        event_name = getattr(event_def, "name", "不明")
        side = "発表前" if diff_min >= 0 else "発表後"
        abs_min = int(abs(diff_min))
        reason = f"指標ブロック: {event_name} ({side}{abs_min}分)"

        log_event("news_filter_block", detail=reason)
        logger.info("🚫 %s → エントリー拒否 / 再開予定: %s", reason, resumes_at)

        return {
            "blocked":             True,
            "reason":              reason,
            "resumes_at":          resumes_at,
            "fail_safe_triggered": False,
        }

    return {"blocked": False, "reason": "ニュースフィルター通過",
            "resumes_at": None, "fail_safe_triggered": False}


# ── 固定ブラックアウト時刻リスト（UTC） ──────────────────────────────────────
# XAUUSDに影響が大きい高インパクト指標の発表時刻（UTC）
# 毎週固定のもの + 月次固定のもの。年次イベントは別途手動更新。
#
# 形式: (月, 日, 時, 分) は月次イベント用
# 毎週固定: (weekday, 時, 分)  weekday: 0=月, 1=火, ..., 4=金
#
# ※ 正確な日時はForex Factory等で毎月確認して更新すること
_WEEKLY_BLACKOUTS = [
    # 米・新規失業保険申請件数（毎週木曜 12:30 UTC）
    (3, 12, 30),
]
_BLACKOUT_MINUTES = 30  # 発表前後何分をブロックするか
def is_news_blackout(dt: datetime | None = None) -> bool:
    """
    指定日時（省略時は現在UTC）が固定ブラックアウト時間帯かどうかを返す。

    Pine Script の news_nearby=false を補完するためのフォールバック。
    外部APIが利用できない環境でも最低限の防御を提供する。

    Returns:
        True  = ブラックアウト中（エントリー禁止）
        False = 通常時
    """
    from datetime import timezone
    if dt is None:
        dt = datetime.now(timezone.utc)

    weekday = dt.weekday()  # 0=月〜4=金
    current_minutes = dt.hour * 60 + dt.minute

    for (target_weekday, hour, minute) in _WEEKLY_BLACKOUTS:
        if weekday != target_weekday:
            continue
        center_minutes = hour * 60 + minute
        if abs(current_minutes - center_minutes) <= _BLACKOUT_MINUTES:
            return True

    return False
