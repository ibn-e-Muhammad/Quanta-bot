import argparse
import math
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


def _slippage_rate_from_notional(notional_usd: float) -> float:
    if notional_usd <= 50000:
        return 0.0002
    penalty_steps = math.floor((notional_usd - 50000) / 50000)
    return 0.0002 + (max(0, penalty_steps) * 0.0001)


def _derive_dataset(df: pd.DataFrame):
    data = df.copy()
    data["entry_time"] = pd.to_datetime(data["timestamp"], errors="coerce")
    data = data.sort_values(["entry_time", "symbol"], kind="mergesort").reset_index(drop=True)

    data["trade_direction"] = data["signal_type"].map({"BUY": 1.0, "SELL": -1.0}).fillna(-1.0)
    data["atr_value"] = pd.to_numeric(data["atr"], errors="coerce").fillna(0.0)
    data["adx_value"] = pd.to_numeric(data["adx"], errors="coerce").fillna(0.0)
    data["ema_distance"] = pd.to_numeric(data["ema_200_dist"], errors="coerce").abs().fillna(0.0)

    notional = pd.to_numeric(data["notional_usd"], errors="coerce").fillna(0.0)
    data["cost_estimate"] = notional.apply(lambda n: 0.0005 + _slippage_rate_from_notional(float(n)))

    entry = pd.to_numeric(data["entry_price"], errors="coerce").replace(0, pd.NA)
    data["candle_range"] = (data["atr_value"] / entry).fillna(0.0)
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

    data["label"] = (pd.to_numeric(data["net_pnl_usd"], errors="coerce").fillna(0.0) > 0).astype(int)

    keep = ["entry_time", *FEATURE_ORDER, "label", "net_pnl_usd", "symbol", "signal_type"]
    return data[keep]


def build_dataset(input_db: str, output_dataset: str):
    conn = sqlite3.connect(input_db)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                timestamp, symbol, signal_type,
                entry_price,
                atr, adx,
                ema_9_24_dist, ema_200_dist,
                hour_of_day, day_of_week,
                notional_usd,
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
