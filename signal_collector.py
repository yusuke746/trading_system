"""
signal_collector.py - 受信バッファ（500ms収集窓）
AI Trading System v2.0

同一足のシグナルが数百ms差で届くため、500msバッファでまとめて処理する。
"""

import logging
import threading
from datetime import datetime, timezone
from config import SYSTEM_CONFIG

logger = logging.getLogger(__name__)

WINDOW_MS       = SYSTEM_CONFIG["collection_window_ms"]   # 500ms
MAX_BUFFER_SIZE = SYSTEM_CONFIG.get("signal_buffer_size", 50) * 4  # 差し戻しループによる無限蓄積防止の上限（上限超過は履歴を破棄）


class SignalCollector:
    """500msの収集窓でシグナルをバッファリングする"""

    def __init__(self, on_batch_ready):
        """
        on_batch_ready: バッチが確定したときに呼ばれるコールバック
                        on_batch_ready(batch: list[dict]) -> None
        """
        self._on_batch_ready = on_batch_ready
        self._buffer: list[dict] = []
        self._lock   = threading.Lock()
        self._timer: threading.Timer | None = None

    def receive(self, signal: dict) -> None:
        """シグナルを受信してバッファに積む。タイマーをリセットする"""
        with self._lock:
            self._buffer.append(signal)
            logger.debug("🟡 バッファ追加: source=%s event=%s direction=%s",
                         signal.get("source"), signal.get("event"),
                         signal.get("direction"))
            self._reset_timer()

    def _reset_timer(self) -> None:
        """既存タイマーをキャンセルして新しいタイマーをセット"""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(
            WINDOW_MS / 1000.0,
            self._flush,
        )
        self._timer.daemon = True
        self._timer.start()

    def _flush(self) -> None:
        """タイマー満了時にバッファを確定して処理コールバックを呼ぶ"""
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer[:]
            self._buffer.clear()
            self._timer = None

        logger.info("📦 バッチ確定 %d件 → 処理開始", len(batch))
        try:
            self._on_batch_ready(batch)
        except Exception as e:
            logger.error("バッチ処理コールバック例外: %s", e, exc_info=True)
            # コールバック失敗時はバッファに差し戻す（シグナル消滅防止）
            with self._lock:
                merged = batch + self._buffer  # batchを先頭に挿入して順序保持
                if len(merged) > MAX_BUFFER_SIZE:
                    excess = len(merged) - MAX_BUFFER_SIZE
                    merged = merged[:MAX_BUFFER_SIZE]
                    logger.error(
                        "🚨 バッファ上限(%d)超過。古いシグナル %d件を破棄しました（コールバック恒常失敗の可能性）",
                        MAX_BUFFER_SIZE, excess,
                    )
                self._buffer = merged
                logger.warning("⚠️ バッファに %d件を差し戻しました（リカバリー）", len(merged))

    # デバッグ・テスト用
    def flush_now(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._flush()
