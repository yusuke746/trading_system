"""
download_ohlcv.py - yfinance を使って OHLCV データを CSV にダウンロード
AI Trading System v3.0

MT5 や口座なしでバックテスト用の OHLCV データを用意するためのスクリプト。
GOLD (GC=F) の 5 分足・15 分足などを yfinance から取得して CSV に保存する。

使い方:
  python download_ohlcv.py                     # GOLD 5m 60日分（デフォルト）
  python download_ohlcv.py --tf 15m            # 15分足
  python download_ohlcv.py --tf 1h --days 730  # 1時間足 2年分
  python download_ohlcv.py --symbol "EURUSD=X" # FX ペア

yfinance の制限:
  1m  : 最大 7日
  2m  : 最大 60日
  5m  : 最大 60日
  15m : 最大 60日
  30m : 最大 60日
  1h  : 最大 730日（2年）
  1d  : 制限なし
"""

import argparse
import sys
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance が未インストールです。")
    print("   pip install yfinance")
    sys.exit(1)

import pandas as pd


# yfinance のシンボル変換マップ（MT5 シンボル → yfinance ティッカー）
SYMBOL_MAP = {
    "GOLD":   "GC=F",     # GOLD先物
    "XAUUSD": "GC=F",
    "SILVER": "SI=F",
    "EURUSD": "EURUSD=X",
    "USDJPY": "JPY=X",
    "GBPUSD": "GBPUSD=X",
    "BTCUSD": "BTC-USD",
    "SP500":  "^GSPC",
}

# 時間足ごとのデフォルト取得期間
DEFAULT_PERIOD = {
    "1m":  "7d",
    "2m":  "60d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h":  "730d",
    "1d":  "max",
}


def download_ohlcv(symbol: str = "GOLD", tf: str = "5m",
                   days: int = None, output: str = None) -> pd.DataFrame:
    """
    yfinance から OHLCV データを取得して CSV に保存する。

    Args:
        symbol : MT5 シンボル名 または yfinance ティッカー（例: "GOLD", "GC=F"）
        tf     : 時間足（例: "5m", "15m", "1h", "1d"）
        days   : 取得日数（None = 時間足に応じたデフォルト）
        output : 出力 CSV ファイルパス（None = 自動生成）

    Returns:
        pd.DataFrame
    """
    # シンボル変換
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)

    # 期間の決定
    if days is not None:
        period = f"{days}d"
    else:
        period = DEFAULT_PERIOD.get(tf, "60d")

    print(f"📡 {ticker} {tf} を yfinance からダウンロード中... (period={period})")

    df = yf.download(ticker, period=period, interval=tf, auto_adjust=True, progress=False)

    if df.empty:
        raise RuntimeError(
            f"データが取得できませんでした。\n"
            f"  シンボル: {ticker}\n"
            f"  時間足: {tf}\n"
            f"  期間: {period}\n"
            "yfinance の制限: 5m/15m/30m は最大 60 日、1m は最大 7 日。"
        )

    # MultiIndex 列の場合はフラット化
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]

    # timestamp 列を追加（UTC に統一）
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "timestamp"
    df = df.reset_index()

    # 必要列のみ保持
    keep_cols = ["timestamp", "open", "high", "low", "close"]
    if "volume" in df.columns:
        keep_cols.append("volume")
    df = df[keep_cols]

    # NaN 除去
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    print(f"  → {len(df)} 本取得 ({df['timestamp'].iloc[0]} 〜 {df['timestamp'].iloc[-1]})")

    # CSV 保存
    if output is None:
        sym_safe = symbol.upper().replace("/", "")
        output = f"ohlcv_{sym_safe}_{tf}.csv"

    df.to_csv(output, index=False)
    print(f"  → 保存: {output}")

    return df


def main():
    parser = argparse.ArgumentParser(
        description="yfinance で OHLCV データを取得して CSV に保存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", default="GOLD",
                        help="シンボル名（例: GOLD, EURUSD, BTCUSD）デフォルト: GOLD")
    parser.add_argument("--tf", default="5m",
                        help="時間足（例: 1m, 5m, 15m, 1h, 1d）デフォルト: 5m")
    parser.add_argument("--days", type=int, default=None,
                        help="取得日数（デフォルト: 時間足に応じた最大値）")
    parser.add_argument("--output", default=None,
                        help="出力 CSV パス（デフォルト: ohlcv_GOLD_5m.csv）")
    args = parser.parse_args()

    try:
        df = download_ohlcv(
            symbol=args.symbol,
            tf=args.tf,
            days=args.days,
            output=args.output,
        )
        print(f"\n✅ 完了。バックテストコマンド例:")
        sym_safe  = args.symbol.upper().replace("/", "")
        out_path  = args.output or f"ohlcv_{sym_safe}_{args.tf}.csv"
        print(f"   python backtester_live.py --alerts <アラートCSV> --ohlcv {out_path}")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
