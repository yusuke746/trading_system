"""
dashboard.py - ブラウザダッシュボード（Flask Blueprint）
AI Trading System v2.0

URL: http://localhost:5000/dashboard （15秒自動更新）
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, render_template_string

from database import get_connection

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

# ─────────────────────────── HTMLテンプレート ─────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Trading Dashboard v2</title>
  <meta http-equiv="refresh" content="15" />
  <style>
    body { font-family: 'Segoe UI', sans-serif; background:#0d1117; color:#c9d1d9; margin:0; padding:20px; }
    h1   { color:#58a6ff; margin-bottom:4px; }
    .sub { color:#8b949e; font-size:.85rem; margin-bottom:24px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin-bottom:24px; }
    .card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; }
    .card h3 { color:#79c0ff; margin:0 0 12px; font-size:.9rem; text-transform:uppercase; letter-spacing:.05em; }
    .stat { font-size:1.8rem; font-weight:700; color:#58a6ff; }
    .label { color:#8b949e; font-size:.8rem; margin-top:4px; }
    table  { width:100%; border-collapse:collapse; font-size:.82rem; }
    th     { color:#8b949e; text-align:left; padding:6px 8px; border-bottom:1px solid #30363d; }
    td     { padding:6px 8px; border-bottom:1px solid #21262d; }
    .buy   { color:#3fb950; } .sell { color:#f85149; }
    .approve { color:#3fb950; } .reject { color:#f85149; } .wait { color:#d29922; }
    .updated { color:#8b949e; font-size:.75rem; text-align:right; margin-top:12px; }
  </style>
</head>
<body>
<h1>🤖 AI Trading System v2.0</h1>
<p class="sub">XAUUSD / XMTrading MT5 | 15秒自動更新</p>

<div class="grid" id="stats-grid">
  <div class="card" id="account-card">
    <h3>💰 口座情報</h3>
    <p class="stat" id="balance">---</p>
    <p class="label">残高 (USD)</p>
    <p id="equity-row">Equity: ---</p>
    <p id="margin-row">Free Margin: ---</p>
    <p id="pos-count">Open: ---</p>
  </div>
  <div class="card">
    <h3>📊 本日成績</h3>
    <p class="stat" id="today-pnl">---</p>
    <p class="label">本日PnL (USD)</p>
    <p id="win-rate">勝率: ---</p>
    <p id="trade-count">取引数: ---</p>
  </div>
  <div class="card">
    <h3>🔌 システム状態</h3>
    <p class="stat" id="mt5-status">---</p>
    <p id="last-signal">最終シグナル: ---</p>
    <p id="wait-count">waitバッファ: ---</p>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h3>📋 直近シグナル</h3>
  <table id="signals-table">
    <thead><tr><th>時刻</th><th>Source</th><th>Type</th><th>Direction</th><th>Price</th></tr></thead>
    <tbody id="signals-body"></tbody>
  </table>
</div>

<div class="card" style="margin-bottom:16px">
  <h3>🤖 直近AI判定</h3>
  <div style="overflow-x:auto">
  <table id="ai-table" style="min-width:700px">
    <thead><tr><th>時刻</th><th>Decision</th><th>EV</th><th>Conf</th><th>理由</th></tr></thead>
    <tbody id="ai-body"></tbody>
  </table>
  </div>
</div>

<div class="card" style="margin-bottom:16px;border-color:#f85149">
  <h3>⚠️ リスク管理（デモ用リセット）</h3>
  <div id="risk-status" style="margin-bottom:10px;font-size:.85rem">読み込み中...</div>
  <button onclick="resetRisk(false)" style="background:#21262d;border:1px solid #f85149;color:#f85149;padding:6px 14px;border-radius:6px;cursor:pointer;margin-right:8px">
    🔄 日次リセット（補正挿入）
  </button>
  <button onclick="resetRisk(true)" style="background:#21262d;border:1px solid #8b949e;color:#8b949e;padding:6px 14px;border-radius:6px;cursor:pointer">
    🗑 当日レコード削除
  </button>
  <div id="reset-msg" style="margin-top:8px;font-size:.8rem;color:#3fb950"></div>
</div>

<div class="card">
  <h3>📈 オープンポジション</h3>
  <table id="pos-table">
    <thead><tr><th>Ticket</th><th>Direction</th><th>Lot</th><th>Entry</th><th>Current</th><th>PnL</th></tr></thead>
  <tbody id="pos-body"></tbody>
  </table>
</div>

<p class="updated" id="updated-at"></p>

<script>
async function refresh() {
  try {
    const r = await fetch('/dashboard/api/status');
    const d = await r.json();

    // 口座
    const acc = d.account || {};
    document.getElementById('balance').textContent    = acc.balance    ? acc.balance.toFixed(2)     : '---';
    document.getElementById('equity-row').textContent = 'Equity: '     + (acc.equity     ?? '---');
    document.getElementById('margin-row').textContent = 'Free Margin: '+ (acc.margin_free ?? '---');
    document.getElementById('pos-count').textContent  = 'Open: '       + (acc.open_positions ?? 0) + '件';

    // 本日
    const today = d.today || {};
    document.getElementById('today-pnl').textContent  = today.pnl_usd !== undefined ? today.pnl_usd.toFixed(2) : '---';
    document.getElementById('win-rate').textContent   = '勝率: ' + (today.win_rate  ?? '---');
    document.getElementById('trade-count').textContent= '取引数: '+ (today.trades   ?? 0) + '件';

    // システム
    document.getElementById('mt5-status').textContent = d.mt5_connected ? '🟢 接続中' : '🔴 切断';

    // シグナル
    const sb = document.getElementById('signals-body'); sb.innerHTML='';
    (d.recent_signals || []).forEach(s => {
      const tr = sb.insertRow();
      tr.innerHTML = `<td>${s.received_at?.substring(11,19)||''}</td>
        <td>${s.source||''}</td><td>${s.signal_type||''}</td>
        <td class="${s.direction}">${s.direction||''}</td><td>${s.price||''}</td>`;
    });

    // AI判定
    const ab = document.getElementById('ai-body'); ab.innerHTML='';
    (d.recent_decisions || []).forEach(a => {
      const tr = ab.insertRow();
      tr.innerHTML = `<td>${a.created_at?.substring(11,19)||''}</td>
        <td class="${a.decision}">${a.decision||''}</td>
        <td>${a.ev_score?.toFixed(2)||''}</td><td>${a.confidence?.toFixed(2)||''}</td>
        <td style="white-space:nowrap">${a.reason||''}</td>`;
    });

    // ポジション
    const pb = document.getElementById('pos-body'); pb.innerHTML='';
    (d.open_positions || []).forEach(p => {
      const tr = pb.insertRow();
      const pnlCls = (p.profit >= 0) ? 'buy' : 'sell';
      tr.innerHTML = `<td>${p.ticket}</td><td class="${p.type===0?'buy':'sell'}">${p.type===0?'BUY':'SELL'}</td>
        <td>${p.volume}</td><td>${p.price_open}</td><td>${p.price_current}</td>
        <td class="${pnlCls}">${p.profit?.toFixed(2)||0}</td>`;
    });

    document.getElementById('updated-at').textContent = '最終更新: ' + new Date().toLocaleTimeString();

    // リスク状態表示
    try {
      const rr = await fetch('/dashboard/api/risk_status');
      const rd = await rr.json();
      const blocked = rd.blocked;
      const daily = rd.details?.daily_loss || {};
      const consec = rd.details?.consecutive || {};
      document.getElementById('risk-status').innerHTML =
        `状態: <strong style="color:${blocked?'#f85149':'#3fb950'}">${blocked?'🔴 ブロック中':'🟢 取引可能'}</strong>` +
        ` &nbsp;|&nbsp; 当日PnL: <span style="color:${(daily.daily_pnl_usd||0)<0?'#f85149':'#3fb950'}">${(daily.daily_pnl_usd||0).toFixed(0)}</span>` +
        ` &nbsp;|&nbsp; 連続負け: ${consec.consecutive_count||0}回` +
        (blocked ? `<br><span style="color:#f85149;font-size:.78rem">${rd.reason}</span>` : '');
    } catch(e) {}
  } catch(e) { console.error(e); }
}
async function resetRisk(deleteRecords) {
  const msg = deleteRecords ? '当日レコードを物理削除します。本当によいですか？' : '補正レコードを挿入して日次PnLをリセットします。';
  if (!confirm(msg)) return;
  const r = await fetch('/dashboard/api/reset_risk', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({delete_records: deleteRecords})
  });
  const d = await r.json();
  document.getElementById('reset-msg').textContent = d.message || d.error || '';
  refresh();
}
refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


@dashboard_bp.route("/", methods=["GET"])
def dashboard_index():
    return render_template_string(HTML_TEMPLATE)


@dashboard_bp.route("/api/status", methods=["GET"])
def api_status():
    """MT5状態・口座サマリー・本日集計・直近シグナル・AI判定・オープンポジション"""
    data: dict = {"mt5_connected": False, "account": {}, "today": {},
                  "recent_signals": [], "recent_decisions": [],
                  "open_positions": [], "news_filter": {}}

    # MT5情報
    try:
        import MetaTrader5 as mt5
        info = mt5.terminal_info()
        data["mt5_connected"] = bool(info and getattr(info, "connected", False))
        acc = mt5.account_info()
        if acc:
            data["account"] = {
                "balance":        acc.balance,
                "equity":         acc.equity,
                "margin_free":    acc.margin_free,
                "open_positions": len(mt5.positions_get() or []),
            }
        # オープンポジション
        positions = mt5.positions_get() or []
        data["open_positions"] = [
            {
                "ticket":        p.ticket,
                "type":          p.type,
                "volume":        p.volume,
                "price_open":    p.price_open,
                "price_current": p.price_current,
                "profit":        p.profit,
            }
            for p in positions
        ]
    except Exception as e:
        logger.warning("MT5情報取得失敗（接続切断の可能性）: %s", e)
        data["mt5_connected"] = False
        data["mt5_error"]     = str(e)

    conn = get_connection()
    try:
        # 直近シグナル10件
        rows = conn.execute("""
            SELECT received_at, source, signal_type, event, direction, price
            FROM signals ORDER BY id DESC LIMIT 10
        """).fetchall()
        data["recent_signals"] = [dict(r) for r in rows]

        # 直近AI判定10件
        rows = conn.execute("""
            SELECT created_at, decision, confidence, ev_score, reason
            FROM ai_decisions ORDER BY id DESC LIMIT 10
        """).fetchall()
        data["recent_decisions"] = [dict(r) for r in rows]

        # 本日集計
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute("""
            SELECT COUNT(*) as trades,
                   SUM(pnl_usd) as pnl_usd,
                   SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
            FROM trade_results WHERE closed_at LIKE ?
        """, (f"{today}%",)).fetchone()
        if row and row["trades"]:
            win_rate = f"{(row['wins'] or 0) / row['trades'] * 100:.1f}%"
            data["today"] = {
                "trades":   row["trades"],
                "pnl_usd":  round(row["pnl_usd"] or 0, 2),
                "win_rate": win_rate,
            }

    finally:
        conn.close()

    # ニュースフィルター状態（最新の状態を取得）
    try:
        from news_filter import check_news_filter
        nf = check_news_filter()
        data["news_filter"] = {
            "blocked":             nf.get("blocked", False),
            "reason":              nf.get("reason", ""),
            "resumes_at":          nf.get("resumes_at"),
            "fail_safe_triggered": nf.get("fail_safe_triggered", False),
        }
    except Exception as e:
        data["news_filter"] = {"error": str(e)}

    return jsonify(data)


@dashboard_bp.route("/api/loss_analysis", methods=["GET"])
def api_loss_analysis():
    """負けトレード一覧"""
    from flask import request
    days = int(request.args.get("days", 30))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT closed_at, mt5_ticket, outcome, pnl_usd, pnl_pips,
                   duration_min, loss_reason, missed_context, prompt_hint
            FROM trade_results
            WHERE outcome = 'sl_hit' AND closed_at >= ?
            ORDER BY closed_at DESC
        """, (since,)).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@dashboard_bp.route("/api/prompt_hints", methods=["GET"])
def api_prompt_hints():
    """プロンプト改善ヒントの頻出パターント10"""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT prompt_hint, COUNT(*) as cnt
            FROM trade_results
            WHERE prompt_hint IS NOT NULL AND prompt_hint != ''
            GROUP BY prompt_hint
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@dashboard_bp.route("/api/stats", methods=["GET"])
def api_stats():
    """勝率・PnL・平均保有時間などの期間別集計"""
    from flask import request
    days = int(request.args.get("days", 30))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                     as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usd)                                 as total_pnl,
                AVG(pnl_usd)                                 as avg_pnl,
                AVG(duration_min)                            as avg_duration_min,
                AVG(pnl_pips)                                as avg_pips
            FROM trade_results WHERE closed_at >= ?
        """, (since,)).fetchone()
        result = dict(row) if row else {}
        if result.get("total_trades"):
            result["win_rate"] = round(
                result["wins"] / result["total_trades"] * 100, 1)
        return jsonify(result)
    finally:
        conn.close()


@dashboard_bp.route("/api/optimizer", methods=["GET"])
def api_optimizer():
    """動的パラメータ最適化の現在値と履歴を返す"""
    import param_optimizer
    from flask import request
    n = int(request.args.get("n", 10))
    latest  = param_optimizer.get_latest_from_db()
    history = param_optimizer.get_history(n=n)
    return jsonify({"latest": latest, "history": history})


@dashboard_bp.route("/api/risk_status", methods=["GET"])
def api_risk_status():
    """現在のリスクチェック状態を返す"""
    import risk_manager
    result = risk_manager.run_all_risk_checks(symbol="GOLD")
    return jsonify(result)


@dashboard_bp.route("/api/reset_risk", methods=["POST"])
def api_reset_risk():
    """日次ストップ・連続損失カウントをリセットする（デモ用）"""
    from flask import request
    import risk_manager
    body         = request.get_json(silent=True) or {}
    delete_records = bool(body.get("delete_records", False))
    result = risk_manager.reset_daily_stats(delete_records=delete_records)
    if result["ok"]:
        return jsonify(result)
    return jsonify({"error": result["message"]}), 500


@dashboard_bp.route("/api/backtest", methods=["GET"])
def api_backtest():
    """
    MT5 または DB のデータを使ってバックテストを実行し、結果を返す。
    パラメータ:
      bars            : 取得バー数（デフォルト 1000）
      sl_mult         : ATR SL乗数（デフォルト config 値）
      tp_mult         : ATR TP乗数（デフォルト config 値）
      strategy        : breakout | rsi（デフォルト breakout）
      grid            : 1 を指定するとグリッドサーチ結果を返す
      ai_mock         : 1 を指定するとAIモックフィルターを有効化
      ai_approve_rate : AIモックの承認率（デフォルト 0.6）
    """
    from flask import request
    from backtester import BacktestEngine, load_mt5_data, grid_search
    from backtester import atr_breakout_signal, rsi_reversal_signal
    from config import SYSTEM_CONFIG

    bars            = int(request.args.get("bars", 1000))
    sl_mult         = request.args.get("sl_mult",  type=float)
    tp_mult         = request.args.get("tp_mult",  type=float)
    strategy        = request.args.get("strategy", "breakout")
    do_grid         = request.args.get("grid", "0") == "1"
    use_ai_mock     = request.args.get("ai_mock", "0") == "1"
    ai_approve_rate = float(request.args.get("ai_approve_rate", 0.6))

    signal_func = rsi_reversal_signal if strategy == "rsi" else atr_breakout_signal

    try:
        df = load_mt5_data(SYSTEM_CONFIG["symbol"], "M15", bars)
    except Exception as e:
        return jsonify({"error": f"MT5データ取得失敗: {e}"}), 503

    if do_grid:
        results = grid_search(df, signal_func=signal_func)
        return jsonify({"grid_results": results[:20]})

    params = {}
    if sl_mult is not None:
        params["atr_sl_multiplier"] = sl_mult
    if tp_mult is not None:
        params["atr_tp_multiplier"] = tp_mult

    engine = BacktestEngine(df, params)
    result = engine.run(signal_func,
                        use_ai_mock=use_ai_mock,
                        ai_approve_rate=ai_approve_rate)

    ai_filter_effect = None
    if use_ai_mock:
        base_result      = engine.run(signal_func, use_ai_mock=False)
        ai_filter_effect = round(result.win_rate * 100 - base_result.win_rate * 100, 2)

    return jsonify({
        "n_trades":         result.n_trades,
        "win_rate":         round(result.win_rate, 3),
        "total_pnl":        round(result.total_pnl, 2),
        "profit_factor":    result.profit_factor,
        "max_drawdown":     result.max_drawdown,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio":     result.sharpe_ratio,
        "params":           result.params,
        "bars":             bars,
        "ai_mock":          use_ai_mock,
        "ai_approve_rate":  ai_approve_rate if use_ai_mock else None,
        "ai_filter_effect": ai_filter_effect,
    })
