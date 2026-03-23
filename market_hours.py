"""
market_hours.py - 市場クローズ判定（XMサーバータイム基準）
AI Trading System v2.0

XMサーバータイムはEET（UTC+2、サマータイム時UTC+3）。
mt5.symbol_info()のtrade_modeで最終判断する。
"""

import logging
from datetime import datetime, timezone, time

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    class _FakeMT5:
        SYMBOL_TRADE_MODE_FULL = 4
    mt5 = _FakeMT5()

from config import SYSTEM_CONFIG

logger = logging.getLogger(__name__)

LIMIT_CANCEL_H = SYSTEM_CONFIG["limit_cancel_start_h"]  # 23
LIMIT_CANCEL_M = SYSTEM_CONFIG["limit_cancel_start_m"]  # 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_weekend(symbol: str = "XAUUSD") -> bool:
    """
    週末クローズ判定（UTC基準）。
    GOLD#(XAUUSD)の取引時間:
      - 週末クローズ: 金曜 22:00 UTC
      - 週明け開場:   日曜 22:00 UTC（= 月曜 00:00 JST）
    """
    now = _utc_now()
    wd  = now.weekday()   # Mon=0 … Sun=6

    if wd == 5:                     # Saturday — all day closed
        return True
    if wd == 6:                     # Sunday — closed until 22:00 UTC
        return now.hour < 22
    if wd == 4 and now.hour >= 22:  # Friday 22:00+ — closed
        return True
    return False



def is_limit_cancel_zone() -> bool:
    """23:30 UTC 以降、未約定指値の自動キャンセル警戒ゾーン"""
    now = _utc_now()
    return now.time() >= time(LIMIT_CANCEL_H, LIMIT_CANCEL_M)


def get_current_session() -> dict:
    """
    現在の市場セッションを UTC 時刻で判定して返す。

    セッション区分（UTC基準）:
        Asia          00:00 〜 07:00   低ボラ・レンジ傾向
        London        07:00 〜 12:00   ボラ上昇・トレンド発生多
        London_NY     12:00 〜 16:00   最高ボラ・GOLDの主戦場
        NY            16:00 〜 21:00   ボラ中・NY引けに向け縮小
        Off_hours     21:00 〜 00:00   ボラ低下・クローズ接近

    Returns:
        {
            "session":        str,   # "Asia" | "London" | "London_NY" | "NY" | "Off_hours"
            "volatility":     str,   # "low" | "medium" | "high" | "very_high"
            "description":    str,
        }
    """
    now_h = _utc_now().hour

    if 0 <= now_h < 7:
        return {"session": "Asia",      "volatility": "low",
                "description": "アジア時間（低ボラ・レンジ）"}
    if 7 <= now_h < 12:
        return {"session": "London",    "volatility": "high",
                "description": "ロンドン時間（ボラ上昇・トレンド発生多）"}
    if 12 <= now_h < 16:
        return {"session": "London_NY", "volatility": "very_high",
                "description": "ロンドン・NYオーバーラップ（GOLD最高ボラ帯）"}
    if 16 <= now_h < 21:
        return {"session": "NY",        "volatility": "medium",
                "description": "NY時間（ボラ中・引けに向け縮小）"}
    # 21:00〜24:00
    return {"session": "Off_hours",     "volatility": "low",
            "description": "オフアワー（低ボラ・クローズ接近）"}


def is_market_open(symbol: str = "XAUUSD") -> dict:
    """
    MT5のtrade_modeで最終判断。
    Returns: {"open": bool, "reason": str}
    """
    if not MT5_AVAILABLE:
        return {"open": True, "reason": "MT5未インストール（テストモード）"}

    info = mt5.symbol_info(symbol)
    if info is None:
        return {"open": False, "reason": f"symbol_info取得失敗: {symbol}"}

    if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        return {"open": False,
                "reason": f"trade_mode={info.trade_mode} (FULL以外)"}

    return {"open": True, "reason": "取引可能"}


def full_market_check(symbol: str = "XAUUSD") -> dict:
    """
    週末・MT5 trade_mode を一括チェック。
    Returns: {"ok": bool, "reason": str}
    """
    if is_weekend(symbol):
        return {"ok": False, "reason": "週末クローズ"}
    m = is_market_open(symbol)
    if not m["open"]:
        return {"ok": False, "reason": m["reason"]}
    return {"ok": True, "reason": "市場オープン"}
