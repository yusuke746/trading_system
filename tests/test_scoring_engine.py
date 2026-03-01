"""
tests/test_scoring_engine.py - scoring_engine.py のユニットテスト
AI Trading System v3.0

テスト対象:
  - calculate_score() の各ルール判定
  - 即rejectパターン
  - スコア閾値によるapprove/wait/reject判定
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring_engine import calculate_score
from config import SCORING_CONFIG


# ──────────────────────────────────────────────────────────
# テスト用ヘルパー: 構造化データ生成
# ──────────────────────────────────────────────────────────

def _make_structured(
    regime="range",
    adx_value=15.0,
    atr_expanding=False,
    zone_touch=False,
    zone_direction=None,
    fvg_touch=False,
    fvg_direction=None,
    liquidity_sweep=False,
    sweep_direction=None,
    rsi_value=50.0,
    rsi_zone="neutral",
    trend_aligned=False,
    bar_close_confirmed=True,
    session="London",
    tv_confidence=None,
    tv_win_rate=None,        # 後方互換のため残す
    pattern_similarity=None, # Lorentzian v2
    sma20_distance_pct=None,
    fields_missing=None,
) -> dict:
    return {
        "regime": {
            "classification": regime,
            "adx_value": adx_value,
            "adx_rising": adx_value > 20 if adx_value else None,
            "atr_expanding": atr_expanding,
            "squeeze_detected": False,
        },
        "price_structure": {
            "above_sma20": True,
            "sma20_distance_pct": sma20_distance_pct if sma20_distance_pct is not None else 0.5,
            "perfect_order": None,
            "higher_highs": None,
            "lower_lows": None,
        },
        "zone_interaction": {
            "zone_touch": zone_touch,
            "zone_direction": zone_direction,
            "fvg_touch": fvg_touch,
            "fvg_direction": fvg_direction,
            "liquidity_sweep": liquidity_sweep,
            "sweep_direction": sweep_direction,
        },
        "momentum": {
            "rsi_value": rsi_value,
            "rsi_zone": rsi_zone,
            "trend_aligned": trend_aligned,
        },
        "signal_quality": {
            "source": "Lorentzian",
            "bar_close_confirmed": bar_close_confirmed,
            "session": session,
            "tv_confidence": tv_confidence,
            "tv_win_rate": tv_win_rate,
            "pattern_similarity": pattern_similarity,
        },
        "data_completeness": {
            "mt5_connected": True,
            "fields_missing": fields_missing or [],
        },
    }


# ──────────────────────────────────────────────────────────
# 即Rejectテスト
# ──────────────────────────────────────────────────────────

class TestInstantReject(unittest.TestCase):

    def test_range_mid_chase_rejected(self):
        """レンジ中央での順張り → 即reject"""
        data = _make_structured(
            regime="range",
            sma20_distance_pct=0.1,  # ±0.3%以内
            zone_touch=False,
            fvg_touch=False,
        )
        result = calculate_score(data, "buy")
        self.assertEqual(result["decision"], "reject")
        self.assertLess(result["score"], 0)

    def test_range_with_zone_touch_not_rejected(self):
        """レンジだがゾーンタッチありなら即rejectしない"""
        data = _make_structured(
            regime="range",
            sma20_distance_pct=0.1,
            zone_touch=True,
            zone_direction="demand",
        )
        result = calculate_score(data, "buy")
        self.assertNotEqual(result["score"], -999.0)

    def test_critical_data_missing_rejected(self):
        """重要データ欠損 → 即reject"""
        data = _make_structured(
            fields_missing=["rsi_value", "adx_value", "atr_expanding"],
        )
        result = calculate_score(data, "buy")
        self.assertEqual(result["decision"], "reject")


# ──────────────────────────────────────────────────────────
# レジームスコアテスト
# ──────────────────────────────────────────────────────────

class TestRegimeScore(unittest.TestCase):

    def test_trend_regime_positive_base(self):
        """Trendレジームは正の基礎スコアを持つ"""
        data = _make_structured(regime="trend")
        result = calculate_score(data, "buy")
        self.assertIn("regime_trend_base", result["score_breakdown"])
        self.assertGreater(result["score_breakdown"]["regime_trend_base"], 0)

    def test_breakout_regime_highest_base(self):
        """Breakoutレジームは最高の基礎スコアを持つ"""
        data = _make_structured(regime="breakout")
        result = calculate_score(data, "buy")
        self.assertIn("regime_breakout_base", result["score_breakdown"])
        breakout_base = result["score_breakdown"]["regime_breakout_base"]
        self.assertEqual(breakout_base, SCORING_CONFIG["regime_breakout_base"])

    def test_range_regime_negative_base(self):
        """Rangeレジームは負の基礎スコアを持つ"""
        data = _make_structured(regime="range", sma20_distance_pct=1.0)
        result = calculate_score(data, "buy")
        self.assertIn("regime_range_base", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["regime_range_base"], 0)


# ──────────────────────────────────────────────────────────
# ゾーン・構造スコアテスト
# ──────────────────────────────────────────────────────────

class TestStructureScore(unittest.TestCase):

    def test_zone_touch_aligned_adds_score(self):
        """ゾーンタッチ（方向一致・Q-trend整合）で加点される"""
        data = _make_structured(
            zone_touch=True,
            zone_direction="demand",
            trend_aligned=True,
        )
        result = calculate_score(data, "buy")
        self.assertIn("zone_touch_aligned_with_trend", result["score_breakdown"])
        self.assertGreater(result["score_breakdown"]["zone_touch_aligned_with_trend"], 0)

    def test_zone_touch_misaligned_no_score(self):
        """ゾーンタッチ（方向不一致）で加点されない"""
        data = _make_structured(
            zone_touch=True,
            zone_direction="supply",
        )
        result = calculate_score(data, "buy")
        self.assertNotIn("zone_touch_aligned_with_trend", result["score_breakdown"])
        self.assertNotIn("zone_touch_counter_trend", result["score_breakdown"])

    def test_fvg_touch_aligned_adds_score(self):
        """FVGタッチ（方向一致・Q-trend整合）で加点される"""
        data = _make_structured(
            fvg_touch=True,
            fvg_direction="bullish",
            trend_aligned=True,
        )
        result = calculate_score(data, "buy")
        self.assertIn("fvg_touch_aligned_with_trend", result["score_breakdown"])

    def test_liquidity_sweep_adds_score(self):
        """リクイディティスイープで加点される（sell_side sweep + buy entry = 正しい逆張り）"""
        data = _make_structured(
            liquidity_sweep=True,
            sweep_direction="sell_side",
        )
        result = calculate_score(data, "buy")
        self.assertIn("liquidity_sweep", result["score_breakdown"])

    def test_sweep_plus_zone_combo_bonus(self):
        """スイープ + ゾーンタッチのコンボボーナス（sell_side sweep + demand zone + buy entry）"""
        data = _make_structured(
            liquidity_sweep=True,
            sweep_direction="sell_side",
            zone_touch=True,
            zone_direction="demand",
        )
        result = calculate_score(data, "buy")
        self.assertIn("sweep_plus_zone", result["score_breakdown"])


# ──────────────────────────────────────────────────────────
# モメンタムスコアテスト
# ──────────────────────────────────────────────────────────

class TestMomentumScore(unittest.TestCase):

    def test_trend_aligned_adds_score(self):
        """トレンド方向一致で加点される"""
        data = _make_structured(trend_aligned=True)
        result = calculate_score(data, "buy")
        self.assertIn("trend_aligned", result["score_breakdown"])

    def test_rsi_confirmation_buy_oversold(self):
        """買い + RSI oversold で加点される"""
        data = _make_structured(rsi_value=25.0, rsi_zone="oversold")
        result = calculate_score(data, "buy")
        self.assertIn("rsi_confirmation", result["score_breakdown"])

    def test_rsi_divergence_buy_overbought(self):
        """買い + RSI overbought で減点される"""
        data = _make_structured(rsi_value=75.0, rsi_zone="overbought")
        result = calculate_score(data, "buy")
        self.assertIn("rsi_divergence", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["rsi_divergence"], 0)


# ──────────────────────────────────────────────────────────
# シグナル品質スコアテスト
# ──────────────────────────────────────────────────────────

class TestSignalQualityScore(unittest.TestCase):

    def test_bar_close_confirmed_adds_score(self):
        """バークローズ確認で加点される"""
        data = _make_structured(bar_close_confirmed=True)
        result = calculate_score(data, "buy")
        self.assertIn("bar_close_confirmed", result["score_breakdown"])

    def test_session_london_ny_bonus(self):
        """London_NYセッションで加点される"""
        data = _make_structured(session="London_NY")
        result = calculate_score(data, "buy")
        self.assertIn("session_london_ny", result["score_breakdown"])

    def test_session_off_hours_penalty(self):
        """off_hoursセッションで減点される"""
        data = _make_structured(session="off_hours")
        result = calculate_score(data, "buy")
        self.assertIn("session_off_hours", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["session_off_hours"], 0)

    def test_tv_confidence_high_bonus(self):
        """高TV confidence で加点される"""
        data = _make_structured(tv_confidence=0.85)
        result = calculate_score(data, "buy")
        self.assertIn("tv_confidence_high", result["score_breakdown"])


# ──────────────────────────────────────────────────────────
# 判定閾値テスト
# ──────────────────────────────────────────────────────────

class TestDecisionThresholds(unittest.TestCase):

    def test_high_score_approves(self):
        """高スコアで approve される（sell_side sweep + buy = 正しい逆張り）"""
        data = _make_structured(
            regime="trend",
            trend_aligned=True,
            zone_touch=True,
            zone_direction="demand",
            bar_close_confirmed=True,
            session="London_NY",
            liquidity_sweep=True,
            sweep_direction="sell_side",
        )
        result = calculate_score(data, "buy")
        self.assertEqual(result["decision"], "approve")
        self.assertGreaterEqual(result["score"], SCORING_CONFIG["approve_threshold"])

    def test_low_score_rejects(self):
        """低スコアで reject される"""
        data = _make_structured(
            regime="range",
            sma20_distance_pct=1.0,  # Not too close to SMA20
            session="off_hours",
            bar_close_confirmed=False,
            rsi_value=75.0,
            rsi_zone="overbought",
        )
        result = calculate_score(data, "buy")
        self.assertEqual(result["decision"], "reject")

    def test_medium_score_waits(self):
        """中間スコアで wait される"""
        data = _make_structured(
            regime="trend",
            trend_aligned=True,
            bar_close_confirmed=False,
            sma20_distance_pct=1.0,
        )
        result = calculate_score(data, "buy")
        # trend_aligned(0.10) + regime_trend_base(0.15) = 0.25
        # bar_close_confirmed=False なので +0.10 がない
        # 合計 0.25 >= wait_threshold(0.10) かつ < approve_threshold(0.45)
        self.assertEqual(result["decision"], "wait")
        self.assertIsNotNone(result["wait_condition"])

    def test_result_has_required_keys(self):
        """結果に必要なキーが含まれる"""
        data = _make_structured()
        result = calculate_score(data, "buy")
        self.assertIn("decision", result)
        self.assertIn("score", result)
        self.assertIn("score_breakdown", result)
        self.assertIn("reject_reasons", result)
        self.assertIn("wait_condition", result)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

class TestPatternSimilarity(unittest.TestCase):

    def test_pattern_similarity_high_adds_score(self):
        """pattern_similarity > 0.70 → pattern_similarity_high加点"""
        structured = _make_structured(
            regime="trend",
            zone_touch=True, zone_direction="demand",
            trend_aligned=True,
            bar_close_confirmed=True,
            pattern_similarity=0.85,
        )
        result = calculate_score(structured, "buy")
        self.assertIn("pattern_similarity_high", result["score_breakdown"])
        self.assertGreater(result["score_breakdown"]["pattern_similarity_high"], 0)

    def test_pattern_similarity_low_reduces_score(self):
        """pattern_similarity < 0.30 → pattern_similarity_low減点"""
        structured = _make_structured(
            regime="trend",
            zone_touch=True, zone_direction="demand",
            trend_aligned=True,
            bar_close_confirmed=True,
            pattern_similarity=0.15,
        )
        result = calculate_score(structured, "buy")
        self.assertIn("pattern_similarity_low", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["pattern_similarity_low"], 0)

    def test_pattern_similarity_none_no_effect(self):
        """pattern_similarity=None（旧バージョン互換）→ 加減点なし"""
        structured_with = _make_structured(
            regime="trend", zone_touch=True, zone_direction="demand",
            trend_aligned=True, pattern_similarity=0.50,
        )
        structured_none = _make_structured(
            regime="trend", zone_touch=True, zone_direction="demand",
            trend_aligned=True, pattern_similarity=None,
        )
        result_with = calculate_score(structured_with, "buy")
        result_none = calculate_score(structured_none, "buy")
        # Noneの場合はpattern_similarity系のキーがbreakdownに存在しない
        self.assertNotIn("pattern_similarity_high", result_none["score_breakdown"])
        self.assertNotIn("pattern_similarity_low", result_none["score_breakdown"])

    def test_tv_win_rate_bonus_removed(self):
        """tv_win_rate_bonusキーがSCORING_CONFIGに存在しないこと"""
        self.assertNotIn("tv_win_rate_bonus", SCORING_CONFIG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
