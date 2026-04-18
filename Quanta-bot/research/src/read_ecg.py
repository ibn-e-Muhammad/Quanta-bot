import argparse
import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def _find_latest_version_dir(backtests_root):
    version_dirs = []
    for p in backtests_root.iterdir():
        if p.is_dir():
            m = re.fullmatch(r"v(\d+)", p.name.lower())
            if m:
                version_dirs.append((int(m.group(1)), p))
    if not version_dirs:
        raise FileNotFoundError(f"No version folders found under: {backtests_root}")
    version_dirs.sort(key=lambda x: x[0])
    return version_dirs[-1][1]


def _resolve_target_path(user_path=None):
    research_root = Path(__file__).resolve().parents[1]
    backtests_root = research_root / "portfolio_backtests"
    if user_path:
        return Path(user_path).expanduser().resolve()
    return _find_latest_version_dir(backtests_root)


def _discover_sqlite_files(target):
    if target.is_file():
        return [target] if target.suffix.lower() == ".sqlite" else []
    if target.is_dir():
        return sorted(target.glob("*.sqlite"))
    return []


def _infer_tier_name(db_path):
    stem = db_path.stem.lower()
    m = re.search(r"tier[_\-]?([a-z0-9]+)", stem)
    if m:
        return f"TIER_{m.group(1).upper()}"
    return db_path.stem.upper()


def _load_json(path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_phase61_matrix(folder):
    matrix_path = folder / "phase61_scaling_matrix.json"
    data = _load_json(matrix_path)
    out = {}
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                tier_name = str(row.get("tier_name", "")).upper().strip()
                if tier_name:
                    out[tier_name] = row
    return out


def _load_phase61_for_db(db_path, matrix_map):
    audit_path = db_path.with_name(f"{db_path.stem}_phase61_audit.json")
    audit = _load_json(audit_path)
    if isinstance(audit, dict):
        return audit
    return matrix_map.get(_infer_tier_name(db_path), {})


def _load_phase62_matrix(folder):
    matrix_path = folder / "phase62_scaling_matrix.json"
    data = _load_json(matrix_path)
    out = {}
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                tier_name = str(row.get("tier_name", "")).upper().strip()
                if tier_name:
                    out[tier_name] = row
    return out


def _load_phase62_for_db(db_path, matrix_map):
    audit_path = db_path.with_name(f"{db_path.stem}_phase62_audit.json")
    audit = _load_json(audit_path)
    if isinstance(audit, dict):
        return audit
    return matrix_map.get(_infer_tier_name(db_path), {})


def _load_phase7_matrix(folder):
    matrix_path = folder / "phase7_scaling_matrix.json"
    data = _load_json(matrix_path)
    out = {}
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                tier_name = str(row.get("tier_name", "")).upper().strip()
                if tier_name:
                    out[tier_name] = row
    return out


def _load_phase7_for_db(db_path, matrix_map):
    audit_path = db_path.with_name(f"{db_path.stem}_phase7_audit.json")
    audit = _load_json(audit_path)
    if isinstance(audit, dict):
        return audit
    return matrix_map.get(_infer_tier_name(db_path), {})


def _read_trades(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return pd.read_sql_query("SELECT * FROM historical_trades ORDER BY timestamp ASC", conn)
    finally:
        conn.close()


def _infer_initial_balance(df, phase61):
    if "initial_balance" in df.columns:
        vals = pd.to_numeric(df["initial_balance"], errors="coerce").dropna()
        if not vals.empty and vals.iloc[0] > 0:
            return float(vals.iloc[0])

    if "running_balance" in df.columns and "net_pnl_usd" in df.columns and len(df) > 0:
        rb0 = pd.to_numeric(df["running_balance"], errors="coerce").iloc[0]
        pnl0 = pd.to_numeric(df["net_pnl_usd"], errors="coerce").iloc[0]
        if pd.notna(rb0) and pd.notna(pnl0):
            inferred = float(rb0 - pnl0)
            if inferred > 0:
                return inferred

    if isinstance(phase61, dict):
        v = phase61.get("initial_balance")
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except Exception:
            pass
    return 10000.0


def _fmt_pf(v):
    return "inf" if np.isinf(v) else f"{v:.3f}"


def _print_single_report(db_path, df, phase61, phase62, phase7, print_report=True):
    tier_name = _infer_tier_name(db_path)
    initial_balance = _infer_initial_balance(df, phase61)

    total_trades = len(df)
    wins = len(df[df["outcome"] > 0])
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0

    final_bal = float(df["running_balance"].iloc[-1])
    net_pnl = final_bal - initial_balance
    net_pnl_pct = (net_pnl / initial_balance * 100.0) if initial_balance else 0.0

    df = df.copy()
    df["peak"] = df["running_balance"].cummax()
    df["drawdown"] = (df["running_balance"] - df["peak"]) / df["peak"]
    max_dd = float(df["drawdown"].min() * 100.0)

    pos_pnl = df[df["net_pnl_usd"] > 0]["net_pnl_usd"]
    neg_pnl = df[df["net_pnl_usd"] <= 0]["net_pnl_usd"]
    pos_sum = float(pos_pnl.sum())
    neg_sum = float(abs(neg_pnl.sum()))
    pf = pos_sum / neg_sum if neg_sum > 0 else float("inf")
    max_win = float(df["net_pnl_usd"].max())
    avg_win = float(pos_pnl.mean()) if len(pos_pnl) > 0 else 0.0
    avg_loss = float(neg_pnl.mean()) if len(neg_pnl) > 0 else 0.0

    max_consec_loss = 0
    cur = 0
    for o in df["outcome"].tolist():
        if o == 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    eq_std = float(df["running_balance"].pct_change().std() * 100.0)

    is_pf_ok = pf >= 1.10
    is_dd_ok = abs(max_dd) <= 12.0
    is_vol_ok = 200 <= total_trades <= 500

    dd_threshold = -0.015 * initial_balance
    df["trade_date"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.date
    daily_pnl = df.groupby("trade_date")["net_pnl_usd"].sum()
    breach_days = int((daily_pnl < dd_threshold).sum())

    if print_report:
        print("=================================================")
        print(f" [PHASE 6] PROP FIRM SURVIVAL SYSTEM REPORT | {db_path.name}")
        print("=================================================")
        print(" [PORTFOLIO METRICS]")
        print(f" Tier/Label     : {tier_name}")
        print(f" Initial Balance: ${initial_balance:.2f}")
        print(f" Final Balance  : ${final_bal:.2f}")
        print(f" Total Trades   : {total_trades} (200-500: {'Y' if is_vol_ok else 'N'})")
        print(f" Win Rate       : {win_rate:.2f}%  ({wins}W / {losses}L)")
        print(f" Profit Factor  : {_fmt_pf(pf)} (>= 1.10: {'Y' if is_pf_ok else 'N'})")
        print(f" Net PnL        : ${net_pnl:.2f} ({net_pnl_pct:.2f}%)")
        print(f" Max Drawdown   : {max_dd:.2f}% (<= 12%: {'Y' if is_dd_ok else 'N'})")
        print(f" Max Win Trade  : ${max_win:.2f}")
        print(f" Avg Win / Loss : ${avg_win:.2f} / ${avg_loss:.2f}")
        print("-------------------------------------------------")
        print(" [RISK METRICS]")
        print(f" Max Consec. Losses : {max_consec_loss}")
        print(f" Equity Curve Std   : {eq_std:.3f}% per trade")
        print(f" Daily DD Breaches  : {breach_days} days (threshold {dd_threshold:.2f} USD/day)")
        print("-------------------------------------------------")
        print(" [PER-STRATEGY]")
        for strat, grp in df.groupby("strategy_used"):
            st = len(grp)
            sw = len(grp[grp["outcome"] > 0])
            sp = grp[grp["net_pnl_usd"] > 0]["net_pnl_usd"].sum()
            sl = abs(grp[grp["net_pnl_usd"] <= 0]["net_pnl_usd"].sum())
            spf = f"{sp / sl:.2f}" if sl > 0 else "inf"
            print(f" * {strat}: {st} trades | WR: {sw / st * 100:.1f}% | PF: {spf} | PnL: ${grp['net_pnl_usd'].sum():.2f}")
        print("-------------------------------------------------")
        print(" [ENGINE STATE DISTRIBUTION]")
        if "engine_state" in df.columns:
            for strat, grp in df.groupby("strategy_used"):
                sc = grp["engine_state"].value_counts().to_dict()
                a = sc.get("ACTIVE", 0)
                c = sc.get("COOLDOWN", 0)
                r = sc.get("RECOVERY", 0)
                t = max(a + c + r, 1)
                print(f" * {strat}: ACTIVE={a}({a / t * 100:.0f}%) COOLDOWN={c} RECOVERY={r}")
        print("-------------------------------------------------")
        print(" [PER-TIMEFRAME BREAKDOWN]")
        if "interval" in df.columns:
            for tf, grp in df.groupby("interval"):
                tt = len(grp)
                tn = grp["net_pnl_usd"].sum()
                tw = len(grp[grp["outcome"] > 0])
                print(f" * {tf}: {tt} trades | WR: {tw / tt * 100:.1f}% | PnL: ${tn:.2f}")

        if phase61:
            print("-------------------------------------------------")
            print(" [PHASE 6.1 AUDIT METRICS]")
            print(
                f" Signals G/E/R  : {phase61.get('total_signals_generated', 'n/a')}"
                f"/{phase61.get('total_signals_executed', 'n/a')}"
                f"/{phase61.get('rejected_signals', 'n/a')}"
            )
            dup_rate = phase61.get("duplicate_signal_rejection_rate")
            if dup_rate is not None:
                print(f" Dup Reject Rate: {float(dup_rate) * 100:.2f}%")
            print(
                f" Cluster Events : {phase61.get('cluster_event_count', 'n/a')}"
                f" | Avg Size: {phase61.get('cluster_event_avg_size', 'n/a')}"
            )

        if phase62:
            print("-------------------------------------------------")
            print(" [PHASE 6.2 PRIORITY METRICS]")
            print(f" Avg Executed Score : {phase62.get('avg_executed_score', 0.0):.4f}")
            print(f" Avg Rejected Score : {phase62.get('avg_rejected_score', 0.0):.4f}")
            print(f" Selection Quality  : {phase62.get('selection_quality_ratio', 0.0):.4f}")
            rej_total = phase62.get("rejected_signals", 0)
            rej_locks = phase62.get("rejected_locks", 0)
            rej_low = phase62.get("rejected_low_priority", 0)
            lock_pct = (rej_locks / rej_total * 100.0) if rej_total else 0.0
            low_pct = (rej_low / rej_total * 100.0) if rej_total else 0.0
            print(f" Rejection Breakdown: LOCKS={rej_locks} ({lock_pct:.2f}%) | LOW_PRIORITY={rej_low} ({low_pct:.2f}%)")

        if phase7:
            print("-------------------------------------------------")
            print(" [PHASE 7 ML METRICS]")
            print(f" Avg ML Score (Executed): {phase7.get('avg_ml_score_executed', 0.0):.4f}")
            print(f" Avg ML Score (Rejected): {phase7.get('avg_ml_score_rejected', 0.0):.4f}")
            print(f" ML Acceptance Rate     : {phase7.get('ml_acceptance_rate', 0.0) * 100.0:.2f}%")
            print(f" ML Filtered Trades     : {phase7.get('ml_filtered_trades', 0)}")
            print(f" ML Fallback Count      : {phase7.get('ml_fallback_count', 0)}")
            print(f" ML Inference Errors    : {phase7.get('ml_inference_error_count', 0)}")
            dist = phase7.get("ml_score_distribution", {})
            print(
                f" ML Score Distribution  : min={dist.get('min', 0.0):.4f} "
                f"max={dist.get('max', 0.0):.4f} mean={dist.get('mean', 0.0):.4f} std={dist.get('std', 0.0):.4f}"
            )

        print("=================================================")
        passed = is_pf_ok and is_dd_ok and is_vol_ok
        if passed:
            print("[SUCCESS] PROP FIRM CRITERIA MET")
        else:
            flags = []
            if not is_pf_ok:
                flags.append(f"PF {_fmt_pf(pf)} < 1.10")
            if not is_dd_ok:
                flags.append(f"MDD {max_dd:.2f}% > 12%")
            if not is_vol_ok:
                flags.append(f"Trades {total_trades} outside 200-500")
            print(f"[FAIL SAFE] NOT MET: {' | '.join(flags)}")
        print()

    return {
        "tier": tier_name,
        "trades": total_trades,
        "wr": win_rate,
        "pf": pf,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "max_dd": max_dd,
        "final_bal": final_bal,
    }


def generate_ecg_report(db_path=None):
    target = _resolve_target_path(db_path)
    sqlite_files = _discover_sqlite_files(target)
    if not sqlite_files:
        print(f"[ERROR] No .sqlite files found at: {target}")
        return []

    print(f"Reading ECG from {target}...")
    print(f"Detected {len(sqlite_files)} sqlite file(s).\n")

    folder = target if target.is_dir() else target.parent
    matrix_map = _load_phase61_matrix(folder)
    matrix62_map = _load_phase62_matrix(folder)
    matrix7_map = _load_phase7_matrix(folder)
    summary_rows = []

    for db in sqlite_files:
        try:
            df = _read_trades(db)
        except Exception as e:
            print(f"Error reading database {db}: {e}")
            continue
        if df.empty:
            print(f"[WARNING] NO TRADES TAKEN in {db.name}.\n")
            continue

        phase61 = _load_phase61_for_db(db, matrix_map)
        phase62 = _load_phase62_for_db(db, matrix62_map)
        phase7 = _load_phase7_for_db(db, matrix7_map)
        summary_rows.append(_print_single_report(db, df, phase61, phase62, phase7, print_report=True))

    if summary_rows:
        print("=================================================")
        print(" [TIER COMPARISON]")
        print("=================================================")
        print(f"{'Tier':<12} {'Trades':>7} {'WR%':>7} {'PF':>8} {'NetPnL%':>9} {'MaxDD%':>9} {'Final Balance':>16}")
        print("-" * 78)
        for r in sorted(summary_rows, key=lambda x: x["tier"]):
            print(
                f"{r['tier']:<12} {r['trades']:>7} {r['wr']:>7.2f} {_fmt_pf(r['pf']):>8} "
                f"{r['net_pnl_pct']:>9.2f} {r['max_dd']:>9.2f} {r['final_bal']:>16,.2f}"
            )
        print()

        if target.is_dir() and target.name.lower() != "v24":
            baseline_dir = target.parent / "v24"
            if baseline_dir.exists() and baseline_dir.is_dir():
                base_phase61 = _load_phase61_matrix(baseline_dir)
                base_phase62 = _load_phase62_matrix(baseline_dir)
                _ = baseline_dir / "phase62_scaling_matrix.json"
                base_rows = {}
                for db in sorted(baseline_dir.glob("*.sqlite")):
                    try:
                        bdf = _read_trades(db)
                        if bdf.empty:
                            continue
                        b61 = _load_phase61_for_db(db, base_phase61)
                        b62 = _load_phase62_for_db(db, base_phase62)
                        bro = _print_single_report(db, bdf, b61, b62, {}, print_report=False)
                        base_rows[bro["tier"]] = bro
                    except Exception:
                        continue

                if base_rows:
                    print("=================================================")
                    print(" [ML IMPACT VS V24 PHASE62 BASELINE]")
                    print("=================================================")
                    print(f"{'Tier':<12} {'ΔPF':>10} {'ΔNetPnL%':>12} {'ΔMaxDD%':>10}")
                    print("-" * 50)
                    for r in sorted(summary_rows, key=lambda x: x["tier"]):
                        b = base_rows.get(r["tier"])
                        if not b:
                            continue
                        d_pf = r["pf"] - b["pf"]
                        d_pnl = r["net_pnl_pct"] - b["net_pnl_pct"]
                        d_dd = r["max_dd"] - b["max_dd"]
                        print(f"{r['tier']:<12} {d_pf:>10.4f} {d_pnl:>12.2f} {d_dd:>10.2f}")
                    print()
    return summary_rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate ECG report from one DB or a backtest version folder.")
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional .sqlite path or folder path (defaults to latest v-folder under research/portfolio_backtests).",
    )
    args = parser.parse_args()
    generate_ecg_report(args.path)