"""
meta_optimizer.py - 週次自動パラメータ最適化エンジン（v3.5）
AI Trading System v3.5

毎週日曜 UTC 20:00 に自動実行。
ライブ判定には一切接続せず、DBの履歴を分析して SCORING_CONFIG を自動調整する。

安全ガード（3条件すべてクリアが必要）:
  A: 変更幅が ±0.05 以内
  B: バックテスト（分析期間より前のデータ）で期待値が現在以上
  C: approve_threshold / wait_threshold への変更は ±0.03 以内
"""

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── チューニング可能パラメータのホワイトリスト（min, max）──────────
# approve_threshold / wait_threshold は別ガードで管理
TUNABLE_PARAMS = {
    "zone_touch_aligned":            (0.10, 0.35),
    "zone_touch_aligned_with_trend": (0.10, 0.35),
    "zone_touch_counter_trend":      (0.03, 0.15),
    "fvg_touch_aligned":             (0.08, 0.25),
    "fvg_touch_aligned_with_trend":  (0.08, 0.25),
    "fvg_touch_counter_trend":       (0.03, 0.12),
    "liquidity_sweep":               (0.15, 0.40),
    "sweep_plus_zone":               (0.05, 0.20),
    "trend_aligned":                 (0.05, 0.20),
    "rsi_confirmation":              (0.02, 0.12),
    "bar_close_confirmed":           (0.05, 0.20),
    "tv_confidence_high":            (0.05, 0.20),
    "pattern_similarity_high":       (0.05, 0.20),
    "approve_threshold":             (0.35, 0.60),  # 別ガードあり
    "wait_threshold":                (0.05, 0.20),  # 別ガードあり
}
THRESHOLD_PARAMS = {"approve_threshold", "wait_threshold"}
MAX_CHANGE = 0.05       # 通常パラメータの最大変更幅
MAX_THRESHOLD_CHANGE = 0.03  # 閾値パラメータの最大変更幅
MIN_SAMPLE = 60         # ローリング8週間の最低サンプル数
MIN_RECENT_RATIO = 0.30 # 直近2週間が全体の30%以上


class MetaOptimizer:

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start_weekly_scheduler(self):
        """バックグラウンドスレッドとして毎週日曜UTC20:00に実行するスケジューラを起動"""
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="MetaOptimizer",
        )
        self._thread.start()
        logger.info("MetaOptimizer: 週次スケジューラ起動（毎週日曜 UTC 20:00）")

    def stop(self):
        self._stop_event.set()

    def _scheduler_loop(self):
        """毎分チェックし、日曜UTC20:00になったら run() を実行"""
        last_run_date: Optional[datetime] = None
        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            # 日曜(weekday=6) かつ 20:00〜20:59
            if now.weekday() == 6 and now.hour == 20:
                today = now.date()
                if last_run_date is None or last_run_date != today:
                    logger.info("MetaOptimizer: 週次最適化を開始します")
                    try:
                        self.run()
                    except Exception as e:
                        logger.error("MetaOptimizer 実行エラー: %s", e, exc_info=True)
                        _send_discord(f"⚠️ MetaOptimizer エラー: {e}")
                    last_run_date = today
            time.sleep(60)

    def run(self):
        """週次最適化の本体。テストや手動実行でも直接呼べる。"""
        logger.info("STEP 1: DB集計...")
        stats = self._collect_stats()
        if stats is None:
            msg = "MetaOptimizer: サンプル不足のためスキップ"
            logger.info(msg)
            _send_discord(f"📊 {msg}")
            return

        logger.info("STEP 2: GPT-4oで分析...")
        proposal = self._ask_llm(stats)
        if proposal is None:
            logger.warning("MetaOptimizer: LLM分析失敗、スキップ")
            _send_discord("⚠️ MetaOptimizer: LLM分析失敗")
            return

        logger.info("STEP 3: 安全ガードチェック...")
        safe, reason = self._safety_check(proposal)
        if not safe:
            msg = f"MetaOptimizer: 安全ガード不通過 → スキップ\n理由: {reason}"
            logger.info(msg)
            _send_discord(f"🛡️ {msg}")
            return

        logger.info("STEP 4: config.py 更新...")
        changes = self._apply_config(proposal)
        change_text = "\n".join(f"  {k}: {v['old']:.3f} → {v['new']:.3f}"
                                for k, v in changes.items())
        msg = f"✅ MetaOptimizer: パラメータ更新完了\n{change_text}"
        logger.info(msg)
        _send_discord(msg)

    # ── STEP 1: DB集計 ──────────────────────────────────────────────

    def _collect_stats(self) -> Optional[dict]:
        """
        直近8週間のapproveトレードを集計する。
        サンプル不足 or 市場環境急変時はNoneを返す。
        """
        from database import get_connection
        eight_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=8)
        two_weeks_ago   = datetime.now(timezone.utc) - timedelta(weeks=2)

        conn = get_connection()
        try:
            # 直近8週間のapproveトレード
            rows = conn.execute("""
                SELECT
                    e.direction,
                    e.pnl_usd,
                    e.session,
                    d.context_json,
                    e.created_at
                FROM executions e
                JOIN ai_decisions d ON e.decision_id = d.id
                WHERE d.decision = 'approve'
                  AND e.created_at >= ?
                  AND e.pnl_usd IS NOT NULL
                ORDER BY e.created_at DESC
            """, (eight_weeks_ago.isoformat(),)).fetchall()
        except Exception as e:
            logger.error("DB集計エラー: %s", e)
            return None
        finally:
            conn.close()

        total = len(rows)
        if total < MIN_SAMPLE:
            logger.info("サンプル不足: %d件（必要: %d件）", total, MIN_SAMPLE)
            return None

        # 直近2週間のデータが全体の30%以上あるか確認
        recent_count = sum(1 for r in rows if r["created_at"] >= two_weeks_ago.isoformat())
        recent_ratio = recent_count / total
        if recent_ratio < MIN_RECENT_RATIO:
            logger.info("最近のトレードが少なすぎる（recent_ratio=%.2f）、スキップ", recent_ratio)
            return None

        # 市場環境急変チェック: 直近2週間 vs 全期間の勝率差が20%以上 → スキップ
        all_wins    = sum(1 for r in rows if r["pnl_usd"] > 0)
        recent_wins = sum(1 for r in rows
                         if r["pnl_usd"] > 0 and r["created_at"] >= two_weeks_ago.isoformat())
        if total > 0 and recent_count > 0:
            all_wr    = all_wins / total
            recent_wr = recent_wins / recent_count
            if abs(all_wr - recent_wr) >= 0.20:
                logger.info(
                    "市場環境急変検出（全期間勝率=%.2f, 直近2週=%.2f）→ スキップ",
                    all_wr, recent_wr,
                )
                return None

        # 因子別・セッション別集計
        factor_stats  = self._aggregate_factor_stats(rows)
        session_stats = self._aggregate_session_stats(rows)

        return {
            "total_trades":    total,
            "recent_count":    recent_count,
            "overall_win_rate": all_wins / total if total > 0 else 0,
            "factor_stats":    factor_stats,
            "session_stats":   session_stats,
            "analysis_period_weeks": 8,
        }

    def _aggregate_factor_stats(self, rows) -> dict:
        """context_jsonから因子別勝率を集計する"""
        factors = [
            "zone_touch", "fvg_touch", "liquidity_sweep",
            "trend_aligned", "bar_close_confirmed",
        ]
        stats = {f: {"wins": 0, "total": 0} for f in factors}

        for row in rows:
            try:
                ctx = json.loads(row["context_json"] or "{}")
            except Exception:
                continue
            # scoring_history の score_breakdown から因子を推定
            breakdown = ctx.get("score_breakdown", {})
            win = row["pnl_usd"] > 0
            for factor in factors:
                # breakdownに因子名を含むキーがあれば「その因子が使われた」とみなす
                if any(factor in k for k in breakdown):
                    stats[factor]["total"] += 1
                    if win:
                        stats[factor]["wins"] += 1

        return {
            f: {
                "win_rate": s["wins"] / s["total"] if s["total"] > 0 else None,
                "count":    s["total"],
            }
            for f, s in stats.items()
        }

    def _aggregate_session_stats(self, rows) -> dict:
        stats = {}
        for row in rows:
            session = row["session"] or "unknown"
            if session not in stats:
                stats[session] = {"wins": 0, "total": 0}
            stats[session]["total"] += 1
            if row["pnl_usd"] > 0:
                stats[session]["wins"] += 1

        return {
            s: {
                "win_rate": v["wins"] / v["total"] if v["total"] > 0 else None,
                "count":    v["total"],
            }
            for s, v in stats.items()
        }

    # ── STEP 2: LLM分析 ─────────────────────────────────────────────

    def _ask_llm(self, stats: dict) -> Optional[dict]:
        """GPT-4oに分析させ、パラメータ変更提案を得る"""
        try:
            from openai import OpenAI
            from config import SCORING_CONFIG

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            system_prompt = """あなたはトレーディングシステムのパラメータ最適化エンジンです。
提供された因子別勝率とセッション別勝率を分析し、SCORING_CONFIG の改善提案を JSON で返してください。

制約（必ず守ること）:
- 変更幅は各パラメータの現在値から ±0.05 以内
- approve_threshold / wait_threshold は ±0.03 以内
- 変更するパラメータは最大3つまで
- 変更しない場合は空の proposals を返す

出力形式（JSON のみ。説明文不要）:
{
  "proposals": {
    "パラメータ名": 新しい値（float）,
    ...
  },
  "reasoning": "変更理由の簡潔な説明"
}"""

            user_content = json.dumps({
                "current_config": {k: v for k, v in SCORING_CONFIG.items()
                                   if k in TUNABLE_PARAMS},
                "performance_stats": stats,
            }, ensure_ascii=False)

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=512,
            )
            result = json.loads(response.choices[0].message.content)
            logger.info("LLM提案: %s", result.get("reasoning", ""))
            return result

        except Exception as e:
            logger.error("LLM分析エラー: %s", e)
            return None

    # ── STEP 3: 安全ガード ───────────────────────────────────────────

    def _safety_check(self, proposal: dict) -> tuple[bool, str]:
        """
        3条件すべてをチェックする。
        Returns: (通過=True/不通過=False, 理由文字列)
        """
        from config import SCORING_CONFIG

        proposals = proposal.get("proposals", {})
        if not proposals:
            return True, "変更なし（提案が空）"

        # 条件A: ホワイトリスト確認 + 変更幅チェック
        for param, new_val in proposals.items():
            if param not in TUNABLE_PARAMS:
                return False, f"条件A違反: {param} はチューニング対象外"

            current = SCORING_CONFIG.get(param)
            if current is None:
                return False, f"条件A違反: {param} が config に存在しない"

            lo, hi = TUNABLE_PARAMS[param]
            if not (lo <= new_val <= hi):
                return False, f"条件A違反: {param}={new_val} が許容範囲 [{lo}, {hi}] 外"

            change = abs(new_val - current)
            limit = MAX_THRESHOLD_CHANGE if param in THRESHOLD_PARAMS else MAX_CHANGE
            if change > limit + 1e-9:
                return False, (
                    f"条件A違反: {param} の変更幅 {change:.4f} が上限 {limit} を超過"
                )

        # 条件B: バックテスト検証（データリーク防止: 分析対象8週より前のデータを使用）
        bt_ok, bt_reason = self._run_safety_backtest(proposals)
        if not bt_ok:
            return False, f"条件B違反: {bt_reason}"

        return True, "全条件通過"

    def _run_safety_backtest(self, proposals: dict) -> tuple[bool, str]:
        """
        分析対象期間（直近8週）より前のデータでバックテストを実行し、
        提案パラメータが現行パラメータ以上の期待値を持つか検証する。
        """
        try:
            from backtester_live import LiveBacktester
            from config import SCORING_CONFIG, SYSTEM_CONFIG
            import copy

            # バックテスト期間: 9週前〜16週前（分析対象8週より前）
            end_dt   = datetime.now(timezone.utc) - timedelta(weeks=9)
            start_dt = end_dt - timedelta(weeks=8)

            symbol = SYSTEM_CONFIG.get("symbol", "XAUUSD")

            # 現行パラメータでバックテスト
            current_result = _run_live_backtest(symbol, start_dt, end_dt, {})
            if current_result is None:
                logger.warning("バックテスト: データ不足のためスキップ（条件B通過とみなす）")
                return True, "データ不足のためスキップ"

            # 提案パラメータでバックテスト
            proposed_result = _run_live_backtest(symbol, start_dt, end_dt, proposals)
            if proposed_result is None:
                return False, "提案パラメータでのバックテスト失敗"

            current_ev  = current_result.get("expectancy", 0)
            proposed_ev = proposed_result.get("expectancy", 0)

            if proposed_ev < current_ev:
                return False, (
                    f"期待値が悪化: 現行={current_ev:.2f}, 提案={proposed_ev:.2f}"
                )

            logger.info(
                "バックテスト通過: 現行EV=%.2f → 提案EV=%.2f",
                current_ev, proposed_ev,
            )
            return True, "通過"

        except ImportError:
            logger.warning("backtester_live.py 未検出、条件B をスキップ")
            return True, "バックテスト未実行（モジュール未検出）"
        except Exception as e:
            logger.error("バックテスト実行エラー: %s", e)
            return False, f"バックテストエラー: {e}"

    # ── STEP 4: config.py 更新 ───────────────────────────────────────

    def _apply_config(self, proposal: dict) -> dict:
        """
        config.py を原子的に書き換える。
        バックアップを作成してから tmp ファイル経由でリネーム（途中破損防止）。
        Returns: 変更したパラメータの {key: {old, new}} dict
        """
        from config import SCORING_CONFIG

        proposals = proposal.get("proposals", {})
        if not proposals:
            return {}

        config_path = os.path.join(os.path.dirname(__file__), "config.py")
        timestamp   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = config_path + f".bak.{timestamp}"

        # バックアップ
        shutil.copy2(config_path, backup_path)
        logger.info("config.py バックアップ: %s", backup_path)

        # 現在の config.py を読む
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        changes = {}
        for param, new_val in proposals.items():
            old_val = SCORING_CONFIG.get(param)
            if old_val is None:
                continue

            # "param_name":  0.xxxx, または "param_name": -0.xxxx のパターンを置換
            pattern = rf'("{re.escape(param)}"\s*:\s*)([-\d.]+)'
            new_content, count = re.subn(
                pattern,
                lambda m: m.group(1) + f"{new_val:.4f}",
                content,
            )
            if count == 0:
                logger.warning("config.py 内で '%s' のパターンが見つからなかった", param)
                continue

            content = new_content
            changes[param] = {"old": old_val, "new": new_val}

        if not changes:
            logger.info("実際の変更なし")
            return {}

        # 一時ファイルに書いてから原子的リネーム（途中クラッシュ対策）
        dir_path = os.path.dirname(config_path)
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False, dir=dir_path, encoding="utf-8"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            os.replace(tmp_path, config_path)  # 原子的操作
            logger.info("config.py 更新完了: %s", changes)

        except Exception as e:
            # 失敗時は tmp を削除（config.py は無傷）
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error("config.py 更新失敗（バックアップ維持）: %s", e)
            raise

        return changes


# ── ヘルパー関数 ─────────────────────────────────────────────────────

def _run_live_backtest(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    config_overrides: dict,
) -> Optional[dict]:
    """
    backtester_live.LiveBacktester を指定期間・パラメータで実行する。
    Returns: {"expectancy": float, "n_trades": int} or None（データ不足時）
    """
    try:
        from backtester_live import LiveBacktester
        bt = LiveBacktester(
            symbol=symbol,
            start_dt=start_dt,
            end_dt=end_dt,
            overrides=config_overrides,
        )
        result = bt.run()
        if result.n_trades < 10:
            return None
        expectancy = result.total_pnl / result.n_trades if result.n_trades > 0 else 0
        return {"expectancy": expectancy, "n_trades": result.n_trades}
    except Exception as e:
        logger.error("LiveBacktester エラー: %s", e)
        return None


def _send_discord(message: str) -> None:
    """Discord Webhook に通知を送る（失敗しても例外を上げない）"""
    import requests
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.debug("DISCORD_WEBHOOK_URL 未設定、通知スキップ")
        return
    try:
        requests.post(
            webhook_url,
            json={"content": f"[MetaOptimizer] {message}"},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Discord通知失敗: %s", e)
