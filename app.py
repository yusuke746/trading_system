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
from signal_collector  import SignalCollector
from wait_buffer       import WaitBuffer
from position_manager  import PositionManager
from loss_analyzer     import LossAnalyzer
from health_monitor    import HealthMonitor, init_mt5
from revaluator        import Revaluator
from batch_processor   import BatchProcessor
from dashboard         import dashboard_bp
from logger_module     import log_event
from config            import SYSTEM_CONFIG

FLASK_PORT = int(os.getenv("FLASK_PORT", 80))

# ── グローバルコンポーネント ───────────────────────────────────
app             = Flask(__name__)
position_manager: PositionManager = None
batch_processor:  BatchProcessor  = None
collector:        SignalCollector  = None

app.register_blueprint(dashboard_bp)


# ─────────────────────────── Webhook受信 ──────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingViewアラートのWebhook受信エンドポイント"""
    try:
        raw = request.get_json(force=True, silent=True)
        if raw is None:
            return jsonify({"error": "JSON parse error"}), 400

        signal = validate_and_normalize(raw)
        if signal is None:
            return jsonify({"error": "invalid signal"}), 400

        collector.receive(signal)
        return jsonify({"status": "ok"}), 200

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

    batch_processor = BatchProcessor(
        wait_buffer      = wait_buffer_obj,
        revaluator       = revaluator_obj,
        position_manager = position_manager,
    )

    collector = SignalCollector(on_batch_ready=batch_processor.process)

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
    """5秒ごとに監視し、23:30 UTC になったら全ポジションを成行クローズする（1日1回）"""
    try:
        import executor as exc_mod
    except ImportError:
        return

    EOD_H = SYSTEM_CONFIG["eod_close_h"]
    EOD_M = SYSTEM_CONFIG["eod_close_m"]

    from datetime import datetime, timezone, time as dtime
    last_fired_date = None   # 当日すでに発火済みかを管理
    while True:
        try:
            now = datetime.now(timezone.utc)
            today = now.date()
            if now.time() >= dtime(EOD_H, EOD_M) and last_fired_date != today:
                logger.info(
                    "⏰ EODクローズ開始（%02d:%02d UTC）", EOD_H, EOD_M
                )
                results = exc_mod.close_all_positions(reason="eod_close")
                closed_count = sum(1 for r in results if r["success"])
                logger.info(
                    "✅ EODクローズ完了: %d/%d ポジション決済",
                    closed_count, len(results)
                )
                log_event(
                    "eod_close_summary",
                    f"closed={closed_count}/{len(results)}"
                )
                last_fired_date = today
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
