import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from project_paths import ensure_storage_dirs, get_storage_context

try:
    import polars as pl
    import HSMTools.data_preparation.image_analyzer as hsm_image_analyzer_module
    from HSMTools.data_preparation.image_analyzer import ImageAnalyzer
    from HSMTools.data_preparation.image_pre_processor import ImagePreProcessor
    from HSMTools.data_preparation.sqlite_data_loader import SQLiteDataLoader
except ImportError as exc:
    raise RuntimeError(
        "Missing dependency for HSMTools pipeline. Ensure this interpreter has HSMTools and polars installed.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install command: {sys.executable} -m pip install HSMTools polars"
    ) from exc

STORAGE_CONTEXT = get_storage_context()
DEFAULT_INPUT_DIR = STORAGE_CONTEXT.raw_data_dir
DEFAULT_OUTPUT_DIR = STORAGE_CONTEXT.parquet_data_dir
LEGACY_ALIAS_FILE = STORAGE_CONTEXT.outputs_dir / "sample_aliases.json"
ALIAS_INDEX_FILE = "sample_alias_index.json"
RESULT_SUFFIX = "_results.parquet"


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _discover_samples_with_dat(input_dir):
    discovered = []
    if not input_dir.exists():
        return discovered

    for sample_dir in sorted(path for path in input_dir.iterdir() if path.is_dir()):
        dat_files = sorted(path for path in sample_dir.rglob("*.dat") if path.is_file())
        if not dat_files:
            continue
        discovered.append((sample_dir.name, sample_dir, dat_files[0], dat_files[1:]))
    return discovered


def _coerce_temperature(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _coerce_optional_float(value):
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _normalize_from_material(material_value):
    text = str(material_value or "").strip()
    match = re.search(r"(EAFD)\s*[_-]?\s*(\d+)\s*[_ ]?\s*(\d+(?:[.,]\d+)?)\s*%", text, re.IGNORECASE)
    if not match:
        return None

    eafd_prefix = match.group(1).upper()
    eafd_number = int(match.group(2))
    percent_text = match.group(3).replace(",", ".")
    percent_value = float(percent_text)
    percent_fmt = str(int(percent_value)) if percent_value.is_integer() else str(percent_value)
    return f"{eafd_prefix}{eafd_number}_{percent_fmt}%"


def _sanitize_name(name):
    value = str(name or "").strip()
    return value.replace(" ", "_")


def _derive_canonical_sample_name(sample_name, tables):
    meta_info = tables.get("MeasurementSeriesMetaInfo")
    if meta_info is None or getattr(meta_info, "height", 0) <= 0:
        return _sanitize_name(sample_name)

    material_value = None
    if "Material" in meta_info.columns:
        material_column = meta_info["Material"].to_list()
        if material_column:
            material_value = material_column[0]

    canonical_from_material = _normalize_from_material(material_value)
    if canonical_from_material:
        return _sanitize_name(canonical_from_material)

    if "Samplename" in meta_info.columns:
        sample_column = meta_info["Samplename"].to_list()
        if sample_column:
            sample_value = _sanitize_name(sample_column[0])
            if sample_value:
                return sample_value

    return _sanitize_name(sample_name)


def _ensure_unique_name(base_name, used_names):
    candidate = base_name
    index = 2
    while candidate in used_names:
        candidate = f"{base_name}_{index}"
        index += 1
    return candidate


def _normalize_image_reference(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        if value <= 0:
            return None
        return f"m_{value:05d}"

    if isinstance(value, float):
        if not math.isfinite(value) or value <= 0.0:
            return None
        as_int = int(round(value))
        if abs(value - as_int) < 1e-9:
            return f"m_{as_int:05d}"
        return str(value).strip()

    text = str(value).strip()
    if not text or text in {"0", "0.0"}:
        return None
    return text


def _resolve_image_path(sample_dir, input_dir, image_reference):
    raw_reference = str(image_reference).strip()
    if not raw_reference:
        return None

    direct_path = Path(raw_reference)
    if direct_path.is_absolute() and direct_path.exists():
        return direct_path

    candidate_names = [raw_reference]
    if direct_path.suffix == "":
        candidate_names.extend(
            [
                f"{raw_reference}.Tif",
                f"{raw_reference}.tif",
                f"{raw_reference}.jpg",
                f"{raw_reference}.jpeg",
                f"{raw_reference}.png",
            ]
        )

    search_bases = [sample_dir, input_dir]
    for base in search_bases:
        for candidate_name in candidate_names:
            candidate_path = base / candidate_name
            if candidate_path.exists():
                return candidate_path
    return None


def _fill_missing_heater_temperatures(rows):
    if not rows:
        return False

    ordered_indices = sorted(range(len(rows)), key=lambda idx: rows[idx]["order_key"])
    ordered_values = [rows[idx]["HeaterTemperature"] for idx in ordered_indices]
    known_positions = [pos for pos, value in enumerate(ordered_values) if value is not None]
    if not known_positions:
        return False

    first_known = known_positions[0]
    last_known = known_positions[-1]

    for pos in range(0, first_known):
        ordered_values[pos] = ordered_values[first_known]
    for pos in range(last_known + 1, len(ordered_values)):
        ordered_values[pos] = ordered_values[last_known]

    for left_pos, right_pos in zip(known_positions, known_positions[1:]):
        if right_pos - left_pos <= 1:
            continue
        left_value = ordered_values[left_pos]
        right_value = ordered_values[right_pos]
        span = right_pos - left_pos
        for pos in range(left_pos + 1, right_pos):
            ratio = (pos - left_pos) / span
            ordered_values[pos] = left_value + (right_value - left_value) * ratio

    for pos, row_idx in enumerate(ordered_indices):
        rows[row_idx]["HeaterTemperature"] = float(ordered_values[pos])
    return True


def _extract_measurement_rows(trial_data):
    measured_values = trial_data.get("MeasuredValues")
    if measured_values is None or measured_values.height == 0:
        return []
    if "Temperature" not in measured_values.columns:
        return []

    temperatures = measured_values["Temperature"].to_list()
    if "ImagePath" in measured_values.columns:
        image_refs = measured_values["ImagePath"].to_list()
    elif "ImageNr" in measured_values.columns:
        image_refs = measured_values["ImageNr"].to_list()
    else:
        image_refs = [None] * len(temperatures)
    mvid_values = measured_values["MVID"].to_list() if "MVID" in measured_values.columns else []
    heater_values = (
        measured_values["HeaterTemperature"].to_list()
        if "HeaterTemperature" in measured_values.columns
        else []
    )
    light_values = (
        measured_values["LightIntensity"].to_list()
        if "LightIntensity" in measured_values.columns
        else []
    )

    rows = []
    for source_index, (temperature, image_ref) in enumerate(zip(temperatures, image_refs)):
        temperature_value = _coerce_temperature(temperature)
        image_reference = _normalize_image_reference(image_ref)
        if temperature_value is None:
            continue

        mvid = mvid_values[source_index] if source_index < len(mvid_values) else None
        mvid_numeric = _coerce_optional_float(mvid)
        if mvid_numeric is None:
            order_key = (1, source_index)
        else:
            order_key = (0, mvid_numeric, source_index)

        heater_raw = heater_values[source_index] if source_index < len(heater_values) else None
        light_raw = light_values[source_index] if source_index < len(light_values) else None
        rows.append(
            {
                "Temperature": float(temperature_value),
                "HeaterTemperature": _coerce_optional_float(heater_raw),
                "LightIntensity": _coerce_optional_float(light_raw),
                "ImageReference": image_reference,
                "order_key": order_key,
            }
        )

    _fill_missing_heater_temperatures(rows)
    return rows


def _measurement_only_result_row(measurement_row, source_image_path):
    return {
        "Temperature": float(measurement_row["Temperature"]),
        "HeaterTemperature": measurement_row["HeaterTemperature"],
        "LightIntensity": measurement_row["LightIntensity"],
        "SampleHolderTemperature": float(measurement_row["Temperature"]),
        "OvenTemperature": measurement_row["HeaterTemperature"],
        "ImagePath": str(source_image_path) if source_image_path is not None else None,
        "shifted_xmin": None,
        "shifted_xmax": None,
        "sample_area_px": None,
        "sample_height_px": None,
        "sample_perimeter_px": None,
        "center_x_sample": None,
        "center_y_sample": None,
        "contour_x": [],
        "contour_y": [],
    }


def _mapping_failure_reason(trial_data):
    measured_values = trial_data.get("MeasuredValues")
    if measured_values is None:
        return "Reason: MeasuredValues table is missing in the .dat file."

    if getattr(measured_values, "height", 0) == 0:
        return (
            "Reason: MeasuredValues table exists but has 0 rows "
            "(likely incomplete EMI export)."
        )

    columns = set(getattr(measured_values, "columns", []))
    if "Temperature" not in columns:
        return "Reason: MeasuredValues has no 'Temperature' column."

    if "ImagePath" not in columns and "ImageNr" not in columns:
        return "Reason: MeasuredValues has no 'ImagePath' or 'ImageNr' column."

    image_column = "ImagePath" if "ImagePath" in columns else "ImageNr"
    try:
        temperatures = measured_values["Temperature"].to_list()
        image_refs = measured_values[image_column].to_list()
    except Exception:
        return "Reason: failed to read Temperature/Image columns from MeasuredValues."

    for temperature, image_ref in zip(temperatures, image_refs):
        if _coerce_temperature(temperature) is None:
            continue
        if _normalize_image_reference(image_ref) is None:
            continue
        return "Reason: mapping failed during extraction despite valid source columns."

    return (
        "Reason: Temperature/Image values are present but all rows are empty or invalid "
        "(e.g. null/0 image references)."
    )


def _result_row(analysis_result, measurement_row, source_image_path):
    row = _measurement_only_result_row(measurement_row, source_image_path)

    contour_coordinates = analysis_result.get("contour_coordinates")
    contour_x = []
    contour_y = []
    if isinstance(contour_coordinates, (list, tuple)) and len(contour_coordinates) >= 2:
        raw_x, raw_y = contour_coordinates[0], contour_coordinates[1]
        if isinstance(raw_x, (list, tuple)) and isinstance(raw_y, (list, tuple)):
            max_len = min(len(raw_x), len(raw_y))
            for x_value, y_value in zip(raw_x[:max_len], raw_y[:max_len]):
                try:
                    x_num = float(x_value)
                    y_num = float(y_value)
                except (TypeError, ValueError):
                    continue
                if not (math.isfinite(x_num) and math.isfinite(y_num)):
                    continue
                contour_x.append(x_num)
                contour_y.append(y_num)

    row.update(
        {
            "shifted_xmin": analysis_result.get("shifted_xmin"),
            "shifted_xmax": analysis_result.get("shifted_xmax"),
            "sample_area_px": analysis_result.get("sample_area_px"),
            "sample_height_px": analysis_result.get("sample_height_px"),
            "sample_perimeter_px": analysis_result.get("sample_perimeter_px"),
            "center_x_sample": analysis_result.get("center_x_sample"),
            "center_y_sample": analysis_result.get("center_y_sample"),
            "contour_x": contour_x,
            "contour_y": contour_y,
        }
    )
    return row


def _load_legacy_aliases(alias_file):
    if not alias_file.exists():
        return {}
    try:
        payload = json.loads(alias_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    alias_map = payload.get("alias_to_canonical")
    if not isinstance(alias_map, dict):
        return {}
    return {str(alias): str(canonical) for alias, canonical in alias_map.items() if alias and canonical}


def _load_existing_alias_index(alias_path):
    if not alias_path.exists():
        return {}, []
    try:
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, []
    if not isinstance(payload, dict):
        return {}, []

    aliases_raw = payload.get("aliases")
    aliases = (
        {str(alias): str(canonical) for alias, canonical in aliases_raw.items() if alias and canonical}
        if isinstance(aliases_raw, dict)
        else {}
    )
    canonical_raw = payload.get("canonical_samples")
    canonical_samples = (
        [str(name) for name in canonical_raw if name]
        if isinstance(canonical_raw, list)
        else []
    )
    return aliases, canonical_samples


def _discover_existing_raw_samples(output_dir):
    suffix_length = len(RESULT_SUFFIX)
    samples = set()
    for parquet_file in Path(output_dir).glob(f"*{RESULT_SUFFIX}"):
        file_name = parquet_file.name
        samples.add(file_name[:-suffix_length])
    return sorted(samples)
def _write_alias_index(output_dir, processed_samples):
    output_dir.mkdir(parents=True, exist_ok=True)
    alias_path = output_dir / ALIAS_INDEX_FILE
    _existing_aliases, existing_samples = _load_existing_alias_index(alias_path)
    discovered_samples = _discover_existing_raw_samples(output_dir)
    raw_samples = sorted(
        set(str(name) for name in processed_samples)
        | set(existing_samples)
        | set(discovered_samples)
    )
    aliases = {sample: sample for sample in raw_samples}
    payload = {
        "generated_at_utc": _utc_now(),
        "aliases": dict(sorted(aliases.items())),
        "canonical_samples": raw_samples,
    }
    try:
        alias_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote alias index: {alias_path}")
    except OSError as exc:
        print(f"Warning: failed to write alias index {alias_path}: {exc}")
def _raw_samples_in_input(input_dir: Path):
    if not input_dir.exists():
        return []
    return sorted(path.name for path in input_dir.iterdir() if path.is_dir())
def migrate_canonical_to_raw_names(input_dir=DEFAULT_INPUT_DIR, output_dir=DEFAULT_OUTPUT_DIR):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    alias_path = output_dir / ALIAS_INDEX_FILE
    aliases, _canonical = _load_existing_alias_index(alias_path)
    raw_samples = _raw_samples_in_input(input_dir)
    raw_set = set(raw_samples)
    if not raw_samples:
        print(f"No raw sample folders found under {input_dir.resolve()}")
        _write_alias_index(output_dir, [])
        return
    suffix_length = len(RESULT_SUFFIX)
    renamed = 0
    skipped_conflict = 0
    skipped_unmapped = 0
    for parquet_file in sorted(output_dir.glob(f"*{RESULT_SUFFIX}")):
        source_name = parquet_file.name[:-suffix_length]
        if source_name in raw_set:
            continue
        mapped_raw = [alias for alias, canonical in aliases.items() if canonical == source_name and alias in raw_set]
        if len(mapped_raw) != 1:
            skipped_unmapped += 1
            print(f"[Migration] Skipping {source_name}: no unique raw-folder mapping found.")
            continue
        target_name = mapped_raw[0]
        target_path = output_dir / f"{target_name}{RESULT_SUFFIX}"
        if target_path.exists():
            skipped_conflict += 1
            print(f"[Migration] Skipping {source_name}: target exists ({target_path.name}).")
            continue
        try:
            parquet_file.rename(target_path)
            renamed += 1
            print(f"[Migration] Renamed {parquet_file.name} -> {target_path.name}")
        except OSError as exc:
            skipped_conflict += 1
            print(f"[Migration] Failed rename {parquet_file.name} -> {target_path.name}: {exc}")
    _write_alias_index(output_dir, raw_samples)
    print(
        "[Migration] Completed | "
        f"renamed: {renamed} | "
        f"unmapped skipped: {skipped_unmapped} | "
        f"conflict skipped: {skipped_conflict}"
    )
def process_samples(
    input_dir=DEFAULT_INPUT_DIR,
    output_dir=DEFAULT_OUTPUT_DIR,
    *,
    force=False,
    samples: list[str] | None = None,
    gaussian_filter_size=(19, 19),
    chunk_size=10,
    threshold_soft=0.1,
    threshold_hard=3.0,
):
    ensure_storage_dirs()
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    discovered = _discover_samples_with_dat(input_dir)
    if samples:
        sample_set = {str(sample).strip() for sample in samples if str(sample).strip()}
        discovered = [item for item in discovered if item[0] in sample_set]
    if not discovered:
        print(f"No .dat files found under {input_dir.resolve()}")
        _write_alias_index(output_dir, [])
        return

    loader = SQLiteDataLoader()
    preprocessor = ImagePreProcessor(gaussian_filter_size=gaussian_filter_size)
    # HSMTools 0.1.3 calls plt.show() during analysis; disable it for batch pipeline execution.
    hsm_image_analyzer_module.plt.show = lambda *args, **kwargs: None
    analyzer = ImageAnalyzer(
        chunk_size=chunk_size,
        threshold_soft=threshold_soft,
        threshold_hard=threshold_hard,
        preprocessor=preprocessor,
    )

    processed_samples = []
    for sample_name, sample_dir, dat_file, extra_dat_files in discovered:
        if extra_dat_files:
            skipped = "; ".join(str(path) for path in extra_dat_files)
            print(f"Warning: sample '{sample_name}' has multiple .dat files. Using '{dat_file}', skipping: {skipped}")

        tables = loader.load_tables_from_file(str(dat_file))
        if not tables:
            print(f"Skipping '{sample_name}': could not load tables from {dat_file}.")
            continue
        if "MeasuredValues" not in tables:
            print(f"Skipping '{sample_name}': missing MeasuredValues table in {dat_file}.")
            continue
        target_sample_name = _sanitize_name(sample_name)
        target_parquet = output_dir / f"{target_sample_name}{RESULT_SUFFIX}"
        if target_parquet.exists() and not force:
            print(f"Skipping '{sample_name}': parquet already exists at {target_parquet}. Use --force to overwrite.")
            processed_samples.append(target_sample_name)
            continue

        trial_data = dict(tables)
        trial_data["directory"] = str(sample_dir)
        measurement_rows = _extract_measurement_rows(trial_data)
        if not measurement_rows:
            print(
                f"Skipping '{sample_name}': no valid measured temperature rows found. "
                f"{_mapping_failure_reason(trial_data)}"
            )
            continue

        if all(row["HeaterTemperature"] is None for row in measurement_rows):
            print(
                f"Warning: sample '{sample_name}' has no valid HeaterTemperature values; "
                "HeaterTemperature/OvenTemperature will be null."
            )

        rows = []
        for measurement_row in measurement_rows:
            image_reference = measurement_row["ImageReference"]
            image_path = _resolve_image_path(sample_dir, input_dir, image_reference)

            result_row = _measurement_only_result_row(measurement_row, image_path)
            if image_reference is not None and image_path is None:
                print(f"Warning: image not found for sample '{sample_name}': {image_reference}")

            if image_path is not None:
                try:
                    analysis_result = analyzer.process(str(image_path))
                except Exception as exc:
                    print(f"Warning: analyzer failed for {image_path}: {exc}")
                    analysis_result = None

                if analysis_result is not None:
                    result_row = _result_row(analysis_result, measurement_row, image_path)

            rows.append(result_row)

        if not rows:
            print(f"Skipping '{sample_name}': no valid measured temperature rows produced results.")
            continue

        dataframe = pl.DataFrame(rows)
        try:
            dataframe.write_parquet(target_parquet, compression="zstd")
        except OSError as exc:
            print(f"Warning: failed to write parquet for '{sample_name}' at {target_parquet}: {exc}")
            continue
        processed_samples.append(target_sample_name)
        print(f"Wrote {target_parquet} ({len(rows)} rows) from sample '{sample_name}'")

    _write_alias_index(output_dir, processed_samples)


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare HSM data using HSMTools and save *_results.parquet files.")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--samples",
        nargs="+",
        default=None,
        help="Optional sample folder names to process (space-separated).",
    )
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--threshold-soft", type=float, default=0.1)
    parser.add_argument("--threshold-hard", type=float, default=3.0)
    parser.add_argument("--gaussian-kernel", type=int, nargs=2, default=(19, 19), metavar=("WIDTH", "HEIGHT"))
    parser.add_argument(
        "--migrate-raw-names",
        action="store_true",
        help="One-time: rename existing canonical parquet files to raw-folder names.",
    )
    return parser.parse_args()


def main():
    storage = ensure_storage_dirs(get_storage_context())
    print(f"Storage mode: {storage.mode} | root: {storage.storage_root}")
    args = parse_args()
    if args.migrate_raw_names:
        migrate_canonical_to_raw_names(
            input_dir=args.input_dir or storage.raw_data_dir,
            output_dir=args.output_dir or storage.parquet_data_dir,
        )
        return
    process_samples(
        input_dir=args.input_dir or storage.raw_data_dir,
        output_dir=args.output_dir or storage.parquet_data_dir,
        force=args.force,
        samples=args.samples,
        gaussian_filter_size=tuple(args.gaussian_kernel),
        chunk_size=args.chunk_size,
        threshold_soft=args.threshold_soft,
        threshold_hard=args.threshold_hard,
    )


if __name__ == "__main__":
    main()






