"""
batch_processor.py - バッチ処理パイプライン
AI Trading System v2.0
"""

import logging
from logger_module import log_signal, log_ai_decision, log_wait
from context_builder import build_context_for_ai
from prompt_builder import build_prompt
from ai_judge import ask_ai, should_execute
from executor import execute_order

logger = logging.getLogger(__name__)


class BatchProcessor:
    """
    500ms収集窓で確定したバッチを処理するパイプライン。
    """

    def __init__(self, wait_buffer, revaluator=None, position_manager=None):
        self._wait_buffer      = wait_buffer
        self._revaluator       = revaluator
        self._position_manager = position_manager

    def process(self, batch: list[dict]) -> None:
        """バッチを種別分類してパイプラインを実行する"""
        entry_triggers = [s for s in batch if s.get("signal_type") == "entry_trigger"]
        structures     = [s for s in batch if s.get("signal_type") == "structure"]

        # structureシグナルをDBに記録
        for s in structures:
            sig_id = log_signal(s)
            s["_db_id"] = sig_id
            logger.debug("🔵 structure記録: event=%s", s.get("event"))

        # structureがあったらwaitバッファを即再評価
        if structures and self._revaluator:
            self._revaluator.on_new_structure()

        if not entry_triggers:
            return

        # entry_triggerをDBに記録
        sig_ids = []
        for t in entry_triggers:
            sig_id = log_signal(t)
            t["_db_id"] = sig_id
            sig_ids.append(sig_id)

        # 方向フィルタリング
        directions = {t["direction"] for t in entry_triggers
                      if t.get("direction")}

        if len(directions) > 1:
            # 逆方向が混在 → 相場が迷い中 → スキップ
            logger.info("⚡ 逆方向シグナル混在 → バッチスキップ: %s",
                        [t.get("source") for t in entry_triggers])
            return

        # 同方向バッチ → ev_score ボーナス付与（プログラム側で一元管理; SYSTEM_PROMPTには記載しない）
        ev_bonus = 0.2 if len(entry_triggers) > 1 else 0.0

        # コンテキスト構築
        context  = build_context_for_ai(entry_triggers)
        messages = build_prompt(context)

        # AI判定
        ai_result = ask_ai(messages)
        if ev_bonus:
            ai_result["ev_score"] = round(
                ai_result.get("ev_score", 0) + ev_bonus, 3)
            logger.info("🔼 同方向バッチボーナス +%.1f → ev_score=%.3f",
                        ev_bonus, ai_result["ev_score"])

        # DB記録
        ai_decision_id = log_ai_decision(
            sig_ids, ai_result, context=context, prompt={"messages": messages}
        )

        decision = ai_result.get("decision")
        logger.info("🤖 AI判定: decision=%s confidence=%.2f ev_score=%.2f",
                    decision,
                    ai_result.get("confidence", 0),
                    ai_result.get("ev_score", 0))

        if decision == "approve" and should_execute(ai_result):
            execute_order(
                trigger        = entry_triggers[0],
                ai_result      = ai_result,
                ai_decision_id = ai_decision_id,
                position_manager = self._position_manager,
            )

        elif decision == "wait":
            wait_id = log_wait(
                ai_decision_id = ai_decision_id,
                wait_scope     = ai_result.get("wait_scope", "cooldown"),
                wait_condition = ai_result.get("wait_condition", ""),
            )
            self._wait_buffer.add(
                entry_signals  = entry_triggers,
                ai_result      = ai_result,
                ai_decision_id = ai_decision_id,
                wait_id        = wait_id,
            )
        else:
            # reject
            logger.info("❌ 拒否: %s", ai_result.get("reason"))
