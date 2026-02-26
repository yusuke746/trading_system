"""
tests/test_news_filter.py - news_filter.py のユニットテスト
AI Trading System v2.0

テスト対象:
  - check_news_filter() のフェイルセーフ動作
  - fail_safe_triggered キーの存在確認
"""

import sys
import os
import unittest
from unittest.mock import patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNewsFilterFailSafe(unittest.TestCase):
    """MT5未インストール時のフェイルセーフ動作テスト"""

    def _run(self, fail_safe: bool):
        """
        MT5_AVAILABLE=False かつ NEWS_FILTER_ENABLED=True の状態で
        fail_safe の値だけ変えて check_news_filter() を呼ぶ。
        """
        with patch("news_filter.MT5_AVAILABLE", False), \
             patch("news_filter.NEWS_FILTER_ENABLED", True), \
             patch("news_filter.NEWS_FILTER_FAIL_SAFE", fail_safe), \
             patch("news_filter.log_event"):
            import news_filter
            return news_filter.check_news_filter()

    def test_mt5_unavailable_fail_safe_true_blocks(self):
        """MT5未インストール時に fail_safe=True なら blocked=True が返る"""
        result = self._run(fail_safe=True)
        self.assertTrue(result["blocked"],
                        "fail_safe=True なら MT5未接続時は blocked=True になるべき")

    def test_mt5_unavailable_fail_safe_false_passes(self):
        """MT5未インストール時に fail_safe=False なら blocked=False が返る"""
        result = self._run(fail_safe=False)
        self.assertFalse(result["blocked"],
                         "fail_safe=False なら MT5未接続時は blocked=False になるべき")

    def test_fail_safe_triggered_true_when_activated(self):
        """フェイルセーフ発動時は fail_safe_triggered=True が返る"""
        result = self._run(fail_safe=True)
        self.assertTrue(result.get("fail_safe_triggered"),
                        "フェイルセーフ発動時は fail_safe_triggered=True になるべき")

    def test_fail_safe_triggered_false_when_not_activated(self):
        """フェイルセーフ未発動時は fail_safe_triggered=False が返る"""
        result = self._run(fail_safe=False)
        self.assertFalse(result.get("fail_safe_triggered"),
                         "フェイルセーフ未発動時は fail_safe_triggered=False になるべき")

    def test_fail_safe_reason_contains_safe_keyword(self):
        """fail_safe=True のとき reason に「安全のためブロック」が含まれる"""
        result = self._run(fail_safe=True)
        self.assertIn("安全のためブロック", result.get("reason", ""),
                      "フェイルセーフ reason に「安全のためブロック」が含まれるべき")


class TestNewsFilterApiError(unittest.TestCase):
    """MT5カレンダーAPI取得失敗時のフェイルセーフ動作テスト"""

    def _run_with_api_error(self, fail_safe: bool):
        """
        MT5_AVAILABLE=True だがカレンダーAPI が例外を投げる状態でテスト。
        """
        import MetaTrader5 as mt5_mock  # 存在してもしなくても patch で上書き

        with patch("news_filter.MT5_AVAILABLE", True), \
             patch("news_filter.NEWS_FILTER_ENABLED", True), \
             patch("news_filter.NEWS_FILTER_FAIL_SAFE", fail_safe), \
             patch("news_filter.log_event"), \
             patch("news_filter.mt5") as mock_mt5:
            mock_mt5.calendar_event_get.side_effect = RuntimeError("API error")
            import news_filter
            return news_filter.check_news_filter()

    def test_api_error_fail_safe_true_blocks(self):
        """API取得失敗時に fail_safe=True なら blocked=True"""
        try:
            result = self._run_with_api_error(fail_safe=True)
            self.assertTrue(result["blocked"])
            self.assertTrue(result["fail_safe_triggered"])
        except ImportError:
            self.skipTest("MetaTrader5 not available in this environment")

    def test_api_error_fail_safe_false_passes(self):
        """API取得失敗時に fail_safe=False なら blocked=False"""
        try:
            result = self._run_with_api_error(fail_safe=False)
            self.assertFalse(result["blocked"])
            self.assertFalse(result["fail_safe_triggered"])
        except ImportError:
            self.skipTest("MetaTrader5 not available in this environment")


class TestNewsFilterReturnStructure(unittest.TestCase):
    """check_news_filter() の戻り値に fail_safe_triggered キーが必ず存在する"""

    def test_disabled_filter_has_fail_safe_triggered(self):
        """NEWS_FILTER_ENABLED=False でも fail_safe_triggered キーが存在する"""
        with patch("news_filter.NEWS_FILTER_ENABLED", False):
            import news_filter
            result = news_filter.check_news_filter()
        self.assertIn("fail_safe_triggered", result)
        self.assertFalse(result["fail_safe_triggered"])


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
