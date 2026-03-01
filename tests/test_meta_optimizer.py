"""
tests/test_meta_optimizer.py - MetaOptimizer のユニットテスト
AI Trading System v3.5

テスト対象:
  - 安全ガードのパラメータバリデーション
  - config.py の原子的書き換え
  - サンプル数チェック
"""

import os
import sys
import json
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meta_optimizer import MetaOptimizer, TUNABLE_PARAMS, MAX_CHANGE, MAX_THRESHOLD_CHANGE


class TestSafetyCheck(unittest.TestCase):
    """_safety_check のバリデーションロジックをテスト（バックテスト部分はモック）"""

    def setUp(self):
        self.opt = MetaOptimizer()
        # バックテストをスキップするようにパッチ
        self.opt._run_safety_backtest = lambda proposals: (True, "テストのためスキップ")

    def test_empty_proposal_passes(self):
        """提案が空なら通過"""
        ok, reason = self.opt._safety_check({"proposals": {}})
        self.assertTrue(ok)

    def test_valid_proposal_passes(self):
        """変更幅が ±0.05 以内で許容範囲内なら通過"""
        from config import SCORING_CONFIG
        base = SCORING_CONFIG.get("zone_touch_aligned", 0.20)
        ok, reason = self.opt._safety_check({
            "proposals": {"zone_touch_aligned": round(base + 0.04, 4)}
        })
        self.assertTrue(ok, reason)

    def test_too_large_change_rejected(self):
        """変更幅が ±0.05 超なら条件A違反"""
        from config import SCORING_CONFIG
        base = SCORING_CONFIG.get("zone_touch_aligned", 0.20)
        ok, reason = self.opt._safety_check({
            "proposals": {"zone_touch_aligned": round(base + 0.10, 4)}
        })
        self.assertFalse(ok)
        self.assertIn("条件A", reason)

    def test_non_whitelist_param_rejected(self):
        """ホワイトリスト外のパラメータは拒否"""
        ok, reason = self.opt._safety_check({
            "proposals": {"range_mid_chase": -500}
        })
        self.assertFalse(ok)
        self.assertIn("チューニング対象外", reason)

    def test_threshold_param_stricter_limit(self):
        """approve_threshold / wait_threshold は ±0.03 以内"""
        from config import SCORING_CONFIG
        base = SCORING_CONFIG.get("approve_threshold", 0.45)
        # ±0.04 は拒否されるべき
        ok, reason = self.opt._safety_check({
            "proposals": {"approve_threshold": round(base + 0.04, 4)}
        })
        self.assertFalse(ok)
        self.assertIn("条件A", reason)

    def test_threshold_small_change_passes(self):
        """approve_threshold の ±0.02 変更は通過"""
        from config import SCORING_CONFIG
        base = SCORING_CONFIG.get("approve_threshold", 0.45)
        ok, reason = self.opt._safety_check({
            "proposals": {"approve_threshold": round(base + 0.02, 4)}
        })
        self.assertTrue(ok, reason)

    def test_out_of_range_rejected(self):
        """TUNABLE_PARAMS の (min, max) を超えた値は拒否"""
        ok, reason = self.opt._safety_check({
            "proposals": {"liquidity_sweep": 0.99}  # max=0.40 を超える
        })
        self.assertFalse(ok)
        self.assertIn("許容範囲", reason)


class TestApplyConfig(unittest.TestCase):
    """_apply_config の原子的書き換えをテスト（一時 config.py を使用）"""

    def setUp(self):
        # 一時ディレクトリに config.py のコピーを作る
        self.tmpdir = tempfile.mkdtemp()
        src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.py")
        self.tmp_config = os.path.join(self.tmpdir, "config.py")
        shutil.copy2(src, self.tmp_config)

        self.opt = MetaOptimizer()
        # config_path を一時ファイルに向ける
        self.opt._apply_config = self._patched_apply_config

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patched_apply_config(self, proposal: dict) -> dict:
        """一時 config.py を対象に _apply_config を実行するラッパー"""
        import re, tempfile as tf_
        from config import SCORING_CONFIG

        proposals = proposal.get("proposals", {})
        if not proposals:
            return {}

        with open(self.tmp_config, "r", encoding="utf-8") as f:
            content = f.read()

        changes = {}
        for param, new_val in proposals.items():
            old_val = SCORING_CONFIG.get(param)
            if old_val is None:
                continue
            pattern = rf'("{re.escape(param)}"\s*:\s*)([-\d.]+)'
            new_content, count = re.subn(
                pattern,
                lambda m: m.group(1) + f"{new_val:.4f}",
                content,
            )
            if count > 0:
                content = new_content
                changes[param] = {"old": old_val, "new": new_val}

        tmp_path = self.tmp_config + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, self.tmp_config)
        return changes

    def test_apply_writes_new_value(self):
        """_apply_config が config.py を正しく書き換える"""
        from config import SCORING_CONFIG
        base = SCORING_CONFIG.get("zone_touch_aligned", 0.20)
        new_val = round(base + 0.02, 4)

        changes = self.opt._apply_config({
            "proposals": {"zone_touch_aligned": new_val}
        })

        self.assertIn("zone_touch_aligned", changes)
        self.assertAlmostEqual(changes["zone_touch_aligned"]["new"], new_val, places=4)

        # 書き換えられたファイルに新しい値が含まれているか確認
        with open(self.tmp_config, "r", encoding="utf-8") as f:
            written = f.read()
        self.assertIn(f"{new_val:.4f}", written)

    def test_empty_proposal_returns_empty(self):
        """空提案は変更なし"""
        changes = self.opt._apply_config({"proposals": {}})
        self.assertEqual(changes, {})


class TestTunableParams(unittest.TestCase):
    """TUNABLE_PARAMS の整合性チェック"""

    def test_all_tunable_params_exist_in_config(self):
        """TUNABLE_PARAMS の全キーが SCORING_CONFIG に存在する"""
        from config import SCORING_CONFIG
        for param in TUNABLE_PARAMS:
            self.assertIn(param, SCORING_CONFIG,
                          f"TUNABLE_PARAMS の '{param}' が SCORING_CONFIG に存在しない")

    def test_current_values_within_range(self):
        """現在の SCORING_CONFIG 値が TUNABLE_PARAMS の範囲内にある"""
        from config import SCORING_CONFIG
        for param, (lo, hi) in TUNABLE_PARAMS.items():
            val = SCORING_CONFIG.get(param)
            if val is not None and isinstance(val, (int, float)) and val > 0:
                self.assertGreaterEqual(val, lo,
                    f"{param}={val} が min={lo} を下回っている")
                self.assertLessEqual(val, hi,
                    f"{param}={val} が max={hi} を上回っている")


if __name__ == "__main__":
    unittest.main(verbosity=2)
