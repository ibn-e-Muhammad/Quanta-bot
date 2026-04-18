import argparse
import json
import os
import pickle
import tempfile
from pathlib import Path
import sys

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from ml.feature_engineering import FEATURE_ORDER, fit_standard_transform, apply_standard_transform, save_feature_config
except ModuleNotFoundError:
    from feature_engineering import FEATURE_ORDER, fit_standard_transform, apply_standard_transform, save_feature_config


def _load_dataset(path: str):
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p)


def train_model(dataset_path: str, model_path: str, config_path: str):
    df = _load_dataset(dataset_path)
    if "entry_time" not in df.columns:
        raise KeyError("Dataset must contain entry_time column for time-based split")

    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
    if "symbol" in df.columns:
        df = df.sort_values(["entry_time", "symbol"], kind="mergesort").reset_index(drop=True)
    else:
        df = df.sort_values("entry_time", kind="mergesort").reset_index(drop=True)

    if "label" not in df.columns:
        raise KeyError("Dataset must contain label column")

    split_idx = int(len(df) * 0.7)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    x_train_raw = train_df[FEATURE_ORDER]
    x_test_raw = test_df[FEATURE_ORDER]
    y_train = train_df["label"].astype(int).to_numpy()
    y_test = test_df["label"].astype(int).to_numpy()

    class_balance = float(pd.Series(y_train).mean()) if len(y_train) > 0 else 0.5
    use_balanced = class_balance < 0.4 or class_balance > 0.6

    x_train, config = fit_standard_transform(x_train_raw, FEATURE_ORDER)
    x_test = apply_standard_transform(x_test_raw, config)

    model = RandomForestClassifier(
        n_estimators=150,
        max_depth=5,
        min_samples_leaf=12,
        random_state=42,
        class_weight="balanced" if use_balanced else None,
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
        "mean_predicted_probability": float(y_prob.mean()) if len(y_prob) else 0.0,
        "std_dev_probability": float(y_prob.std(ddof=0)) if len(y_prob) else 0.0,
        "class_ratio_positive_train": class_balance,
        "class_weight_used": "balanced" if use_balanced else "none",
        "random_state": 42,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
    }

    fail_reasons = []
    if metrics["accuracy"] > 0.80:
        fail_reasons.append(f"overfitting_guard_accuracy={metrics['accuracy']:.4f} > 0.80")
    if metrics["roc_auc"] > 0.85:
        fail_reasons.append(f"overfitting_guard_roc_auc={metrics['roc_auc']:.4f} > 0.85")
    if metrics["std_dev_probability"] < 0.05:
        fail_reasons.append(f"distribution_guard_std={metrics['std_dev_probability']:.4f} < 0.05")
    if metrics["mean_predicted_probability"] < 0.20 or metrics["mean_predicted_probability"] > 0.80:
        fail_reasons.append(
            f"distribution_guard_mean={metrics['mean_predicted_probability']:.4f} outside [0.20, 0.80]"
        )

    metrics["status"] = "failed" if fail_reasons else "passed"
    metrics["fail_reasons"] = fail_reasons

    report_path = Path(model_path).with_name("training_metrics.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    if fail_reasons:
        raise RuntimeError("Training aborted by Phase 7.1 guardrails: " + " | ".join(fail_reasons))

    model_out = Path(model_path)
    model_out.parent.mkdir(parents=True, exist_ok=True)

    config_out = Path(config_path)
    config_out.parent.mkdir(parents=True, exist_ok=True)

    model_fd, model_tmp_name = tempfile.mkstemp(dir=str(model_out.parent), suffix=".tmp")
    config_fd, config_tmp_name = tempfile.mkstemp(dir=str(config_out.parent), suffix=".tmp")
    os.close(model_fd)
    os.close(config_fd)
    model_tmp_path = Path(model_tmp_name)
    config_tmp_path = Path(config_tmp_name)

    try:
        with open(model_tmp_path, "wb") as f:
            pickle.dump(model, f)
            f.flush()
            os.fsync(f.fileno())

        save_feature_config(config, str(config_tmp_path))

        os.replace(str(model_tmp_path), str(model_out))
        os.replace(str(config_tmp_path), str(config_out))
    finally:
        if model_tmp_path.exists():
            model_tmp_path.unlink(missing_ok=True)
        if config_tmp_path.exists():
            config_tmp_path.unlink(missing_ok=True)

    print("[TRAIN] Metrics")
    print(f"  Accuracy : {metrics['accuracy']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall   : {metrics['recall']:.4f}")
    print(f"  ROC-AUC  : {metrics['roc_auc']:.4f}")
    print(f"  Prob Mean: {metrics['mean_predicted_probability']:.4f}")
    print(f"  Prob Std : {metrics['std_dev_probability']:.4f}")
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
