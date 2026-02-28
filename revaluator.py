"""
revaluator.py - wait再評価エンジン
AI Trading System v2.0
"""

import logging
import threading
import time
from datetime import datetime, timezone

from context_builder import build_context_for_ai
from prompt_builder import build_prompt
from ai_judge import ask_ai, should_execute
from executor import execute_order
from logger_module import update_wait_history, log_event

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15


class Revaluator:
    """
    waitバッファのアイテムを定期的に再評価するエンジン。
    - 新規structureシグナル受信 → structure_neededを即再評価
    - 15秒ポーリング → 期限チェック・タイマー再評価
    """

    def __init__(self, wait_buffer, position_manager=None):
        self._buffer           = wait_buffer
        self._position_manager = position_manager
        self._stop_event       = threading.Event()
        self._thread           = threading.Thread(
            target=self._run, daemon=True, name="Revaluator"
        )

    def start(self):
        self._thread.start()
        logger.info("▶ Revaluator 開始")

    def stop(self):
        self._stop_event.set()

    def on_new_structure(self):
        """新規structureシグナル受信時に呼ぶ（即再評価トリガー）"""
        structure_items = self._buffer.get_by_scope("structure_needed")
        if structure_items:
            logger.info("🔄 structure受信で即再評価: %d件", len(structure_items))
            for item in structure_items:
                self._reeval_item(item)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error("Revaluator例外: %s", e, exc_info=True)
            time.sleep(POLL_INTERVAL_SEC)

    def _tick(self):
        for item in self._buffer.get_waiting():
            # タイムアウト判定
            if self._buffer.is_expired(item):
                logger.info("⌛ タイムアウト: id=%s scope=%s",
                             item.item_id[:8], item.wait_scope)
                self._buffer.expire_item(item.item_id)
                update_wait_history(item.wait_id, item.reeval_count, "timeout")
                log_event("wait_timeout",
                          f"id={item.item_id[:8]} scope={item.wait_scope}")
                continue

            # next_bar / cooldown はタイマーベースで再評価
            if item.wait_scope in ("next_bar", "cooldown"):
                self._reeval_item(item)

        self._buffer.cleanup_done()

    def _reeval_item(self, item):
        if self._buffer.should_reject_by_reeval(item):
            logger.info("❌ 再評価上限超過: id=%s", item.item_id[:8])
            self._buffer.resolve_item(item.item_id, "rejected")
            update_wait_history(item.wait_id, item.reeval_count, "rejected")
            return

        item.reeval_count = self._buffer.increment_reeval(item.item_id)
        elapsed = (datetime.now(timezone.utc) - item.created_at).total_seconds()

        # 最新コンテキストを再構築
        context = build_context_for_ai(item.entry_signals)
        context["reeval_meta"] = {
            "is_reeval":       True,
            "reeval_count":    item.reeval_count,
            "original_reason": item.original_reason,
            "wait_condition":  item.wait_condition,
            "elapsed_seconds": elapsed,
        }

        messages         = build_prompt(context)
        signal_direction = item.entry_signals[0].get("direction") if item.entry_signals else None
        ai_result        = ask_ai(messages, context=context, signal_direction=signal_direction)
        decision         = ai_result.get("decision")

        logger.info("🔄 再評価結果[%d]: id=%s decision=%s",
                    item.reeval_count, item.item_id[:8], decision)

        if decision == "approve" and should_execute(ai_result):
            result = execute_order(
                trigger        = item.entry_signals[0],
                ai_result      = ai_result,
                ai_decision_id = item.ai_decision_id,
                position_manager = self._position_manager,
            )
            status = "approved" if result["success"] else "rejected"
            self._buffer.resolve_item(item.item_id, status)
            update_wait_history(item.wait_id, item.reeval_count, status)

        elif decision == "reject":
            self._buffer.resolve_item(item.item_id, "rejected")
            update_wait_history(item.wait_id, item.reeval_count, "rejected")

        else:
            # 再度 wait → wait_scope 更新のみ
            new_scope = ai_result.get("wait_scope", item.wait_scope)
            item.wait_scope     = new_scope
            item.wait_condition = ai_result.get("wait_condition", item.wait_condition)
