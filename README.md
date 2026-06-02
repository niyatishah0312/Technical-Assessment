Clinical Trials Data Pipeline

Overview

This project analyses a clinical trials dataset and builds a reproducible pipeline for:

- Data ingestion and quality assessment
- Schema normalization
- Operationalisation of a trial success metric
- Stratified success rate analysis across phases, indications, technologies, and target classes

The project is divided into three parts corresponding to the assignment requirements.

Repository Structure

clinical-trials-data-pipeline/
│
├── SampleDateExtract.xlsx
├── Part_1.py
├── Part_2.py
├── Part_3b.py
│
├── clinical_trial_schema_output.xlsx
├── clinical_trial_success_analysis.xlsx
│
└── README.md


Files

| File                                  | Description                                                                                 |
| ------------------------------------  | ------------------------------------------------------------------------------------------- |
| SampleDateExtract.xlsx                | Raw clinical trials dataset provided for the assignment                                     |
| Part_1.py                             | Data ingestion, quality profiling, and schema normalization                                 |
| Part_2.py                             | Success metric operationalisation and stratified cohort analysis                            |
| Part_3b.py                            | Written response discussing limitations, additional data needs, and future schema evolution |
| clinical_trial_schema_output.xlsx     | Output generated from Part 1                                                                |
| clinical_trial_success_analysis.xlsx  | Output generated from Part 2                                                                |


Part 1 – Data Ingestion, Quality Profiling & Schema Normalization

Objectives

- Load and inspect the raw clinical trials dataset
- Assess data quality
- Identify missing values, dirty values, and structural anomalies
- Normalize multi-valued fields into relational tables
- Create a clean analytical schema

Quality Checks Performed

1. Field completeness analysis
2. Cardinality analysis
3. Dirty value detection
     Capitalization inconsistencies
     Status and phase vocabulary review
     Date format inconsistencies
     Encoding artefacts
4. Structural anomaly detection
     Duplicate IDs
     Empty rows
     List length mismatches

Output Tables

trials_fact: One row per clinical trial containing normalized trial-level information.

drugs_bridge: Maps trials to drugs and associated technologies.

targets_bridge: Maps trials to targets and target abbreviations.

indications_bridge: Maps trials to disease indications.

Output File -> clinical_trial_schema_output.xlsx


Part 2 – Success Rate Operationalisation & Stratified Analysis

Defining Success
The dataset does not contain clinical efficacy results, regulatory approvals, or patient outcome measures.
Therefore, a computable operational proxy was defined using recruitment status.

Outcome Tiers
Tier 2 – Success
    - COMPLETED
Tier 1 – Ongoing / Ambiguous
    - RECRUITING
    - NOT_YET_RECRUITING
    - ACTIVE_NOT_RECRUITING
    - ENROLLING_BY_INVITATION
    - SUSPENDED
    - UNKNOWN
Tier 0 – Failure
    - TERMINATED
    - WITHDRAWN

Binary Success Variable
Success = 1  → Tier 2
Success = 0  → Tier 0

Tier 1 trials are excluded from success-rate denominators because their final outcomes are not yet known.

Analyses Performed
   Success rate by phase
   Success rate by phase and indication
   Success rate by technology
   Success rate by target class
   Success rate by phase and technology
   Success rate by start-year cohort

Output File -> clinical_trial_success_analysis.xlsx

Requirements
Python 3.10+

Libraries
pip install pandas numpy openpyxl


Running the Pipeline
Part 1
python part1.py

Generates:
clinical_trial_schema_output.xlsx

Part 2
python part2.py

Generates:
clinical_trial_success_analysis.xlsx

Author
Niyati Shah
