from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def compute_atr_adx_pandas(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series]:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean()

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean()

    return atr, adx


def compute_indicators(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()

    try:
        import talib  # type: ignore

        high = out["high"].astype(float).values
        low = out["low"].astype(float).values
        close = out["close"].astype(float).values
        out["atr"] = talib.ATR(high, low, close, timeperiod=period)
        out["adx"] = talib.ADX(high, low, close, timeperiod=period)
    except Exception:
        atr, adx = compute_atr_adx_pandas(out, period=period)
        out["atr"] = atr
        out["adx"] = adx

    out["atr_mean"] = out["atr"].rolling(20).mean()
    out["volume_mean"] = out["volume"].astype(float).rolling(20).mean()
    return out


def prep_one_file(src_path: Path, dst_path: Path) -> dict[str, object]:
    df = pd.read_csv(src_path)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    enriched = compute_indicators(df, period=14)

    leak_after_30 = (
        enriched.loc[30:, ["atr", "adx", "atr_mean", "volume_mean"]]
        .isna()
        .sum()
        .to_dict()
    )

    clean = enriched.dropna(subset=["atr", "adx", "atr_mean", "volume_mean"]).reset_index(drop=True)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(dst_path, index=False)

    dt = pd.to_datetime(clean["datetime_utc"], utc=True, errors="coerce")
    return {
        "file": src_path.name,
        "rows_in": int(len(df)),
        "rows_out": int(len(clean)),
        "start": str(dt.min()) if len(clean) else "N/A",
        "end": str(dt.max()) if len(clean) else "N/A",
        "leak_after_30": leak_after_30,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare 15m features for R1 momentum engine")
    parser.add_argument("--raw-dir", default="research/data/raw", help="Directory containing *_15m_raw.csv")
    parser.add_argument("--processed-dir", default="research/data/processed", help="Output directory")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    processed_dir = Path(args.processed_dir)
    files = sorted(raw_dir.glob("*_15m_raw.csv"))

    if not files:
        print("[R1] No raw 15m files found.")
        return

    for src in files:
        dst = processed_dir / src.name.replace("_15m_raw.csv", "_15m_features.csv")
        stats = prep_one_file(src, dst)
        print(f"[R1] processed={dst.name} rows_in={stats['rows_in']} rows_out={stats['rows_out']}")
        print(f"      range={stats['start']} -> {stats['end']}")
        print(f"      nan_leak_after_30={stats['leak_after_30']}")


if __name__ == "__main__":
    main()
