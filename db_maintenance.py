"""
db_maintenance.py - SQLite DB 定期メンテナンス
AI Trading System v3.5

保持ポリシー（半永久稼働時のDBパンク防止）:
  system_events            : 90日超のレコードを削除
  signals                  : 180日超のレコードを削除
  scoring_history          : 90日超のレコードを削除
  wait_history             : 180日超のレコードを削除
  ai_decisions.prompt_json : 90日超の行を NULL 化（大容量カラムの解放）
  ai_decisions.context_json: 180日超の行を NULL 化
  executions / trade_results / param_history : 永久保存

毎週日曜 UTC 21:00 に自動実行（MetaOptimizer の1時間後）。
VACUUM は WAL トランザクションと競合しないよう専用の autocommit 接続で実行。
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

from database import DB_PATH

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# 保持設定
# ──────────────────────────────────────────────────────────

RETENTION = {
    # (テーブル名, 日時カラム名, 保持日数, 削除 or NULL化対象カラム(Noneなら行削除))
    "delete_system_events":    ("system_events",    "created_at", 90,  None),
    "delete_signals":          ("signals",          "received_at", 180, None),
    "delete_scoring_history":  ("scoring_history",  "created_at", 90,  None),
    "delete_wait_history":     ("wait_history",     "created_at", 180, None),
    "null_prompt_json":        ("ai_decisions",     "created_at", 90,  ["prompt_json"]),
    "null_context_json":       ("ai_decisions",     "created_at", 180, ["context_json"]),
}


# ──────────────────────────────────────────────────────────
# DbMaintenance クラス
# ──────────────────────────────────────────────────────────

class DbMaintenance:
    """
    定期DBメンテナンスを実行するクラス。

    Args:
        db_path: SQLite DB ファイルパス（省略時は database.DB_PATH）
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or str(DB_PATH)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ──────────────────────────────────────────────
    # 週次スケジューラ
    # ──────────────────────────────────────────────

    def start_weekly_scheduler(self) -> None:
        """毎週日曜 UTC 21:00 に run() を実行するバックグラウンドスレッドを起動する。"""
        if self._thread and self._thread.is_alive():
            logger.warning("DbMaintenance: スケジューラは既に起動中です")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="DbMaintenance-Scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("DbMaintenance: 週次スケジューラ起動（毎週日曜 UTC 21:00）")

    def stop(self) -> None:
        """スケジューラを停止する。"""
        self._stop_event.set()

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            # 日曜(weekday=6) かつ 21:00〜21:04 の間に実行
            if now.weekday() == 6 and now.hour == 21 and now.minute < 5:
                logger.info("DbMaintenance: 週次メンテナンス開始 (%s UTC)", now.strftime("%Y-%m-%d %H:%M"))
                try:
                    summary = self.run()
                    logger.info("DbMaintenance: 完了 %s", summary)
                except Exception:
                    logger.exception("DbMaintenance: メンテナンス中に例外発生")
                # 実行後は次の週まで待つ（重複実行防止: 10分スリープ）
                self._stop_event.wait(600)
            else:
                # 1分ごとに時刻チェック
                self._stop_event.wait(60)

    # ──────────────────────────────────────────────
    # メンテナンス本体
    # ──────────────────────────────────────────────

    def run(self) -> dict:
        """
        保持ポリシーに従いDBを整理し、VACUUM を実行する。

        Returns:
            {'deleted': {...}, 'nulled': {...}, 'vacuum': bool, 'db_size_mb': float}
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        deleted: dict[str, int] = {}
        nulled:  dict[str, int] = {}

        try:
            cur = conn.cursor()

            for key, (table, dt_col, days, null_cols) in RETENTION.items():
                cutoff = f"datetime('now', '-{days} days')"
                if null_cols is None:
                    # 行削除
                    sql = f"DELETE FROM {table} WHERE {dt_col} < {cutoff}"
                    cur.execute(sql)
                    deleted[table] = deleted.get(table, 0) + cur.rowcount
                else:
                    # 指定カラムを NULL 化
                    set_clause = ", ".join(f"{c} = NULL" for c in null_cols)
                    sql = (
                        f"UPDATE {table} SET {set_clause} "
                        f"WHERE {dt_col} < {cutoff} "
                        f"AND ({' OR '.join(f'{c} IS NOT NULL' for c in null_cols)})"
                    )
                    cur.execute(sql)
                    nulled[key] = cur.rowcount

            conn.commit()
            logger.info(
                "DbMaintenance: 削除 %s / NULL化 %s",
                deleted, nulled,
            )
        finally:
            conn.close()

        # VACUUM は autocommit 専用接続で実行（WAL 競合回避）
        vacuum_ok = self._vacuum()

        db_size_mb = round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0.0

        return {
            "deleted":    deleted,
            "nulled":     nulled,
            "vacuum":     vacuum_ok,
            "db_size_mb": db_size_mb,
        }

    # ──────────────────────────────────────────────
    # VACUUM
    # ──────────────────────────────────────────────

    def _vacuum(self) -> bool:
        """
        VACUUM を autocommit 接続（isolation_level=None）で実行する。
        WAL チェックポイントを先に行い、未コミット WAL フレームを DB に取り込む。
        """
        try:
            conn = sqlite3.connect(self._db_path, isolation_level=None, check_same_thread=False)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
                logger.info("DbMaintenance: VACUUM 完了")
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("DbMaintenance: VACUUM 失敗 – %s", exc)
            return False
