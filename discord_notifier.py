"""
discord_notifier.py - Discord Webhook通知モジュール
AI Trading System

DISCORD_WEBHOOK_URL が .env に設定されている場合のみ通知を送信する。
未設定・送信失敗時はサイレントスキップし、トレードロジックに影響を与えない。

ファイル配置: プロジェクトルート（python/app.py・executor.py 等から共通インポート可能）
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# レート制限対策: 連続送信時のインターバル（秒）
_RATE_LIMIT_INTERVAL = 0.5
_last_send_time: float = 0.0


def notify(
    title: str,
    description: str,
    color: int = 0x00FF00,
    fields: dict = None,
) -> None:
    """
    Discord Embed形式で通知を送信する。

    Args:
        title:       Embedタイトル（例: "✅ エントリー承認"）
        description: Embed本文
        color:       枠の色（16進数 例: 0x00ff00=緑, 0xff0000=赤, 0xFFFF00=黄）
        fields:      追加フィールド {名前: 値, ...}（全てinline=True）
    """
    global _last_send_time

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    # レート制限対策: 前回送信から0.5秒未満なら待機
    elapsed = time.time() - _last_send_time
    if elapsed < _RATE_LIMIT_INTERVAL:
        time.sleep(_RATE_LIMIT_INTERVAL - elapsed)

    embed: dict = {
        "title":       title,
        "description": description,
        "color":       color,
    }

    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ]

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        _last_send_time = time.time()

        if resp.status_code not in (200, 204):
            logger.warning(
                "Discord通知失敗: status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
    except Exception as e:
        logger.warning("Discord通知例外（トレード処理は継続）: %s", e)
