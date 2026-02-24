"""
prompt_builder.py - GPT-4o-miniプロンプト生成
AI Trading System v2.0
"""

import json
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはプロのFXトレーダーAIアシスタントです。
XAUUSD（GOLD）の取引シグナルを受け取り、「期待値（Expected Value）」を中心概念として取引可否を判断してください。

## 判断の原則
- 「100回エントリーしたら長期的にプラスになるか」という観点で判断する
- 逆張りエントリーも条件付きで承認可能（liquidity_sweep + zone/FVG一致時）
- 不確実な時は wait を返す。無理にエントリーしない

## 期待値スコア（ev_score）の加減点ルール
加点要素（ev_score UP）:
- macro_zonesと方向一致 +0.3
- liquidity_sweep後の逆張り +0.3
- bar_close確認済みシグナル複数一致 +0.2
- strength=strongが方向を裏付け +0.1

減点要素（ev_score DOWN）:
- macro_zonesと逆方向 -0.3
- intrabarのみで未確認 -0.2
- 直近structureシグナルがゼロ -0.2
- 相反するstructureが混在 -0.2
- RSI過熱かつmacro_zone逆方向 -0.2

## レスポンス形式（JSON必須）
{
  "decision":       "approve" | "reject" | "wait",
  "confidence":     0.0〜1.0,
  "ev_score":       -1.0〜1.0,
  "order_type":     "market" | "limit",
  "limit_price":    float | null,
  "limit_expiry":   "next_bar" | "30min" | null,
  "reason":         "判定理由（2〜3文・期待値根拠に言及）",
  "risk_note":      "リスク要因 | null",
  "wait_scope":     "next_bar" | "structure_needed" | "cooldown" | null,
  "wait_condition": "昇格条件の説明 | null"
}

## 統計データの使い方（必ず考慮すること）
statistical_context が提供された場合は、以下のルールを適用してください。

### ATRパーセンタイル（atr_percentile_15m）
- >= 80 → ボラ過多。reject を優先し、period="range" 解消まで待機
- <= 20 → 値動きが小さすぎる。スプレッド費用対効果が悪化するため reject を優先
- 40〜70 → 通常レンジ。EV計算を通常通り適用

### 連続損失（consecutive_losses）
- >= 3 → confidence を -0.15 調整（システム状態が悪い可能性）
- >= 5 → wait を強制（`wait_scope: "cooldown"`）

### 勝率（win_rate）
- < 0.50 → ev_score を -0.10 調整（エッジが機能していない可能性）
- < 0.40 → ev_score を -0.25 調整（セットアップ自体の見直しが必要）

### トレンド強度（trend_strength）
- "strong_bull" かつ sell シグナル → ev_score を -0.15 調整（逆張り）
- "strong_bear" かつ buy シグナル → ev_score を -0.15 調整（逆張り）
- "range" → どちらの方向も公平に判断

### RSI Zスコア（rsi_zscore_5m）
- > 2.0 → 買われすぎ圏。buy 方向の approve には risk_note を必ず付記
- < -2.0 → 売られすぎ圏。sell 方向の approve には risk_note を必ず付記
"""


def build_prompt(context: dict) -> list[dict]:
    """
    コンテキストをもとにGPT-4o-miniへのメッセージリストを生成する。
    Returns: [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
    """
    entry_signals  = context.get("entry_signals", [])
    mt5_ctx        = context.get("mt5_context", {})
    structure      = context.get("structure", {})
    reeval_meta    = context.get("reeval_meta", None)
    stat_ctx       = context.get("statistical_context", {})

    # ── セクション1: 大局構造（12時間）──────────────
    macro_zones = structure.get("macro_zones", [])
    macro_text = "（なし）"
    if macro_zones:
        macro_text = json.dumps(
            [{"event": s["event"], "direction": s["direction"],
              "price": s["price"], "time": s.get("received_at", "")}
             for s in macro_zones],
            ensure_ascii=False, indent=2
        )

    # ── セクション2: 直近コンテキスト（15〜30分）────
    recent_structure = (
        structure.get("zone_retrace", []) +
        structure.get("fvg_touch",    []) +
        structure.get("liquidity_sweep", [])
    )
    recent_text = "（なし）"
    if recent_structure:
        recent_text = json.dumps(
            [{"event": s["event"], "direction": s.get("direction", ""),
              "price": s["price"], "time": s.get("received_at", "")}
             for s in recent_structure],
            ensure_ascii=False, indent=2
        )

    # ── セクション3: MT5テクニカル情報 ───────────────
    mt5_text = json.dumps(mt5_ctx, ensure_ascii=False, indent=2)

    # ── トリガーシグナル情報 ──────────────────────────
    trigger_text = json.dumps(
        [{"source": s.get("source"), "direction": s.get("direction"),
          "strength": s.get("strength"), "confirmed": s.get("confirmed"),
          "price": s.get("price"), "tf": s.get("tf"),
          "event": s.get("event")}
         for s in entry_signals],
        ensure_ascii=False, indent=2
    )

    # ── 再評価メタ情報 ────────────────────────────────
    reeval_text = ""
    if reeval_meta:
        reeval_text = f"""
## 再評価情報
- 再評価回数: {reeval_meta.get('reeval_count', 1)}
- 初回wait理由: {reeval_meta.get('original_reason', '')}
- 昇格条件: {reeval_meta.get('wait_condition', '')}
- 経過時間: {reeval_meta.get('elapsed_seconds', 0):.0f}秒
"""

    # ── セクション4: 統計コンテキスト ───────────────
    stat_text = "（データなし）"
    if stat_ctx:
        stat_text = json.dumps(stat_ctx, ensure_ascii=False, indent=2)

    user_content = f"""## 受信エントリートリガー
{trigger_text}

## 大局構造（12時間以内のnew_zone_confirmed）
{macro_text}

## 直近コンテキスト（15〜30分以内）
{recent_text}

## MT5テクニカル情報
{mt5_text}

## 統計コンテキスト（マーケットレジーム・過去成績）
{stat_text}
{reeval_text}
上記情報をもとに、期待値を中心に取引可否をJSON形式で判定してください。"""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]
