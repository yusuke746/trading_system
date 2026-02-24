"""
tests/test_executor.py - executor.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - build_order_params()
  - pre_execution_check()
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SYSTEM_CONFIG


# ──────────────────────────────────────────────────────────
# テスト用モックとヘルパー
# ──────────────────────────────────────────────────────────

def _make_trigger(direction: str = "buy", price: float = 5200.0,
                  symbol: str = "XAUUSD") -> dict:
    return {"direction": direction, "price": price, "symbol": symbol}


def _make_ai_result(order_type: str = "market",
                    limit_price: float | None = None) -> dict:
    return {
        "decision":    "approve",
        "confidence":  0.85,
        "ev_score":    0.45,
        "order_type":  order_type,
        "limit_price": limit_price,
        "limit_expiry": None,
    }


def _live_params_default() -> dict:
    """動的最適化をバイパスしてデフォルト値を返すモック"""
    return {
        "atr_sl_multiplier": SYSTEM_CONFIG["atr_sl_multiplier"],
        "atr_tp_multiplier": SYSTEM_CONFIG["atr_tp_multiplier"],
        "regime": "range",
        "win_rate": 0.5,
        "consecutive_losses": 0,
        "reason": "テストモード",
    }


# ──────────────────────────────────────────────────────────
# build_order_params のテスト
# ──────────────────────────────────────────────────────────

class TestBuildOrderParams(unittest.TestCase):
    """
    MT5 なし（テストモード）の状態で build_order_params を検証する。
    _get_atr15m はモックして固定 ATR 値を返す。
    param_optimizer.get_live_params もモックしてデフォルト乗数を返す。
    """

    def _run(self, direction="buy", price=5200.0, atr=10.0,
             order_type="market", limit_price=None, symbol="XAUUSD"):
        trigger   = _make_trigger(direction, price, symbol)
        ai_result = _make_ai_result(order_type, limit_price)
        with patch("executor._get_atr15m", return_value=atr), \
             patch("executor.param_optimizer.get_live_params",
                   return_value=_live_params_default()):
            import executor
            return executor.build_order_params(trigger, ai_result)

    # ── 基本的な方向別テスト ──────────────────────────────

    def test_buy_sl_below_price(self):
        """BUY: SL は エントリー価格より低い"""
        params = self._run(direction="buy", price=5200.0, atr=10.0)
        self.assertIsNotNone(params)
        self.assertLess(params["sl_price"], params["entry_price"])

    def test_buy_tp_above_price(self):
        """BUY: TP は エントリー価格より高い"""
        params = self._run(direction="buy", price=5200.0, atr=10.0)
        self.assertIsNotNone(params)
        self.assertGreater(params["tp_price"], params["entry_price"])

    def test_sell_sl_above_price(self):
        """SELL: SL は エントリー価格より高い"""
        params = self._run(direction="sell", price=5200.0, atr=10.0)
        self.assertIsNotNone(params)
        self.assertGreater(params["sl_price"], params["entry_price"])

    def test_sell_tp_below_price(self):
        """SELL: TP は エントリー価格より低い"""
        params = self._run(direction="sell", price=5200.0, atr=10.0)
        self.assertIsNotNone(params)
        self.assertLess(params["tp_price"], params["entry_price"])

    # ── SL 計算の検証 ─────────────────────────────────────

    def test_sl_uses_atr_multiplier(self):
        """SL距離 = ATR × SL乗数 でクランプ前の値が一致する"""
        atr      = 10.0
        sl_mult  = SYSTEM_CONFIG["atr_sl_multiplier"]
        price    = 5200.0
        params   = self._run(direction="buy", price=price, atr=atr)
        expected_sl_dollar = min(
            max(atr * sl_mult, SYSTEM_CONFIG["min_sl_pips"]),
            SYSTEM_CONFIG["max_sl_pips"],
        )
        self.assertAlmostEqual(
            params["entry_price"] - params["sl_price"],
            expected_sl_dollar, places=2,
        )

    def test_sl_clamped_to_min(self):
        """ATR × SL_MULT < min_sl_pips のとき SL は min_sl_pips にクランプされる"""
        # min_sl_pips=8, sl_mult=2.0 → atr*2 < 8 のとき発動
        # atr=3.5: ボラ下限(3.0)を超えるのでフィルター通過し、クランプが発動する
        atr    = 3.5   # 3.5 * 2.0 = 7.0 < min_sl_pips(8)
        params = self._run(direction="buy", price=5200.0, atr=atr)
        self.assertIsNotNone(params)
        self.assertAlmostEqual(
            params["entry_price"] - params["sl_price"],
            SYSTEM_CONFIG["min_sl_pips"], places=2,
        )

    def test_sl_clamped_to_max(self):
        """ATR が非常に大きい場合 SL は max_sl_pips にクランプされる"""
        atr    = 50.0   # ATR × SL_MULT = 100 > max_sl_pips(80)
        # ただし ATR > atr_volatility_max(30) の場合は None が返る
        # ここでは atr_volatility_max をパッチして上書き
        with patch.dict("executor.SYSTEM_CONFIG", {"atr_volatility_max": 200.0}):
            params = self._run(direction="buy", price=5200.0, atr=atr)
        self.assertIsNotNone(params)
        self.assertLessEqual(
            params["entry_price"] - params["sl_price"],
            SYSTEM_CONFIG["max_sl_pips"] + 0.01,
        )

    # ── TP 計算の検証 ─────────────────────────────────────

    def test_tp_uses_atr_tp_multiplier(self):
        """TP距離 = ATR × TP乗数"""
        atr     = 10.0
        tp_mult = SYSTEM_CONFIG["atr_tp_multiplier"]
        price   = 5200.0
        params  = self._run(direction="buy", price=price, atr=atr)
        expected_tp_dollar = round(atr * tp_mult, 3)
        self.assertAlmostEqual(
            params["tp_price"] - params["entry_price"],
            expected_tp_dollar, places=2,
        )

    # ── ロットサイズの検証 ───────────────────────────────

    def test_lot_size_positive(self):
        """ロットサイズは必ず正"""
        params = self._run()
        self.assertGreater(params["lot_size"], 0)

    def test_lot_size_minimum_001(self):
        """ロット計算結果が 0.01 未満でも最小 0.01 に切り上げられる"""
        # RISK_PERCENT を極小にして lot_size の計算値 < 0.01 を作り出す
        import executor
        trigger   = _make_trigger("buy", 5200.0)
        ai_result = _make_ai_result()
        with patch("executor._get_atr15m", return_value=10.0), \
             patch("executor.RISK_PERCENT", 0.0001), \
             patch("executor.param_optimizer.get_live_params",
                   return_value=_live_params_default()):
            params = executor.build_order_params(trigger, ai_result)
        self.assertIsNotNone(params)
        self.assertGreaterEqual(params["lot_size"], 0.01)

    def test_lot_size_formula(self):
        """ロット = risk_amount / (sl_dollar × 100)"""
        atr      = 10.0
        sl_mult  = SYSTEM_CONFIG["atr_sl_multiplier"]
        sl_dollar= min(
            max(atr * sl_mult, SYSTEM_CONFIG["min_sl_pips"]),
            SYSTEM_CONFIG["max_sl_pips"],
        )
        balance   = 10000.0   # MT5 なしのデフォルト残高
        risk_pct  = SYSTEM_CONFIG["risk_percent"] / 100.0
        expected  = round(balance * risk_pct / (sl_dollar * 100.0), 2)
        params    = self._run(atr=atr)
        self.assertAlmostEqual(params["lot_size"], expected, places=2)

    # ── ATR ボラティリティフィルター ────────────────────

    def test_atr_too_high_returns_none(self):
        """ATR > atr_volatility_max → None を返す"""
        atr_max = SYSTEM_CONFIG["atr_volatility_max"]
        result  = self._run(atr=atr_max + 1.0)
        self.assertIsNone(result)

    def test_atr_too_low_returns_none(self):
        """ATR < atr_volatility_min → None を返す"""
        atr_min = SYSTEM_CONFIG["atr_volatility_min"]
        result  = self._run(atr=atr_min - 0.5)
        self.assertIsNone(result)

    def test_atr_at_boundary_passes(self):
        """ATR がボラ境界値ちょうど（min < atr < max）→ None ではない"""
        atr_min = SYSTEM_CONFIG["atr_volatility_min"]
        atr_max = SYSTEM_CONFIG["atr_volatility_max"]
        mid_atr = (atr_min + atr_max) / 2
        result  = self._run(atr=mid_atr)
        self.assertIsNotNone(result)

    # ── 指値注文 ──────────────────────────────────────────

    def test_limit_order_uses_limit_price(self):
        """指値注文: entry_price は limit_price になる"""
        limit = 5190.0
        params = self._run(direction="buy", price=5200.0,
                           order_type="limit", limit_price=limit)
        self.assertIsNotNone(params)
        self.assertAlmostEqual(params["entry_price"], limit)
        self.assertEqual(params["order_type"], "limit")

    def test_market_order_uses_trigger_price(self):
        """成行注文: entry_price はトリガーの price になる"""
        price  = 5200.0
        params = self._run(direction="buy", price=price, order_type="market")
        self.assertIsNotNone(params)
        self.assertAlmostEqual(params["entry_price"], price)

    # ── 動的パラメータの記録 ─────────────────────────────

    def test_dynamic_mult_keys_present(self):
        """atr_sl_mult / atr_tp_mult キーが返り値に含まれる"""
        params = self._run()
        self.assertIn("atr_sl_mult", params)
        self.assertIn("atr_tp_mult", params)

    def test_dynamic_mult_values_match_config(self):
        """動的乗数がデフォルト config 値と一致する（モック時）"""
        params = self._run()
        self.assertAlmostEqual(
            params["atr_sl_mult"], SYSTEM_CONFIG["atr_sl_multiplier"])
        self.assertAlmostEqual(
            params["atr_tp_mult"], SYSTEM_CONFIG["atr_tp_multiplier"])


# ──────────────────────────────────────────────────────────
# pre_execution_check のテスト
# ──────────────────────────────────────────────────────────

class TestPreExecutionCheck(unittest.TestCase):

    def _pass_all(self):
        """ニュース・市場・リスクの全チェックをパスするパッチセット"""
        news_ok = patch(
            "executor.check_news_filter",
            return_value={"blocked": False, "reason": "pass", "resumes_at": None},
        )
        mkt_ok = patch(
            "executor.full_market_check",
            return_value={"ok": True, "reason": "市場オープン"},
        )
        risk_ok = patch(
            "executor.risk_manager.run_all_risk_checks",
            return_value={"blocked": False, "reason": "ok", "details": {}},
        )
        return news_ok, mkt_ok, risk_ok

    def test_all_checks_pass(self):
        """全チェック通過 → ok=True"""
        import executor
        n, m, r = self._pass_all()
        with n, m, r:
            result = executor.pre_execution_check("XAUUSD", 5200.0)
        self.assertTrue(result["ok"])

    def test_news_filter_blocks(self):
        """ニュースフィルターがブロック → ok=False"""
        import executor
        news_blocked = patch(
            "executor.check_news_filter",
            return_value={"blocked": True, "reason": "指標ブロック",
                          "resumes_at": "2026-02-24T12:00:00"},
        )
        _, m, r = self._pass_all()
        with news_blocked, m, r:
            result = executor.pre_execution_check("XAUUSD", 5200.0)
        self.assertFalse(result["ok"])
        self.assertIn("指標ブロック", result["reason"])

    def test_market_closed_blocks(self):
        """市場クローズ → ok=False"""
        import executor
        n, _, r = self._pass_all()
        mkt_closed = patch(
            "executor.full_market_check",
            return_value={"ok": False, "reason": "週末クローズ"},
        )
        with n, mkt_closed, r:
            result = executor.pre_execution_check("XAUUSD", 5200.0)
        self.assertFalse(result["ok"])
        self.assertIn("週末", result["reason"])

    def test_risk_manager_blocks(self):
        """リスク管理がブロック → ok=False"""
        import executor
        n, m, _ = self._pass_all()
        risk_blocked = patch(
            "executor.risk_manager.run_all_risk_checks",
            return_value={
                "blocked": True,
                "reason": "当日損失超過",
                "details": {},
            },
        )
        with n, m, risk_blocked:
            result = executor.pre_execution_check("XAUUSD", 5200.0)
        self.assertFalse(result["ok"])
        self.assertIn("損失", result["reason"])

    def test_news_filter_checked_before_market(self):
        """ニュースフィルターが市場チェックより先に評価される"""
        import executor
        call_order = []

        def news_check(_sym):
            call_order.append("news")
            return {"blocked": False, "reason": "pass", "resumes_at": None}

        def mkt_check(_sym):
            call_order.append("market")
            return {"ok": True, "reason": "pass"}

        risk_ok = patch(
            "executor.risk_manager.run_all_risk_checks",
            return_value={"blocked": False, "reason": "ok", "details": {}},
        )
        with patch("executor.check_news_filter", side_effect=news_check), \
             patch("executor.full_market_check",  side_effect=mkt_check), \
             risk_ok:
            executor.pre_execution_check("XAUUSD", 5200.0)

        self.assertEqual(call_order[0], "news")
        self.assertEqual(call_order[1], "market")

    def test_mt5_unavailable_returns_ok(self):
        """MT5 なし（テストモード）でも全チェック通過なら ok=True"""
        import executor
        n, m, r = self._pass_all()
        with n, m, r, patch("executor.MT5_AVAILABLE", False):
            result = executor.pre_execution_check("XAUUSD", 5200.0)
        self.assertTrue(result["ok"])
        self.assertIn("テストモード", result["reason"])


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
