"""
test_position_manager.py - ポジション誤クローズバグのテスト
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest

# MT5が無い環境用のスタブ
import sys
_fake_mt5 = MagicMock()
_fake_mt5.SYMBOL_TRADE_MODE_FULL = 4
_fake_mt5.ORDER_TYPE_SELL = 1
_fake_mt5.ORDER_TYPE_BUY = 0
_fake_mt5.TRADE_ACTION_DEAL = 1
_fake_mt5.TRADE_ACTION_SLTP = 6
_fake_mt5.ORDER_FILLING_IOC = 2
_fake_mt5.TRADE_RETCODE_DONE = 10009
sys.modules.setdefault("MetaTrader5", _fake_mt5)

from position_manager import PositionManager, ManagedPosition


def _make_pos(entered_seconds_ago: float = 0) -> ManagedPosition:
    """テスト用 ManagedPosition を作成"""
    pos = ManagedPosition(
        ticket=12345,
        direction="buy",
        entry_price=2350.0,
        lot_size=0.10,
        sl_price=2340.0,
        atr_pips=5.0,
        execution_id=1,
    )
    pos.entered_at = datetime.now(timezone.utc) - timedelta(seconds=entered_seconds_ago)
    return pos


class TestManagePositionRetry:
    """_manage() がエントリー直後のpositions_get()空を誤クローズしないことを確認"""

    def setup_method(self):
        self.pm = PositionManager()

    @patch("position_manager.mt5")
    def test_entry_grace_period_skips_close(self, mock_mt5):
        """エントリーから5秒後: positions_get()が空でも 'ok' を返す"""
        pos = _make_pos(entered_seconds_ago=5)
        tick = MagicMock()
        tick.bid = 2351.0
        tick.ask = 2351.5
        mock_mt5.symbol_info_tick.return_value = tick
        mock_mt5.positions_get.return_value = []  # 空 = 一時的な未取得

        result = self.pm._manage(pos)
        assert result == "ok"

    @patch("position_manager.mt5")
    def test_after_grace_period_retry_finds_position(self, mock_mt5):
        """エントリーから60秒後: 1回目空→リトライで見つかれば 'ok'"""
        pos = _make_pos(entered_seconds_ago=60)
        tick = MagicMock()
        tick.bid = 2351.0
        tick.ask = 2351.5
        mock_mt5.symbol_info_tick.return_value = tick

        fake_position = MagicMock()
        # 1回目: 空, 2回目: 見つかる
        mock_mt5.positions_get.side_effect = [[], [fake_position]]

        with patch("position_manager.time") as mock_time:
            mock_time.sleep = MagicMock()
            result = self.pm._manage(pos)

        assert result == "ok"
        mock_time.sleep.assert_called_once_with(1.0)

    @patch("position_manager.mt5")
    def test_after_grace_period_retry_still_empty_returns_closed(self, mock_mt5):
        """エントリーから60秒後: リトライ後も空なら 'closed'"""
        pos = _make_pos(entered_seconds_ago=60)
        tick = MagicMock()
        tick.bid = 2351.0
        tick.ask = 2351.5
        mock_mt5.symbol_info_tick.return_value = tick
        mock_mt5.positions_get.return_value = []  # 常に空

        with patch("position_manager.time") as mock_time:
            mock_time.sleep = MagicMock()
            with patch("position_manager.discord_notifier"):
                result = self.pm._manage(pos)

        assert result == "closed"

    @patch("position_manager.mt5")
    def test_position_exists_returns_ok(self, mock_mt5):
        """ポジションが正常に見つかる場合は 'ok'"""
        pos = _make_pos(entered_seconds_ago=60)
        tick = MagicMock()
        tick.bid = 2351.0
        tick.ask = 2351.5
        mock_mt5.symbol_info_tick.return_value = tick

        fake_position = MagicMock()
        mock_mt5.positions_get.return_value = [fake_position]

        result = self.pm._manage(pos)
        assert result == "ok"


class TestBreakoutRegime:
    """BREAKOUTレジームでBE・部分決済・トレーリングが無効化されることを確認"""

    def setup_method(self):
        self.pm = PositionManager()

    def _make_breakout_pos(self, entered_seconds_ago: float = 60) -> ManagedPosition:
        pos = ManagedPosition(
            ticket=99999,
            direction="buy",
            entry_price=2350.0,
            lot_size=0.10,
            sl_price=2340.0,
            atr_pips=5.0,
            execution_id=2,
            regime="BREAKOUT",
        )
        pos.entered_at = datetime.now(timezone.utc) - timedelta(seconds=entered_seconds_ago)
        return pos

    @patch("position_manager.mt5")
    def test_breakout_does_not_call_apply_be(self, mock_mt5):
        """BREAKOUTレジームでは unrealized が BE_TRIGGER_ATR_MULT を超えても _apply_be が呼ばれない"""
        pos = self._make_breakout_pos()
        # atr_pips=5.0, be_trigger_atr_mult=1.8 → 閾値=9.0
        # 現在価格 = entry(2350) + 10.0 → unrealized=10.0 > 9.0（閾値超え）
        tick = MagicMock()
        tick.bid = 2360.0
        tick.ask = 2360.5
        mock_mt5.symbol_info_tick.return_value = tick

        fake_position = MagicMock()
        mock_mt5.positions_get.return_value = [fake_position]

        with patch.object(self.pm, "_apply_be") as mock_be:
            result = self.pm._manage(pos)

        assert result == "ok"
        mock_be.assert_not_called()

    @patch("position_manager.mt5")
    def test_breakout_does_not_call_partial_close(self, mock_mt5):
        """BREAKOUTレジームでは unrealized が PARTIAL_TP_ATR_MULT を超えても _partial_close が呼ばれない"""
        pos = self._make_breakout_pos()
        # atr_pips=5.0, partial_tp_atr_mult=3.6 → 閾値=18.0
        # 現在価格 = entry(2350) + 20.0 → unrealized=20.0 > 18.0（閾値超え）
        tick = MagicMock()
        tick.bid = 2370.0
        tick.ask = 2370.5
        mock_mt5.symbol_info_tick.return_value = tick

        fake_position = MagicMock()
        mock_mt5.positions_get.return_value = [fake_position]

        with patch.object(self.pm, "_partial_close") as mock_pc:
            result = self.pm._manage(pos)

        assert result == "ok"
        mock_pc.assert_not_called()


class TestTrendNoTrailingAfterPartialClose:
    """TRENDレジームで部分決済後に _update_trailing が呼ばれないことを確認"""

    def setup_method(self):
        self.pm = PositionManager()

    def _make_trend_partial_pos(self, entered_seconds_ago: float = 60) -> ManagedPosition:
        pos = ManagedPosition(
            ticket=11111,
            direction="buy",
            entry_price=2350.0,
            lot_size=0.10,
            sl_price=2350.0,
            atr_pips=5.0,
            execution_id=3,
            regime="TREND",
        )
        pos.entered_at = datetime.now(timezone.utc) - timedelta(seconds=entered_seconds_ago)
        pos.be_applied = True
        pos.partial_closed = True
        return pos

    @patch("position_manager.mt5")
    def test_trend_partial_closed_does_not_call_update_trailing(self, mock_mt5):
        """TRENDレジームで partial_closed=True のとき _update_trailing が呼ばれない"""
        pos = self._make_trend_partial_pos()
        tick = MagicMock()
        tick.bid = 2360.0
        tick.ask = 2360.5
        mock_mt5.symbol_info_tick.return_value = tick

        fake_position = MagicMock()
        mock_mt5.positions_get.return_value = [fake_position]

        with patch.object(self.pm, "_update_trailing") as mock_trail:
            result = self.pm._manage(pos)

        assert result == "ok"
        mock_trail.assert_not_called()
