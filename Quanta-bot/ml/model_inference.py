import pickle
from pathlib import Path
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from ml.feature_engineering import load_feature_config
except ModuleNotFoundError:
    from feature_engineering import load_feature_config


ML_RUNTIME_METRICS = {
    "ml_fallback_count": 0,
}


_MODEL = None
_CONFIG = None
_LOAD_ATTEMPTED = False


def _artifacts_paths():
    root = Path(__file__).resolve().parent
    return root / "artifacts" / "model.pkl", root / "artifacts" / "feature_config.json"


def reset_ml_runtime_metrics():
    ML_RUNTIME_METRICS["ml_fallback_count"] = 0


def get_ml_runtime_metrics():
    return dict(ML_RUNTIME_METRICS)


def _fallback_probability():
    ML_RUNTIME_METRICS["ml_fallback_count"] += 1
    return 1.0


def _load_once():
    global _MODEL, _CONFIG, _LOAD_ATTEMPTED
    if _LOAD_ATTEMPTED:
        return _MODEL, _CONFIG
    _LOAD_ATTEMPTED = True

    model_path, config_path = _artifacts_paths()
    if not model_path.exists() or not config_path.exists():
        return None, None

    try:
        with open(model_path, "rb") as f:
            _MODEL = pickle.load(f)
        _CONFIG = load_feature_config(str(config_path))
    except Exception:
        _MODEL = None
        _CONFIG = None
    return _MODEL, _CONFIG


def predict_trade_quality(features_dict) -> float:
    """Returns probability between 0 and 1. Fail-open returns 1.0."""
    model, config = _load_once()
    if model is None or config is None:
        return _fallback_probability()

    try:
        feature_order = config.get("feature_order", [])
        min_map = config.get("min", {})
        max_map = config.get("max", {})

        ordered = []
        for name in feature_order:
            if name not in features_dict:
                return _fallback_probability()
            raw = float(features_dict[name])
            f_min = float(min_map[name])
            f_max = float(max_map[name])
            if f_max == f_min:
                norm = 0.5
            else:
                norm = (raw - f_min) / (f_max - f_min)
            ordered.append(norm)

        x = np.array([ordered], dtype=float)
        proba = model.predict_proba(x)
        return float(proba[0][1])
    except Exception:
        return _fallback_probability()
