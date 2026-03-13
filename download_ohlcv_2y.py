#!/usr/bin/env python3
"""
Download XAUUSD (Gold Futures GC=F) 5-minute OHLCV data for ~2 years
and split into in-sample / out-of-sample CSVs for backtesting.

Priority order for data acquisition:
  1. yfinance 5m  (limited to ~60 days by Yahoo Finance API)
  2. yfinance 1h  (falls back to 1h when 5m returns < MIN_5M_BARS bars)
  3. Local CSV    (falls back to existing ohlcv_GOLD_5m.csv when network
                   is unavailable, e.g. in air-gapped / proxy-blocked envs)

Notes:
  - Out-of-sample CSV must NOT be used in Optuna optimisation.
  - When yfinance 5m data is limited to ~60 days, the script falls back
    to 1h interval to cover the full 2-year window.
"""

import os
import json
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd

# ── Config ──────────────────────────────────────────────────────────────────
SYMBOL        = "GC=F"
TARGET_SYM    = "XAUUSD"
DATA_DIR      = "data"
TODAY         = datetime.now(timezone.utc).date()
START_DATE    = TODAY - timedelta(days=730)
INTERVAL_5M   = "5m"
INTERVAL_1H   = "1h"
MIN_5M_BARS   = 100          # fewer → treat 5m fetch as failed
LOCAL_CSV     = "ohlcv_GOLD_5m.csv"   # pre-existing local cache


# ── Helpers ──────────────────────────────────────────────────────────────────
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def clean_df(df: pd.DataFrame, datetime_col: str | None = None) -> pd.DataFrame:
    """
    Normalise column names, set datetime index, drop NaN / duplicates,
    sort ascending.

    Parameters
    ----------
    df           : raw DataFrame
    datetime_col : name of the datetime column (None → already the index)
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if datetime_col and datetime_col.lower() in df.columns:
        df = df.set_index(datetime_col.lower())
    elif datetime_col:
        # column rename might have happened above
        matched = [c for c in df.columns if "time" in c or "date" in c]
        if matched:
            df = df.set_index(matched[0])

    df.index.name = "datetime"

    # Keep only OHLCV columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing column after normalisation: {col}")

    df = df[["open", "high", "low", "close", "volume"]]

    # Ensure UTC-aware DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df.dropna(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    return df


def fetch_yfinance(symbol: str, interval: str, start, end) -> pd.DataFrame:
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    df = ticker.history(
        start=str(start),
        end=str(end),
        interval=interval,
        auto_adjust=True,
        actions=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def load_local_csv(path: str) -> pd.DataFrame:
    """Load the pre-existing local OHLCV CSV as a fallback."""
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    # Detect datetime column
    dt_col = None
    for candidate in ["timestamp", "datetime", "date", "time"]:
        if candidate in df.columns:
            dt_col = candidate
            break
    return clean_df(df, datetime_col=dt_col)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ensure_dir(DATA_DIR)
    now_utc = datetime.now(timezone.utc)

    actual_interval = INTERVAL_5M
    fname_suffix    = "5m"
    data_source     = "yfinance"
    df              = pd.DataFrame()

    # ── Attempt 1: yfinance 5m ───────────────────────────────────────────────
    print(f"[INFO] Attempting 5m download: {SYMBOL}  {START_DATE} → {TODAY}")
    try:
        raw5 = fetch_yfinance(SYMBOL, INTERVAL_5M, START_DATE, TODAY)
    except Exception as e:
        print(f"[WARN] 5m fetch raised exception: {e}", file=sys.stderr)
        raw5 = pd.DataFrame()

    if len(raw5) >= MIN_5M_BARS:
        df = clean_df(raw5)
        actual_interval = INTERVAL_5M
        fname_suffix    = "5m"
        data_source     = "yfinance_5m"
        print(f"[OK]   5m fetch succeeded: {len(df)} bars")
    else:
        # ── Attempt 2: yfinance 1h ───────────────────────────────────────────
        print(
            f"[WARN] 5m data returned only {len(raw5)} bars "
            f"(threshold={MIN_5M_BARS}). "
            "yfinance limits 5m history to ~60 days. "
            "Falling back to 1h interval for 2-year range.",
            file=sys.stderr,
        )
        print(f"[INFO] Attempting 1h download: {SYMBOL}  {START_DATE} → {TODAY}")
        try:
            raw1h = fetch_yfinance(SYMBOL, INTERVAL_1H, START_DATE, TODAY)
        except Exception as e:
            print(f"[WARN] 1h fetch raised exception: {e}", file=sys.stderr)
            raw1h = pd.DataFrame()

        if len(raw1h) >= 1:
            df = clean_df(raw1h)
            actual_interval = INTERVAL_1H
            fname_suffix    = "1h"
            data_source     = "yfinance_1h"
            print(
                f"[WARN] Using 1h interval data instead of 5m. "
                f"Bars: {len(df)}. File names will include '_1h'.",
                file=sys.stderr,
            )
        else:
            # ── Attempt 3: local CSV fallback ────────────────────────────────
            print(
                f"[WARN] yfinance network unavailable "
                "(proxy/firewall block detected). "
                f"Attempting local CSV fallback: {LOCAL_CSV}",
                file=sys.stderr,
            )
            df = load_local_csv(LOCAL_CSV)
            if df.empty:
                print(
                    f"[ERROR] No data from yfinance and no local CSV found "
                    f"at '{LOCAL_CSV}'. Exiting.",
                    file=sys.stderr,
                )
                sys.exit(1)
            actual_interval = INTERVAL_5M
            fname_suffix    = "5m"
            data_source     = f"local_csv:{LOCAL_CSV}"
            print(
                f"[INFO] Loaded local CSV: {LOCAL_CSV}  ({len(df)} bars)  "
                f"[{df.index[0]} → {df.index[-1]}]"
            )

    total_bars = len(df)

    # ── Save full CSV ─────────────────────────────────────────────────────────
    full_csv = os.path.join(
        DATA_DIR, f"ohlcv_{TARGET_SYM}_{fname_suffix}_2y.csv"
    )
    df.to_csv(full_csv, date_format="%Y-%m-%d %H:%M:%S%z")
    print(f"[OK]   Saved full data  → {full_csv}  ({total_bars} bars)")

    # ── 50/50 split ───────────────────────────────────────────────────────────
    mid_idx   = total_bars // 2
    insample  = df.iloc[:mid_idx]
    outsample = df.iloc[mid_idx:]

    insample_csv  = os.path.join(
        DATA_DIR, f"ohlcv_{TARGET_SYM}_{fname_suffix}_insample.csv"
    )
    outsample_csv = os.path.join(
        DATA_DIR, f"ohlcv_{TARGET_SYM}_{fname_suffix}_outsample.csv"
    )

    insample.to_csv(insample_csv,  date_format="%Y-%m-%d %H:%M:%S%z")
    outsample.to_csv(outsample_csv, date_format="%Y-%m-%d %H:%M:%S%z")
    print(f"[OK]   Saved in-sample  → {insample_csv}  ({len(insample)} bars)")
    print(f"[OK]   Saved out-sample → {outsample_csv} ({len(outsample)} bars)")
    print("[NOTE] out-of-sample CSV must NOT be used in Optuna optimisation.")

    # ── split_manifest.json ───────────────────────────────────────────────────
    manifest = {
        "generated_at":   now_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "source_symbol":  SYMBOL,
        "target_symbol":  TARGET_SYM,
        "interval":       actual_interval,
        "data_source":    data_source,
        "total_bars":     total_bars,
        "insample": {
            "start": str(insample.index[0]),
            "end":   str(insample.index[-1]),
            "bars":  len(insample),
        },
        "outsample": {
            "start": str(outsample.index[0]),
            "end":   str(outsample.index[-1]),
            "bars":  len(outsample),
        },
        "split_ratio": "50/50",
    }
    manifest_path = os.path.join(DATA_DIR, "split_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"[OK]   Saved manifest   → {manifest_path}")

    # ── Data quality report ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DATA QUALITY REPORT")
    print("=" * 60)
    print(f"  Data source          : {data_source}")
    print(f"  Interval used        : {actual_interval}")
    print(f"  Total bars           : {total_bars}")
    print(f"  First datetime (UTC) : {df.index[0]}")
    print(f"  Last  datetime (UTC) : {df.index[-1]}")
    print(f"  Columns              : {list(df.columns)}")
    print(f"  NaN count            : {df.isna().sum().sum()}")

    # Business days (Mon–Fri)
    dates        = df.index.normalize().unique()
    biz_days     = sum(1 for d in dates if d.weekday() < 5)
    avg_bars_day = total_bars / biz_days if biz_days else float("nan")
    print(f"  Unique business days : {biz_days}")
    if actual_interval == INTERVAL_5M:
        print(f"  Avg bars/day (biz)   : {avg_bars_day:.1f}  (max ~288 for 5m)")
    else:
        print(f"  Avg bars/day (biz)   : {avg_bars_day:.1f}  (max ~24 for 1h)")

    print(f"\n  ── Split point ──")
    print(f"  In-sample  : {insample.index[0]}  →  {insample.index[-1]}  ({len(insample)} bars)")
    print(f"  Out-sample : {outsample.index[0]}  →  {outsample.index[-1]}  ({len(outsample)} bars)")
    print("=" * 60)


if __name__ == "__main__":
    main()
