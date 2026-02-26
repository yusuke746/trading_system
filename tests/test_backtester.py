"""
tests/test_backtester.py - backtester.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - AiJudgeMock の approve/reject 確率
  - BacktestEngine.run() の use_ai_mock フラグ
"""

import sys
import os
import unittest

import pandas as pd

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtester import AiJudgeMock, BacktestEngine, atr_breakout_signal


# ──────────────────────────────────────────────────────────
# テスト用 OHLCV DataFrame 生成
# ──────────────────────────────────────────────────────────

def _make_df(n: int = 200) -> pd.DataFrame:
    """
    単純なトレンドつき疑似 OHLCV データを生成する。
    シグナルが発生するよう十分な本数を用意する。
    """
    import random
    rng   = random.Random(0)
    price = 5000.0
    rows  = []
    for i in range(n):
        open_  = price
        high   = price + rng.uniform(5, 20)
        low    = price - rng.uniform(5, 20)
        close  = price + rng.uniform(-10, 10)
        price  = close
        rows.append({"open": open_, "high": high, "low": low,
                     "close": close, "volume": 1})
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────
# AiJudgeMock のテスト
# ──────────────────────────────────────────────────────────

class TestAiJudgeMock(unittest.TestCase):

    def test_approve_rate_1_always_approves(self):
        """approve_rate=1.0 のとき全件 approve を返す"""
        mock = AiJudgeMock(approve_rate=1.0)
        for i in range(50):
            result = mock.judge(i, "buy", 10.0, {})
            self.assertEqual(result["decision"], "approve",
                             f"bar={i}: expected approve, got reject")

    def test_approve_rate_0_always_rejects(self):
        """approve_rate=0.0 のとき全件 reject を返す"""
        mock = AiJudgeMock(approve_rate=0.0)
        for i in range(50):
            result = mock.judge(i, "sell", 10.0, {})
            self.assertEqual(result["decision"], "reject",
                             f"bar={i}: expected reject, got approve")

    def test_result_has_required_keys(self):
        """返り値に decision/confidence/ev_score/reason キーが含まれる"""
        mock   = AiJudgeMock(approve_rate=0.5)
        result = mock.judge(0, "buy", 5.0, {"context": "test"})
        self.assertIn("decision",   result)
        self.assertIn("confidence", result)
        self.assertIn("ev_score",   result)
        self.assertIn("reason",     result)

    def test_confidence_in_range(self):
        """confidence は 0.6〜0.9 の範囲内"""
        mock = AiJudgeMock(approve_rate=1.0)
        for i in range(100):
            result = mock.judge(i, "buy", 5.0, {})
            self.assertGreaterEqual(result["confidence"], 0.6)
            self.assertLessEqual(result["confidence"], 0.9)

    def test_ev_score_in_range(self):
        """ev_score は 0.2〜0.5 の範囲内"""
        mock = AiJudgeMock(approve_rate=1.0)
        for i in range(100):
            result = mock.judge(i, "buy", 5.0, {})
            self.assertGreaterEqual(result["ev_score"], 0.2)
            self.assertLessEqual(result["ev_score"], 0.5)

    def test_approve_rate_roughly_respected(self):
        """approve_rate=0.6 なら approve の割合がおよそ 60% になる"""
        mock     = AiJudgeMock(approve_rate=0.6)
        approves = sum(
            1 for i in range(1000)
            if mock.judge(i, "buy", 5.0, {})["decision"] == "approve"
        )
        # ±10% の許容範囲（500〜700 件）
        self.assertGreaterEqual(approves, 500)
        self.assertLessEqual(approves,    700)


# ──────────────────────────────────────────────────────────
# BacktestEngine.run() の use_ai_mock フラグテスト
# ──────────────────────────────────────────────────────────

class TestBacktestEngineAiMock(unittest.TestCase):

    def setUp(self):
        self.df     = _make_df(300)
        self.engine = BacktestEngine(self.df, random_seed=42)

    def test_ai_mock_false_baseline(self):
        """use_ai_mock=False でも正常に動作する"""
        result = self.engine.run(atr_breakout_signal, use_ai_mock=False)
        self.assertIsNotNone(result)

    def test_ai_mock_true_fewer_or_equal_trades(self):
        """
        use_ai_mock=True にするとトレード数が use_ai_mock=False 以下になる
        （AI が一部のエントリーを reject するため）
        """
        result_no_ai = self.engine.run(atr_breakout_signal, use_ai_mock=False)
        result_with_ai = self.engine.run(atr_breakout_signal,
                                          use_ai_mock=True,
                                          ai_approve_rate=0.6)
        self.assertLessEqual(
            result_with_ai.n_trades,
            result_no_ai.n_trades,
            f"AI mock should reduce or maintain trade count: "
            f"no_ai={result_no_ai.n_trades}, with_ai={result_with_ai.n_trades}",
        )

    def test_ai_mock_approve_rate_0_no_trades(self):
        """approve_rate=0.0 → AI が全 reject → トレード 0 件"""
        result = self.engine.run(atr_breakout_signal,
                                  use_ai_mock=True,
                                  ai_approve_rate=0.0)
        self.assertEqual(result.n_trades, 0,
                         "approve_rate=0.0 なのでトレードが発生してはならない")

    def test_ai_mock_approve_rate_1_same_as_no_mock(self):
        """approve_rate=1.0 → AI が全 approve → AI なし と同じトレード数"""
        result_no_ai  = self.engine.run(atr_breakout_signal, use_ai_mock=False)
        result_all_ok = self.engine.run(atr_breakout_signal,
                                         use_ai_mock=True,
                                         ai_approve_rate=1.0)
        self.assertEqual(result_all_ok.n_trades, result_no_ai.n_trades,
                         "approve_rate=1.0 なら AI なしと同じトレード数になるべき")

    def test_summary_includes_ai_info_when_enabled(self):
        """use_ai_mock=True のとき summary に AIモック情報が含まれる"""
        result  = self.engine.run(atr_breakout_signal, use_ai_mock=True)
        summary = result.summary(use_ai_mock=True, ai_approve_rate=0.6,
                                  ai_filter_effect=1.5)
        self.assertIn("AIモック", summary)
        self.assertIn("AIフィルター効果", summary)

    def test_summary_no_ai_info_when_disabled(self):
        """use_ai_mock=False のとき summary に AIモック情報が含まれない"""
        result  = self.engine.run(atr_breakout_signal, use_ai_mock=False)
        summary = result.summary(use_ai_mock=False)
        self.assertNotIn("AIモック", summary)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
