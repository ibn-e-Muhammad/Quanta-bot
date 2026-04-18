import json
from pathlib import Path

import pandas as pd


FEATURE_ORDER = [
    "trade_direction",
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


def fit_standard_transform(df: pd.DataFrame, feature_order=None):
    feature_order = feature_order or FEATURE_ORDER
    means = {}
    stds = {}
    transformed = pd.DataFrame(index=df.index)

    for col in feature_order:
        if col not in df.columns:
            raise KeyError(f"Missing feature column: {col}")
        s = _to_float_series(df[col])
        c_mean = float(s.mean())
        c_std = float(s.std(ddof=0))
        means[col] = c_mean
        stds[col] = c_std
        if c_std == 0.0:
            transformed[col] = 0.0
        else:
            transformed[col] = (s - c_mean) / c_std

    config = {
        "version": "phase71_v1",
        "scaler": "standard",
        "feature_order": feature_order,
        "scaler_params": {
            "mean": means,
            "std": stds,
        },
    }
    return transformed, config


def apply_standard_transform(df: pd.DataFrame, config: dict):
    feature_order = config.get("feature_order", FEATURE_ORDER)
    scaler_params = config.get("scaler_params", {})
    means = scaler_params.get("mean", {})
    stds = scaler_params.get("std", {})

    transformed = pd.DataFrame(index=df.index)
    for col in feature_order:
        if col not in df.columns:
            raise KeyError(f"Missing feature column for transform: {col}")
        s = _to_float_series(df[col])
        c_mean = float(means[col])
        c_std = float(stds[col])
        if c_std == 0.0:
            transformed[col] = 0.0
        else:
            transformed[col] = (s - c_mean) / c_std
    return transformed


def save_feature_config(config: dict, output_path: str):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_feature_config(config_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
