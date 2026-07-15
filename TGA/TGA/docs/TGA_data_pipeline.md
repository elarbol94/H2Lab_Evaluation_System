# TGA Data Pipeline (`overview_3samples_new.py`)

## 1. Setup and Environment
- Resolves script directory and project root with `Path(__file__)`.
- Adds `H2LAB_ROOT` to `sys.path` to import local helpers.
- Loads preprocessing configuration from `config.json` via `PreparationConfig.load_from_file(...)`.
- Resolves SharePoint/project path with `get_path_for_folder('PUB_25_9 Lime in EAFD Recycling')` and changes working directory to it.

## 2. Data Sources
- **Raw TGA experiment files**: `TGA/data/boudouard_equilibrium/*.txt`.
- **Experiment metadata**: Google Sheet loaded by `GoogleSheetLoader().load_sheet('H2Lab_PUB_25_9 Lime in EAFD Recycling')`.
- **Dust composition (optional)**: `DustComposition_allDusts_normalized.xlsx`.
  - If missing/fails to load, theoretical mass-loss overlays are skipped.

## 3. Indexing and Caching
- Builds metadata index `_META_INDEX` from `df_meta['id']` using base IDs (removes trailing `_<number>`).
- Uses `_META_ROW_CACHE` to cache metadata lookup results per experiment ID.
- Uses `_FILE_CACHE` to cache matched `.txt` filenames per experiment ID.

## 4. Core Utility Logic
- `_flatten_experiment_ids(...)`: flattens grouped IDs and removes placeholders (`'-'`).
- `_normalize_float(...)`: parses numeric values (handles comma decimal strings), returns `NaN` on failure.
- `_get_metadata_rows(experiment_id)`:
  - Matches exact ID or `ID_<replicate>` pattern.
  - Prefers indexed subset lookup when available.
- `_find_experiment_file(experiment_id)`:
  - Finds first `.txt` filename containing the experiment ID.
- `_get_temperature_index(df, target_temp)`:
  - Returns first index where `temperature_C >= target_temp`; else last index.

## 5. Raw Experiment Loading
`_load_raw_experiment(experiment_id, compute_theoretical=True)`:
- Creates `TGAExperiment` with:
  - selected raw file
  - preparation config
  - metadata DataFrame
  - experiment ID
  - optional dust composition DataFrame
- Trims data up to the first maximum-temperature occurrence (`exp.df = exp.df.iloc[:idx_end]`).
- Fetches experiment-specific metadata rows.
- Computes theoretical mass loss (`exp.get_theoretical_mass_loss(...)`) when dust composition is available and enabled.
- Returns `(exp, df_meta_single)`.

## 6. Equalization Baseline Selection
`compute_equalization_targets(experiment_groups, reference_temp=950)`:
- For each material, selects one baseline experiment:
  - chooses the experiment with the **lowest Lime m-%** among provided IDs.
- Loads each selected baseline experiment.
- Reads `dm_filtered_pct` at reference temperature (`950 °C` by default).
- Returns `rel_mass_dict` mapping:
  - `material -> reference relative mass`.

## 7. Final Experiment Preparation
`load_experiment(experiment_id, rel_mass_dict, reference_temp=950, plot_internal=False)`:
- Loads raw experiment and metadata via `_load_raw_experiment(...)`.
- If material has a baseline in `rel_mass_dict`, equalizes mass using:
  - `exp.equalize_on_mass(temp_idx=<idx at reference temp>, rel_mass=<baseline>)`
- Returns processed outputs:
  - `df` (processed experiment DataFrame)
  - `df_meta_single` (metadata rows for that experiment)
  - `exp` (experiment object, including optional `theoretical_massloss`)

## 8. Group Plotting Pipeline
`plot_experiment_groups(experiment_groups, rel_mass_dict)`:
- Creates a 4-column layout per material/group:
  - `temperature_C` vs `dm_filtered_pct`
  - `temperature_C` vs `dmdt_filtered_pctmin`
  - `dm_filtered_pct` vs `dmdt_filtered_pctmin`
  - `time_min` vs `CO` (plus twin axis for `temperature_C`)
- Applies consistent styling:
  - stable color mapping for standard lime levels `[0, 5, 10, 15]`
  - line-style variation by `(material, lime_addition)`
- Adds theoretical overlays when available:
  - horizontal line in mass plot at `100 - theoretical_massloss`
  - vertical line in phase-space plot at same mass value
- Annotates each row with material label and final axis formatting.
- Shows the figure and returns it.

## 9. Main Execution Flow
When run as a script:
1. Defines `experiment_groups` (IDs grouped by material).
2. Computes equalization targets with `compute_equalization_targets(...)`.
3. Plots all groups via `plot_experiment_groups(...)`.
4. (Optional, commented) save figure to `TGA/diagram/overview_new.png`.

## 10. Data Pipeline Summary (Compact)
1. Load config + resolve project path.
2. Load metadata sheet and optional dust composition.
3. Discover raw `.txt` experiment files.
4. For each material, infer baseline experiment (lowest lime).
5. Read baseline mass at 950 °C to build equalization targets.
6. Load each experiment, preprocess/trim, compute optional theoretical mass loss.
7. Equalize experiment mass to material baseline at 950 °C.
8. Generate multi-panel comparative plots across experiment groups.
