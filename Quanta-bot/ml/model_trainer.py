import argparse
import json
import pickle
from pathlib import Path
import sys

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from ml.feature_engineering import FEATURE_ORDER, fit_minmax_transform, apply_minmax_transform, save_feature_config
except ModuleNotFoundError:
    from feature_engineering import FEATURE_ORDER, fit_minmax_transform, apply_minmax_transform, save_feature_config


def _load_dataset(path: str):
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p)


def train_model(dataset_path: str, model_path: str, config_path: str):
    df = _load_dataset(dataset_path)
    if "timestamp" not in df.columns:
        raise KeyError("Dataset must contain timestamp column for time-based split")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.sort_values("timestamp").reset_index(drop=True)

    if "label" not in df.columns:
        raise KeyError("Dataset must contain label column")

    split_idx = int(len(df) * 0.7)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    x_train_raw = train_df[FEATURE_ORDER]
    x_test_raw = test_df[FEATURE_ORDER]
    y_train = train_df["label"].astype(int).to_numpy()
    y_test = test_df["label"].astype(int).to_numpy()

    x_train, config = fit_minmax_transform(x_train_raw, FEATURE_ORDER)
    x_test = apply_minmax_transform(x_test_raw, config)

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        random_state=42,
        class_weight="balanced_subsample",
        n_jobs=-1,
    )
    model.fit(x_train.values, y_train)

    y_pred = model.predict(x_test.values)
    y_prob = model.predict_proba(x_test.values)[:, 1]

    metrics = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, y_prob)) if len(set(y_test.tolist())) > 1 else 0.5,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
    }

    model_out = Path(model_path)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    with open(model_out, "wb") as f:
        pickle.dump(model, f)

    save_feature_config(config, config_path)

    report_path = model_out.with_name("training_metrics.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print("[TRAIN] Metrics")
    print(f"  Accuracy : {metrics['accuracy']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall   : {metrics['recall']:.4f}")
    print(f"  ROC-AUC  : {metrics['roc_auc']:.4f}")
    print(f"[TRAIN] Model saved: {model_out}")
    print(f"[TRAIN] Feature config saved: {config_path}")
    print(f"[TRAIN] Metrics file: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Phase 7 ML model.")
    parser.add_argument("--dataset", default="ml/artifacts/train_dataset_v24.csv")
    parser.add_argument("--output-model", default="ml/artifacts/model.pkl")
    parser.add_argument("--output-feature-config", default="ml/artifacts/feature_config.json")
    args = parser.parse_args()

    train_model(args.dataset, args.output_model, args.output_feature_config)
