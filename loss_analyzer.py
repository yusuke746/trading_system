"""
loss_analyzer.py - 決済監視・フィードバックループ
AI Trading System v3.5

10秒ポーリングでポジション決済を検知し、結果をDBに記録する。
v3.0: scoring_history への結果記録（outcome/pnl）フィードバック機能を追加。
※ SL被弾時の振り返りAI（OpenAI呼び出し）は v3.5 で廃止。
   チャートによる手動分析に移行したため。
"""

import logging
import threading
import time
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from database import get_connection
from logger_module import log_trade_result, log_event
from config import SYSTEM_CONFIG

logger = logging.getLogger(__name__)


def _convert_to_usd(amount: float) -> float:
    """
    MT5の損益（口座通貨建て）をUSDに換算する。
    口座通貨がUSDの場合はそのまま返す。
    換算レート取得失敗時はフォールバックレート（150.0）を使用。
    """
    if not MT5_AVAILABLE:
        return amount
    try:
        acc = mt5.account_info()
        if acc is None:
            return amount
        currency = acc.currency
        if currency == "USD":
            return amount
        # JPY → USD: USDJPY のbidで割る
        sym = f"USD{currency}"   # 例: USDJPY
        info = mt5.symbol_info(sym)
        if info is not None and info.bid > 0:
            return round(amount / info.bid, 2)
        # フォールバック: 150円/ドル
        logger.warning(
            "⚠️ %s レート取得失敗。フォールバックレート150.0を使用", sym)
        return round(amount / 150.0, 2)
    except Exception as e:
        logger.error("_convert_to_usd エラー: %s", e)
        return amount


LOSS_ALERT_USD        = SYSTEM_CONFIG["loss_alert_usd"]
POSITION_CHECK_SEC    = SYSTEM_CONFIG["position_check_interval_sec"]


class LossAnalyzer:
    """決済監視とSL被弾時の振り返りAI"""

    def __init__(self, notifier=None):
        self._notifier    = notifier
        self._open_positions: dict[int, dict] = {}  # ticket → {info}
        self._stop_event  = threading.Event()
        self._thread      = threading.Thread(
            target=self._run, daemon=True, name="LossAnalyzer"
        )

    def start(self):
        # 起動時に既存ポジションを同期（再起動直後の取りこぼし防止）
        self._sync_existing_positions()
        self._thread.start()
        logger.info("▶ LossAnalyzer 開始")

    def stop(self):
        self._stop_event.set()

    def _sync_existing_positions(self):
        """
        MT5 の現在オープンポジションを _open_positions に事前登録する。
        システム再起動後でも決済イベントを正しく検知できるようにする。
        """
        if not MT5_AVAILABLE:
            return
        try:
            positions = mt5.positions_get() or []
            for pos in positions:
                if pos.ticket not in self._open_positions:
                    self._open_positions[pos.ticket] = {
                        "ticket":      pos.ticket,
                        "symbol":      pos.symbol,
                        "direction":   "buy" if pos.type == 0 else "sell",
                        "entry_price": pos.price_open,
                        "lot_size":    pos.volume,
                        "opened_at":   datetime.fromtimestamp(
                            pos.time, tz=timezone.utc).isoformat(),
                    }
            if positions:
                logger.info(
                    "🔄 LossAnalyzer: 既存ポジション %d 件を同期しました",
                    len(positions),
                )
        except Exception as e:
            logger.error("_sync_existing_positions 失敗: %s", e)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error("LossAnalyzer例外: %s", e, exc_info=True)
            time.sleep(POSITION_CHECK_SEC)

    def _tick(self):
        if not MT5_AVAILABLE:
            return

        current_tickets = set()
        positions = mt5.positions_get() or []

        for pos in positions:
            ticket = pos.ticket
            current_tickets.add(ticket)

            # 新規ポジションを追跡
            if ticket not in self._open_positions:
                self._open_positions[ticket] = {
                    "ticket":     ticket,
                    "symbol":     pos.symbol,
                    "direction":  "buy" if pos.type == 0 else "sell",
                    "entry_price": pos.price_open,
                    "lot_size":   pos.volume,
                    "opened_at":  datetime.fromtimestamp(
                        pos.time, tz=timezone.utc).isoformat(),
                }

        # 決済検知（追跡中だが現在ポジションにない）
        closed_tickets = set(self._open_positions.keys()) - current_tickets
        for ticket in closed_tickets:
            info = self._open_positions.pop(ticket)
            self._on_position_closed(ticket, info)

    def _on_position_closed(self, ticket: int, info: dict):
        """ポジション決済時の処理"""
        # MT5の成立済み取引履歴から損益取得
        # ticket= はdeal自体のID。position= でポジションticketに対応するdealを取得する
        history = mt5.history_deals_get(position=ticket)
        if not history:
            logger.warning("決済履歴なし: ticket=%d", ticket)
            return

        total_pnl_raw = sum(d.profit for d in history)
        total_pnl     = _convert_to_usd(total_pnl_raw)
        pips      = 0.0
        outcome   = "manual"

        # 最後のディール
        last_deal = history[-1]
        if last_deal.comment:
            c = last_deal.comment.lower()
            if "sl"       in c: outcome = "sl_hit"
            elif "tp"     in c: outcome = "tp_hit"
            elif "trail"  in c: outcome = "trailing_sl"
            elif "partial" in c: outcome = "partial_tp"

        # pips計算
        if info["direction"] == "buy":
            pips = (last_deal.price - info["entry_price"]) / 0.1
        else:
            pips = (info["entry_price"] - last_deal.price) / 0.1

        opened_dt = datetime.fromisoformat(info["opened_at"])
        dur_min   = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60

        # executionsテーブルからexecution_idを取得
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM executions WHERE mt5_ticket=? LIMIT 1",
            (ticket,)
        ).fetchone()
        execution_id = row["id"] if row else None

        result_id = log_trade_result(
            execution_id    = execution_id,
            ticket          = ticket,
            outcome         = outcome,
            pnl_usd         = total_pnl,
            pnl_pips        = round(pips, 1),
            duration_min    = round(dur_min, 1),
        )

        logger.info(
            "📊 決済記録: ticket=%d outcome=%s pnl_raw=%.2f→pnl_usd=%.2f pips=%.1f",
            ticket, outcome, total_pnl_raw, total_pnl, pips
        )

        # v3.0: scoring_history へのフィードバック
        self._update_scoring_history(ticket, outcome, total_pnl)

    def _update_scoring_history(self, ticket: int, outcome: str, pnl_usd: float):
        """
        v3.0: scoring_history テーブルに結果をフィードバックする。
        executions.ai_decision_id（= scoring_history の id）を使って
        直接 scoring_history の outcome と pnl_usd を更新する。
        """
        try:
            conn = get_connection()

            # mt5_ticket から executions.ai_decision_id を取得
            row = conn.execute(
                "SELECT ai_decision_id FROM executions WHERE mt5_ticket = ?",
                (ticket,)
            ).fetchone()

            if not row or row["ai_decision_id"] is None:
                return

            from logger_module import update_scoring_history_outcome
            score_outcome = (
                "win"       if pnl_usd > 0 else
                "loss"      if pnl_usd < 0 else
                "breakeven"
            )
            update_scoring_history_outcome(row["ai_decision_id"], score_outcome, pnl_usd)

            logger.debug(
                "📝 scoring_history フィードバック: ticket=%d outcome=%s pnl=%.2f",
                ticket, score_outcome, pnl_usd
            )
        except Exception as e:
            logger.error("scoring_history フィードバック失敗: %s", e)
