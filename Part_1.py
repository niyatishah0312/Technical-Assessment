"""
Clinical Trials Data Pipeline
Part 1: Data Ingestion, Quality Profiling & Schema Normalization
"""

import pandas as pd
import numpy as np
import ast
import re
from datetime import datetime, timedelta

LIST_COLUMNS = [
    "indications",
    "interventions_drugs",
    "drugs_datalake",
    "main_technologies",
    "specific_technologies",
    "target_names",
    "target_abbreviations",
]

# ─────────────────────────────────────────────────────────────────
# PART A: DATA INGESTION & QUALITY REPORT
# ─────────────────────────────────────────────────────────────────

def load_raw_data(filepath: str) -> pd.DataFrame:
    """Load the Excel file into a DataFrame."""
    df = pd.read_excel(filepath, sheet_name=0)
    print(f"[INFO] Loaded {len(df)} rows × {len(df.columns)} columns")
    return df


def parse_list_field(val):
    """
    Safely parse list-like fields and treat [], [[]], [[None]], [['']]
    as empty values.
    """
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
    """Convert Excel numeric serial dates to Python datetime."""
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


# ── A1. FIELD COMPLETENESS & NULL DISTRIBUTION ──────────────────

def field_completeness(df: pd.DataFrame) -> pd.DataFrame:
    """Return completeness rate, null count, and null % per column."""

    total = len(df)
    rows = []

    for col in df.columns:

        if col in LIST_COLUMNS:
            missing_mask = df[col].apply(
                lambda x: len(parse_list_field(x)) == 0
            )
        else:
            missing_mask = df[col].isna()

        null_count = int(missing_mask.sum())
        non_null_count = total - null_count

        rows.append({
            "column": col,
            "non_null_count": non_null_count,
            "null_count": null_count,
            "completeness_%": round(non_null_count * 100 / total, 2),
            "dtype": str(df[col].dtype),
        })

    return pd.DataFrame(rows)


# ── A2. CARDINALITY ─────────────────────────────────────────────

def cardinality_report(df: pd.DataFrame, max_unique_display: int = 10) -> pd.DataFrame:
    """Report unique value counts and sample values per column."""
    rows = []
    for col in df.columns:
        n_unique = df[col].nunique(dropna=True)
        sample = df[col].dropna().unique()[:max_unique_display].tolist()
        rows.append({"column": col, "unique_values": n_unique, "sample_values": sample})
    return pd.DataFrame(rows)


# ── A3. DIRTY VALUE DETECTION ────────────────────────────────────

def detect_dirty_values(df: pd.DataFrame) -> dict:
    """
    Identify:
      - Inconsistent capitalisation in categorical columns
      - Free-text synonyms in status/phase columns
      - Date format variations
      - Encoding artefacts (garbled characters)
    """
    report = {}

    # 3a. Capitalisation inconsistencies in key string columns
    cat_cols = ["phase", "recruitment_status", "enrollment_type"]
    cap_issues = {}
    for col in cat_cols:
        if col not in df.columns:
            continue
        vals = df[col].dropna().astype(str)
        variants = vals.str.strip().value_counts()
        # Flag if same normalised value has multiple surface forms
        groups = {}
        for v in variants.index:
            key = v.upper().strip()
            groups.setdefault(key, []).append(v)
        inconsistent = {k: v for k, v in groups.items() if len(v) > 1}
        if inconsistent:
            cap_issues[col] = inconsistent
    report["capitalisation_inconsistencies"] = cap_issues

    # 3b. Free-text synonym detection in phase & status
    phase_vals = df["phase"].dropna().astype(str).str.strip().unique().tolist() if "phase" in df.columns else []
    status_vals = df["recruitment_status"].dropna().astype(str).str.strip().unique().tolist() if "recruitment_status" in df.columns else []
    report["raw_phase_values"] = sorted(set(phase_vals))
    report["raw_status_values"] = sorted(set(status_vals))

    # 3c. Date format variation
    date_cols = ["start_date", "completion_date", "primary_completion_date"]
    date_format_issues = {}
    for col in date_cols:
        if col not in df.columns:
            continue
        sample = df[col].dropna().astype(str).unique()[:10].tolist()
        # Detect if values look like serial numbers vs strings
        numeric_count = df[col].apply(lambda x: isinstance(x, (int, float)) and not pd.isna(x)).sum()
        string_count  = df[col].apply(lambda x: isinstance(x, str)).sum()
        date_format_issues[col] = {
            "numeric_serial_count": int(numeric_count),
            "string_count": int(string_count),
            "sample": sample,
        }
    report["date_format_issues"] = date_format_issues

    # 3d. Encoding artefacts (non-ASCII characters outside expected range)
    encoding_issues = {}
    text_cols = ["brief_title", "official_title", "target_names", "target_abbreviations"]
    for col in text_cols:
        if col not in df.columns:
            continue
        mask = df[col].dropna().astype(str).str.contains(r"[^\x00-\x7F]", regex=True)
        bad_rows = df.loc[mask[mask].index, ["nct_id", col]].head(5)
        if not bad_rows.empty:
            encoding_issues[col] = bad_rows.to_dict("records")
    report["encoding_artefacts"] = encoding_issues

    return report


# ── A4. STRUCTURAL ANOMALIES ─────────────────────────────────────

def structural_anomalies(df: pd.DataFrame) -> dict:
    """Check for duplicate IDs, misaligned columns, and empty rows."""
    report = {}

    # Duplicate trial IDs
    if "nct_id" in df.columns:
        dup_nct = df[df["nct_id"].duplicated(keep=False)]["nct_id"].tolist()
        report["duplicate_nct_ids"] = dup_nct

    if "ID-datalake" in df.columns:
        dup_dl = df[df["ID-datalake"].duplicated(keep=False)]["ID-datalake"].tolist()
        report["duplicate_datalake_ids"] = dup_dl

    # Completely empty rows
    empty_rows = df[df.isnull().all(axis=1)].index.tolist()
    report["fully_empty_rows"] = empty_rows

    # Column count consistency (all rows should have same number of columns — always true in pandas)
    report["total_columns"] = len(df.columns)
    report["total_rows"] = len(df)

    # Rows where list-type fields have mismatched lengths
    list_cols = ["interventions_drugs", "drugs_datalake", "main_technologies",
                 "specific_technologies", "target_names", "target_abbreviations"]
    mismatch_rows = []
    for idx, row in df.iterrows():
        lengths = []
        for col in list_cols:
            if col in df.columns:
                parsed = parse_list_field(row[col])
                lengths.append(len(parsed))
        non_zero = [l for l in lengths if l > 0]
        if non_zero and (max(non_zero) != min(non_zero)):
            mismatch_rows.append(idx)
    report["list_field_length_mismatches"] = len(mismatch_rows)
    report["mismatch_row_indices_sample"] = mismatch_rows[:10]

    return report


def run_quality_report(df: pd.DataFrame) -> None:
    """Print a full structured data quality report to stdout."""
    sep = "=" * 70

    print(f"\n{sep}")
    print("  DATA QUALITY REPORT")
    print(sep)

    # Completeness
    print("\n── A1. FIELD COMPLETENESS ──────────────────────────────────────────")
    comp = field_completeness(df)
    print(comp.to_string(index=False))
    print("\n[INFO] Completed A1: Field Completeness")

    # Cardinality
    print("\n── A2. CARDINALITY ─────────────────────────────────────────────────")
    card = cardinality_report(df, max_unique_display=5)
    for _, row in card.iterrows():
        print(f"  {row['column']:<35} unique={row['unique_values']:<6} samples={row['sample_values']}")
    print("\n[INFO] Completed A2: Cardinality")

    # Dirty values
    print("\n── A3. DIRTY VALUES ────────────────────────────────────────────────")
    dirty = detect_dirty_values(df)

    print("\n  Capitalisation inconsistencies:")
    if dirty["capitalisation_inconsistencies"]:
        for col, groups in dirty["capitalisation_inconsistencies"].items():
            print(f"    [{col}] {groups}")
    else:
        print("    None detected.")

    print(f"\n  Raw phase values  : {dirty['raw_phase_values']}")
    print(f"  Raw status values : {dirty['raw_status_values']}")

    print("\n  Date format issues:")
    for col, info in dirty["date_format_issues"].items():
        print(f"    [{col}] numeric_serial={info['numeric_serial_count']}, "
              f"string={info['string_count']}, sample={info['sample'][:3]}")

    print("\n  Encoding artefacts:")
    if dirty["encoding_artefacts"]:
        for col, rows in dirty["encoding_artefacts"].items():
            print(f"    [{col}] {rows}")
    else:
        print("    None detected.")

    print("\n[INFO] Completed A3: Dirty Value Detection")

    # Structural anomalies
    print("\n── A4. STRUCTURAL ANOMALIES ────────────────────────────────────────")
    struct = structural_anomalies(df)
    print(f"  Total rows        : {struct['total_rows']}")
    print(f"  Total columns     : {struct['total_columns']}")
    print(f"  Duplicate NCT IDs : {len(struct['duplicate_nct_ids'])}")
    print(f"  Duplicate DL IDs  : {len(struct['duplicate_datalake_ids'])}")
    print(f"  Fully empty rows  : {len(struct['fully_empty_rows'])}")
    print(f"  List length mismatches: {struct['list_field_length_mismatches']} rows "
          f"(sample indices: {struct['mismatch_row_indices_sample']})")

    print("\n[INFO] Completed A4: Structural Anomaly Detection")

    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────
# PART B: CLEAN ANALYTICAL SCHEMA
# ─────────────────────────────────────────────────────────────────

# ── Controlled Vocabularies ──────────────────────────────────────

PHASE_VOCAB = {
    "PHASE1": 1,
    "PHASE2": 2,
    "PHASE3": 3,
    "PHASE4": 4,
    "PHASE1/PHASE2": 1,   # treated as lower bound
    "PHASE2/PHASE3": 2,
    "EARLY_PHASE1": 0,
    "": None,
    "N/A": None,
}

PHASE_LABEL_MAP = {
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "PHASE1/PHASE2": "Phase 1/2",
    "PHASE2/PHASE3": "Phase 2/3",
    "EARLY_PHASE1": "Early Phase 1",
}

STATUS_VOCAB = {
    "RECRUITING": "Recruiting",
    "NOT_YET_RECRUITING": "Not Yet Recruiting",
    "ACTIVE_NOT_RECRUITING": "Active, Not Recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by Invitation",
    "COMPLETED": "Completed",
    "TERMINATED": "Terminated",
    "WITHDRAWN": "Withdrawn",
    "SUSPENDED": "Suspended",
    "UNKNOWN": "Unknown",
}


def normalise_phase(raw_val) -> tuple:
    """Return (phase_label_clean, phase_integer)."""
    if pd.isna(raw_val):
        return (None, None)
    key = str(raw_val).strip().upper()
    label = PHASE_LABEL_MAP.get(key, key.title())
    integer = PHASE_VOCAB.get(key, None)
    return (label, integer)


def normalise_status(raw_val) -> str:
    """Standardise recruitment_status to controlled vocabulary."""
    if pd.isna(raw_val):
        return "Unknown"
    key = str(raw_val).strip().upper()
    return STATUS_VOCAB.get(key, raw_val.strip().title())


# ── Core Fact Table ──────────────────────────────────────────────

def build_trials_fact_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the clean core trials fact table (one row per trial).
    Includes:
      - Normalised scalar fields
      - Controlled vocabulary status & phase
      - Derived fields: trial_duration_days, start_year, phase_integer
    """
    trials = pd.DataFrame()

    trials["trial_id"]         = df["ID-datalake"].astype("Int64")
    trials["nct_id"]           = df["nct_id"].astype(str).str.strip()
    trials["brief_title"]      = df["brief_title"].astype(str).str.strip()
    trials["official_title"]   = df["official_title"].astype(str).str.strip()

    # Dates (convert Excel serials to proper dates)
    for col in ["start_date", "completion_date", "primary_completion_date"]:
        trials[col] = df[col].apply(excel_serial_to_date)

    # Controlled vocabulary fields
    phase_clean, phase_int = zip(*df["phase"].apply(normalise_phase))
    trials["phase"]           = phase_clean
    trials["phase_integer"]   = pd.array(phase_int, dtype="Int64")
    trials["status"]          = df["recruitment_status"].apply(normalise_status)

    # Enrollment
    trials["enrollment"]       = pd.to_numeric(df["enrollment"], errors="coerce").astype("Int64")
    trials["enrollment_type"]  = df["enrollment_type"].str.strip().str.title() if "enrollment_type" in df.columns else None

    # ── Derived fields ──────────────────────────────────────────
    # Trial duration: start_date → completion_date (days)
    trials["trial_duration_days"] = (
        (trials["completion_date"] - trials["start_date"])
        .dt.days
        .astype("Int64")
    )

    # Start year (for cohort stratification)
    trials["start_year"] = trials["start_date"].dt.year.astype("Int64")

    # Is the trial a combination therapy? (>1 drug listed)
    drug_counts = df["interventions_drugs"].apply(lambda x: len(parse_list_field(x)))
    trials["is_combination_therapy"] = drug_counts > 1

    # Number of drugs / targets
    trials["n_drugs"]   = drug_counts.astype("Int64")
    trials["n_targets"] = df["target_names"].apply(lambda x: len(parse_list_field(x))).astype("Int64")

    return trials.reset_index(drop=True)


# ── Dimension Tables (normalised multi-valued fields) ────────────

def build_drugs_bridge(df: pd.DataFrame) -> pd.DataFrame:
    """
    Explode multi-valued interventions_drugs into a bridge table.
    Schema: trial_id | drug_name | drug_datalake_id | main_technology | specific_technology
    """
    rows = []
    for _, row in df.iterrows():
        tid      = row["ID-datalake"]
        drugs    = parse_list_field(row.get("interventions_drugs"))
        dl_ids   = parse_list_field(row.get("drugs_datalake"))
        main_t   = parse_list_field(row.get("main_technologies"))
        spec_t   = parse_list_field(row.get("specific_technologies"))

        if len(drugs) == 0:
            continue

        max_len = len(drugs)
        for i in range(max_len):
            rows.append({
                "trial_id":          tid,
                "drug_name":         drugs[i] if i < len(drugs) else None,
                "drug_datalake_id":  dl_ids[i] if i < len(dl_ids) else None,
                "main_technology":   main_t[i] if i < len(main_t) else None,
                "specific_technology": spec_t[i] if i < len(spec_t) else None,
            })
    return pd.DataFrame(rows)


def build_targets_bridge(df: pd.DataFrame) -> pd.DataFrame:
    """
    Explode multi-valued target_names into a bridge table.
    Schema: trial_id | target_name | target_abbreviation
    """
    rows = []
    for _, row in df.iterrows():
        tid    = row["ID-datalake"]
        names  = parse_list_field(row.get("target_names"))
        abbrevs = parse_list_field(row.get("target_abbreviations"))
        for i, name in enumerate(names):
            rows.append({
                "trial_id":            tid,
                "target_name":         name,
                "target_abbreviation": abbrevs[i] if i < len(abbrevs) else None,
            })
    return pd.DataFrame(rows)


def build_indications_bridge(df: pd.DataFrame) -> pd.DataFrame:
    """
    Explode multi-valued indications into a bridge table.
    Schema: trial_id | indication (deduplicated per trial)
    """
    rows = []
    for _, row in df.iterrows():
        tid  = row["ID-datalake"]
        inds = parse_list_field(row.get("indications"))
        seen = set()
        for ind in inds:
            ind_clean = ind.strip()
            if ind_clean and ind_clean not in seen:
                seen.add(ind_clean)
                rows.append({"trial_id": tid, "indication": ind_clean})
    return pd.DataFrame(rows)


# ── Print Schema Summary ─────────────────────────────────────────

def print_schema_summary(trials, drugs, targets, indications):
    sep = "=" * 70
    print(f"\n{sep}")
    print("  NORMALISED SCHEMA SUMMARY")
    print(sep)

    print("\n── TABLE: trials_fact ──────────────────────────────────────────────")
    print(f"  Rows: {len(trials)}")
    print("  Columns:")
    for col in trials.columns:
        print(f"    {col:<30} dtype={trials[col].dtype}")
    print("\n  Sample (first 3 rows):")
    print(trials.head(3).to_string())

    print("\n── TABLE: trial_drugs_bridge ───────────────────────────────────────")
    print(f"  Rows: {len(drugs)}")
    print("  Columns:", list(drugs.columns))
    print("\n  Sample:")
    print(drugs.head(5).to_string())

    print("\n── TABLE: trial_targets_bridge ─────────────────────────────────────")
    print(f"  Rows: {len(targets)}")
    print("  Columns:", list(targets.columns))
    print("\n  Sample:")
    print(targets.head(5).to_string())

    print("\n── TABLE: trial_indications_bridge ─────────────────────────────────")
    print(f"  Rows: {len(indications)}")
    print("  Columns:", list(indications.columns))
    print("\n  Sample:")
    print(indications.head(5).to_string())

    # Quick cohort analytics preview
    print("\n── DERIVED FIELD PREVIEW ────────────────────────────────────────────")
    print("\n  Phase distribution:")
    print(trials["phase"].value_counts(dropna=False).to_string())

    print("\n  Status distribution:")
    print(trials["status"].value_counts(dropna=False).to_string())

    print("\n  Start year distribution:")
    print(trials["start_year"].value_counts(dropna=False).sort_index().to_string())

    print("\n  Combination therapy rate:")
    ct_rate = trials["is_combination_therapy"].mean() * 100
    print(f"    {ct_rate:.1f}% of trials test a combination therapy")

    print("\n  Trial duration stats (days):")
    print(trials["trial_duration_days"].describe().to_string())

    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    FILE = "SampleDateExtract.xlsx"

    # ── Part A ────────────────────────────────────────────────────
    raw_df = load_raw_data(FILE)
    run_quality_report(raw_df)

    # ── Part B ────────────────────────────────────────────────────
    trials_fact     = build_trials_fact_table(raw_df)
    drugs_bridge    = build_drugs_bridge(raw_df)
    targets_bridge  = build_targets_bridge(raw_df)
    indications_bridge = build_indications_bridge(raw_df)

    print_schema_summary(trials_fact, drugs_bridge, targets_bridge, indications_bridge)

    with pd.ExcelWriter("clinical_trial_schema_output.xlsx") as writer:
        trials_fact.to_excel(writer, sheet_name="trials_fact", index=False)
        drugs_bridge.to_excel(writer, sheet_name="drugs_bridge", index=False)
        targets_bridge.to_excel(writer, sheet_name="targets_bridge", index=False)
        indications_bridge.to_excel(writer, sheet_name="indications_bridge", index=False)

    print("\n[INFO] Output written to clinical_trial_schema_output.xlsx")