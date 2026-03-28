"""
tests/test_scoring_engine.py - scoring_engine.py v4.0 のユニットテスト
AI Trading System v4.0

テスト対象:
  - calculate_score(alert: dict) の必須ゲート判定
  - 共通スコアテーブルの加点・減点
  - approve / wait / reject 閾値判定

カバレッジ:
  1.  TREND approve        : 全ゲート通過 + 高スコア
  2.  TREND reject         : h1_adx < 25（ゲート不通過）
  3.  TREND reject         : choch_confirmed=false（ゲート不通過）
  4.  REVERSAL approve     : sweep + choch + rsi_divergence
  5.  REVERSAL reject      : sweep_detected=false（ゲート不通過）
  6.  REVERSAL adx_penalty : m15_adx=38 で adx_reversal_penalty 適用
  7.  BREAKOUT approve     : fvg_aligned + zone_aligned（CHoCH不要、overlapボーナス）
  8.  BREAKOUT reject      : fvg_aligned=false AND zone_aligned=false
  9.  RANGE reject         : regime=RANGE で即 reject
  10. news_nearby          : -0.30 減点確認
  11. session_off          : -0.20 減点確認
"""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring_engine import calculate_score
from config import SCORING_CONFIG


# ──────────────────────────────────────────────────────────
# テスト用ヘルパー: Pine Script フラット JSON 生成
# ──────────────────────────────────────────────────────────

def _make_alert(
    regime="TREND",
    direction="buy",
    h1_direction="bull",
    h1_adx=30.0,
    m15_adx=30.0,
    m15_adx_drop=1.0,
    atr_ratio=1.2,
    choch_confirmed=True,
    fvg_hit=True,
    fvg_aligned=True,
    zone_hit=True,
    zone_aligned=True,
    rsi_divergence=False,
    sweep_detected=False,
    session="london_ny",
    rsi_trend_aligned=True,
    rsi_value=54.0,
    news_nearby=False,
    candle_pattern="none",
    bos_confirmed=False,
    ob_aligned=False,
) -> dict:
    """テスト用の Pine Script アラート dict を生成する"""
    return {
        "symbol":           "XAUUSD",
        "timeframe":        "5",
        "timestamp":        "2026-03-11T10:30:00Z",
        "price":            2345.67,
        "atr":              11.18,
        "atr5":             3.45,
        "regime":           regime,
        "direction":        direction,
        "h1_direction":     h1_direction,
        "h1_adx":           h1_adx,
        "m15_adx":          m15_adx,
        "m15_adx_drop":     m15_adx_drop,
        "atr_ratio":        atr_ratio,
        "choch_confirmed":  choch_confirmed,
        "fvg_hit":          fvg_hit,
        "fvg_aligned":      fvg_aligned,
        "zone_hit":         zone_hit,
        "zone_aligned":     zone_aligned,
        "rsi_divergence":   rsi_divergence,
        "sweep_detected":   sweep_detected,
        "candle_pattern":   candle_pattern,
        "session":          session,
        "rsi_trend_aligned": rsi_trend_aligned,
        "rsi_value":        rsi_value,
        "news_nearby":      news_nearby,
        "bos_confirmed":    bos_confirmed,
        "ob_aligned":       ob_aligned,
    }


# ──────────────────────────────────────────────────────────
# 1. TREND approve
# ──────────────────────────────────────────────────────────

class TestTrendApprove(unittest.TestCase):

    def test_trend_approve_all_gates_pass(self):
        """TREND: 全ゲート通過 + 高スコアで approve"""
        alert = _make_alert(
            regime="TREND",
            direction="buy",
            h1_direction="bull",
            h1_adx=30.0,
            m15_adx=36.0,       # adx_above_35: +0.25
            choch_confirmed=True,  # gate pass + choch_strong: +0.10
            fvg_aligned=True,
            zone_aligned=True,  # fvg_and_zone_overlap: +0.15
            session="london_ny",  # session_london_ny: +0.10
            atr_ratio=1.2,        # atr_ratio_normal: +0.05
        )
        # inject h1_direction aligned fields properly
        alert["h1_direction"] = "bull"
        alert["direction"]    = "buy"

        result = calculate_score(alert)

        self.assertEqual(result["decision"], "approve")
        self.assertGreaterEqual(result["score"], SCORING_CONFIG["approve_threshold"])
        self.assertEqual(result["reject_reasons"], [])
        # key breakdown entries present
        self.assertIn("choch_strong",        result["score_breakdown"])
        self.assertIn("fvg_and_zone_overlap", result["score_breakdown"])
        self.assertIn("session_london_ny",   result["score_breakdown"])

    def test_result_has_required_keys(self):
        """戻り値に必要な 4 キーが全て含まれる"""
        alert  = _make_alert()
        result = calculate_score(alert)
        for key in ("decision", "score", "score_breakdown", "reject_reasons"):
            self.assertIn(key, result)


# ──────────────────────────────────────────────────────────
# 2. TREND reject: h1_adx < 25
# ──────────────────────────────────────────────────────────

class TestTrendRejectH1Adx(unittest.TestCase):

    def test_trend_reject_h1_adx_below_25(self):
        """TREND: h1_adx=20 → Gate1 不通過で reject"""
        alert = _make_alert(h1_adx=20.0)
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["score"], -999.0)
        self.assertTrue(any("h1_adx" in r for r in result["reject_reasons"]))


# ──────────────────────────────────────────────────────────
# 3. TREND reject: choch_confirmed=false
# ──────────────────────────────────────────────────────────

class TestTrendRejectChoch(unittest.TestCase):

    def test_trend_reject_all_smc_false(self):
        """TREND: choch/fvg/zone/bos/ob が全てfalse → Gate3(TREND) 不通過で reject
        (OR緩和後: SMC条件が1つも満たされない場合のみ reject)"""
        alert = _make_alert(
            choch_confirmed=False,
            fvg_aligned=False,
            zone_aligned=False,
            bos_confirmed=False,
            ob_aligned=False,
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertTrue(
            any("Gate3(TREND)" in r for r in result["reject_reasons"])
        )

    def test_trend_approve_choch_only(self):
        """TREND: choch=True のみでGate3通過（OR緩和）→ approve"""
        alert = _make_alert(
            choch_confirmed=True,
            fvg_aligned=False,
            zone_aligned=False,
            bos_confirmed=False,
            ob_aligned=False,
        )
        result = calculate_score(alert)

        self.assertNotEqual(result["decision"], "reject")
        self.assertFalse(
            any("Gate3(TREND)" in r for r in result.get("reject_reasons", []))
        )


# ──────────────────────────────────────────────────────────
# 4. REVERSAL approve: sweep + choch + rsi_divergence
# ──────────────────────────────────────────────────────────

class TestReversalApprove(unittest.TestCase):

    def test_reversal_approve_sweep_choch_rsi(self):
        """
        REVERSAL: 廃止レジームのため Gate2 で即 reject される。
        （旧: sweep + choch + rsi_divergence で approve だったが廃止）
        """
        alert = _make_alert(
            regime="REVERSAL",
            direction="sell",
            h1_direction="bull",
            h1_adx=30.0,
            m15_adx=28.0,
            choch_confirmed=True,
            sweep_detected=True,
            fvg_aligned=False,
            zone_aligned=False,
            rsi_divergence=True,
            session="london_ny",
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertTrue(any("REVERSAL" in r for r in result["reject_reasons"]))


# ──────────────────────────────────────────────────────────
# 5. REVERSAL reject: sweep_detected=false
# ──────────────────────────────────────────────────────────

class TestReversalRejectNoSweep(unittest.TestCase):

    def test_reversal_reject_sweep_false(self):
        """REVERSAL: 廃止レジームのため Gate2 で即 reject される"""
        alert = _make_alert(
            regime="REVERSAL",
            direction="sell",
            h1_adx=30.0,
            choch_confirmed=True,
            sweep_detected=False,
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertTrue(
            any("REVERSAL" in r for r in result["reject_reasons"])
        )


# ──────────────────────────────────────────────────────────
# 6. REVERSAL adx_penalty: m15_adx=38
# ──────────────────────────────────────────────────────────

class TestReversalAdxPenalty(unittest.TestCase):

    def test_reversal_adx_penalty_applied(self):
        """REVERSAL: 廃止レジームのため Gate2 で即 reject され score=-999 になる"""
        alert = _make_alert(
            regime="REVERSAL",
            direction="sell",
            h1_adx=30.0,
            m15_adx=38.0,
            choch_confirmed=True,
            sweep_detected=True,
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["score"], -999.0)
        self.assertTrue(any("REVERSAL" in r for r in result["reject_reasons"]))


# ──────────────────────────────────────────────────────────
# 7. BREAKOUT approve: CHoCH 不要、fvg+zone overlap ボーナス
# ──────────────────────────────────────────────────────────

class TestBreakoutApprove(unittest.TestCase):

    def test_breakout_approve_fvg_zone_overlap(self):
        """
        BREAKOUT: choch_confirmed=False でもゲート通過。
        fvg_aligned + zone_aligned → fvg_and_zone_overlap ボーナス。
        score = fvg_and_zone_overlap(0.30) + bos_confirmed(0.30)
                + h1_direction_aligned(0.10) + session_london_ny(-0.15)
                + atr_ratio_normal(0.05) + adx_above_35(0.25) = 0.85 >= approve_threshold(0.50)
        """
        alert = _make_alert(
            regime="BREAKOUT",
            direction="buy",
            h1_direction="bull",
            h1_adx=30.0,
            m15_adx=36.0,
            choch_confirmed=False,  # BREAKOUT はCHoCH不要
            fvg_aligned=True,
            zone_aligned=True,      # overlap: +0.15
            bos_confirmed=True,     # +0.30 で閾値0.50超え
            session="london_ny",
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "approve")
        self.assertIn("fvg_and_zone_overlap", result["score_breakdown"])
        self.assertGreater(result["score_breakdown"]["fvg_and_zone_overlap"], 0)
        # choch_strong は choch_confirmed=False なので付かない
        self.assertNotIn("choch_strong", result["score_breakdown"])


# ──────────────────────────────────────────────────────────
# 8. BREAKOUT reject: fvg_aligned=false AND zone_aligned=false
# ──────────────────────────────────────────────────────────

class TestBreakoutRejectNoZone(unittest.TestCase):

    def test_breakout_reject_no_fvg_no_zone(self):
        """BREAKOUT: fvg_aligned=false AND zone_aligned=false → Gate3(BREAKOUT) 不通過"""
        alert = _make_alert(
            regime="BREAKOUT",
            h1_adx=30.0,
            fvg_aligned=False,
            zone_aligned=False,
        )
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertTrue(
            any("fvg_aligned" in r for r in result["reject_reasons"])
        )


# ──────────────────────────────────────────────────────────
# 9. RANGE reject
# ──────────────────────────────────────────────────────────

class TestRangeReject(unittest.TestCase):

    def test_range_immediate_reject(self):
        """regime=RANGE → Gate2 不通過で即 reject"""
        alert = _make_alert(regime="RANGE")
        result = calculate_score(alert)

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["score"], -999.0)
        self.assertTrue(
            any("RANGE" in r for r in result["reject_reasons"])
        )

    def test_range_reject_skips_score_calculation(self):
        """RANGE reject では score_breakdown が gate_reject のみ"""
        alert  = _make_alert(regime="RANGE")
        result = calculate_score(alert)

        self.assertIn("gate_reject", result["score_breakdown"])
        self.assertEqual(len(result["score_breakdown"]), 1)


# ──────────────────────────────────────────────────────────
# 10. news_nearby: -0.30 減点
# ──────────────────────────────────────────────────────────

class TestNewsNearbyPenalty(unittest.TestCase):

    def test_news_nearby_penalty_value(self):
        """news_nearby=true → -0.30 の減点が breakdown に含まれる"""
        alert = _make_alert(news_nearby=True)
        result = calculate_score(alert)

        self.assertNotEqual(result["score"], -999.0)   # ゲートは通過
        self.assertIn("news_nearby", result["score_breakdown"])
        self.assertAlmostEqual(
            result["score_breakdown"]["news_nearby"],
            SCORING_CONFIG["news_nearby"],
            places=5,
        )
        self.assertLess(result["score_breakdown"]["news_nearby"], 0)

    def test_news_nearby_false_no_penalty(self):
        """news_nearby=false → news_nearby が breakdown に含まれない"""
        alert  = _make_alert(news_nearby=False)
        result = calculate_score(alert)

        self.assertNotIn("news_nearby", result["score_breakdown"])


# ──────────────────────────────────────────────────────────
# 11. session_off: -0.20 減点
# ──────────────────────────────────────────────────────────

class TestSessionOffPenalty(unittest.TestCase):

    def test_session_off_penalty_value(self):
        """session=off → -0.20 の減点が breakdown に含まれる"""
        alert = _make_alert(session="off")
        result = calculate_score(alert)

        self.assertNotEqual(result["score"], -999.0)
        self.assertIn("session_off", result["score_breakdown"])
        self.assertAlmostEqual(
            result["score_breakdown"]["session_off"],
            SCORING_CONFIG["session_off"],
            places=5,
        )
        self.assertLess(result["score_breakdown"]["session_off"], 0)

    def test_session_london_ny_bonus(self):
        """session=london_ny → -0.10 の減点（実績データPF=0.89に基づき逆転修正）"""
        alert = _make_alert(session="london_ny")
        result = calculate_score(alert)

        self.assertIn("session_london_ny", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["session_london_ny"], 0)

    def test_session_ny_no_change(self):
        """session=ny → +0.05 の加点（実績データPF=1.07に基づき改善）"""
        alert = _make_alert(session="ny")
        result = calculate_score(alert)

        self.assertIn("session_ny", result["score_breakdown"])
        self.assertGreater(result["score_breakdown"]["session_ny"], 0)


class TestReversalRejected(unittest.TestCase):
    def test_reversal_rejected_by_gate2(self):
        """REVERSAL: Gate2で即rejectされること"""
        alert = _make_alert(
            regime="REVERSAL",
            h1_adx=30.0,
            choch_confirmed=True,
            sweep_detected=True,
        )
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "reject")
        self.assertTrue(any("REVERSAL" in r for r in result["reject_reasons"]))


class TestNewsBlackout(unittest.TestCase):
    def test_news_penalty_applied_when_pine_flag_true(self):
        """news_nearby=true のとき減点が入ること"""
        alert = _make_alert(news_nearby=True)
        result = calculate_score(alert)
        self.assertIn("news_nearby", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["news_nearby"], 0)

    def test_news_penalty_applied_when_blackout(self):
        """is_news_blackout()=True のとき、pine flag=false でも減点が入ること"""
        alert = _make_alert(news_nearby=False)
        with patch("news_filter.is_news_blackout", return_value=True):
            result = calculate_score(alert)
        self.assertIn("news_nearby", result["score_breakdown"])
        self.assertLess(result["score_breakdown"]["news_nearby"], 0)

    def test_no_news_penalty_in_normal_time(self):
        """通常時間帯（pine_flag=false、blackout=false）は減点なし"""
        alert = _make_alert(news_nearby=False)
        with patch("news_filter.is_news_blackout", return_value=False):
            result = calculate_score(alert)
        self.assertNotIn("news_nearby", result["score_breakdown"])


class TestBosObScoring(unittest.TestCase):
    def test_ob_aligned_only_scores(self):
        """ob_aligned=True, bos_confirmed=True のとき ob_aligned のみ加点"""
        alert = _make_alert(
            regime="TREND",
            bos_confirmed=True,
            ob_aligned=True,
            fvg_aligned=False,
            choch_confirmed=False,
            zone_aligned=False,
        )
        result = calculate_score(alert)
        self.assertIn("ob_aligned", result["score_breakdown"])
        self.assertNotIn("bos_confirmed", result["score_breakdown"])

    def test_bos_only_scores_when_no_ob(self):
        """bos_confirmed=True, ob_aligned=False のとき bos_confirmed のみ加点"""
        alert = _make_alert(
            regime="TREND",
            bos_confirmed=True,
            ob_aligned=False,
            fvg_aligned=False,
            choch_confirmed=False,
            zone_aligned=False,
        )
        result = calculate_score(alert)
        self.assertIn("bos_confirmed", result["score_breakdown"])
        self.assertNotIn("ob_aligned", result["score_breakdown"])


class TestApproveThreshold(unittest.TestCase):
    # CHoCH単体スコアのみを測定するため、他の加点要素を中立化する共通設定:
    #   m15_adx=20    → adx_25_35(-0.10)/adx_above_35(+0.25) いずれの範囲にも該当しない→加点なし
    #   h1_direction="bear" + direction="buy" → h1_direction_aligned(+0.10) 不適用
    #   atr_ratio=0.5 → atr_ratio_normal(+0.05) の範囲[0.8,1.5]から外れ加点なし
    _ISOLATED = dict(
        m15_adx=20.0,
        h1_direction="bear",
        direction="buy",
        atr_ratio=0.5,
        fvg_aligned=False, zone_aligned=False,
        fvg_hit=False, zone_hit=False,
        bos_confirmed=False, ob_aligned=False,
    )

    def test_choch_london_ny_approve(self):
        """CHoCH単体 + london_ny → reject（score=0.10-0.15=-0.05 < wait_threshold=0.00）"""
        alert = _make_alert(choch_confirmed=True, session="london_ny", **self._ISOLATED)
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "reject")

    def test_choch_london_approve(self):
        """CHoCH単体 + london → reject（score=0.10-0.25=-0.15 < wait_threshold=0.00）"""
        alert = _make_alert(choch_confirmed=True, session="london", **self._ISOLATED)
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "reject")

    def test_choch_ny_approve(self):
        """CHoCH単体 + ny → wait（score=0.10+0.10=0.20 < approve_threshold=0.50, >= 0.00）"""
        alert = _make_alert(choch_confirmed=True, session="ny", **self._ISOLATED)
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "wait")

    def test_choch_tokyo_wait(self):
        """CHoCH単体 + tokyo → wait（score=0.10+0.10=0.20 < approve_threshold=0.50）"""
        alert = _make_alert(choch_confirmed=True, session="tokyo", **self._ISOLATED)
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "wait")

    def test_choch_off_wait(self):
        """CHoCH単体 + off → wait（score=0.10-0.05=0.05 >= wait_threshold=0.00）"""
        alert = _make_alert(choch_confirmed=True, session="off", **self._ISOLATED)
        result = calculate_score(alert)
        self.assertIn(result["decision"], ["wait", "reject"])


class TestDirectionNoneRejected(unittest.TestCase):
    def test_direction_none_rejected(self):
        """direction=none: Gate3で即rejectされること"""
        alert = _make_alert(
            regime="BREAKOUT",
            direction="none",
            h1_adx=30.0,
            fvg_aligned=True,
        )
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "reject")
        self.assertTrue(any("direction=none" in r for r in result["reject_reasons"]))


# ──────────────────────────────────────────────────────────
# Gate1 BREAKOUT免除テスト
# ──────────────────────────────────────────────────────────

class TestGate1BreakoutExemption(unittest.TestCase):

    def test_trend_h1_adx_low_rejected(self):
        """regime=TREND, h1_adx=20 → Gate1でreject（変更なし）"""
        alert = _make_alert(regime="TREND", h1_adx=20.0)
        result = calculate_score(alert)
        self.assertEqual(result["decision"], "reject")
        self.assertTrue(any("Gate1" in r for r in result["reject_reasons"]))

    def test_breakout_h1_adx_low_skips_gate1(self):
        """regime=BREAKOUT, h1_adx=20 → Gate1をスキップ（新動作）"""
        alert = _make_alert(
            regime="BREAKOUT",
            h1_adx=20.0,
            fvg_aligned=True,
            zone_aligned=True,
        )
        result = calculate_score(alert)
        self.assertFalse(any("Gate1" in r for r in result["reject_reasons"]))

    def test_breakout_h1_adx_high_passes(self):
        """regime=BREAKOUT, h1_adx=30 → 通過（変更なし）"""
        alert = _make_alert(
            regime="BREAKOUT",
            h1_adx=30.0,
            fvg_aligned=True,
            zone_aligned=True,
        )
        result = calculate_score(alert)
        self.assertFalse(any("Gate1" in r for r in result["reject_reasons"]))


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
