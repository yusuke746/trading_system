"""
app.py - メインエントリーポイント（Flask起動・初期化）
AI Trading System v2.0

起動シーケンス:
1. DB初期化
2. MT5接続（3回リトライ）
3. シグナルコレクター初期化
4. バックグラウンドスレッド起動
   - position_manager（10秒）
   - loss_analyzer（10秒）
   - revaluator（15秒）
   - health_monitor（60秒）
5. Flask起動（port=5000）
"""

import logging
import os
import sys
import threading
import time

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ── 環境変数ロード ─────────────────────────────────────────────
load_dotenv()

# ── ロガー設定 ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("app")

# ── 内部モジュール ────────────────────────────────────────────
from database          import init_db
from validation        import validate_and_normalize
# from signal_collector  import SignalCollector   # 旧パイプライン（v4.0では未使用）
# from batch_processor   import BatchProcessor    # 旧パイプライン（v4.0では未使用）
from wait_buffer       import WaitBuffer
from position_manager  import PositionManager
from loss_analyzer     import LossAnalyzer
from health_monitor    import HealthMonitor, init_mt5
from revaluator        import Revaluator
from executor          import execute_order
from dashboard         import dashboard_bp
from logger_module     import log_event
from config            import SYSTEM_CONFIG

FLASK_PORT = int(os.getenv("FLASK_PORT", 80))

# ── グローバルコンポーネント ───────────────────────────────────
app             = Flask(__name__)
position_manager: PositionManager = None
batch_processor = None   # 旧パイプライン（v4.0では未使用）
collector       = None   # 旧パイプライン（v4.0では未使用）

app.register_blueprint(dashboard_bp)


# ─────────────────────────── Webhook受信 ──────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingViewアラートのWebhook受信エンドポイント（v4.0 新アーキテクチャ）"""
    try:
        alert = request.get_json(force=True, silent=True)
        if alert is None:
            return jsonify({"error": "JSON parse error"}), 400

        # 必須フィールドの存在確認（最低限）
        required = {"regime", "direction", "price", "atr5"}
        missing  = required - set(alert.keys())
        if missing:
            logger.warning("必須フィールド欠損: %s", missing)
            return jsonify({"error": f"missing fields: {missing}"}), 400

        # スコアリング（LLM不使用・ルールベース）
        from scoring_engine import calculate_score
        result = calculate_score(alert)
        decision = result["decision"]

        logger.info(
            "📊 scoring: regime=%s dir=%s decision=%s score=%.3f",
            alert.get("regime"), alert.get("direction"),
            decision, result["score"],
        )

        # DBにアラートを記録
        log_event(
            "alert_received",
            f"regime={alert.get('regime')} dir={alert.get('direction')} "
            f"decision={decision} score={result['score']:.3f}",
        )

        if decision == "approve":
            # 高インパクト時間帯チェック
            from risk_manager import is_high_impact_period
            if is_high_impact_period():
                logger.info("🚫 高インパクト時間帯のため執行スキップ")
                log_event("execution_blocked", "high_impact_period")
                return jsonify({"status": "blocked", "reason": "high_impact_period"}), 200

            # execute_order 用の trigger / ai_result を構築
            trigger = {
                "symbol":    alert.get("symbol", "XAUUSD"),
                "price":     float(alert.get("price", 0)),
                "direction": alert.get("direction", ""),  # "buy" / "sell"
                "atr5":      float(alert.get("atr5", 0)), # SL用ATR（5M）
            }
            ai_result = {
                "decision":     "approve",
                "score":        result["score"],
                "order_type":   "market",
                "limit_price":  None,
                "limit_expiry": None,
            }

            exec_result = execute_order(
                trigger          = trigger,
                ai_result        = ai_result,
                ai_decision_id   = None,
                position_manager = position_manager,
            )
            logger.info("🚀 執行結果: success=%s ticket=%s",
                        exec_result.get("success"), exec_result.get("ticket"))
            return jsonify({"status": "approved", "exec": exec_result}), 200

        elif decision == "wait":
            logger.info("⏳ wait: score=%.3f", result["score"])
            return jsonify({"status": "wait", "score": result["score"]}), 200

        else:  # reject
            logger.info("❌ reject: reasons=%s", result.get("reject_reasons"))
            return jsonify({
                "status":  "rejected",
                "reasons": result.get("reject_reasons"),
            }), 200

    except Exception as e:
        logger.error("Webhook例外: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────── 死活確認 ─────────────────────────
@app.route("/health", methods=["GET"])
def health():
    try:
        import MetaTrader5 as mt5
        info = mt5.terminal_info()
        connected = bool(info and getattr(info, "connected", False))
    except Exception:
        connected = False

    if connected:
        return jsonify({"status": "ok", "mt5": "connected"}), 200
    else:
        return jsonify({"status": "error", "mt5": "disconnected"}), 503


# ─────────────────────────── 起動シーケンス ───────────────────
def startup():
    global position_manager, batch_processor, collector

    logger.info("=" * 60)
    logger.info("  AI Trading System v2.0 起動開始")
    logger.info("=" * 60)

    # 1. DB初期化
    logger.info("[1/5] DB初期化...")
    init_db()

    # 2. MT5接続
    logger.info("[2/5] MT5接続...")
    mt5_ok = init_mt5()
    if not mt5_ok:
        logger.warning("MT5接続失敗。テストモードで続行します。")
    log_event("system_start", f"MT5接続={'OK' if mt5_ok else 'NG（テストモード）'}")

    # 3. コンポーネント初期化
    logger.info("[3/5] コンポーネント初期化...")

    wait_buffer_obj  = WaitBuffer()
    position_manager = PositionManager()
    notifier_module  = _build_notifier()

    # revaluator（position_managerを注入）
    revaluator_obj = Revaluator(
        wait_buffer      = wait_buffer_obj,
        position_manager = position_manager,
    )

    # ── 以下は旧パイプライン（v3.5以前）。v4.0では未使用。削除せずコメントアウト。
    # batch_processor = BatchProcessor(
    #     wait_buffer      = wait_buffer_obj,
    #     revaluator       = revaluator_obj,
    #     position_manager = position_manager,
    # )
    # collector = SignalCollector(on_batch_ready=batch_processor.process)

    # 4. バックグラウンドスレッド起動
    logger.info("[4/5] バックグラウンドスレッド起動...")

    health_monitor = HealthMonitor(notifier=notifier_module)
    loss_analyzer  = LossAnalyzer(notifier=notifier_module)

    position_manager.start()
    loss_analyzer.start()
    revaluator_obj.start()
    health_monitor.start()

    # pending_monitor（指値監視・23:30以降のキャンセル）
    threading.Thread(
        target=_pending_monitor_loop,
        daemon=True,
        name="PendingMonitor"
    ).start()

    # eod_close_monitor（デイリーブレイク前の全ポジション強制クローズ）
    threading.Thread(
        target=_eod_close_loop,
        daemon=True,
        name="EodCloseMonitor"
    ).start()

    # MetaOptimizer: 毎週日曜UTC20:00に自動実行（バックグラウンド）
    from meta_optimizer import MetaOptimizer
    meta_opt = MetaOptimizer()
    meta_opt.start_weekly_scheduler()

    # DbMaintenance: 毎週日曜UTC21:00にDB保持ポリシー適用+VACUUM（バックグラウンド）
    from db_maintenance import DbMaintenance
    db_maint = DbMaintenance()
    db_maint.start_weekly_scheduler()

    logger.info("[5/5] Flask起動 port=%d", FLASK_PORT)
    logger.info("=" * 60)
    logger.info("  🚀 システム起動完了")
    logger.info("  Webhook: http://0.0.0.0:%d/webhook", FLASK_PORT)
    logger.info("  Dashboard: http://localhost:%d/dashboard", FLASK_PORT)
    logger.info("  Health: http://localhost:%d/health", FLASK_PORT)
    logger.info("=" * 60)


def _build_notifier():
    """notifierモジュールをラップしたオブジェクトを返す"""
    import notifier as n
    class _Notifier:
        def notify_mt5_disconnected(self):
            n.notify_mt5_disconnected()
        def notify_ai_api_error(self):
            n.notify_ai_api_error()
        def notify_loss_alert(self, pnl_usd, ticket):
            n.notify_loss_alert(pnl_usd, ticket)
    return _Notifier()


def _eod_close_loop():
    """5秒ごとに監視し、23:30 UTC 以降はデイリーブレイク終了（翌01:00 UTC）まで
    残存ポジションを継続クローズする。
    eod_close 発火後に開設された新ポジションも見逃さないよう、
    1日1回ではなく「EOD時間帯にポジションがある限り繰り返す」設計。"""
    try:
        import executor as exc_mod
    except ImportError:
        return

    EOD_H = SYSTEM_CONFIG["eod_close_h"]
    EOD_M = SYSTEM_CONFIG["eod_close_m"]
    DB_END_H = SYSTEM_CONFIG["daily_break_end_h"]   # 1（翌01:00 UTC）

    from datetime import datetime, timezone, time as dtime
    last_log_minute = None   # 同一分に重複ログを出さないための制御

    while True:
        try:
            now = datetime.now(timezone.utc)
            t = now.time()

            # EOD時間帯判定: 23:30以降 OR 翌00:00〜01:00（日付をまたぐケア）
            in_eod_window = (
                t >= dtime(EOD_H, EOD_M)          # 23:30〜23:59
                or t < dtime(DB_END_H, 0)          # 00:00〜00:59（翌01:00まで）
            )

            if in_eod_window:
                try:
                    import MetaTrader5 as mt5
                    positions = mt5.positions_get(symbol=SYSTEM_CONFIG["symbol"])
                    has_positions = positions is not None and len(positions) > 0
                except Exception:
                    has_positions = True   # MT5取得失敗時は安全側（クローズ試行）

                if has_positions:
                    cur_min = now.strftime("%H:%M")
                    if last_log_minute != cur_min:
                        logger.info(
                            "⏰ EODクローズ実行（%02d:%02d UTC）残ポジションを決済します",
                            EOD_H, EOD_M
                        )
                        last_log_minute = cur_min

                    results = exc_mod.close_all_positions(reason="eod_close")
                    closed_count = sum(1 for r in results if r["success"])
                    if closed_count > 0:
                        logger.info(
                            "✅ EODクローズ: %d/%d ポジション決済",
                            closed_count, len(results)
                        )
                        log_event(
                            "eod_close_summary",
                            f"closed={closed_count}/{len(results)}"
                        )
                    time.sleep(30)   # クローズ後は30秒待機（残ポジションを再チェック）
                    continue

        except Exception as e:
            logger.error("EodCloseMonitor例外: %s", e)
        time.sleep(5)


def _pending_monitor_loop():
    """5秒ごとに未約定指値を監視し、23:30以降は自動キャンセル"""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return

    SYMBOL     = SYSTEM_CONFIG["symbol"]
    MAGIC      = SYSTEM_CONFIG["magic_number"]
    # 仕様通り 23:30 UTC からキャンセル開始（limit_cancel_start_h/mを直接使用）
    CANCEL_H   = SYSTEM_CONFIG["limit_cancel_start_h"]
    CANCEL_M   = SYSTEM_CONFIG["limit_cancel_start_m"]

    from datetime import datetime, timezone, time as dtime
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.time() >= dtime(CANCEL_H, CANCEL_M):
                orders = mt5.orders_get(symbol=SYMBOL) or []
                for order in orders:
                    if order.magic == MAGIC:
                        mt5.order_send({
                            "action":   mt5.TRADE_ACTION_REMOVE,
                            "order":    order.ticket,
                        })
                        log_event("pending_cancelled",
                                  f"ticket={order.ticket} (デイリーブレイク前)")
                        logger.info("🗑 指値キャンセル: ticket=%d", order.ticket)
        except Exception as e:
            logger.error("PendingMonitor例外: %s", e)
        time.sleep(5)


# ─────────────────────────── エントリーポイント ───────────────
if __name__ == "__main__":
    startup()
    app.run(
        host="0.0.0.0",
        port=FLASK_PORT,
        debug=False,
        use_reloader=False,
        threaded=True,   # 500ms収集窓で複数Webhookが同時着信するため必須
    )
