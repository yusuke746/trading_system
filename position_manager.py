"""
position_manager.py - 段階的利確・BE・トレーリングストップ管理（v2追加）
AI Trading System v2.0

3ステップ管理:
STEP1: ブレークイーブン（含み益 ATR×1.0 到達時）
STEP2: 第1TP 50%部分決済（含み益 ATR×1.5 到達時）
STEP3: トレーリングストップ更新（ATR×1.0追跡）
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config import SYSTEM_CONFIG
from logger_module import log_event, log_trade_result
import discord_notifier

logger = logging.getLogger(__name__)

PARTIAL_CLOSE_RATIO    = SYSTEM_CONFIG["partial_close_ratio"]
PARTIAL_TP_ATR_MULT    = SYSTEM_CONFIG["partial_tp_atr_mult"]
BE_TRIGGER_ATR_MULT    = SYSTEM_CONFIG["be_trigger_atr_mult"]
# BE_BUFFER_PIPS       = SYSTEM_CONFIG["be_buffer_pips"]   # 旧: Forex pips単位（不適切）→ be_buffer_atr_mult に移行
BE_BUFFER_ATR_MULT     = SYSTEM_CONFIG.get("be_buffer_atr_mult", 0.15)  # BEバッファ = ATR × 0.15
TRAILING_STEP_ATR_MULT = SYSTEM_CONFIG["trailing_step_atr_mult"]
PM_CHECK_INTERVAL_SEC  = SYSTEM_CONFIG["pm_check_interval_sec"]
SYMBOL                 = SYSTEM_CONFIG["symbol"]


@dataclass
class ManagedPosition:
    ticket:           int
    direction:        str            # buy / sell
    entry_price:      float
    lot_size:         float
    sl_price:         float
    atr_pips:         float
    execution_id:     int
    entered_at:       datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tp_price:         float    = 0.0
    regime:           str      = "TREND"  # TREND / REVERSAL / BREAKOUT
    be_applied:       bool     = False
    partial_closed:   bool     = False
    trailing_active:  bool     = False
    max_price:        float    = 0.0   # buy: 最高値 / sell: 最安値
    remaining_lots:   float    = 0.0
    partial_pnl:      float    = 0.0

    def __post_init__(self):
        self.max_price     = self.entry_price
        self.remaining_lots = self.lot_size


class PositionManager:
    """ポジション管理スレッド（10秒ポーリング）"""

    def __init__(self):
        self._positions: dict[int, ManagedPosition] = {}
        self._lock       = threading.Lock()
        self._stop_event = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, daemon=True, name="PositionManager"
        )

    def start(self):
        self._thread.start()
        logger.info("▶ PositionManager 開始")

    def stop(self):
        self._stop_event.set()

    def register_position(self, ticket: int, direction: str,
                           entry_price: float, lot_size: float,
                           sl_price: float, atr_pips: float,
                           execution_id: int,
                           tp_price: float = 0.0,
                           regime: str = "TREND"):
        """新規ポジションを登録する"""
        with self._lock:
            pos = ManagedPosition(
                ticket       = ticket,
                direction    = direction,
                entry_price  = entry_price,
                lot_size     = lot_size,
                sl_price     = sl_price,
                tp_price     = tp_price,
                atr_pips     = atr_pips,
                execution_id = execution_id,
                regime       = regime,
            )
            self._positions[ticket] = pos
            logger.info(
                "📋 PositionManager登録: ticket=%d %s %.2flot entry=%.3f",
                ticket, direction, lot_size, entry_price
            )

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error("PositionManager例外: %s", e, exc_info=True)
            time.sleep(PM_CHECK_INTERVAL_SEC)

    def _tick(self):
        if not MT5_AVAILABLE:
            return

        # 先にmanageを実行し、決済済みticketをまとめて収集してから削除
        # （with内でdictを変更するとイテレーション不整合が起きるため）
        to_remove = []
        with self._lock:
            for ticket, pos in list(self._positions.items()):
                result = self._manage(pos)
                if result == "closed":
                    to_remove.append(ticket)
        for t in to_remove:
            with self._lock:
                self._positions.pop(t, None)

    def _get_current_price(self, symbol: str, direction: str) -> Optional[float]:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return tick.bid if direction == "buy" else tick.ask

    def _manage(self, pos: ManagedPosition) -> str:
        """ポジションを管理する。決済済みなら 'closed'、継続なら 'ok' を返す。"""
        current_price = self._get_current_price(SYMBOL, pos.direction)
        if current_price is None:
            return "ok"

        # MT5にポジションが存在するか確認
        mt5_pos = mt5.positions_get(ticket=pos.ticket)
        if not mt5_pos:
            # エントリー直後はMT5が一時的に空を返すことがある
            elapsed = (datetime.now(timezone.utc) - pos.entered_at).total_seconds()
            if elapsed < 30:
                logger.info(
                    "ポジション未確認（エントリー直後 %.0f秒）: ticket=%d スキップ",
                    elapsed, pos.ticket,
                )
                return "ok"
            # 30秒以上経過: 1秒待って再確認
            time.sleep(1.0)
            mt5_pos = mt5.positions_get(ticket=pos.ticket)
        if not mt5_pos:
            # 決済済み → Discord通知してから呼び出し元(_tick)が削除
            try:
                # GOLD: price_diff(USD) × lots × 100 → USD建て（通貨換算不要）
                if pos.direction == "buy":
                    pnl_est = (current_price - pos.entry_price) * pos.lot_size * 100
                else:
                    pnl_est = (pos.entry_price - current_price) * pos.lot_size * 100
                if pnl_est >= 0:
                    title  = "💰 決済（利確）"
                    color  = 0x00FF00
                    reason = "TP/trailing"
                else:
                    title  = "🔴 決済（損切）"
                    color  = 0xFF0000
                    reason = "SL"
                discord_notifier.notify(
                    title=title,
                    description=f"{SYMBOL} {pos.direction} ticket={pos.ticket}",
                    color=color,
                    fields={
                        "symbol":    SYMBOL,
                        "損益(推定)": f"${pnl_est:.2f}",
                        "決済理由":   reason,
                        "entry":     f"{pos.entry_price:.3f}",
                        "exit(推定)": f"{current_price:.3f}",
                    },
                )
            except Exception:
                pass
            return "closed"

        if pos.direction == "buy":
            unrealized = current_price - pos.entry_price
            pos.max_price = max(pos.max_price, current_price)
        else:
            unrealized = pos.entry_price - current_price
            pos.max_price = min(pos.max_price, current_price)

        # pos.atr_pips は dollar価格単位（executor.pyのatr_dollarを格納）
        atr_value = pos.atr_pips  # すでにdollar価格単位

        # ── BREAKOUTは固定SL/TPのみ（BE・部分決済・トレーリング不要）
        if pos.regime == "BREAKOUT":
            return "ok"

        # ── STEP1: ブレークイーブン ──────────────────────────
        if not pos.be_applied and unrealized >= atr_value * BE_TRIGGER_ATR_MULT:
            self._apply_be(pos)

        # ── STEP2: 第1TP（50%部分決済）───────────────────────
        if not pos.partial_closed and unrealized >= atr_value * PARTIAL_TP_ATR_MULT:
            self._partial_close(pos, current_price)

        # ── STEP3: トレーリングストップ更新 ───────────────────
        if pos.partial_closed:
            self._update_trailing(pos)

        return "ok"

    def _apply_be(self, pos: ManagedPosition):
        """SLをエントリー価格 + ATR×0.15 に移動（BEバッファ）"""
        # pos.atr_pips は dollar価格単位（executor.py の atr_dollar を格納）
        buffer = round(pos.atr_pips * BE_BUFFER_ATR_MULT, 3)
        if pos.direction == "buy":
            new_sl = round(pos.entry_price + buffer, 3)
        else:
            new_sl = round(pos.entry_price - buffer, 3)

        success = self._update_sl(pos.ticket, new_sl, current_tp=pos.tp_price)
        if success:
            pos.be_applied = True
            pos.sl_price   = new_sl
            log_event("pm_be_applied",
                      f"ticket={pos.ticket} new_sl={new_sl} buffer={buffer:.3f}")
            logger.info("🔒 BE移動: ticket=%d sl→%.3f (buffer=%.3f)", pos.ticket, new_sl, buffer)

    def _partial_close(self, pos: ManagedPosition, current_price: float):
        """50%を成行決済"""
        close_vol = round(pos.lot_size * PARTIAL_CLOSE_RATIO, 2)

        # ブローカーのmin_lot確認
        sym_info = mt5.symbol_info(SYMBOL)
        min_lot  = sym_info.volume_min if sym_info else 0.01
        if close_vol < min_lot:
            logger.warning(
                "部分決済スキップ（close_vol %.2f < min_lot %.2f）→トレーリングへ",
                close_vol, min_lot
            )
            pos.partial_closed  = True
            pos.trailing_active = True
            return

        if pos.direction == "buy":
            order_type = mt5.ORDER_TYPE_SELL
            price      = mt5.symbol_info_tick(SYMBOL).bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price      = mt5.symbol_info_tick(SYMBOL).ask

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       SYMBOL,
            "volume":       close_vol,
            "type":         order_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    20,
            "magic":        SYSTEM_CONFIG["magic_number"],
            "comment":      "partial_tp",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            # GOLD の損益計算: 1lot × 1ドル動き = $100
            # price_diff(USD) × lots × 100 → すでにUSD建てなので通貨換算不要
            if pos.direction == "buy":
                pnl      = (current_price - pos.entry_price) * close_vol * 100
                pnl_pips = (current_price - pos.entry_price) / 0.1
            else:
                pnl      = (pos.entry_price - current_price) * close_vol * 100
                pnl_pips = (pos.entry_price - current_price) / 0.1
            pos.partial_pnl     = pnl
            pos.partial_closed  = True
            pos.trailing_active = True
            pos.remaining_lots  = round(pos.lot_size - close_vol, 2)

            log_event("pm_partial_close",
                      f"ticket={pos.ticket} vol={close_vol} price={current_price}")
            logger.info("💰 部分決済: ticket=%d vol=%.2f @ %.3f pnl=%.2f USD",
                        pos.ticket, close_vol, current_price, pnl)
            try:
                discord_notifier.notify(
                    title="💰 決済（利確）",
                    description=f"{SYMBOL} {pos.direction} ticket={pos.ticket}",
                    color=0x00FF00,
                    fields={
                        "symbol":   SYMBOL,
                        "損益":     f"${pnl:.2f}",
                        "決済理由": "partial_tp",
                        "vol":      f"{close_vol:.2f}",
                        "price":    f"{current_price:.3f}",
                    },
                )
            except Exception:
                pass

            # DB記録
            dur = (datetime.now(timezone.utc) - pos.entered_at).total_seconds() / 60
            log_trade_result(
                execution_id=pos.execution_id,
                ticket=pos.ticket,
                outcome="partial_tp",
                pnl_usd=pnl,
                pnl_pips=round(pnl_pips, 1),
                duration_min=dur,
                partial_close_pnl=pnl,
            )
        else:
            err = result.retcode if result else "None"
            logger.error("部分決済失敗: ticket=%d retcode=%s", pos.ticket, err)

    def _update_trailing(self, pos: ManagedPosition):
        """トレーリングストップ更新"""
        # pos.atr_pipsはdollar価格単位なのでそのまま掛ける（*0.1不要）
        trail_dist = pos.atr_pips * TRAILING_STEP_ATR_MULT

        if pos.direction == "buy":
            new_sl = round(pos.max_price - trail_dist, 3)
        else:
            new_sl = round(pos.max_price + trail_dist, 3)

        # SLを有利な方向にのみ動かす
        if pos.direction == "buy" and new_sl <= pos.sl_price:
            return
        if pos.direction == "sell" and new_sl >= pos.sl_price:
            return

        success = self._update_sl(pos.ticket, new_sl, current_tp=pos.tp_price)
        if success:
            pos.sl_price = new_sl
            log_event("pm_trailing_update",
                      f"ticket={pos.ticket} sl→{new_sl} max_price={pos.max_price}")
            logger.debug("📈 トレーリング更新: ticket=%d sl→%.3f", pos.ticket, new_sl)

    def _update_sl(self, ticket: int, new_sl: float, current_tp: float = 0.0) -> bool:
        if not MT5_AVAILABLE:
            return True
        # current_tpを明示的に指定しTPを保持する
        # （TRADE_ACTION_SLTPでtpを省略またわ0にするとMT5がTPをリセットするため）
        req    = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   SYMBOL,    # 必須フィールド（欠落するとorder_sendが失敗）
            "position": ticket,
            "sl":       new_sl,
            "tp":       current_tp,  # TPを明示的に保持
        }
        result = mt5.order_send(req)
        return bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)
