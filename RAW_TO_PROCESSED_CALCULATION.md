# EMI Raw-to-Processed Calculation

This documents the calculation used in this project to convert raw EMI/HSM data
into processed height and shape data.

## Source Files

- Pipeline wrapper: `EMI/data_preparation/pipeline.py`
- Raw conversion implementation: `EMI/data_preparation/EMI_calculation.py`
- Processed data reader: `EMI/data_preparation/sample_io.py`

The geometry calculation itself is performed by `HSMTools`:

- `HSMTools.data_preparation.sqlite_data_loader.SQLiteDataLoader`
- `HSMTools.data_preparation.image_pre_processor.ImagePreProcessor`
- `HSMTools.data_preparation.image_analyzer.ImageAnalyzer`

## Dependencies

Install these in the Python environment used for raw processing:

```powershell
python -m pip install HSMTools polars pyarrow opencv-python numpy
```

The repository wrapper can use a different interpreter for HSMTools if
`HSMTOOLS_PYTHON` points to one.

## Raw Data Contract

The input directory contains one folder per EMI sample. Each sample folder must
contain at least one `.dat` file. The `.dat` file is treated as a SQLite database.

The required SQLite table is `MeasuredValues`.

Required column:

- `Temperature`

Image reference column, one of:

- `ImagePath`
- `ImageNr`

Optional columns:

- `MVID`, used for ordering when available
- `HeaterTemperature`
- `LightIntensity`

Image references are resolved as follows:

- positive integers become `m_00001`, `m_00002`, etc.
- paths without extensions are searched as-is and with `.Tif`, `.tif`, `.jpg`,
  `.jpeg`, `.png`
- search roots are the sample folder first, then the input root

## Processing Parameters

Defaults in `EMI_calculation.py`:

- Gaussian kernel: `(19, 19)`
- boundary chunk size: `10`
- soft slope threshold: `0.1`
- hard slope threshold: `3.0`
- output suffix: `_results.parquet`
- parquet compression: `zstd`

## Per-Image Calculation

For each measured row, the linked image is processed by `ImageAnalyzer.process`.

1. Load image with OpenCV and convert BGR to grayscale.
2. Preprocess grayscale image:
   - Gaussian blur with the configured kernel.
   - OTSU binary threshold.
   - Invert the thresholded image.
   - Keep only the largest connected component; set other components to
     background.
3. Apply a final binary inverse threshold at gray value `127`.
4. For each image column, find the first white pixel. This is the top boundary
   `top_y[x]`; columns without white pixels become `NaN`.
5. Chunk the top boundary in blocks of `chunk_size` columns and compute mean
   `(x, y)` per chunk, ignoring `NaN` y values.
6. Estimate baseline slope from the first and last chunk.
7. Find left and right sample boundaries from chunk-to-chunk slope offsets:
   - left boundary: slope offset below negative thresholds
   - right boundary: slope offset above positive thresholds
   - hard threshold returns immediately
   - soft threshold requires 5 consecutive chunks
8. Define the top/reference line between left and right boundaries. In the
   installed HSMTools version used here, the boundary y values are then
   overwritten by the mean of `top_y[0:3]` and `top_y[-3:]`.
9. For each x between the boundaries, compute the reference-line y value and
   fill all white threshold pixels above that line into `area_mask`.
10. Compute `sample_height_px` as the maximum vertical distance:

```text
sample_height_px = max(y_line[x] - contour_y[x])
```

11. Compute `sample_area_px` as the number of nonzero pixels in `area_mask`.
12. Compute sample centroid from all nonzero `area_mask` pixels.
13. Build processed shape contour:

```text
contour_x = unshifted_x - center_x_sample
contour_y = top_y
```

Only x is shifted to center the sample laterally.
14. Extract the largest OpenCV contour of `area_mask`.
15. Remove the straight reference-line segment from the contour and compute
   `sample_perimeter_px` as the sum of Euclidean distances between consecutive
   perimeter points.

If an image is missing or analysis fails, the pipeline still writes the
measurement row with temperature metadata and null geometry fields.

## Processed Output Schema

Each sample produces:

```text
<sample_name>_results.parquet
```

Columns:

- `Temperature`
- `HeaterTemperature`
- `LightIntensity`
- `SampleHolderTemperature`
- `OvenTemperature`
- `ImagePath`
- `shifted_xmin`
- `shifted_xmax`
- `sample_area_px`
- `sample_height_px`
- `sample_perimeter_px`
- `center_x_sample`
- `center_y_sample`
- `contour_x`
- `contour_y`

`contour_x` and `contour_y` are list-valued columns.

## Height Normalization Used Downstream

Plotting and feature exports normalize height from the processed parquet:

```text
height_rel_pct = sample_height_px / first_valid_sample_height_px * 100
height_drop_pct = 100 - final_height_rel_pct
```

Interpolated height at a target temperature is linear interpolation of
`height_rel_pct` over `Temperature`.

Shrinkage onset in `EMIExportCalculation.py` is the first linearly interpolated
temperature where:

```text
height_rel_pct <= onset_relative_height_pct
```

The default onset threshold is `95%`.

## Minimal Porting Plan

For another repository with new EMI data:

1. Copy `EMI_calculation.py`.
2. Replace `project_paths` imports with explicit `input_dir` and `output_dir`
   configuration, or copy/adapt `project_paths.py`.
3. Keep the sample-folder and `.dat` raw-data layout described above.
4. Run:

```powershell
python EMI_calculation.py --input-dir "path\to\raw_data" --output-dir "path\to\parquet_data" --force
```

5. Read `<sample>_results.parquet` and use `sample_height_px`, `contour_x`, and
   `contour_y` as the processed height and shape outputs.
