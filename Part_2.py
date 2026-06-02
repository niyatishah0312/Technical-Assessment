"""
Clinical Trials Data Pipeline
Part 2: Success Rate Operationalisation & Stratified Cohort Analysis
=====================================================================

A. OPERATIONALISING "SUCCESS"
──────────────────────────────────────────────────────────────────────
There is no binary success flag in this dataset.  The dataset contains
*operational* trial metadata (status, phase, enrollment numbers, dates)
but not clinical outcome data (response rates, OS/PFS, regulatory
approvals).  The proxy defined here therefore sits firmly on the
"trial completion / progression" end of the spectrum, not the
"therapeutic efficacy" end.

OUTCOME TIER DEFINITION
────────────────────────
We assign each trial a 3-tier ordinal label:

  Tier 2 – "Completed / Advanced"   (treat as "success" in binary)
    • status == COMPLETED

  Tier 1 – "Ongoing / Ambiguous"    (exclude from binary rate denominator)
    • status == RECRUITING
    • status == NOT_YET_RECRUITING
    • status == ENROLLING_BY_INVITATION
    • status == SUSPENDED             (paused but not stopped)
    • status == UNKNOWN               (insufficient information)
    • status == ACTIVE_NOT_RECRUITING  (enrolled, executing, on-track)

  Tier 0 – "Stopped / Failed"       (treat as "failure" in binary)
    • status == TERMINATED            (stopped early, usually due to
                                       safety/futility/resource)
    • status == WITHDRAWN             (never started or abandoned)

BINARY SUCCESS FLAG
───────────────────
  success = 1  if  outcome_tier == 2
  success = 0  if  outcome_tier == 0
  (Tier-1 trials are EXCLUDED from rate denominators; they have not
   yet resolved and including them would artificially deflate rates.)

PHASE WEIGHTING RATIONALE
──────────────────────────
Phase information modulates how we *interpret* the rate, not how we
*define* it.  A Phase 1 trial that completes is equally "tier-2" as a
Phase 3 that completes.  We report raw and phase-stratified rates and
leave weighting to the consumer.

LIMITATIONS / WHAT THIS PROXY IS NOT
──────────────────────────────────────
  • COMPLETED ≠ therapeutically successful.  Many completed trials
    report null or negative results.  A Phase 2 completion rate of
    70 % does not mean 70 % of drugs in that phase are efficacious.
  • NOT_YET_RECRUITING trials are very early; most will never appear
    in the "resolved" pool, so the denominator is heavily skewed
    toward recently started programmes.
  • This proxy is best used for *operational pipeline health* metrics
    (attrition, discontinuation patterns) not drug-discovery KPIs.

B. STRATIFIED SUCCESS RATES
──────────────────────────────────────────────────────────────────────
Dimensions computed:
  1. Phase  ×  Indication (top indications)
  2. Main Technology type
  3. Target class (first target per trial)
  4. Phase alone
  5. Start-year cohort (temporal trend)
"""

import pandas as pd
import numpy as np
import ast
import re
from datetime import datetime, timedelta

# ─── Re-use helpers from Part 1 ───────────────────────────────────

LIST_COLUMNS = [
    "indications", "interventions_drugs", "drugs_datalake",
    "main_technologies", "specific_technologies",
    "target_names", "target_abbreviations",
]

def parse_list_field(val):
    if pd.isna(val):
        return []
    text = str(val).strip()
    if text in ("", "[]", "[[]]", "nan", "None"):
        return []
    try:
        result = ast.literal_eval(text)
        if not isinstance(result, list):
            return []
        flat = []
        for item in result:
            if isinstance(item, list):
                for subitem in item:
                    if pd.notna(subitem) and str(subitem).strip():
                        flat.append(str(subitem).strip())
            elif pd.notna(item) and str(item).strip():
                flat.append(str(item).strip())
        return flat
    except Exception:
        return []


def excel_serial_to_date(val):
    if pd.isna(val):
        return pd.NaT
    if isinstance(val, (int, float)):
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(val))
        except Exception:
            return pd.NaT
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y"):
            try:
                return datetime.strptime(val.strip(), fmt)
            except ValueError:
                continue
    if isinstance(val, datetime):
        return val
    return pd.NaT


# ─────────────────────────────────────────────────────────────────
# A. OUTCOME LABELLING
# ─────────────────────────────────────────────────────────────────

# Tier assignment
TIER_MAP = {
    # Tier 2 – resolved positively
    "COMPLETED":             2,
    # Tier 0 – resolved negatively
    "TERMINATED":            0,
    "WITHDRAWN":             0,
    # Tier 1 – unresolved / ambiguous
    "ACTIVE_NOT_RECRUITING":     1,
    "RECRUITING":                1,
    "NOT_YET_RECRUITING":        1,
    "ENROLLING_BY_INVITATION":   1,
    "SUSPENDED":                 1,
    "UNKNOWN":                   1,
}

TIER_LABELS = {0: "Stopped/Failed", 1: "Ongoing/Ambiguous", 2: "Completed/Advanced"}


def assign_outcome(raw_status: str) -> int:
    """Return tier 0, 1, or 2 for a raw status string."""
    if pd.isna(raw_status):
        return 1                          # unknown → ambiguous
    key = str(raw_status).strip().upper()
    return TIER_MAP.get(key, 1)           # unmapped → ambiguous


def label_trials(df: pd.DataFrame) -> pd.DataFrame:
    """Add outcome columns to the raw dataframe."""
    df = df.copy()
    df["outcome_tier"]  = df["recruitment_status"].apply(assign_outcome)
    df["outcome_label"] = df["outcome_tier"].map(TIER_LABELS)
    # Binary success flag (only defined for resolved trials)
    df["success"]       = df["outcome_tier"].apply(
        lambda t: 1 if t == 2 else (0 if t == 0 else np.nan)
    )
    # Normalised phase
    def _norm_phase(raw):
        if pd.isna(raw):
            return "Unknown"
        k = str(raw).strip().upper()
        label_map = {
            "PHASE1": "Phase 1", "PHASE2": "Phase 2",
            "PHASE3": "Phase 3", "PHASE4": "Phase 4",
            "PHASE1/PHASE2": "Phase 1/2",
            "PHASE2/PHASE3": "Phase 2/3",
            "EARLY_PHASE1": "Early Phase 1",
        }
        return label_map.get(k, "Unknown")
    df["phase_clean"] = df["phase"].apply(_norm_phase)
    # Start year
    df["start_year"] = df["start_date"].apply(
        lambda x: excel_serial_to_date(x).year
        if not pd.isna(excel_serial_to_date(x)) else np.nan
    )
    # First indication (primary)
    df["primary_indication"] = df["indications"].apply(
        lambda x: parse_list_field(x)[0] if parse_list_field(x) else "Unknown"
    )
    # First main technology
    df["primary_technology"] = df["main_technologies"].apply(
        lambda x: parse_list_field(x)[0] if parse_list_field(x) else "Unknown"
    )
    # First target name
    df["primary_target"] = df["target_names"].apply(
        lambda x: parse_list_field(x)[0] if parse_list_field(x) else "Unknown"
    )
    return df


# ─────────────────────────────────────────────────────────────────
# B. SUCCESS RATE ENGINE
# ─────────────────────────────────────────────────────────────────

def success_rate_table(
    df: pd.DataFrame,
    group_cols: list,
    min_resolved: int = 3,
) -> pd.DataFrame:
    """
    Compute success rates for resolved trials only.

    Parameters
    ----------
    df           : labelled dataframe (must have 'success' column)
    group_cols   : list of column names to group by
    min_resolved : minimum resolved trial count to include a group
                   (groups below this are marked 'insufficient data')

    Returns a DataFrame with columns:
      group_cols | n_total | n_resolved | n_success | n_failure |
      success_rate_pct | data_quality
    """
    resolved = df[df["success"].notna()].copy()

    grp = resolved.groupby(group_cols, dropna=False)["success"].agg(
        n_resolved="count",
        n_success="sum",
    ).reset_index()

    # Total trials in each group (including unresolved)
    total = df.groupby(group_cols, dropna=False).size().reset_index(name="n_total")
    grp = grp.merge(total, on=group_cols, how="left")

    grp["n_failure"]        = grp["n_resolved"] - grp["n_success"]
    grp["success_rate_pct"] = (grp["n_success"] / grp["n_resolved"] * 100).round(1)

    grp["data_quality"] = grp["n_resolved"].apply(
        lambda n: "ok" if n >= min_resolved else "low-n"
    )

    return grp.sort_values("n_resolved", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
# C. RUN ALL STRATIFIED ANALYSES
# ─────────────────────────────────────────────────────────────────

def run_stratified_analysis(df: pd.DataFrame) -> dict:
    """
    Run all stratified analyses and return a dict of DataFrames.
    """
    results = {}

    # ── Dim 1: Phase alone ─────────────────────────────────────
    results["by_phase"] = success_rate_table(df, ["phase_clean"], min_resolved=2)

    # ── Dim 2: Phase × Primary Indication ──────────────────────
    # Limit to top 10 indications to keep table readable
    top_inds = (
        df["primary_indication"].value_counts()
        .head(10).index.tolist()
    )
    df_top_ind = df[df["primary_indication"].isin(top_inds)].copy()
    results["phase_x_indication"] = success_rate_table(
        df_top_ind, ["phase_clean", "primary_indication"], min_resolved=2
    )

    # ── Dim 3: Main Technology ──────────────────────────────────
    results["by_technology"] = success_rate_table(
        df, ["primary_technology"], min_resolved=2
    )

    # ── Dim 4: Target Class (first target per trial) ───────────
    # Cluster into broad classes via keyword matching
    def classify_target(target_str: str) -> str:
        t = str(target_str).upper()
        if t in ("", "UNKNOWN", "NONE", "NAN"):
            return "Unknown / None"
        if any(x in t for x in ["PD-1", "PDL1", "PD-L1", "CTLA4",
                                  "TIGIT", "LAG3", "TIM3", "BTLA"]):
            return "Immune Checkpoint"
        if any(x in t for x in ["HER2", "EGFR", "VEGF", "VEGFR", "FGFR",
                                  "PDGFR", "ALK", "RET", "MET", "KRAS",
                                  "BRAF", "MEK", "ERK", "CDK", "PARP",
                                  "mTOR", "PI3K", "AKT", "SHH", "WNT"]):
            return "Oncogenic Signalling"
        if any(x in t for x in ["CD19", "CD20", "CD38", "BCMA", "CD3",
                                  "CD276", "CD7", "GPRC5D", "NCR3LG1"]):
            return "Haematologic Surface Antigen"
        if any(x in t for x in ["DNA", "TUBB", "TYMS", "TOP1", "TOP2",
                                  "RNR", "TOPO"]):
            return "DNA / Cytotoxic Mechanism"
        if any(x in t for x in ["TROP2", "HER2", "NECTIN", "FOLR", "MUC"]):
            return "Tumour-associated Antigen"
        if "JAK" in t or "XPO1" in t or "PROTEASOME" in t or "BCL" in t:
            return "Haematologic Signalling"
        return "Other / Novel"

    df["target_class"] = df["primary_target"].apply(classify_target)
    results["by_target_class"] = success_rate_table(
        df, ["target_class"], min_resolved=2
    )

    # ── Dim 5: Phase × Technology ──────────────────────────────
    results["phase_x_technology"] = success_rate_table(
        df, ["phase_clean", "primary_technology"], min_resolved=2
    )

    # ── Dim 6: Start-year trend ────────────────────────────────
    df_year = df[df["start_year"].notna()].copy()
    df_year["start_year"] = df_year["start_year"].astype(int)
    results["by_start_year"] = success_rate_table(
        df_year, ["start_year"], min_resolved=2
    )

    return results


# ─────────────────────────────────────────────────────────────────
# PRINT HELPERS
# ─────────────────────────────────────────────────────────────────

def _bar(rate, width=20):
    """ASCII progress bar for success rate."""
    filled = int(round(rate / 100 * width))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {rate:5.1f}%"


def print_analysis(results: dict, df: pd.DataFrame) -> None:
    sep = "=" * 72

    print(f"\n{sep}")
    print("  PART 2 — SUCCESS RATE OPERATIONALISATION & COHORT ANALYSIS")
    print(sep)

    # Overall outcome distribution
    print("\n── OVERALL OUTCOME DISTRIBUTION ────────────────────────────────────")
    total = len(df)
    for tier, label in TIER_LABELS.items():
        n = (df["outcome_tier"] == tier).sum()
        print(f"  {label:<25} : {n:>5} trials  ({n/total*100:.1f}%)")
    resolved = df["success"].notna().sum()
    print(f"\n  Resolved (tier 0+2)          : {resolved:>5} trials")
    print(f"  Unresolved / ambiguous       : {total-resolved:>5} trials")
    if resolved > 0:
        overall_sr = df["success"].mean() * 100
        print(f"\n  Overall success rate         : {overall_sr:.1f}%  "
              f"(of {resolved} resolved trials)")

    # ── Dimension 1: Phase ─────────────────────────────────────
    print(f"\n── DIM 1: SUCCESS RATE BY PHASE ────────────────────────────────────")
    _print_table(results["by_phase"], ["phase_clean"])

    # ── Dimension 2: Phase × Indication ───────────────────────
    print(f"\n── DIM 2: SUCCESS RATE — PHASE × TOP-10 INDICATION ────────────────")
    _print_table(results["phase_x_indication"], ["phase_clean", "primary_indication"])

    # ── Dimension 3: Technology ────────────────────────────────
    print(f"\n── DIM 3: SUCCESS RATE BY MAIN TECHNOLOGY ──────────────────────────")
    _print_table(results["by_technology"], ["primary_technology"])

    # ── Dimension 4: Target Class ──────────────────────────────
    print(f"\n── DIM 4: SUCCESS RATE BY TARGET CLASS ─────────────────────────────")
    _print_table(results["by_target_class"], ["target_class"])

    # ── Dimension 5: Phase × Technology ───────────────────────
    print(f"\n── DIM 5: SUCCESS RATE — PHASE × TECHNOLOGY ───────────────────────")
    _print_table(results["phase_x_technology"],
                 ["phase_clean", "primary_technology"])

    # ── Dimension 6: Start-year trend ─────────────────────────
    print(f"\n── DIM 6: SUCCESS RATE BY START YEAR (COHORT TREND) ───────────────")
    _print_table(results["by_start_year"], ["start_year"])

    print(f"\n{sep}\n")


def _print_table(df: pd.DataFrame, key_cols: list) -> None:
    """Pretty-print a success-rate table with ASCII bars."""
    df_ok = df[df["data_quality"] == "ok"]
    df_lown = df[df["data_quality"] == "low-n"]

    if df_ok.empty and df_lown.empty:
        print("  No data.")
        return

    for _, row in df_ok.iterrows():
        key = " | ".join(str(row[c]) for c in key_cols)
        bar = _bar(row["success_rate_pct"])
        print(f"  {key:<40} {bar}  "
              f"(n={row['n_resolved']:>3}: "
              f"{int(row['n_success'])}✓ {int(row['n_failure'])}✗)")

    if not df_lown.empty:
        print(f"  [low-n groups omitted: {len(df_lown)} with < 3 resolved trials]")


# ─────────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────────

def export_results(results: dict, df_labelled: pd.DataFrame,
                   outpath: str = "clinical_trial_success_analysis.xlsx") -> None:
    """Write all tables to an Excel workbook."""
    with pd.ExcelWriter(outpath, engine="openpyxl") as writer:
        # Full labelled dataset
        df_labelled.to_excel(writer, sheet_name="labelled_trials", index=False)
        # Each analysis
        sheet_names = {
            "by_phase":           "SR_by_phase",
            "phase_x_indication": "SR_phase_x_indication",
            "by_technology":      "SR_by_technology",
            "by_target_class":    "SR_by_target_class",
            "phase_x_technology": "SR_phase_x_technology",
            "by_start_year":      "SR_by_start_year",
        }
        for key, sheet in sheet_names.items():
            results[key].to_excel(writer, sheet_name=sheet, index=False)
    print(f"\n[INFO] Results written to {outpath}")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    FILE = "SampleDateExtract.xlsx"

    # Load (re-use Part 1 loader)
    raw_df = pd.read_excel(FILE, sheet_name=0)
    print(f"[INFO] Loaded {len(raw_df)} rows × {len(raw_df.columns)} columns")

    # A – Label outcomes
    df_labelled = label_trials(raw_df)

    # B – Stratified analyses
    results = run_stratified_analysis(df_labelled)

    # Print report
    print_analysis(results, df_labelled)

    # Export
    export_results(results, df_labelled)