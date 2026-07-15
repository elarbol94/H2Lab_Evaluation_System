# TGAExperiment Application Blueprint

This document turns the current `TGAExperiment` behavior into a clean, implementation-ready pipeline spec for future app development.

Primary source: `helper/TGA.py`
Pipeline caller context: `H2Lab_PUB_25_9 Lime in EAFD Recycling/TGA/overview_3samples_new.py`

## 1. Purpose
`TGAExperiment` is the core processing unit that:
- loads one raw TGA measurement file,
- normalizes and prepares time/mass signals,
- applies configured filtering and trimming,
- derives kinetics metrics,
- optionally computes theoretical mass-loss from dust composition,
- exposes a processed DataFrame for plotting/comparison.

## 2. Inputs and Dependencies

## 2.1 Required runtime input
- `file_path`: path to one experiment raw file (`.csv` typically, cached `.parquet` supported).
- `config`: `PreparationConfig` object (must provide filter/cut settings and gas mapping).

## 2.2 Optional context input
- `experiment_id`: metadata key like `RT54`.
- `df_meta`: experiment metadata table (must contain `id` for lookup).
- `df_comp`: dust composition table (must contain `Dust` + species columns).
- `df_corr`: correction run DataFrame (for corrected delta-mass path).
- `save_parquet`: whether to persist processed output.
- `parser`: parser class (default `TGAFile`).

## 2.3 Upstream helper objects
- `PreparationConfig`
- `FilterConfig` and `FILTER_REGISTRY`
- `TGAFile`
- `theoretical_mass_loss_from_composition(...)`

## 3. Data Contract (Expected Columns)

## 3.1 Raw parser output (minimum)
- `Time(s)` (or close match to `time`)
- `Temperature(°C)` (or mapped to `temperature_C`)
- `Delta m(mg)` or `Corrected delta m(mg)` (mapped to `dm`)

## 3.2 Processed canonical columns
- `time_min`
- `temperature_C`
- `dm_original_mg`
- `dm_filtered_mg`
- `dm_original_pct`
- `dm_filtered_pct`
- `dmdt_original_mgmin`
- `dmdt_filtered_mgmin`
- `dmdt_original_pctmin`
- `dmdt_filtered_pctmin`
- `m_filtered_mg`
- `time_abs`
- optional gas columns after remap (`CO`, `CO2`, `H2O`, etc.)

## 4. End-to-End Lifecycle (Constructor Flow)

1. Validate `file_path` exists.
2. Store constructor inputs on instance.
3. If `df_comp` provided, map dust composition for this experiment (`load_dust_composition`).
4. Build parquet cache path (`same file stem + .parquet`).
5. Cache short-circuit:
- If parquet exists and `config.process_file.upper() != 'YES'`, load parquet and return early.
6. Parse raw file via `parser(file_path)`.
7. Capture parser outputs:
- `df` raw DataFrame
- `experiment_datetime`, `start_timestamp`
- `initial_weight` (mg)
8. Execute `load_and_process()`.
9. Save parquet if `save_parquet=True`.

## 5. Processing Pipeline (`load_and_process` + `prepare`)

## Stage A: Initial standardization
1. Rename known columns (`_safe_rename_columns`).
2. Ensure continuous sampling:
- detect fixed step via `_is_continuous_sampling`
- if not continuous, interpolate all channels onto uniform `time` grid (`_make_sampling_continuous`).
3. Drop irrelevant hardware columns (`_columns_to_drop`).
4. Add absolute timestamp: `time_abs = time + start_timestamp`.

## Stage B: Filter object resolution
1. Resolve pre-filter from `config.pre_filter`:
- validate filter type exists in `FILTER_REGISTRY`
- build from params
- fallback to class defaults on invalid params.
2. Resolve post-filter the same way.

## Stage C: Time and correction prep
1. Convert `time` seconds to `time_min`.
2. Smooth `Water` signal if present (`_smooth_h2o`):
- scale by factor `1240`
- apply Butterworth low-pass via `filtfilt`.
3. If correction data (`df_corr`) provided, compute corrected mass curve (`_calculate_corrected_delta`).

## Stage D: Mass basis and pre-filter
1. Require `dm` column; fail fast if missing.
2. Rename `dm` -> `dm_original_mg`.
3. Apply pre-filter:
- If filter is `MovingAverageDecimator`, run dataframe filter and copy to `dm_filtered_mg`.
- Else filter `dm_original_mg` into `dm_filtered_mg`.
- On failure, keep original as filtered fallback.
4. If this file is a correction run (`'CORRECTION' in file_path.upper()`), exit early from deeper derivation.

## Stage E: Derived metrics
1. Relative mass:
- `dm_original_pct = (dm_original_mg + initial_weight) / initial_weight * 100`
- `dm_filtered_pct = (dm_filtered_mg + initial_weight) / initial_weight * 100`
2. First derivatives (`derive_column`):
- `dmdt_filtered_pctmin`
- `dmdt_filtered_mgmin`
- `dmdt_original_pctmin`
- `dmdt_original_mgmin`
3. Absolute filtered mass:
- `m_filtered_mg = initial_weight + dm_filtered_mg`
- enforce first point exactly equals `initial_weight`.

## Stage F: Post-filter
1. If post-filter exists:
- For decimator: apply dataframe filter.
- Else smooth both derivative columns:
  - `dmdt_filtered_pctmin`
  - `dmdt_filtered_mgmin`
2. Log row counts before/pre/post filtering.

## Stage G: Cutting and gas remap (`cut_and_rename`)
1. Reactive segment cut (temperature window): `cut_reactive_segment(lower_temp, upper_temp)`.
2. Optional tail cut by second derivative threshold (`cut_tail`).
3. Gas column remap using `config.gas_columns`:
- copy source column to target name
- drop original source column.

## 6. Post-Processing Utilities

## 6.1 Equalization utility (`equalize_on_mass`)
Purpose: shift mass signals so a selected reference temperature matches a target relative mass.

Algorithm:
1. Validate target column and index exist.
2. Read current value at `temp_idx`.
3. Compute `delta_pct = rel_mass - current`.
4. Shift `%` columns by `delta_pct`.
5. Convert to mg shift: `delta_mg = delta_pct * initial_weight / 100`.
6. Shift mg columns (`dm_filtered_mg`, `dm_original_mg`, `m_filtered_mg`) by `delta_mg` if present.

## 6.2 Theoretical mass-loss utility (`get_theoretical_mass_loss`)
1. Require mapped dust composition and initial weight.
2. Convert sample mass `mg -> g`.
3. Call `theoretical_mass_loss_from_composition(...)` with options:
- `fe_stage`
- `pb_mode`
- `zn_fraction`
- `pb_fraction`
4. Return dict with:
- `mass_loss_g`
- `mass_loss_pct`
- `breakdown_g`

## 7. Metadata/Composition Resolution

## 7.1 Metadata row selection (`find_rows_by_rt_id`)
- Regex match on `id` column: `^<experiment_id>(_\d+)?$`
- supports replicate suffixes.

## 7.2 Dust composition mapping (`load_dust_composition`)
- from matched metadata row, read `material`
- find row in dust table where `Dust == material`
- return all composition species columns as dict.

## 8. App-Oriented Modularization Plan
For an application, separate concerns into explicit layers:

1. `InputAdapter`
- file discovery
- parser selection
- metadata/composition retrieval

2. `ProcessingEngine`
- implements Stages A-G exactly
- stateless pure transforms where possible

3. `CalibrationService`
- equalization operations
- correction curve operations

4. `TheoryService`
- theoretical mass-loss computations

5. `OutputStore`
- parquet cache read/write
- run manifests and version tags

6. `PlotService`
- chart generation, no data mutation

## 9. Deterministic Execution Order (for Codex automation)
Use this fixed call order when rebuilding logic:
1. instantiate parser and load raw df
2. rename columns
3. enforce continuous sampling
4. drop irrelevant columns
5. add absolute time
6. convert time to minutes
7. smooth H2O
8. apply correction curve if available
9. rename `dm`
10. pre-filter
11. derive relative mass columns
12. derive first-derivative columns
13. create absolute filtered mass
14. post-filter derivatives
15. reactive cut
16. tail cut
17. gas remap
18. optional equalization
19. optional theoretical overlay metrics
20. persist/cache output

## 10. Failure and Guard Conditions to Preserve
- missing file -> hard error
- missing `dm` column -> hard error
- unknown filter type -> warning and skip/fallback
- invalid filter params -> warning and default constructor fallback
- missing metadata/composition -> warning, keep pipeline running
- equalization on NaN anchor value -> warning and skip
- correction data without required columns -> hard error

## 11. Minimal Integration Example (Pseudo-Workflow)
```text
config = PreparationConfig.load_from_file(config_path)
meta_df = GoogleSheetLoader().load_sheet(sheet_name)
comp_df = read_excel(dust_file)

exp = TGAExperiment(
  file_path=raw_txt_or_csv,
  config=config,
  experiment_id=rt_id,
  df_meta=meta_df,
  df_comp=comp_df,
  df_corr=optional_correction_df,
  save_parquet=True
)

# Optional normalization to material baseline
exp.equalize_on_mass(temp_idx=idx_at_950C, rel_mass=target_mass)

# Optional theory
theory = exp.get_theoretical_mass_loss(fe_stage='Fe', pb_mode='evaporate')

df_processed = exp.df
```

## 12. Notes for Future Refactor
- Current `TGAExperiment` combines IO + transformation + plotting; app version should isolate side effects.
- Cache reuse depends on `config.process_file`; add explicit pipeline versioning in future app to avoid stale cache ambiguity.
- Keep column-name normalization centralized to avoid downstream branching.
