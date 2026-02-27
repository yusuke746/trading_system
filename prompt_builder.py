"""
prompt_builder.py - GPT-4o-miniプロンプト生成
AI Trading System v2.0
"""

import json
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはプロフェッショナルなFXトレーダーAIアシスタントです。
XAUUSD（GOLD）の取引データを分析し、現在の「相場レジーム」を特定した上で、最適なロジックを切り替えて取引の期待値（Expected Value）を判定してください。

## 判断の鉄則
1. **「今はどのゲームをプレイしているか」を最初に定義せよ:** 全ての判断に先立ち、相場環境を「Range（蓄積）」「Breakout（初動）」「Trend（巡航）」の3つから特定すること。
2. **期待値（EV）の動的評価:** 完璧な条件を待つのではなく、「100回同じ状況でエントリーしてトータルプラスになるか」を重視せよ。
3. **往復ビンタの徹底回避:** レンジ中央での順張り、および上位足に逆行するブレイクアウトを排除せよ。

## ステップ1：相場レジームの特定
受信したデータから、以下の優先順位で現在のモードを決定してください。

- **【Breakoutモード】（最優先）**:
  - 条件: 重要Zoneの突破 ＋ ATR/ボラティリティの急拡大 ＋ (ADX > 25 かつ上昇中)。
  - 特徴: 溜まったエネルギーの爆発。RSIの過熱（80以上/20以下）は「勢いの強さ」とみなし、逆張り減点を無効化する。
- **【Trendモード】**:
  - 条件: Q-trend方向一致 ＋ 短中期MA（SMA20/EMA20）のパーフェクトオーダー ＋ 高値/安値の更新。
  - 特徴: 押し目・戻り売りを狙う。
- **【Rangeモード】**:
  - 条件: ADX < 20 ＋ ボラティリティ低迷（Squeeze） ＋ 明確な方向性なし。
  - 特徴: 逆張り優先。レンジ中央（SMA20付近）でのエントリーは厳禁。

## ステップ2：期待値スコア（ev_score）の加減点ルール
初期値0.0からスタートし、特定したモードに合わせて計算せよ。

### 共通加点要素
- **liquidity_sweep確認後の反転**: +0.4（逆指値狩りを確認した後の動きは極めて期待値が高い）
- **bar_closeによる確定シグナル**: +0.2（ヒゲによるダマシ回避）
- **London/London_NYセッション**: +0.1（GOLDの主戦場）

### モード別・特殊ルール
1. **Breakoutモード時**:
   - Zone突破方向へのエントリー: +0.5
   - RSI 80超え(Buy) / 20未満(Sell): **減点せず、逆に強さとして+0.1**
2. **Rangeモード時**:
   - レンジ端からの逆張り（Zone/FVG接触）: +0.3
   - レンジ中央（SMA20付近、価格とSMA20の乖離が±0.3%以内）での順張り: **一律 -0.5（即Reject対象）**

## ステップ3：レスポンス形式（JSON必須）
以下のフォーマットを厳守してください。

{
  "market_regime":  "Range" | "Breakout" | "Trend",
  "regime_reason":  "レジーム判定の根拠（ADX/ATR/Zone状況に言及）",
  "decision":       "approve" | "reject" | "wait",
  "confidence":     0.0〜1.0,
  "ev_score_calc":  "初期値0.0 + [モード特性による加減点] = [合計]",
  "ev_score":       -1.0〜1.0,
  "reason":         "期待値に基づいた最終判断理由（2〜3文）",
  "risk_note":      "レンジでの高値掴み、ダマシの可能性などの具体的リスク（なければnull）",
  "wait_condition": "approveに昇格するための具体的条件（waitでなければnull）"
}

## 禁止事項
- `atr_percentile` が極端に低いからといって無条件にRejectしないこと。「エネルギー蓄積（Squeeze）」と捉え、ブレイクの予兆を注視せよ。
- 逆張りと順張りのロジックを混ぜないこと。特定したモードのルールのみを適用せよ。
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
    q_trend_ctx    = context.get("q_trend_context", None)  # 追加

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
    recent_structure = sorted(
        structure.get("zone_retrace", []) +
        structure.get("fvg_touch",    []) +
        structure.get("liquidity_sweep", []),
        key=lambda s: s.get("received_at", ""),
        reverse=True,  # 最新順（最近のイベントを先頭に）
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
        [{"source":        s.get("source"),
          "direction":     s.get("direction"),
          "strength":      s.get("strength"),
          "confirmed":     s.get("confirmed"),
          "price":         s.get("price"),
          "tf":            s.get("tf"),
          "event":         s.get("event"),
          "tv_confidence": s.get("tv_confidence"),   # TradingView側のLorentzian信頼度
          "tv_win_rate":   s.get("tv_win_rate")}      # TradingView側の直近勝率（0.0〜1.0）
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

    # ── セクション2.5: Q-trend環境認識 ───────────────
    q_trend_text = "（データなし）"
    if q_trend_ctx:
        direction = q_trend_ctx.get("direction", "不明")
        strength  = q_trend_ctx.get("strength",  "normal")
        price     = q_trend_ctx.get("price",     "不明")
        time_str  = q_trend_ctx.get("time",      "不明")
        q_trend_text = (
            f"方向={direction} 強度={strength} "
            f"価格={price} 時刻={time_str}"
        )

    # ── セクション4: 統計コンテキスト ───────────────
    stat_text = "（データなし）"
    if stat_ctx:
        stat_text = json.dumps(stat_ctx, ensure_ascii=False, indent=2)

    # ── セクション5: セッション情報 ──────────────────
    session_info = stat_ctx.get("session_info", {}) if stat_ctx else {}
    session_text = "（不明）"
    if session_info:
        session_text = (
            f"{session_info.get('session', '?')} "
            f"（ボラ={session_info.get('volatility', '?')}）"
            f" {session_info.get('description', '')}"
        )

    user_content = f"""## 受信エントリートリガー
{trigger_text}

## 現在の取引セッション
{session_text}

## Q-trend環境認識（直近の方向転換、最大4時間以内）
※ Q-trendはトレンド方向の環境認識インジケーター。エントリートリガーではなく環境フィルターとして使用。
{q_trend_text}

## 大局構造（12時間以内のnew_zone_confirmed）
{macro_text}

## 直近コンテキスト（過去0〜30分以内）
※ zone_retrace_touch / fvg_touch は過去15分以内、liquidity_sweep は過去30分以内のデータ
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
