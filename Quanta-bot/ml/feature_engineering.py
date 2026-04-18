import json
from pathlib import Path

import numpy as np
import pandas as pd


FEATURE_ORDER = [
    "trade_direction",
    "score",
    "atr_value",
    "adx_value",
    "ema_distance",
    "cost_estimate",
    "volatility_regime",
    "candle_range",
    "trend_strength",
    "hour_of_day",
    "day_of_week",
]


def _to_float_series(series):
    return pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)


def fit_minmax_transform(df: pd.DataFrame, feature_order=None):
    feature_order = feature_order or FEATURE_ORDER
    mins = {}
    maxs = {}
    transformed = pd.DataFrame(index=df.index)

    for col in feature_order:
        if col not in df.columns:
            raise KeyError(f"Missing feature column: {col}")
        s = _to_float_series(df[col])
        c_min = float(s.min())
        c_max = float(s.max())
        mins[col] = c_min
        maxs[col] = c_max
        if c_max == c_min:
            transformed[col] = 0.5
        else:
            transformed[col] = (s - c_min) / (c_max - c_min)

    config = {
        "version": "phase7_v1",
        "scaler": "minmax",
        "feature_order": feature_order,
        "min": mins,
        "max": maxs,
    }
    return transformed, config


def apply_minmax_transform(df: pd.DataFrame, config: dict):
    feature_order = config.get("feature_order", FEATURE_ORDER)
    mins = config.get("min", {})
    maxs = config.get("max", {})

    transformed = pd.DataFrame(index=df.index)
    for col in feature_order:
        if col not in df.columns:
            raise KeyError(f"Missing feature column for transform: {col}")
        s = _to_float_series(df[col])
        c_min = float(mins[col])
        c_max = float(maxs[col])
        if c_max == c_min:
            transformed[col] = 0.5
        else:
            transformed[col] = (s - c_min) / (c_max - c_min)
    return transformed


def save_feature_config(config: dict, output_path: str):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_feature_config(config_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
