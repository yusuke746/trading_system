"""
batch_processor.py - バッチ処理パイプライン
AI Trading System v2.0
"""

import logging
from datetime import datetime, timezone, timedelta
from database import get_connection
from config import SYSTEM_CONFIG
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
        # 逆張り自動昇格：直近昇格時刻（方向別クールダウン管理）
        self._reversal_last_triggered: dict[str, datetime] = {}

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

        # 逆張りセットアップ自動検出 → entry_triggerがなくてもAI判定を起動
        if not entry_triggers and structures:
            reversal_trigger = self._detect_reversal_setup(structures)
            if reversal_trigger:
                # 疑似トリガーをDBに記録してAI判定と紐付けできるようにする
                sig_id = log_signal(reversal_trigger)
                reversal_trigger["_db_id"] = sig_id
                logger.info("🔄 逆張りセットアップ自動検出 → AI判定起動: direction=%s",
                            reversal_trigger.get("direction"))
                self._process_by_direction([reversal_trigger])
            return

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
            # 逆方向シグナルが混在している場合、方向ごとに分けてAI判定にかける
            logger.info("⚡ 逆方向シグナル混在 → 方向別に分割してAI判定: %s",
                        [t.get("source") for t in entry_triggers])
            for direction in directions:
                direction_triggers = [t for t in entry_triggers if t.get("direction") == direction]
                self._process_by_direction(direction_triggers)
            return

        # 単一方向の場合は通常処理
        self._process_by_direction(entry_triggers)

    def _detect_reversal_setup(self, structures: list[dict]) -> dict | None:
        """
        structureシグナルの組み合わせから逆張りセットアップを検出する。

        条件：
        1. 今回受信したstructureにliquidity_sweepが含まれる
           または直近30分以内のDBにliquidity_sweepが存在する
        2. 直近15分以内のDBにzone_retrace_touchまたはfvg_touchが存在する
        3. クールダウン期間（5分）を過ぎている

        Returns:
            条件を満たした場合は疑似entry_trigger dict、満たさない場合はNone
        """
        if not SYSTEM_CONFIG.get("reversal_auto_trigger_enabled", True):
            return None

        now = datetime.now(timezone.utc)

        # ── 今回受信したstructureのeventを確認 ──────────────
        received_events = {s.get("event") for s in structures}

        # ── DBから直近シグナルを取得 ──────────────────────────
        try:
            conn = get_connection()

            # liquidity_sweep（直近30分以内）
            since_30m = (now - timedelta(minutes=30)).isoformat()
            sweep_rows = conn.execute("""
                SELECT direction, price, received_at FROM signals
                WHERE event = 'liquidity_sweep'
                  AND received_at >= ?
                ORDER BY received_at DESC
                LIMIT 1
            """, (since_30m,)).fetchall()

            # zone_retrace_touch / fvg_touch（直近15分以内）
            since_15m = (now - timedelta(minutes=15)).isoformat()
            zone_rows = conn.execute("""
                SELECT direction, price, received_at FROM signals
                WHERE event IN ('zone_retrace_touch', 'fvg_touch')
                  AND received_at >= ?
                ORDER BY received_at DESC
                LIMIT 1
            """, (since_15m,)).fetchall()

            conn.close()
        except Exception as e:
            logger.error("_detect_reversal_setup DB error: %s", e)
            return None

        # ── 条件チェック ──────────────────────────────────────
        has_sweep = (
            "liquidity_sweep" in received_events or len(sweep_rows) > 0
        )
        has_zone = len(zone_rows) > 0

        if not has_sweep or not has_zone:
            return None

        # ── 方向決定（liquidity_sweepの逆方向がエントリー方向）──
        # sweep方向がsell → 売り側の流動性を狩った → buy方向に逆張り
        # sweep方向がbuy  → 買い側の流動性を狩った → sell方向に逆張り
        sweep_direction = None
        if sweep_rows:
            sweep_direction = sweep_rows[0]["direction"]
        elif "liquidity_sweep" in received_events:
            for s in structures:
                if s.get("event") == "liquidity_sweep":
                    sweep_direction = s.get("direction")
                    break

        if sweep_direction == "sell":
            entry_direction = "buy"
        elif sweep_direction == "buy":
            entry_direction = "sell"
        else:
            # 方向不明の場合はzone/FVGの方向を使用
            entry_direction = zone_rows[0]["direction"] if zone_rows else None

        if not entry_direction:
            return None

        # ── クールダウンチェック ──────────────────────────────
        cooldown_sec = SYSTEM_CONFIG.get("reversal_cooldown_sec", 300)
        last_triggered = self._reversal_last_triggered.get(entry_direction)
        if last_triggered:
            elapsed = (now - last_triggered).total_seconds()
            if elapsed < cooldown_sec:
                logger.debug(
                    "⏳ 逆張り昇格クールダウン中: direction=%s 残り%.0f秒",
                    entry_direction, cooldown_sec - elapsed
                )
                return None

        # ── クールダウン更新 ──────────────────────────────────
        self._reversal_last_triggered[entry_direction] = now

        # ── 疑似entry_triggerを生成 ───────────────────────────
        # zone/FVGの価格をエントリー価格として使用
        entry_price = (
            float(zone_rows[0]["price"]) if zone_rows
            else float(sweep_rows[0]["price"]) if sweep_rows
            else 0.0
        )

        synthetic_trigger = {
            "symbol":        SYSTEM_CONFIG.get("symbol", "GOLD"),
            "price":         entry_price,
            "tf":            5,
            "direction":     entry_direction,
            "signal_type":   "entry_trigger",
            "event":         "prediction_signal",
            "source":        "ReverseAutoTrigger",
            "strength":      "normal",
            "comment":       f"逆張り自動昇格: liquidity_sweep({sweep_direction}) + zone/FVG検出",
            "confirmed":     "bar_close",
            "tv_confidence": None,
            "tv_win_rate":   None,
            "received_at":   now.isoformat(),
        }

        logger.info(
            "✅ 逆張り疑似トリガー生成: direction=%s price=%.3f",
            entry_direction, entry_price
        )
        return synthetic_trigger

    def _process_by_direction(self, entry_triggers: list[dict]) -> None:
        """指定されたエントリートリガーリストに対してAI判定・執行を行う"""
        if not entry_triggers:
            return

        # コンテキスト構築
        context  = build_context_for_ai(entry_triggers)
        messages = build_prompt(context)

        # AI判定
        ai_result = ask_ai(messages)

        # DB記録
        sig_ids = [t.get("_db_id") for t in entry_triggers if t.get("_db_id")]
        ai_decision_id = log_ai_decision(
            sig_ids, ai_result, context=context, prompt={"messages": messages}
        )

        decision = ai_result.get("decision")
        logger.info("🤖 AI判定: decision=%s confidence=%.2f ev_score=%.2f direction=%s",
                    decision,
                    ai_result.get("confidence", 0),
                    ai_result.get("ev_score", 0),
                    entry_triggers[0].get("direction", "?"))

        if decision == "approve" and should_execute(ai_result):
            if len(entry_triggers) > 1:
                logger.info("📦 複数トリガーによるapprove: sources=%s → 代表トリガー=%s",
                            [t.get("source") for t in entry_triggers],
                            entry_triggers[0].get("source"))
            execute_order(
                trigger          = entry_triggers[0],
                ai_result        = ai_result,
                ai_decision_id   = ai_decision_id,
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
            logger.info("❌ 拒否: %s", ai_result.get("reason"))
