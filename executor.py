"""
executor.py - MT5注文執行モジュール
AI Trading System v2.0
"""

import logging
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from config import SYSTEM_CONFIG
from market_hours import full_market_check
from news_filter import check_news_filter
from logger_module import log_execution, log_event
import risk_manager

logger = logging.getLogger(__name__)

SYMBOL         = SYSTEM_CONFIG["symbol"]
MAX_POSITIONS  = SYSTEM_CONFIG["max_positions"]
MIN_MARGIN     = SYSTEM_CONFIG["min_free_margin"]
RISK_PERCENT   = SYSTEM_CONFIG["risk_percent"]
ATR_SL_MULT    = SYSTEM_CONFIG["atr_sl_multiplier"]
ATR_TP_MULT    = SYSTEM_CONFIG["atr_tp_multiplier"]
MAX_SL_PIPS    = SYSTEM_CONFIG["max_sl_pips"]
MIN_SL_PIPS    = SYSTEM_CONFIG["min_sl_pips"]
PIP_POINTS     = SYSTEM_CONFIG["pip_points"]
DEVIATION      = SYSTEM_CONFIG["deviation"]
MAGIC          = SYSTEM_CONFIG["magic_number"]
ORDER_COMMENT  = SYSTEM_CONFIG["order_comment"]


# ─────────────────────────── 事前チェック ─────────────────

def pre_execution_check(symbol: str = SYMBOL, entry_price: float = 0.0) -> dict:
    """
    執行前チェック（ニュース→市場クローズ→リスク管理→ポジション→証拠金）。
    Returns: {"ok": bool, "reason": str}
    """
    # ① ニュースフィルター（最優先）
    news = check_news_filter(symbol)
    if news["blocked"]:
        return {
            "ok": False,
            "reason": news["reason"],
            "resumes_at": news.get("resumes_at"),
        }

    # ② 市場クローズ判定
    mkt = full_market_check(symbol)
    if not mkt["ok"]:
        return {"ok": False, "reason": mkt["reason"]}

    # ③ リスク管理チェック（当日損失 / 連続損失 / ギャップ）
    risk = risk_manager.run_all_risk_checks(symbol, entry_price)
    if risk["blocked"]:
        return {"ok": False, "reason": risk["reason"]}

    if not MT5_AVAILABLE:
        return {"ok": True, "reason": "MT5未インストール（テストモード）"}

    # ④ ポジション数チェック
    positions = mt5.positions_get(symbol=symbol) or []
    if len(positions) >= MAX_POSITIONS:
        return {"ok": False,
                "reason": f"ポジション上限 {MAX_POSITIONS} に到達"}

    # ⑤ フリーマージンチェック
    acc = mt5.account_info()
    if acc and acc.margin_free < MIN_MARGIN:
        return {"ok": False,
                "reason": f"フリーマージン不足: ${acc.margin_free:.0f}"}

    return {"ok": True, "reason": "全チェック通過"}


# ─────────────────────────── ATRベース計算 ────────────────

def _get_atr15m(symbol: str) -> float:
    """15分足ATR14を返す（MT5から取得）"""
    if not MT5_AVAILABLE:
        return 20.0  # テスト用デフォルト

    try:
        import pandas as pd

        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 50)
        if rates is None or len(rates) < 20:
            return 20.0

        df = pd.DataFrame(rates)

        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr_value = tr.rolling(window=14, min_periods=14).mean().iloc[-1]
        if pd.isna(atr_value):
            return 20.0

        return float(atr_value)
    except Exception as e:
        logger.error("ATR取得エラー: %s", e)
        return 20.0


def build_order_params(trigger: dict, ai_result: dict,
                        ai_decision_id: int = None) -> dict:
    """
    ATRベースでSL/TP・ロットサイズを計算して注文パラメータを返す。
    """
    symbol    = trigger.get("symbol", SYMBOL)
    direction = trigger.get("direction", "buy")
    price     = trigger.get("price", 0.0)

    # _get_atr15m は MT5から取得したATRをdollar価格単位で返す
    # 例: GOLD 15m ATR = 3.5（価格が平均3.5ドル動く）
    atr_dollar = _get_atr15m(symbol)

    # ATRボラティリティフィルター（異常ボラ・値動きなし を排除）
    atr_max = SYSTEM_CONFIG.get("atr_volatility_max", 30.0)
    atr_min = SYSTEM_CONFIG.get("atr_volatility_min", 3.0)
    if atr_dollar > atr_max:
        logger.warning(
            "ATRボラ過多フィルター: atr=%.2f > max=%.1f → エントリー却下",
            atr_dollar, atr_max,
        )
        return None
    if atr_dollar < atr_min:
        logger.warning(
            "ATRボラ不足フィルター: atr=%.2f < min=%.1f → エントリー却下",
            atr_dollar, atr_min,
        )
        return None

    # SL距離計算（dollar価格単位）
    # MIN_SL_PIPS / MAX_SL_PIPS もdollar価格単位として流用（5.0〜50.0ドル上限）
    sl_dollar = round(atr_dollar * ATR_SL_MULT, 3)
    sl_dollar = max(MIN_SL_PIPS, min(MAX_SL_PIPS, sl_dollar))

    # ロットサイズ計算
    # GOLD 1 lot = 100 oz → 価格1ドル変動 = $100/lot の損益
    # ∴ lot_size = risk_amount / (sl_dollar × 100)
    balance   = 10000.0
    if MT5_AVAILABLE:
        acc = mt5.account_info()
        if acc:
            balance = acc.balance

    risk_amount = balance * (RISK_PERCENT / 100.0)
    lot_size    = round(risk_amount / (sl_dollar * 100.0), 2)
    lot_size    = max(0.01, lot_size)

    # 価格計算（ATRはdollar価格単位なのでそのまま引き算）
    if direction == "buy":
        sl_price = round(price - sl_dollar, 3)
        tp_price = round(price + atr_dollar * ATR_TP_MULT, 3)
    else:
        sl_price = round(price + sl_dollar, 3)
        tp_price = round(price - atr_dollar * ATR_TP_MULT, 3)

    order_type    = ai_result.get("order_type", "market")
    limit_price   = ai_result.get("limit_price")
    limit_expiry  = ai_result.get("limit_expiry")

    return {
        "symbol":          symbol,
        "direction":       direction,
        "order_type":      order_type,
        "lot_size":        lot_size,
        "entry_price":     limit_price if order_type == "limit" else price,
        "sl_price":        sl_price,
        "tp_price":        tp_price,
        "sl_dollar":       sl_dollar,    # dollar価格単位（旧sl_pips）
        "atr_dollar":      atr_dollar,   # dollar価格単位（旧atr_pips）
        "limit_expiry":    limit_expiry,
        "ai_decision_id":  ai_decision_id,
    }


# ─────────────────────────── 注文送信 ─────────────────────

def _build_mt5_request(params: dict) -> dict:
    direction  = params["direction"]
    order_type = params["order_type"]
    symbol     = params["symbol"]

    action     = mt5.TRADE_ACTION_DEAL
    price      = params["entry_price"]

    if direction == "buy":
        order_type_mt5 = mt5.ORDER_TYPE_BUY
    else:
        order_type_mt5 = mt5.ORDER_TYPE_SELL

    if order_type == "limit":
        action = mt5.TRADE_ACTION_PENDING
        if direction == "buy":
            order_type_mt5 = mt5.ORDER_TYPE_BUY_LIMIT
        else:
            order_type_mt5 = mt5.ORDER_TYPE_SELL_LIMIT

    req = {
        "action":       action,
        "symbol":       symbol,
        "volume":       params["lot_size"],
        "type":         order_type_mt5,
        "price":        price,
        "sl":           params["sl_price"],
        "tp":           params["tp_price"],
        "deviation":    DEVIATION,
        "magic":        MAGIC,
        "comment":      ORDER_COMMENT,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return req


def send_order(params: dict) -> tuple[bool, int, str]:
    """
    MT5に注文を送信する。
    Returns: (success: bool, ticket: int, error_msg: str)
    """
    if not MT5_AVAILABLE:
        logger.info("【テストモード】注文スキップ: %s", params)
        return True, 0, ""

    req    = _build_mt5_request(params)
    result = mt5.order_send(req)

    if result is None:
        err = f"order_send返りNone: {mt5.last_error()}"
        logger.error(err)
        return False, 0, err

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err = f"retcode={result.retcode} comment={result.comment}"
        logger.error("注文失敗: %s | req=%s", err, params)
        return False, 0, err

    logger.info(
        "✅ 注文成功: ticket=%d %s %s %.2flot entry=%.3f sl=%.3f tp=%.3f",
        result.order, params["symbol"], params["direction"],
        params["lot_size"], params["entry_price"],
        params["sl_price"], params["tp_price"],
    )
    return True, result.order, ""


# ─────────────────────────── execute_order ────────────────

def execute_order(trigger: dict, ai_result: dict,
                   ai_decision_id: int = None,
                   position_manager=None) -> dict:
    """
    完全な注文執行フロー（pre_check → build → send → log → register）。
    Returns: {"success": bool, "ticket": int, "reason": str}
    """
    symbol      = trigger.get("symbol", SYMBOL)
    entry_price = trigger.get("price", 0.0)

    # 1. 事前チェック
    check = pre_execution_check(symbol, entry_price)
    if not check["ok"]:
        logger.info("🚫 執行前チェック NG: %s", check["reason"])
        log_event("execution_blocked", check["reason"])
        return {"success": False, "ticket": 0, "reason": check["reason"]}

    # 2. パラメータ構築
    params = build_order_params(trigger, ai_result, ai_decision_id)
    if params is None:
        reason = "ATRボラティリティフィルターによりエントリー却下"
        logger.info("🚫 %s", reason)
        log_event("execution_blocked", reason)
        return {"success": False, "ticket": 0, "reason": reason}

    # 3. 注文送信
    success, ticket, error_msg = send_order(params)

    # 4. DB記録
    exec_id = log_execution(
        ai_decision_id=ai_decision_id,
        params=params,
        ticket=ticket,
        success=success,
        error_msg=error_msg if not success else None,
    )

    # 5. ポジション管理に登録（v2）
    if success and position_manager is not None:
        position_manager.register_position(
            ticket=ticket,
            direction=params["direction"],
            entry_price=params["entry_price"],
            lot_size=params["lot_size"],
            sl_price=params["sl_price"],
            atr_pips=params["atr_dollar"],   # dollar価格単位（position_managerで流用）
            execution_id=exec_id,
        )

    return {
        "success":      success,
        "ticket":       ticket,
        "reason":       error_msg or "注文成功",
        "execution_id": exec_id,
    }
