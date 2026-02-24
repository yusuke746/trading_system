"""
wait_buffer.py - wait判定のバッファ管理
AI Trading System v2.0
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from config import SYSTEM_CONFIG

logger = logging.getLogger(__name__)

WAIT_EXPIRY      = SYSTEM_CONFIG["wait_expiry"]
MAX_REEVAL_COUNT = SYSTEM_CONFIG["max_reeval_count"]


@dataclass
class WaitItem:
    item_id:         str
    entry_signals:   list
    ai_result:       dict
    ai_decision_id:  int
    wait_id:         int        # DB wait_history.id
    wait_scope:      str        # next_bar / structure_needed / cooldown
    wait_condition:  str
    original_reason: str
    created_at:      datetime   = field(default_factory=lambda: datetime.now(timezone.utc))
    reeval_count:    int        = 0
    status:          str        = "waiting"   # waiting / approved / rejected / timeout


class WaitBuffer:
    """waitシグナルをバッファリングして再評価エンジンに渡す"""

    def __init__(self):
        self._items: dict[str, WaitItem] = {}
        self._lock  = threading.RLock()   # Revaluatorスレッド と batchスレッドの競合を防ぐ

    def add(self, entry_signals: list, ai_result: dict,
            ai_decision_id: int, wait_id: int) -> str:
        item_id = str(uuid.uuid4())
        item = WaitItem(
            item_id        = item_id,
            entry_signals  = entry_signals,
            ai_result      = ai_result,
            ai_decision_id = ai_decision_id,
            wait_id        = wait_id,
            wait_scope     = ai_result.get("wait_scope", "cooldown"),
            wait_condition = ai_result.get("wait_condition", ""),
            original_reason= ai_result.get("reason", ""),
        )
        with self._lock:
            self._items[item_id] = item
        logger.info("⏳ waitバッファ追加: id=%s scope=%s",
                    item_id[:8], item.wait_scope)
        return item_id

    def get_all(self) -> list[WaitItem]:
        with self._lock:
            return list(self._items.values())

    def get_by_scope(self, scope: str) -> list[WaitItem]:
        with self._lock:
            return [i for i in self._items.values()
                    if i.status == "waiting" and i.wait_scope == scope]

    def get_waiting(self) -> list[WaitItem]:
        with self._lock:
            return [i for i in self._items.values() if i.status == "waiting"]

    def expire_item(self, item_id: str) -> None:
        with self._lock:
            if item_id in self._items:
                self._items[item_id].status = "timeout"
        logger.info("⌛ waitタイムアウト: id=%s", item_id[:8])

    def resolve_item(self, item_id: str, status: str) -> None:
        """status: 'approved' | 'rejected' | 'timeout'"""
        with self._lock:
            if item_id in self._items:
                self._items[item_id].status = status

    def increment_reeval(self, item_id: str) -> int:
        """
        再評価カウンタをスレッドセーフにインクリメントして新しい値を返す。
        read-modify-write を lock 内で完結させる。
        """
        with self._lock:
            if item_id in self._items:
                self._items[item_id].reeval_count += 1
                return self._items[item_id].reeval_count
        return 0

    def cleanup_done(self) -> None:
        """完了済み（非waiting）アイテムを削除する"""
        with self._lock:
            done = [k for k, v in self._items.items()
                    if v.status != "waiting"]
            for k in done:
                del self._items[k]

    def is_expired(self, item: WaitItem) -> bool:
        elapsed = (datetime.now(timezone.utc) - item.created_at).total_seconds()
        expiry  = WAIT_EXPIRY.get(item.wait_scope, 60 * 15)
        return elapsed >= expiry

    def should_reject_by_reeval(self, item: WaitItem) -> bool:
        return item.reeval_count >= MAX_REEVAL_COUNT
