"""
notifier.py - Discord Webhook通知（MT5接続断・AI API障害のみ）
AI Trading System v2.0

※ LINE Notifyは2025年4月1日にサービス終了済みのため、
   Discord Webhookに移行しています。
"""

import logging
import os
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


def send_discord(message: str) -> bool:
    """Discord Webhookにメッセージを送信する"""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL未設定 - 通知スキップ: %s", message)
        return False
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info("Discord通知送信成功: %s", message)
            return True
        else:
            logger.error("Discord通知失敗: status=%d body=%s",
                         resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("Discord通知例外: %s", e)
        return False


def notify_mt5_disconnected():
    send_discord("⚠️ MT5接続断 - システムを確認してください")


def notify_ai_api_error():
    send_discord("⚠️ AI API障害 - シグナルがスキップされています")


def notify_loss_alert(pnl_usd: float, ticket: int):
    send_discord(f"⚠️ ポジション損失アラート: {pnl_usd:.2f} USD (Ticket: {ticket})")
