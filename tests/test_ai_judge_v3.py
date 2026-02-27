"""
tests/test_ai_judge_v3.py - ai_judge.py v3.0 のユニットテスト
AI Trading System v3.0

テスト対象:
  - ask_ai() の v3.0 パイプライン（LLM構造化 → スコアリング）
  - should_execute() の判定ロジック
  - _score_to_confidence() のマッピング
  - 後方互換性
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────────────────
# テスト用モックデータ
# ──────────────────────────────────────────────────────────

def _make_context():
    """テスト用コンテキスト"""
    return {
        "entry_signals": [
            {
                "direction": "buy",
                "source": "Lorentzian",
                "price": 5200.0,
                "confirmed": "bar_close",
                "tv_confidence": 0.75,
                "tv_win_rate": 0.60,
            }
        ],
        "mt5_context": {
            "indicators_5m": {
                "rsi14": 45.0,
                "atr14": 10.0,
                "sma20": 5190.0,
                "close": 5200.0,
                "adx14": 22.0,
            },
            "indicators_15m": {
                "rsi14": 48.0,
                "atr14": 12.0,
                "sma20": 5185.0,
                "adx14": 22.0,
            },
            "indicators_1h": {
                "sma50": 5180.0,
                "sma200": 5100.0,
                "close": 5200.0,
            },
        },
        "structure": {
            "macro_zones": [],
            "zone_retrace": [{"direction": "buy", "price": 5190.0}],
            "fvg_touch": [],
            "liquidity_sweep": [{"direction": "sell", "price": 5180.0}],
        },
        "q_trend_context": {
            "direction": "buy",
            "strength": "normal",
        },
        "statistical_context": {
            "market_regime": {
                "atr_percentile_15m": 50,
                "rsi_zscore_5m": 0.0,
                "trend_strength": "bull",
            },
            "trading_stats": {
                "win_rate": 0.55,
                "avg_pnl_usd": 10.0,
                "consecutive_losses": 0,
                "trade_count": 20,
            },
            "session_info": {
                "session": "London",
                "volatility": "medium",
            },
        },
        "generated_at": "2026-02-28T10:00:00",
    }


def _make_mock_structured():
    """LLM構造化出力のモック"""
    return {
        "regime": {
            "classification": "trend",
            "adx_value": 22.0,
            "adx_rising": True,
            "atr_expanding": False,
            "squeeze_detected": False,
        },
        "price_structure": {
            "above_sma20": True,
            "sma20_distance_pct": 0.5,
            "perfect_order": True,
            "higher_highs": True,
            "lower_lows": False,
        },
        "zone_interaction": {
            "zone_touch": True,
            "zone_direction": "demand",
            "fvg_touch": False,
            "fvg_direction": None,
            "liquidity_sweep": True,
            "sweep_direction": "buy_side",
        },
        "momentum": {
            "rsi_value": 45.0,
            "rsi_zone": "neutral",
            "trend_aligned": True,
        },
        "signal_quality": {
            "source": "Lorentzian",
            "bar_close_confirmed": True,
            "session": "London",
            "tv_confidence": 0.75,
            "tv_win_rate": 0.60,
        },
        "data_completeness": {
            "mt5_connected": True,
            "fields_missing": [],
        },
    }


# ──────────────────────────────────────────────────────────
# ask_ai v3.0 テスト
# ──────────────────────────────────────────────────────────

class TestAskAiV3(unittest.TestCase):

    @patch("ai_judge.structurize")
    def test_v3_pipeline_with_context(self, mock_structurize):
        """context渡し時にv3パイプラインが使用される"""
        mock_structurize.return_value = _make_mock_structured()

        from ai_judge import ask_ai
        result = ask_ai(
            messages=[],
            context=_make_context(),
            signal_direction="buy",
        )

        self.assertIn("decision", result)
        self.assertIn("score_breakdown", result)
        self.assertIn("market_regime", result)
        mock_structurize.assert_called_once()

    @patch("ai_judge.structurize")
    def test_v3_returns_valid_decision(self, mock_structurize):
        """v3パイプラインが有効な判定を返す"""
        mock_structurize.return_value = _make_mock_structured()

        from ai_judge import ask_ai
        result = ask_ai(
            messages=[],
            context=_make_context(),
            signal_direction="buy",
        )

        self.assertIn(result["decision"], ["approve", "reject", "wait"])

    @patch("ai_judge.structurize")
    def test_v3_confidence_in_range(self, mock_structurize):
        """confidenceが0.0〜1.0の範囲内"""
        mock_structurize.return_value = _make_mock_structured()

        from ai_judge import ask_ai
        result = ask_ai(
            messages=[],
            context=_make_context(),
            signal_direction="buy",
        )

        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)

    @patch("ai_judge.structurize")
    def test_v3_regime_from_structured(self, mock_structurize):
        """market_regimeが構造化データから取得される"""
        mock_structurize.return_value = _make_mock_structured()

        from ai_judge import ask_ai
        result = ask_ai(
            messages=[],
            context=_make_context(),
            signal_direction="buy",
        )

        self.assertEqual(result["market_regime"], "trend")


# ──────────────────────────────────────────────────────────
# should_execute テスト
# ──────────────────────────────────────────────────────────

class TestShouldExecute(unittest.TestCase):

    def test_approve_with_high_scores(self):
        """approve + 高confidence + 高ev_score → True"""
        from ai_judge import should_execute
        result = should_execute({
            "decision": "approve",
            "confidence": 0.85,
            "ev_score": 0.45,
        })
        self.assertTrue(result)

    def test_reject_returns_false(self):
        """reject → False"""
        from ai_judge import should_execute
        result = should_execute({
            "decision": "reject",
            "confidence": 0.85,
            "ev_score": 0.45,
        })
        self.assertFalse(result)

    def test_low_confidence_returns_false(self):
        """approve だが低confidence → False"""
        from ai_judge import should_execute
        result = should_execute({
            "decision": "approve",
            "confidence": 0.50,
            "ev_score": 0.45,
        })
        self.assertFalse(result)

    def test_low_ev_score_returns_false(self):
        """approve だが低ev_score → False"""
        from ai_judge import should_execute
        result = should_execute({
            "decision": "approve",
            "confidence": 0.85,
            "ev_score": 0.10,
        })
        self.assertFalse(result)

    def test_wait_returns_false(self):
        """wait → False"""
        from ai_judge import should_execute
        result = should_execute({
            "decision": "wait",
            "confidence": 0.85,
            "ev_score": 0.45,
        })
        self.assertFalse(result)


# ──────────────────────────────────────────────────────────
# _score_to_confidence テスト
# ──────────────────────────────────────────────────────────

class TestScoreToConfidence(unittest.TestCase):

    def test_zero_score(self):
        """スコア0 → confidence 0"""
        from ai_judge import _score_to_confidence
        self.assertEqual(_score_to_confidence(0), 0.0)

    def test_negative_score(self):
        """負のスコア → confidence 0"""
        from ai_judge import _score_to_confidence
        self.assertEqual(_score_to_confidence(-0.5), 0.0)

    def test_high_score_capped(self):
        """高スコア → confidence ≤ 1.0"""
        from ai_judge import _score_to_confidence
        self.assertLessEqual(_score_to_confidence(2.0), 1.0)

    def test_mid_score_reasonable(self):
        """中間スコアで妥当なconfidence"""
        from ai_judge import _score_to_confidence
        conf = _score_to_confidence(0.30)
        self.assertGreater(conf, 0.5)
        self.assertLess(conf, 1.0)


# ──────────────────────────────────────────────────────────
# エラーハンドリングテスト
# ──────────────────────────────────────────────────────────

class TestErrorHandling(unittest.TestCase):

    @patch("ai_judge.structurize", side_effect=Exception("API Error"))
    @patch("notifier.notify_ai_api_error")
    def test_error_returns_reject(self, mock_notify, mock_structurize):
        """エラー時に reject を返す"""
        from ai_judge import ask_ai
        result = ask_ai(
            messages=[],
            context=_make_context(),
            signal_direction="buy",
        )

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["confidence"], 0.0)
        mock_notify.assert_called_once()


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
