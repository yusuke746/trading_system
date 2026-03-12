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

from config import SYSTEM_CONFIG, SESSION_SLTP_ADJUST
from market_hours import full_market_check, get_current_session
from news_filter import check_news_filter
from logger_module import log_execution, log_event
import risk_manager
import param_optimizer

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

    # ④-2 口座全体リスクエクスポージャーチェック
    acc = mt5.account_info()
    if acc and acc.balance > 0:
        max_total_risk = acc.balance * SYSTEM_CONFIG.get("max_total_risk_percent", 0.05)
        total_risk_usd = sum(
            abs(p.price_open - p.sl) * p.volume * 100
            for p in positions
            if p.sl > 0
        )
        if total_risk_usd > max_total_risk:
            return {
                "ok": False,
                "reason": (
                    f"口座全体リスク上限超過: "
                    f"現在リスク ${total_risk_usd:.1f} / 上限 ${max_total_risk:.1f} "
                    f"(残高 ${acc.balance:.0f} × {SYSTEM_CONFIG.get('max_total_risk_percent', 0.05)*100:.1f}%)"
                ),
            }

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

        # start_pos=1: 形成中（未確定）の現在バーを除外し、確定済みバーのみで ATR を計算
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 50)
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


def _get_current_market_price(symbol: str, direction: str) -> float | None:
    """
    MT5から最新の成行価格を取得する。
    buy  → ask（売り手が提示する価格＝買い手が払う価格）
    sell → bid（買い手が提示する価格＝売り手が受け取る価格）
    取得失敗時は None を返す。
    """
    if not MT5_AVAILABLE:
        return None
    try:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return tick.ask if direction == "buy" else tick.bid
    except Exception as e:
        logger.error("現在価格取得エラー: %s", e)
        return None


def _get_setup_type(ai_result: dict) -> str:
    """
    score_breakdownからセットアップ種別を判定する。
    Returns: 'sweep_reversal' | 'trend_continuation' | 'standard'
    """
    breakdown = ai_result.get("score_breakdown", {})
    if "liquidity_sweep" in breakdown:
        return "sweep_reversal"
    regime = ai_result.get("market_regime", "range")
    if regime in ("trend", "breakout") and "zone_touch_aligned_with_trend" in breakdown:
        return "trend_continuation"
    return "standard"


def build_order_params(trigger: dict, ai_result: dict,
                        ai_decision_id: int = None,
                        atr_override: float | None = None) -> dict | None:
    """
    ATRベースでSL/TP・ロットサイズを計算して注文パラメータを返す。
    ATR乗数は param_optimizer.get_live_params() により動的に調整される。
    market注文の場合はMT5の最新価格でエントリー価格を上書きする（再評価時の古値ズレ対策）。
    """
    symbol    = trigger.get("symbol", SYMBOL)
    direction = trigger.get("direction", "buy")
    price     = trigger.get("price", 0.0)

    # trigger に atr5 があればそれを atr_override として使用
    _atr5 = trigger.get("atr5", None)
    if _atr5 is not None:
        _atr5 = float(_atr5)
        atr_override = _atr5 if _atr5 > 0 else None

    order_type = ai_result.get("order_type", "market")

    # market注文の場合はMT5の現在価格に上書き（再評価時の古値ズレ対策）
    if order_type == "market":
        fresh_price = _get_current_market_price(symbol, direction)
        if fresh_price is not None and fresh_price > 0:
            if abs(fresh_price - price) > 0.1:   # 0.1ドル以上ズレていればログ
                logger.info(
                    "⏱ 価格更新: trigger_price=%.3f → fresh_price=%.3f (Δ%.3f)",
                    price, fresh_price, fresh_price - price,
                )
            price = fresh_price

    # 動的パラメータ取得（市場環境・成績に基づくATR乗数調整）
    live_params = param_optimizer.get_live_params()
    dyn_sl_mult = live_params.get("atr_sl_multiplier", ATR_SL_MULT)
    dyn_tp_mult = live_params.get("atr_tp_multiplier", ATR_TP_MULT)

    # セッション別 SL/TP 補正
    session_info = get_current_session()
    session_name = session_info.get("session", "London")
    sess_adj     = SESSION_SLTP_ADJUST.get(session_name, {"sl_mult": 1.0, "tp_mult": 1.0})
    dyn_sl_mult  = round(dyn_sl_mult * sess_adj["sl_mult"], 4)
    dyn_tp_mult  = round(dyn_tp_mult * sess_adj["tp_mult"], 4)
    logger.info(
        "📅 セッション補正: session=%s sl_mult=%.2f tp_mult=%.2f",
        session_name, dyn_sl_mult, dyn_tp_mult,
    )

    # SL用ATR: atr5（atr_override）があればそれを使う、なければ15M ATR
    if atr_override is not None and atr_override > 0:
        sl_atr = atr_override
        logger.info("📐 SL用ATR override 使用: atr5=%.3f", sl_atr)
    else:
        sl_atr = _get_atr15m(symbol)

    # TP用ATR: 常に15M ATR（利幅は15Mの値動き幅を基準にする）
    tp_atr = _get_atr15m(symbol)
    if tp_atr is None or tp_atr <= 0:
        tp_atr = sl_atr

    # ATRボラティリティフィルター（sl_atr に対して適用）
    atr_max = SYSTEM_CONFIG.get("atr_volatility_max", 30.0)
    if atr_override is not None and atr_override > 0:
        atr_min = SYSTEM_CONFIG.get("atr5_volatility_min", 1.5)  # atr5用閾値
    else:
        atr_min = SYSTEM_CONFIG.get("atr_volatility_min", 3.0)   # 15M用閾値（従来）
    if sl_atr > atr_max:
        logger.warning(
            "ATRボラ過多フィルター: sl_atr=%.2f > max=%.1f → エントリー却下",
            sl_atr, atr_max,
        )
        return None
    if sl_atr < atr_min:
        logger.warning(
            "ATRボラ不足フィルター: sl_atr=%.2f < min=%.1f → エントリー却下",
            sl_atr, atr_min,
        )
        return None

    # セットアップ種別に応じた動的SL/TP乗数
    setup = _get_setup_type(ai_result)
    if setup == "sweep_reversal":
        # TODO: sweep_priceフィールド追加後にSLをATR×0.8から変更すること（仮実装）
        sl_mult = max(dyn_sl_mult * 0.8, SYSTEM_CONFIG.get("min_sl_pips", 5) / sl_atr)
        tp_mult = dyn_tp_mult * 1.3
    elif setup == "trend_continuation":
        sl_mult = dyn_sl_mult
        tp_mult = dyn_tp_mult * 1.2
    else:
        sl_mult = dyn_sl_mult
        tp_mult = dyn_tp_mult
    # setup_typeをai_resultに記録してDBログに残す
    ai_result["setup_type"] = setup

    # SL距離計算（dollar価格単位）
    # MIN_SL_PIPS / MAX_SL_PIPS もdollar価格単位として流用（5.0〜50.0ドル上限）
    sl_dollar = round(sl_atr * sl_mult, 3)
    sl_dollar = max(MIN_SL_PIPS, min(MAX_SL_PIPS, sl_dollar))

    # ロットサイズ計算
    # GOLD 1 lot = 100 oz → 価格1ドル変動 = $100/lot の損益
    # ∴ lot_size = risk_amount_usd / (sl_dollar × 100)
    # 注意: 口座通貨がJPY等の場合はUSDに換算してからリスク計算を行う
    balance_usd = 10000.0
    if MT5_AVAILABLE:
        acc = mt5.account_info()
        if acc:
            balance = acc.balance
            currency = acc.currency
            if currency == "USD":
                balance_usd = balance
            else:
                # 口座通貨 → USD 換算レートを取得
                # 例: 口座通貨=JPY → USDJPY のbidで割る
                sym_direct = f"USD{currency}"   # USDJPY
                sym_inv    = f"{currency}USD"   # JPYUSD（存在しない場合が多い）
                info_direct = mt5.symbol_info(sym_direct)
                if info_direct is not None:
                    usdjpy = info_direct.bid
                    balance_usd = balance / usdjpy if usdjpy > 0 else balance
                    logger.info(
                        "💱 口座通貨=%s balance=%.2f %s → USD換算=%.2f (rate=%.4f)",
                        currency, balance, currency, balance_usd, usdjpy,
                    )
                else:
                    info_inv = mt5.symbol_info(sym_inv)
                    if info_inv is not None:
                        rate = info_inv.bid
                        balance_usd = balance * rate if rate > 0 else balance
                        logger.info(
                            "💱 口座通貨=%s balance=%.2f %s → USD換算=%.2f (rate=%.4f)",
                            currency, balance, currency, balance_usd, rate,
                        )
                    else:
                        # レート取得不可: 生残高をそのまま使わず fallback_balance を使用（過大ロット防止）
                        balance_usd = SYSTEM_CONFIG.get("fallback_balance", 10000.0)
                        logger.warning(
                            "⚠️ 口座通貨=%s のUSD換算レートが取得できません。"
                            "fallback_balance=%.2f USDを使用します（過大ロット防止）",
                            currency, balance_usd,
                        )

    risk_amount = balance_usd * (RISK_PERCENT / 100.0)
    lot_size    = round(risk_amount / (sl_dollar * 100.0), 2)
    lot_size    = max(0.01, lot_size)

    # 価格計算（ATRはdollar価格単位なのでそのまま引き算）
    if direction == "buy":
        sl_price = round(price - sl_dollar, 3)
        tp_price = round(price + tp_atr * tp_mult, 3)
    else:
        sl_price = round(price + sl_dollar, 3)
        tp_price = round(price - tp_atr * tp_mult, 3)

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
        "atr_dollar":      tp_atr,       # 15M ATR dollar価格単位（旧atr_pips / position_manager用）
        "atr_sl_mult":     sl_mult,       # 動的調整後のSL乗数（記録用）
        "atr_tp_mult":     tp_mult,       # 動的調整後のTP乗数（記録用）
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


# ─────────────────────────── EOD全ポジションクローズ ────────

def close_all_positions(symbol: str = SYMBOL, reason: str = "eod_close") -> list[dict]:
    """
    指定シンボルの全オープンポジションを成行で決済する。
    Returns: 各ポジションの結果リスト [{ticket, success, error}]
    """
    results = []
    if not MT5_AVAILABLE:
        logger.info("【テストモード】close_all_positions スキップ")
        return results

    positions = mt5.positions_get(symbol=symbol) or []
    if not positions:
        logger.info("close_all_positions: オープンポジションなし")
        return results

    for pos in positions:
        if pos.magic != MAGIC:
            continue  # このシステム以外のポジションは触らない

        # 決済方向はエントリーの逆
        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price      = mt5.symbol_info_tick(symbol).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price      = mt5.symbol_info_tick(symbol).ask

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    DEVIATION,
            "magic":        MAGIC,
            "comment":      reason,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        ok  = bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)
        err = "" if ok else (f"retcode={result.retcode}" if result else "None")

        log_event(reason, f"ticket={pos.ticket} vol={pos.volume} ok={ok} {err}")
        logger.info(
            "🔒 EODクローズ: ticket=%d vol=%.2f price=%.3f ok=%s %s",
            pos.ticket, pos.volume, price, ok, err
        )
        results.append({"ticket": pos.ticket, "success": ok, "error": err})

    return results


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
            tp_price=params["tp_price"],     # TP保持用（BE/トレーリング時に消えないよう）
            atr_pips=params["atr_dollar"],   # dollar価格単位（position_managerで流用）
            execution_id=exec_id,
        )

    return {
        "success":      success,
        "ticket":       ticket,
        "reason":       error_msg or "注文成功",
        "execution_id": exec_id,
    }
