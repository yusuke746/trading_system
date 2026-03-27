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
        ai_decisions から score_breakdown を取得し、scoring_history の
        対応レコードに outcome と pnl_usd を記録する。
        """
        try:
            conn = get_connection()

            # executions → ai_decisions を結合して score_breakdown を取得
            row = conn.execute("""
                SELECT ad.id as ai_id, ad.score_breakdown, ad.decision,
                       ad.ev_score, ad.market_regime, e.direction
                FROM executions e
                JOIN ai_decisions ad ON ad.id = e.ai_decision_id
                WHERE e.mt5_ticket = ?
                LIMIT 1
            """, (ticket,)).fetchone()

            if not row:
                return

            # scoring_history に既にレコードがある場合は更新
            existing = conn.execute("""
                SELECT id FROM scoring_history
                WHERE created_at >= datetime('now', '-1 day')
                  AND signal_direction = ?
                  AND decision = ?
                ORDER BY id DESC LIMIT 1
            """, (row["direction"], row["decision"])).fetchone()

            if existing:
                conn.execute("""
                    UPDATE scoring_history
                    SET outcome = ?, pnl_usd = ?
                    WHERE id = ?
                """, (outcome, pnl_usd, existing["id"]))
            else:
                # 新規レコードとして挿入
                conn.execute("""
                    INSERT INTO scoring_history
                    (signal_direction, regime, total_score, decision,
                     breakdown_json, outcome, pnl_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["direction"],
                    row["market_regime"],
                    row["ev_score"],
                    row["decision"],
                    row["score_breakdown"],
                    outcome,
                    pnl_usd,
                ))
            conn.commit()

            logger.debug(
                "📝 scoring_history フィードバック: ticket=%d outcome=%s pnl=%.2f",
                ticket, outcome, pnl_usd
            )
        except Exception as e:
            logger.error("scoring_history フィードバック失敗: %s", e)
