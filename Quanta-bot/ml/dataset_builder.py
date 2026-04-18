import argparse
import sqlite3
from pathlib import Path
import sys

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from ml.feature_engineering import FEATURE_ORDER
except ModuleNotFoundError:
    from feature_engineering import FEATURE_ORDER


def _normalize_group(series):
    s_min = series.min()
    s_max = series.max()
    if s_max == s_min:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - s_min) / (s_max - s_min)


def _derive_dataset(df: pd.DataFrame):
    data = df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    data = data.sort_values("timestamp").reset_index(drop=True)

    data["trade_direction"] = data["signal_type"].map({"BUY": 1.0, "SELL": -1.0}).fillna(-1.0)
    data["atr_value"] = pd.to_numeric(data["atr"], errors="coerce").fillna(0.0)
    data["adx_value"] = pd.to_numeric(data["adx"], errors="coerce").fillna(0.0)
    data["ema_distance"] = pd.to_numeric(data["ema_200_dist"], errors="coerce").abs().fillna(0.0)

    fees = pd.to_numeric(data["fees_paid"], errors="coerce").fillna(0.0)
    slip = pd.to_numeric(data["slippage_paid"], errors="coerce").fillna(0.0)
    notional = pd.to_numeric(data["notional_usd"], errors="coerce").replace(0, pd.NA)
    data["cost_estimate"] = ((fees + slip) / notional).fillna(0.0)

    entry = pd.to_numeric(data["entry_price"], errors="coerce").replace(0, pd.NA)
    sl = pd.to_numeric(data["sl_price"], errors="coerce")
    data["candle_range"] = ((entry - sl).abs() / entry).fillna(0.0)
    data["trend_strength"] = pd.to_numeric(data["ema_9_24_dist"], errors="coerce").abs().fillna(0.0)

    data["hour_of_day"] = pd.to_numeric(data["hour_of_day"], errors="coerce").fillna(0).astype(float)
    data["day_of_week"] = pd.to_numeric(data["day_of_week"], errors="coerce").fillna(0).astype(float)

    atr_rank = data["atr_value"].rank(method="average", pct=True)
    data["volatility_regime"] = pd.cut(
        atr_rank,
        bins=[0.0, 1 / 3, 2 / 3, 1.0],
        labels=[0.0, 1.0, 2.0],
        include_lowest=True,
    ).astype(float)

    data["atr_ratio"] = (data["atr_value"] / entry).fillna(0.0)

    data["_norm_atr"] = data.groupby("timestamp")["atr_ratio"].transform(_normalize_group)
    data["_norm_adx"] = data.groupby("timestamp")["adx_value"].transform(_normalize_group)
    data["_norm_ema"] = data.groupby("timestamp")["ema_distance"].transform(_normalize_group)
    data["_norm_cost"] = data.groupby("timestamp")["cost_estimate"].transform(_normalize_group)

    data["score"] = (
        (data["_norm_atr"] * 0.4)
        + (data["_norm_adx"] * 0.3)
        + (data["_norm_ema"] * 0.2)
        - (data["_norm_cost"] * 0.1)
    )

    data["label"] = (pd.to_numeric(data["net_pnl_usd"], errors="coerce").fillna(0.0) > 0).astype(int)

    keep = ["timestamp", *FEATURE_ORDER, "label", "net_pnl_usd", "symbol", "signal_type"]
    return data[keep]


def build_dataset(input_db: str, output_dataset: str):
    conn = sqlite3.connect(input_db)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                timestamp, symbol, signal_type,
                entry_price, sl_price,
                atr, adx,
                ema_9_24_dist, ema_200_dist,
                hour_of_day, day_of_week,
                fees_paid, slippage_paid, notional_usd,
                net_pnl_usd
            FROM historical_trades
            ORDER BY timestamp ASC
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        raise ValueError("No trades found in database")

    out = _derive_dataset(df)
    out_path = Path(output_dataset)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.suffix.lower() == ".parquet":
        out.to_parquet(out_path, index=False)
    else:
        out.to_csv(out_path, index=False)

    print(f"[DATASET] Rows: {len(out)}")
    print(f"[DATASET] Positive label rate: {out['label'].mean() * 100:.2f}%")
    print(f"[DATASET] Output: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Phase 7 ML dataset from v24 tier_100k trades.")
    parser.add_argument(
        "--input-db",
        default="research/portfolio_backtests/v24/portfolio_results_tier_100k.sqlite",
    )
    parser.add_argument(
        "--output-dataset",
        default="ml/artifacts/train_dataset_v24.csv",
    )
    args = parser.parse_args()
    build_dataset(args.input_db, args.output_dataset)
