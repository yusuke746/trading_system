"""
llm_structurer.py - LLMを使った生データの正規化構造変換
AI Trading System v3.0

LLMの役割を「approve/reject判定」から「データ構造化」に変更する。
LLMは判定を行わない。データのパース・分類・正規化のみ。
APIエラー時はルールベースのフォールバックで動作する。
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """OpenAI クライアントのシングルトン取得"""
    global _client
    if _client is None:
        from openai import OpenAI
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未設定")
        _client = OpenAI(api_key=api_key)
    return _client


# ── JSON Schema定義 ──────────────────────────────────────
STRUCTURED_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["regime", "price_structure", "zone_interaction",
                  "momentum", "signal_quality", "data_completeness"],
    "properties": {
        "regime": {
            "type": "object",
            "properties": {
                "classification": {"type": "string", "enum": ["range", "breakout", "trend"]},
                "adx_value": {"type": ["number", "null"]},
                "adx_rising": {"type": ["boolean", "null"]},
                "atr_expanding": {"type": "boolean"},
                "squeeze_detected": {"type": "boolean"}
            }
        },
        "price_structure": {
            "type": "object",
            "properties": {
                "above_sma20": {"type": ["boolean", "null"]},
                "sma20_distance_pct": {"type": ["number", "null"]},
                "perfect_order": {"type": ["boolean", "null"]},
                "higher_highs": {"type": ["boolean", "null"]},
                "lower_lows": {"type": ["boolean", "null"]}
            }
        },
        "zone_interaction": {
            "type": "object",
            "properties": {
                "zone_touch": {"type": "boolean"},
                "zone_direction": {"type": ["string", "null"]},
                "fvg_touch": {"type": "boolean"},
                "fvg_direction": {"type": ["string", "null"]},
                "liquidity_sweep": {"type": "boolean"},
                "sweep_direction": {"type": ["string", "null"]}
            }
        },
        "momentum": {
            "type": "object",
            "properties": {
                "rsi_value": {"type": ["number", "null"]},
                "rsi_zone": {"type": "string", "enum": ["oversold", "neutral", "overbought"]},
                "trend_aligned": {"type": "boolean"}
            }
        },
        "signal_quality": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "bar_close_confirmed": {"type": "boolean"},
                "session": {"type": "string"},
                "tv_confidence": {"type": ["number", "null"]},
                "tv_win_rate": {"type": ["number", "null"]},
                "pattern_similarity": {"type": ["number", "null"]}
            }
        },
        "data_completeness": {
            "type": "object",
            "properties": {
                "mt5_connected": {"type": "boolean"},
                "fields_missing": {"type": "array", "items": {"type": "string"}}
            }
        }
    }
}


# ── システムプロンプト（構造化専用）──────────────────────
STRUCTURING_SYSTEM_PROMPT = """あなたはマーケットデータの構造化エンジンです。
与えられた生のマーケットデータを、以下の正規化JSONスキーマに変換してください。

## 絶対に守るルール
- 入力データに含まれていない数値を推測・補完してはいけない
- 「おそらく」「推定では」という表現は使用禁止
- データが不足している場合は null を返し、fields_missing に記載する
- あなたの役割は「分類」であり「判断」ではない
- approve/reject/waitなどの判断・推奨・提案は一切しないこと

## 出力JSONスキーマ
{
  "regime": {
    "classification": "range" | "breakout" | "trend",
    "adx_value": float | null,
    "adx_rising": bool | null,
    "atr_expanding": bool,
    "squeeze_detected": bool
  },
  "price_structure": {
    "above_sma20": bool | null,
    "sma20_distance_pct": float | null,
    "perfect_order": bool | null,
    "higher_highs": bool | null,
    "lower_lows": bool | null
  },
  "zone_interaction": {
    "zone_touch": bool,
    "zone_direction": "demand" | "supply" | null,
    "fvg_touch": bool,
    "fvg_direction": "bullish" | "bearish" | null,
    "liquidity_sweep": bool,
    "sweep_direction": "buy_side" | "sell_side" | null
  },
  "momentum": {
    "rsi_value": float | null,
    "rsi_zone": "oversold" | "neutral" | "overbought",
    "trend_aligned": bool
  },
  "signal_quality": {
    "source": str,
    "bar_close_confirmed": bool,
    "session": "Tokyo" | "London" | "NY" | "London_NY" | "off_hours",
    "tv_confidence": float | null,
    "tv_win_rate": null（Lorentzian v2では廃止。常にnullとして扱う）,
    "pattern_similarity": float(0.0〜1.0) | null（Lorentzian v2の新フィールド。avg_distanceの反転正規化値。高いほど過去パターンと高類似）
  },
  "data_completeness": {
    "mt5_connected": bool,
    "fields_missing": [str]
  }
}

## 分類ルール

### regime.classification の判定基準
- "breakout": ADX > 25 かつ上昇中 + ATR拡大中 + ゾーン突破
- "trend": ADX > 20 + 短中期MA順序整列 + 高値安値更新
- "range": ADX < 20 または明確な方向性なし

### momentum.rsi_zone の判定基準
- "oversold": RSI < 30
- "overbought": RSI > 70
- "neutral": 30 <= RSI <= 70

### momentum.trend_aligned の判定基準
- Q-trend方向とシグナル方向が一致していれば true

## Few-shot Examples

### Example 1: トレンド相場で全データ揃っている場合

入力概要: entry_trigger: buy, source: Lorentzian, RSI14: 35, ADX14: 28 (上昇中), ATR拡大なし, zone_retrace_touch 1件(buy方向), session: London_NY, Q-trend: buy, bar_close確認済

出力:
{
  "regime": {
    "classification": "trend",
    "adx_value": 28,
    "adx_rising": true,
    "atr_expanding": false,
    "squeeze_detected": false
  },
  "price_structure": {
    "above_sma20": true,
    "sma20_distance_pct": 0.45,
    "perfect_order": true,
    "higher_highs": true,
    "lower_lows": false
  },
  "zone_interaction": {
    "zone_touch": true,
    "zone_direction": "demand",
    "fvg_touch": false,
    "fvg_direction": null,
    "liquidity_sweep": false,
    "sweep_direction": null
  },
  "momentum": {
    "rsi_value": 35,
    "rsi_zone": "neutral",
    "trend_aligned": true
  },
  "signal_quality": {
    "source": "Lorentzian",
    "bar_close_confirmed": true,
    "session": "London_NY",
    "tv_confidence": 0.72,
    "tv_win_rate": 0.61
  },
  "data_completeness": {
    "mt5_connected": true,
    "fields_missing": []
  }
}

### Example 2: レンジ相場でRSI/ADXがnullの場合

入力概要: entry_trigger: sell, source: Lorentzian, MT5データなし（接続エラー）, session: Asia, Q-trend: なし

出力:
{
  "regime": {
    "classification": "range",
    "adx_value": null,
    "adx_rising": null,
    "atr_expanding": false,
    "squeeze_detected": false
  },
  "price_structure": {
    "above_sma20": null,
    "sma20_distance_pct": null,
    "perfect_order": null,
    "higher_highs": null,
    "lower_lows": null
  },
  "zone_interaction": {
    "zone_touch": false,
    "zone_direction": null,
    "fvg_touch": false,
    "fvg_direction": null,
    "liquidity_sweep": false,
    "sweep_direction": null
  },
  "momentum": {
    "rsi_value": null,
    "rsi_zone": "neutral",
    "trend_aligned": false
  },
  "signal_quality": {
    "source": "Lorentzian",
    "bar_close_confirmed": false,
    "session": "Tokyo",
    "tv_confidence": null,
    "tv_win_rate": null
  },
  "data_completeness": {
    "mt5_connected": false,
    "fields_missing": ["rsi_value", "adx_value", "adx_rising", "atr_expanding", "sma20_distance_pct", "above_sma20", "perfect_order", "higher_highs", "lower_lows"]
  }
}

### Example 3: ブレイクアウト初動でzone_touchが発生している場合

入力概要: entry_trigger: buy, source: Lorentzian, RSI14: 82, ADX14: 32 (上昇中), ATR急拡大, liquidity_sweep(sell方向), zone_retrace_touch 1件(buy方向), session: London, Q-trend: buy

出力:
{
  "regime": {
    "classification": "breakout",
    "adx_value": 32,
    "adx_rising": true,
    "atr_expanding": true,
    "squeeze_detected": false
  },
  "price_structure": {
    "above_sma20": true,
    "sma20_distance_pct": 1.2,
    "perfect_order": true,
    "higher_highs": true,
    "lower_lows": false
  },
  "zone_interaction": {
    "zone_touch": true,
    "zone_direction": "demand",
    "fvg_touch": false,
    "fvg_direction": null,
    "liquidity_sweep": true,
    "sweep_direction": "sell_side"
  },
  "momentum": {
    "rsi_value": 82,
    "rsi_zone": "overbought",
    "trend_aligned": true
  },
  "signal_quality": {
    "source": "Lorentzian",
    "bar_close_confirmed": true,
    "session": "London",
    "tv_confidence": 0.85,
    "tv_win_rate": 0.65
  },
  "data_completeness": {
    "mt5_connected": true,
    "fields_missing": []
  }
}

### Example 4: Q-trendとLorentzianが逆方向のケース

入力概要: entry_trigger: buy, source: Lorentzian, Q-trend direction: sell（4時間以内）,
zone_retrace_touch: buy方向, RSI: 38, ADX: 18（レンジ）, session: Tokyo

出力:
{
  "regime": {
    "classification": "range",
    "adx_value": 18,
    "adx_rising": false,
    "atr_expanding": false,
    "squeeze_detected": false
  },
  "price_structure": {
    "above_sma20": null,
    "sma20_distance_pct": null,
    "perfect_order": null,
    "higher_highs": null,
    "lower_lows": null
  },
  "zone_interaction": {
    "zone_touch": true,
    "zone_direction": "demand",
    "fvg_touch": false,
    "fvg_direction": null,
    "liquidity_sweep": false,
    "sweep_direction": null
  },
  "momentum": {
    "rsi_value": 38,
    "rsi_zone": "neutral",
    "trend_aligned": false
  },
  "signal_quality": {
    "source": "Lorentzian",
    "bar_close_confirmed": true,
    "session": "Tokyo",
    "tv_confidence": null,
    "tv_win_rate": null,
    "pattern_similarity": null
  },
  "data_completeness": {
    "mt5_connected": true,
    "fields_missing": []
  }
}

Note: Q-trendがentry方向と逆のとき、trend_alignedは必ずfalseにすること。

### Example 5: liquidity_sweep後の反転（sweep_reversalセットアップ）

入力概要: entry_trigger: buy, liquidity_sweep: sell方向（30分以内）,
zone_retrace_touch: buy方向, RSI: 28（oversold）, ADX: 22（弱トレンド）, Q-trend: buy

出力:
{
  "regime": {
    "classification": "trend",
    "adx_value": 22,
    "adx_rising": true,
    "atr_expanding": false,
    "squeeze_detected": false
  },
  "price_structure": {
    "above_sma20": true,
    "sma20_distance_pct": 0.3,
    "perfect_order": null,
    "higher_highs": null,
    "lower_lows": null
  },
  "zone_interaction": {
    "zone_touch": true,
    "zone_direction": "demand",
    "fvg_touch": false,
    "fvg_direction": null,
    "liquidity_sweep": true,
    "sweep_direction": "sell_side"
  },
  "momentum": {
    "rsi_value": 28,
    "rsi_zone": "oversold",
    "trend_aligned": true
  },
  "signal_quality": {
    "source": "Lorentzian",
    "bar_close_confirmed": true,
    "session": "London",
    "tv_confidence": null,
    "tv_win_rate": null,
    "pattern_similarity": null
  },
  "data_completeness": {
    "mt5_connected": true,
    "fields_missing": []
  }
}

Note: liquidity_sweepがsell方向の場合、sweep_direction: "sell_side"とすること。
      buy方向エントリーとの組み合わせが正しいsweep_reversalパターン。

上記のスキーマとルールに従って、入力データを正規化JSONに変換してください。
JSON以外のテキストは一切出力しないでください。"""


def structurize(context: dict) -> dict:
    """
    LLMを使ってコンテキストを構造化する。
    APIエラー時はrule-basedフォールバックを返す。

    Args:
        context: context_builder.py が生成するコンテキスト dict

    Returns:
        正規化された構造データ dict
    """
    try:
        client = _get_client()

        user_content = json.dumps(context, ensure_ascii=False, default=str)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": STRUCTURING_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
        )
        content = response.choices[0].message.content
        result = json.loads(content)

        # スキーマの基本検証
        result = _validate_and_fix_schema(result)

        logger.info(
            "🔧 LLM構造化完了: regime=%s",
            result.get("regime", {}).get("classification", "unknown"),
        )
        return result

    except Exception as e:
        logger.warning("LLM構造化API失敗、フォールバック使用: %s", e)
        return _fallback_structurize(context)


def _fallback_structurize(context: dict) -> dict:
    """
    LLM不要のルールベースフォールバック。
    context_builder.pyのmt5_contextから直接数値を抽出する。
    """
    mt5_ctx = context.get("mt5_context", {})
    entry_signals = context.get("entry_signals", [])
    structure = context.get("structure", {})
    q_trend_ctx = context.get("q_trend_context")
    stat_ctx = context.get("statistical_context", {})

    fields_missing: list[str] = []
    mt5_connected = "error" not in mt5_ctx

    # ── MT5指標の抽出 ──────────────────────────────────
    ind_5m = mt5_ctx.get("indicators_5m", {})
    ind_15m = mt5_ctx.get("indicators_15m", {})
    ind_1h = mt5_ctx.get("indicators_1h", {})

    rsi_value = _safe_float(ind_5m.get("rsi14"))
    adx_value = _safe_float(ind_15m.get("adx14"))
    atr_15m = _safe_float(ind_15m.get("atr14"))
    sma20_5m = _safe_float(ind_5m.get("sma20"))
    close_5m = _safe_float(ind_5m.get("close"))

    if rsi_value is None:
        fields_missing.append("rsi_value")
    if adx_value is None:
        fields_missing.append("adx_value")

    # ── レジーム判定 ────────────────────────────────────
    adx_rising = None
    if adx_value is not None:
        # 簡易判定：ADX > 20 ならtrend可能性あり
        adx_rising = adx_value > 20  # 正確なrising判定はLLMに任せたいがフォールバック
    else:
        fields_missing.append("adx_rising")

    atr_expanding = False
    squeeze_detected = False
    if atr_15m is not None:
        atr_percentile = stat_ctx.get("market_regime", {}).get("atr_percentile_15m", 50)
        atr_expanding = atr_percentile > 70
        squeeze_detected = atr_percentile < 20
    else:
        fields_missing.append("atr_expanding")

    # レジーム分類
    classification = "range"
    if adx_value is not None:
        if adx_value > 25 and atr_expanding:
            classification = "breakout"
        elif adx_value > 20:
            classification = "trend"
        else:
            classification = "range"

    # ── 価格構造 ────────────────────────────────────────
    above_sma20 = None
    sma20_distance_pct = None
    if sma20_5m is not None and close_5m is not None and sma20_5m > 0:
        above_sma20 = close_5m > sma20_5m
        sma20_distance_pct = round((close_5m - sma20_5m) / sma20_5m * 100, 2)
    else:
        fields_missing.extend(["above_sma20", "sma20_distance_pct"])

    # perfect_order: SMA20 5m > SMA50 1h (簡易判定)
    sma50_1h = _safe_float(ind_1h.get("sma50"))
    perfect_order = None
    if sma20_5m is not None and sma50_1h is not None:
        perfect_order = sma20_5m > sma50_1h
    else:
        fields_missing.append("perfect_order")

    # higher_highs / lower_lows は時系列データが必要なので省略
    fields_missing.extend(["higher_highs", "lower_lows"])

    # ── ゾーンインタラクション ──────────────────────────
    zone_retrace = structure.get("zone_retrace", [])
    fvg_touch_list = structure.get("fvg_touch", [])
    sweep_list = structure.get("liquidity_sweep", [])

    zone_touch = len(zone_retrace) > 0
    zone_direction = None
    if zone_touch and zone_retrace:
        raw_dir = zone_retrace[0].get("direction", "")
        if raw_dir == "buy":
            zone_direction = "demand"
        elif raw_dir == "sell":
            zone_direction = "supply"

    fvg_touch = len(fvg_touch_list) > 0
    fvg_direction = None
    if fvg_touch and fvg_touch_list:
        raw_dir = fvg_touch_list[0].get("direction", "")
        if raw_dir == "buy":
            fvg_direction = "bullish"
        elif raw_dir == "sell":
            fvg_direction = "bearish"

    has_sweep = len(sweep_list) > 0
    sweep_direction = None
    if has_sweep and sweep_list:
        raw_dir = sweep_list[0].get("direction", "")
        if raw_dir == "sell":
            sweep_direction = "sell_side"
        elif raw_dir == "buy":
            sweep_direction = "buy_side"

    # ── モメンタム ──────────────────────────────────────
    rsi_zone = "neutral"
    if rsi_value is not None:
        if rsi_value < 30:
            rsi_zone = "oversold"
        elif rsi_value > 70:
            rsi_zone = "overbought"

    # Q-trendとの方向一致
    signal_direction = None
    if entry_signals:
        signal_direction = entry_signals[0].get("direction")

    q_trend_direction = None
    if q_trend_ctx:
        q_trend_direction = q_trend_ctx.get("direction")

    trend_aligned = (
        signal_direction is not None
        and q_trend_direction is not None
        and signal_direction == q_trend_direction
    )

    # ── シグナル品質 ────────────────────────────────────
    source = "unknown"
    bar_close_confirmed = False
    tv_confidence = None
    tv_win_rate = None
    pattern_similarity = None
    if entry_signals:
        sig = entry_signals[0]
        source = sig.get("source", "unknown")
        bar_close_confirmed = sig.get("confirmed") == "bar_close"
        tv_confidence = _safe_float(sig.get("tv_confidence"))
        tv_win_rate = _safe_float(sig.get("tv_win_rate"))          # 後方互換（旧バージョン）
        pattern_similarity = _safe_float(sig.get("pattern_similarity"))  # Lorentzian v2

    session = "off_hours"
    session_info = stat_ctx.get("session_info", {})
    if session_info:
        raw_session = session_info.get("session", "Off_hours")
        session_map = {
            "Asia": "Tokyo",
            "London": "London",
            "NY": "NY",
            "London_NY": "London_NY",
            "Off_hours": "off_hours",
        }
        session = session_map.get(raw_session, "off_hours")

    return {
        "regime": {
            "classification": classification,
            "adx_value": adx_value,
            "adx_rising": adx_rising,
            "atr_expanding": atr_expanding,
            "squeeze_detected": squeeze_detected,
        },
        "price_structure": {
            "above_sma20": above_sma20,
            "sma20_distance_pct": sma20_distance_pct,
            "perfect_order": perfect_order,
            "higher_highs": None,
            "lower_lows": None,
        },
        "zone_interaction": {
            "zone_touch": zone_touch,
            "zone_direction": zone_direction,
            "fvg_touch": fvg_touch,
            "fvg_direction": fvg_direction,
            "liquidity_sweep": has_sweep,
            "sweep_direction": sweep_direction,
        },
        "momentum": {
            "rsi_value": rsi_value,
            "rsi_zone": rsi_zone,
            "trend_aligned": trend_aligned,
        },
        "signal_quality": {
            "source": source,
            "bar_close_confirmed": bar_close_confirmed,
            "session": session,
            "tv_confidence": tv_confidence,
            "tv_win_rate": tv_win_rate,          # 後方互換（旧バージョン、通常None）
            "pattern_similarity": pattern_similarity,   # Lorentzian v2（新フィールド）
        },
        "data_completeness": {
            "mt5_connected": mt5_connected,
            "fields_missing": fields_missing,
        },
    }


def _validate_and_fix_schema(data: dict) -> dict:
    """LLM出力のスキーマ検証と欠損フィールドの補完"""
    defaults = {
        "regime": {
            "classification": "range",
            "adx_value": None,
            "adx_rising": None,
            "atr_expanding": False,
            "squeeze_detected": False,
        },
        "price_structure": {
            "above_sma20": None,
            "sma20_distance_pct": None,
            "perfect_order": None,
            "higher_highs": None,
            "lower_lows": None,
        },
        "zone_interaction": {
            "zone_touch": False,
            "zone_direction": None,
            "fvg_touch": False,
            "fvg_direction": None,
            "liquidity_sweep": False,
            "sweep_direction": None,
        },
        "momentum": {
            "rsi_value": None,
            "rsi_zone": "neutral",
            "trend_aligned": False,
        },
        "signal_quality": {
            "source": "unknown",
            "bar_close_confirmed": False,
            "session": "off_hours",
            "tv_confidence": None,
            "tv_win_rate": None,
            "pattern_similarity": None,
        },
        "data_completeness": {
            "mt5_connected": False,
            "fields_missing": [],
        },
    }

    for section_key, section_defaults in defaults.items():
        if section_key not in data:
            data[section_key] = section_defaults
        else:
            for field_key, default_val in section_defaults.items():
                if field_key not in data[section_key]:
                    data[section_key][field_key] = default_val

    return data


def _safe_float(val: Any) -> float | None:
    """値をfloatに安全に変換する。失敗時はNoneを返す。"""
    if val is None:
        return None
    try:
        result = float(val)
        if result != result:  # NaN check
            return None
        return result
    except (TypeError, ValueError):
        return None
