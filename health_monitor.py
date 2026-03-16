"""
health_monitor.py - MT5接続監視・自動再接続
AI Trading System v2.0

60秒ごとにMT5接続を確認し、切断時はLINE通知+自動再接続（3回・10秒間隔）。
"""

import logging
import os
import threading
import time
from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config import SYSTEM_CONFIG
from logger_module import log_event
import discord_notifier

load_dotenv()

logger = logging.getLogger(__name__)

HEALTH_CHECK_INTERVAL = SYSTEM_CONFIG["health_check_interval_sec"]   # 60秒
RECONNECT_RETRIES     = 3
RECONNECT_INTERVAL    = 10


def _get_mt5_credentials():
    return (
        int(os.getenv("MT5_LOGIN",    "0")),
        os.getenv("MT5_PASSWORD", ""),
        os.getenv("MT5_SERVER",   ""),
    )


def init_mt5() -> bool:
    """MT5接続を初期化する（最大3回リトライ）"""
    if not MT5_AVAILABLE:
        logger.warning("MetaTrader5パッケージ未インストール - スキップ")
        return False

    login, password, server = _get_mt5_credentials()
    symbol = SYSTEM_CONFIG["symbol"]

    for attempt in range(1, RECONNECT_RETRIES + 1):
        try:
            if mt5.initialize(login=login, password=password, server=server):
                logger.info("✅ MT5接続成功 (試行%d)", attempt)
                # 接続直後はターミナルが内部準備中のことがある。少し待ってからシンボルを購読
                time.sleep(2)
                for sel_attempt in range(1, 4):
                    if mt5.symbol_select(symbol, True):
                        logger.info("✅ symbol_select成功: %s", symbol)
                        break
                    logger.warning("symbol_select試行%d/3失敗: %s, last_error=%s",
                                   sel_attempt, symbol, mt5.last_error())
                    time.sleep(2)
                else:
                    logger.error("❌ symbol_select最終失敗: %s — データ取得が失敗し続ける可能性あり", symbol)
                return True
        except Exception as e:
            logger.error("MT5 initialize例外: %s", e)

        logger.warning("MT5接続失敗 試行%d/%d", attempt, RECONNECT_RETRIES)
        if attempt < RECONNECT_RETRIES:
            time.sleep(RECONNECT_INTERVAL)

    return False


class HealthMonitor:
    """MT5接続死活監視スレッド"""

    def __init__(self, notifier=None):
        self._notifier    = notifier
        self._stop_event  = threading.Event()
        self._is_connected = True
        self._thread       = threading.Thread(
            target=self._run, daemon=True, name="HealthMonitor"
        )

    def start(self):
        self._thread.start()
        logger.info("▶ HealthMonitor 開始")

    def stop(self):
        self._stop_event.set()

    def is_connected(self) -> bool:
        return self._is_connected and MT5_AVAILABLE

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._check()
            except Exception as e:
                logger.error("HealthMonitor例外: %s", e, exc_info=True)
            time.sleep(HEALTH_CHECK_INTERVAL)

    def _check(self):
        if not MT5_AVAILABLE:
            return

        info = mt5.terminal_info()
        connected = info is not None and getattr(info, "connected", False)

        if not connected:
            if self._is_connected:
                # 初めて切断を検知
                logger.error("🔴 MT5接続断を検知")
                log_event("mt5_disconnected", "MT5接続断検知", level="ERROR")
                if self._notifier:
                    self._notifier.notify_mt5_disconnected()
                try:
                    discord_notifier.notify(
                        title="🆘 システムアラート",
                        description="MT5接続断を検知しました。自動再接続を試みます。",
                        color=0xFF0000,
                        fields={"状態": "MT5 disconnected"},
                    )
                except Exception:
                    pass

            self._is_connected = False
            self._reconnect()
        else:
            if not self._is_connected:
                logger.info("🟢 MT5接続回復")
                log_event("mt5_reconnected", "MT5接続回復")
            self._is_connected = True

    def _reconnect(self):
        """自動再接続（3回・10秒間隔）"""
        logger.info("🔄 MT5再接続試行...")
        if init_mt5():
            self._is_connected = True
            log_event("mt5_reconnect_success", "MT5自動再接続成功")
        else:
            log_event("mt5_reconnect_failed", "MT5自動再接続失敗", level="ERROR")
