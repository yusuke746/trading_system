"""
tests/test_llm_structurer.py - llm_structurer.py のユニットテスト
AI Trading System v3.0

テスト対象:
  - _fallback_structurize() のルールベース構造化
  - _validate_and_fix_schema() のスキーマ検証
  - _safe_float() のエッジケース
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_structurer import _fallback_structurize, _validate_and_fix_schema, _safe_float


# ──────────────────────────────────────────────────────────
# テスト用コンテキスト生成
# ──────────────────────────────────────────────────────────

def _make_context(
    rsi14=50.0,
    adx14=18.0,
    atr14=10.0,
    sma20=5190.0,
    close_5m=5200.0,
    direction="buy",
    source="Lorentzian",
    confirmed="bar_close",
    q_trend_direction=None,
    zone_retrace=None,
    fvg_touch=None,
    liquidity_sweep=None,
    session="London",
    atr_percentile_15m=50,
) -> dict:
    return {
        "entry_signals": [
            {
                "direction": direction,
                "source": source,
                "price": close_5m,
                "confirmed": confirmed,
                "tv_confidence": 0.7,
                "tv_win_rate": 0.55,
            }
        ],
        "mt5_context": {
            "indicators_5m": {
                "rsi14": rsi14,
                "atr14": atr14,
                "sma20": sma20,
                "close": close_5m,
                "adx14": adx14,
            },
            "indicators_15m": {
                "rsi14": rsi14,
                "atr14": atr14,
                "sma20": sma20,
                "adx14": adx14,
            },
            "indicators_1h": {
                "sma50": 5180.0,
                "sma200": 5100.0,
                "close": close_5m,
            },
        },
        "structure": {
            "macro_zones": [],
            "zone_retrace": zone_retrace or [],
            "fvg_touch": fvg_touch or [],
            "liquidity_sweep": liquidity_sweep or [],
        },
        "q_trend_context": {
            "direction": q_trend_direction,
            "strength": "normal",
        } if q_trend_direction else None,
        "statistical_context": {
            "market_regime": {
                "atr_percentile_15m": atr_percentile_15m,
                "rsi_zscore_5m": 0.0,
                "trend_strength": "range",
            },
            "trading_stats": {
                "win_rate": 0.55,
                "avg_pnl_usd": 10.0,
                "consecutive_losses": 0,
                "trade_count": 20,
            },
            "session_info": {
                "session": session,
                "volatility": "medium",
                "description": "test",
            },
        },
        "generated_at": "2026-02-28T10:00:00",
    }


# ──────────────────────────────────────────────────────────
# _fallback_structurize のテスト
# ──────────────────────────────────────────────────────────

class TestFallbackStructurize(unittest.TestCase):

    def test_basic_structure_keys(self):
        """フォールバックが必要なキーを全て返す"""
        ctx = _make_context()
        result = _fallback_structurize(ctx)
        self.assertIn("regime", result)
        self.assertIn("price_structure", result)
        self.assertIn("zone_interaction", result)
        self.assertIn("momentum", result)
        self.assertIn("signal_quality", result)
        self.assertIn("data_completeness", result)

    def test_regime_classification_range(self):
        """ADX < 20 → range分類"""
        ctx = _make_context(adx14=15.0)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["regime"]["classification"], "range")

    def test_regime_classification_trend(self):
        """ADX > 20 → trend分類"""
        ctx = _make_context(adx14=25.0)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["regime"]["classification"], "trend")

    def test_regime_classification_breakout(self):
        """ADX > 25 + ATR拡大 → breakout分類"""
        ctx = _make_context(adx14=30.0, atr_percentile_15m=80)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["regime"]["classification"], "breakout")

    def test_rsi_oversold(self):
        """RSI < 30 → oversold"""
        ctx = _make_context(rsi14=25.0)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["momentum"]["rsi_zone"], "oversold")
        self.assertEqual(result["momentum"]["rsi_value"], 25.0)

    def test_rsi_overbought(self):
        """RSI > 70 → overbought"""
        ctx = _make_context(rsi14=75.0)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["momentum"]["rsi_zone"], "overbought")

    def test_rsi_neutral(self):
        """30 <= RSI <= 70 → neutral"""
        ctx = _make_context(rsi14=50.0)
        result = _fallback_structurize(ctx)
        self.assertEqual(result["momentum"]["rsi_zone"], "neutral")

    def test_zone_retrace_detected(self):
        """zone_retrace_touch 検出"""
        ctx = _make_context(
            zone_retrace=[{"direction": "buy", "price": 5190.0}]
        )
        result = _fallback_structurize(ctx)
        self.assertTrue(result["zone_interaction"]["zone_touch"])
        self.assertEqual(result["zone_interaction"]["zone_direction"], "demand")

    def test_fvg_touch_detected(self):
        """fvg_touch 検出"""
        ctx = _make_context(
            fvg_touch=[{"direction": "sell", "price": 5210.0}]
        )
        result = _fallback_structurize(ctx)
        self.assertTrue(result["zone_interaction"]["fvg_touch"])
        self.assertEqual(result["zone_interaction"]["fvg_direction"], "bearish")

    def test_liquidity_sweep_detected(self):
        """liquidity_sweep 検出"""
        ctx = _make_context(
            liquidity_sweep=[{"direction": "sell", "price": 5180.0}]
        )
        result = _fallback_structurize(ctx)
        self.assertTrue(result["zone_interaction"]["liquidity_sweep"])
        self.assertEqual(result["zone_interaction"]["sweep_direction"], "sell_side")

    def test_trend_aligned_true(self):
        """Q-trend方向一致 → trend_aligned=True"""
        ctx = _make_context(direction="buy", q_trend_direction="buy")
        result = _fallback_structurize(ctx)
        self.assertTrue(result["momentum"]["trend_aligned"])

    def test_trend_aligned_false(self):
        """Q-trend方向不一致 → trend_aligned=False"""
        ctx = _make_context(direction="buy", q_trend_direction="sell")
        result = _fallback_structurize(ctx)
        self.assertFalse(result["momentum"]["trend_aligned"])

    def test_bar_close_confirmed(self):
        """bar_close確認済み"""
        ctx = _make_context(confirmed="bar_close")
        result = _fallback_structurize(ctx)
        self.assertTrue(result["signal_quality"]["bar_close_confirmed"])

    def test_mt5_error_context(self):
        """MT5エラー時のフォールバック"""
        ctx = _make_context()
        ctx["mt5_context"] = {"error": "MT5未インストール"}
        result = _fallback_structurize(ctx)
        self.assertFalse(result["data_completeness"]["mt5_connected"])
        self.assertIn("rsi_value", result["data_completeness"]["fields_missing"])

    def test_session_mapping(self):
        """セッション名のマッピング"""
        for session_in, session_out in [
            ("Asia", "Tokyo"),
            ("London", "London"),
            ("NY", "NY"),
            ("London_NY", "London_NY"),
            ("Off_hours", "off_hours"),
        ]:
            ctx = _make_context(session=session_in)
            result = _fallback_structurize(ctx)
            self.assertEqual(result["signal_quality"]["session"], session_out,
                            f"Session {session_in} should map to {session_out}")


# ──────────────────────────────────────────────────────────
# _validate_and_fix_schema のテスト
# ──────────────────────────────────────────────────────────

class TestValidateAndFixSchema(unittest.TestCase):

    def test_empty_dict_gets_defaults(self):
        """空辞書に全デフォルト値が補完される"""
        result = _validate_and_fix_schema({})
        self.assertIn("regime", result)
        self.assertIn("zone_interaction", result)
        self.assertEqual(result["regime"]["classification"], "range")

    def test_partial_regime_gets_defaults(self):
        """regimeの一部フィールドが欠落している場合に補完される"""
        data = {"regime": {"classification": "trend"}}
        result = _validate_and_fix_schema(data)
        self.assertEqual(result["regime"]["classification"], "trend")
        self.assertIn("adx_value", result["regime"])

    def test_complete_data_unchanged(self):
        """完全なデータはそのまま保持される"""
        ctx = _make_context()
        original = _fallback_structurize(ctx)
        result = _validate_and_fix_schema(original.copy())
        self.assertEqual(result["regime"]["classification"],
                         original["regime"]["classification"])


# ──────────────────────────────────────────────────────────
# _safe_float のテスト
# ──────────────────────────────────────────────────────────

class TestSafeFloat(unittest.TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_safe_float(None))

    def test_valid_float(self):
        self.assertEqual(_safe_float(3.14), 3.14)

    def test_valid_int(self):
        self.assertEqual(_safe_float(42), 42.0)

    def test_valid_string(self):
        self.assertEqual(_safe_float("5.5"), 5.5)

    def test_invalid_string(self):
        self.assertIsNone(_safe_float("abc"))

    def test_nan_returns_none(self):
        self.assertIsNone(_safe_float(float("nan")))


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
