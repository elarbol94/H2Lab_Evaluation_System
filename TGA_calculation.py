"""
TGA_calculation.py

Rohdaten-zu-Processed-Berechnung fuer TGA-Versuche - im SELBEN CLI-Vertrag
wie EMI_calculation.py (siehe main.py: finde_berechnung_skript() /
fuehre_berechnung_aus()), damit der "Berechnen"-Button in main.py 1:1
genauso fuer TGA funktioniert wie fuer EMI:

    python TGA_calculation.py --input-dir <TGA/data/raw_data-Ordner> \
                               --output-dir <TGA/data/processed_data-Ordner> \
                               --samples <rohdatei1.txt> <rohdatei2.txt> ... \
                               [--force]

main.py ruft dieses Skript automatisch mit genau diesen Argumenten auf,
sobald in der GUI "Berechnen" gedrueckt wird - siehe README-Hinweis unten
zur Ablage dieser Datei.

Ordnerprinzip (1:1 wie bei EMI, siehe RAW_TO_PROCESSED_CALCULATION.md):

    TGA/data/raw_data/<Folgenummer>_<Versuchsname>.txt
        <- Rohdaten, FLACH (kein Unterordner pro Versuch, anders als EMI -
           jede .txt-Datei direkt in raw_data IST bereits ein Versuch)
    TGA/data/processed_data/<Folgenummer>_<Versuchsname>_results.parquet
        <- wird von diesem Skript erzeugt

main.py verwendet os.path.splitext(eintrag)[0] als Versuchsnamen (also den
KOMPLETTEN Dateistamm inkl. Folgenummer, z.B. "1986_RT1") und erwartet die
Ausgabedatei "<sanitierter_kompletter_dateistamm>_results.parquet" (siehe
main.py: _sanitiere_versuchsnamen()). Das wird hier ueber _sanitize_name()
exakt genauso gehandhabt.

Verarbeitungsschritte pro Rohdatei (Portierung der Kernrechnung aus
helper/TGA.py: TGAFile/TGAExperiment - OHNE die dortigen Abhaengigkeiten
zu Google Sheets, Staubzusammensetzung oder YAML-Filterkonfiguration, da
main.py fuer die Ergebnisse-Anzeige nur die reinen Masse-/Temperaturkurven
braucht, siehe main.py: zeichne_ergebnis_plot_tga()):

    1. Kopfzeilen einlesen (u.a. "# Weight: <mg> mg") -> Startgewicht.
    2. CSV ab der Zeile, die mit "Time(s)" beginnt, einlesen.
    3. Spalten TOLERANT per Teilstring erkennen (nicht per exaktem
       Vergleich), da das Geraet das Grad-Zeichen in "Temperature(...C)"
       je nach Encoding leicht unterschiedlich exportiert.
    4. Berechnen:
         time_min              = Time(s) / 60
         dm_original_pct       = (Delta_m_mg + Startgewicht) / Startgewicht * 100
         dm_filtered_pct       = wie oben, aber geglaettete Delta_m-Kurve
                                  (Savitzky-Golay, falls scipy installiert
                                  ist, sonst gleitender Mittelwert als
                                  Fallback)
         dmdt_original_pctmin  = d(dm_original_pct)/d(time_min)
         dmdt_filtered_pctmin  = d(dm_filtered_pct)/d(time_min)
    5. Ergebnis als "<Versuch>_results.parquet" schreiben mit den Spalten,
       die main.py: zeichne_ergebnis_plot_tga() erwartet:
           temperature_C, dm_filtered_pct, dmdt_filtered_pctmin
       (zusaetzlich: time_min, dm_original_pct, dmdt_original_pctmin, fuer
       spaetere Auswertungen/Debugging).

Fehlt eine Rohdatei oder schlaegt die Verarbeitung fehl, wird NUR dieser
Versuch uebersprungen (mit Warnung auf stdout) - nicht die ganze
Berechnung abgebrochen. Gleiches Verhalten wie in EMI_calculation.py.

Ablage: main.py sucht dieses Skript in dieser Reihenfolge (siehe
finde_berechnung_skript() in main.py):
    1) BERECHNUNGS_SKRIPT_PFADE["TGA"] in main.py (falls dort ein voller
       Pfad eingetragen wurde)
    2) direkt neben main.py
    3) <main.py-Ordner>/TGA/data_preparation/TGA_calculation.py
Am einfachsten: diese Datei direkt neben main.py legen (Punkt 2) - dann
wird sie automatisch gefunden.

Abhaengigkeiten: pandas, numpy, pyarrow (fuer .to_parquet). scipy ist
OPTIONAL (bessere Glaettung); ist es nicht installiert, wird automatisch
auf einen gleitenden Mittelwert zurueckgefallen.
    python -m pip install pandas numpy pyarrow
    python -m pip install scipy   # optional, fuer bessere Glaettung
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# project_paths.py ist optional - main.py uebergibt --input-dir/--output-dir
# bei JEDEM Aufruf explizit (siehe RAW_TO_PROCESSED_CALCULATION.md), die
# Defaults hier greifen daher nur, wenn man das Skript manuell ohne diese
# Argumente aufruft.
try:
    from project_paths import ensure_storage_dirs, get_storage_context
except ImportError:
    ensure_storage_dirs = None
    get_storage_context = None

try:
    from scipy.signal import savgol_filter
    _HAT_SCIPY = True
except ImportError:
    _HAT_SCIPY = False

if get_storage_context is not None:
    STORAGE_CONTEXT = get_storage_context("TGA")
    DEFAULT_INPUT_DIR = STORAGE_CONTEXT.raw_data_dir
    DEFAULT_OUTPUT_DIR = STORAGE_CONTEXT.parquet_data_dir
else:
    STORAGE_CONTEXT = None
    DEFAULT_INPUT_DIR = Path("TGA/data/raw_data")
    DEFAULT_OUTPUT_DIR = Path("TGA/data/processed_data")

RESULT_SUFFIX = "_results.parquet"
WEIGHT_PATTERN = re.compile(r"Weight:\s*([0-9]+\.?[0-9]*)\s*mg", re.IGNORECASE)


def _sanitize_name(name):
    """Muss ident zu main.py:_sanitiere_versuchsnamen() sein, damit main.py
    dieselbe Ausgabedatei vorhersagen kann (Leerzeichen -> _)."""
    return str(name or "").strip().replace(" ", "_")


def _versuchsname_aus_dateiname(dateiname):
    """
    Extrahiert den am Geraet eingegebenen Versuchsnamen aus
    '<Folgenummer>_<Name>.txt' (z.B. '1986_RT1.txt' -> 'RT1'). Analog zu
    main.py:_tga_versuchsname_fuer_sheet() - NUR fuer eine evtl. spaetere
    Sheet-/Material-Zuordnung gedacht. Fuer den Ausgabedateinamen wird
    IMMER der komplette, sanitierte Dateistamm verwendet (siehe
    _sanitize_name() oben), NICHT dieser gekuerzte Name.
    """
    stem = Path(dateiname).stem
    match = re.match(r"^\d+_(.+)$", stem)
    return match.group(1) if match else stem


def _discover_raw_files(input_dir):
    if not input_dir.exists():
        return []
    return sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt")


def _find_column(columns, *teilstrings):
    """Findet die erste Spalte, deren (kleingeschriebener) Name ALLE
    'teilstrings' enthaelt - toleranter als ein exakter Vergleich, da das
    Geraet z.B. das Grad-Zeichen je nach Encoding unterschiedlich
    exportiert ("Temperature(°C)" vs. Mojibake-Varianten)."""
    for col in columns:
        lowered = str(col).lower()
        if all(s.lower() in lowered for s in teilstrings):
            return col
    return None


def _get_weight_mg(path):
    """Liest das Startgewicht aus der Kommentarzeile '# Weight: <mg> mg'
    (nur in den fuehrenden '#'-Kopfzeilen gesucht)."""
    with open(path, encoding="ISO-8859-1") as f:
        for line in f:
            if not line.startswith("#"):
                break
            match = WEIGHT_PATTERN.search(line)
            if match:
                return float(match.group(1))
    return None


def _read_raw_dataframe(path):
    """Liest den CSV-Messwertblock ein - beginnend bei der Zeile, die mit
    'Time(s)' startet (davor stehen nur '#'-Kommentarzeilen mit
    Export-/Geraete-Metadaten)."""
    header_zeile = None
    with open(path, encoding="ISO-8859-1") as f:
        for i, line in enumerate(f):
            if line.startswith("Time(s)"):
                header_zeile = i
                break
    if header_zeile is None:
        raise ValueError(f"Konnte Header-Zeile 'Time(s)' nicht finden in {path}")

    return pd.read_csv(path, delimiter=",", header=header_zeile, encoding="unicode_escape")


def _smooth(werte):
    """Glaettung fuer die 'filtered'-Kurven: Savitzky-Golay (scipy), sonst
    Fallback auf einen zentrierten gleitenden Mittelwert."""
    n = len(werte)
    if n < 5:
        return werte.copy()
    if _HAT_SCIPY:
        fenster = min(51, n if n % 2 == 1 else n - 1)
        fenster = max(fenster, 5)
        polyorder = min(3, fenster - 1)
        try:
            return savgol_filter(werte, window_length=fenster, polyorder=polyorder)
        except Exception:
            pass
    fenster = min(21, n)
    return pd.Series(werte).rolling(window=fenster, center=True, min_periods=1).mean().to_numpy()


def _process_file(path):
    initial_weight_mg = _get_weight_mg(path)
    if not initial_weight_mg:
        raise ValueError(f"Kein Startgewicht ('# Weight: X mg') in {path.name} gefunden.")

    raw = _read_raw_dataframe(path)

    zeit_spalte = _find_column(raw.columns, "time")
    temp_spalte = _find_column(raw.columns, "temperat")
    dm_spalte = _find_column(raw.columns, "delta", "m")

    if zeit_spalte is None:
        raise ValueError(f"Zeit-Spalte nicht gefunden in {path.name} (Spalten: {list(raw.columns)})")
    if temp_spalte is None:
        raise ValueError(f"Temperatur-Spalte nicht gefunden in {path.name} (Spalten: {list(raw.columns)})")
    if dm_spalte is None:
        raise ValueError(f"'Delta m'-Spalte nicht gefunden in {path.name} (Spalten: {list(raw.columns)})")

    df = pd.DataFrame({
        "time_min": raw[zeit_spalte].astype(float) / 60.0,
        "temperature_C": raw[temp_spalte].astype(float),
        "dm_original_mg": raw[dm_spalte].astype(float),
    }).dropna(subset=["time_min", "temperature_C", "dm_original_mg"])

    if df.empty:
        raise ValueError(f"Keine gueltigen Messwertzeilen in {path.name}.")

    df = df.sort_values("time_min").reset_index(drop=True)

    df["dm_filtered_mg"] = _smooth(df["dm_original_mg"].to_numpy())

    df["dm_original_pct"] = (df["dm_original_mg"] + initial_weight_mg) / initial_weight_mg * 100.0
    df["dm_filtered_pct"] = (df["dm_filtered_mg"] + initial_weight_mg) / initial_weight_mg * 100.0

    df["dmdt_original_pctmin"] = (df["dm_original_pct"].diff() / df["time_min"].diff()).bfill()
    df["dmdt_filtered_pctmin"] = (df["dm_filtered_pct"].diff() / df["time_min"].diff()).bfill()
    df["m_filtered_mg"] = initial_weight_mg + df["dm_filtered_mg"]
    df["dmdt_filtered_mgmin"] = (df["dm_filtered_mg"].diff() / df["time_min"].diff()).bfill()

    return df[[
        "temperature_C", "time_min",
        "dm_original_pct", "dm_filtered_pct", "m_filtered_mg",
        "dmdt_original_pctmin", "dmdt_filtered_pctmin", "dmdt_filtered_mgmin",
    ]]


def process_samples(input_dir, output_dir, *, force=False, samples=None):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    discovered = _discover_raw_files(input_dir)
    if samples:
        # main.py uebergibt bei --samples die kompletten raw_data-Eintraege
        # (z.B. "1986_RT1.txt"), toleriert hier aber auch Namen ohne
        # Endung.
        gewuenscht = {Path(str(s).strip()).stem for s in samples if str(s).strip()}
        discovered = [p for p in discovered if p.stem in gewuenscht]

    if not discovered:
        print(f"Keine .txt-Rohdateien unter {input_dir.resolve()} gefunden.")
        return

    for raw_path in discovered:
        versuch_name = _sanitize_name(raw_path.stem)
        target_parquet = output_dir / f"{versuch_name}{RESULT_SUFFIX}"
        if target_parquet.exists() and not force:
            print(
                f"Ueberspringe '{raw_path.name}': Parquet existiert bereits "
                f"({target_parquet}). --force zum Ueberschreiben."
            )
            continue

        try:
            df = _process_file(raw_path)
        except Exception as exc:
            print(f"Ueberspringe '{raw_path.name}': {exc}")
            continue

        try:
            df.to_parquet(target_parquet, compression="zstd")
        except OSError as exc:
            print(f"Warnung: Konnte Parquet fuer '{raw_path.name}' nicht schreiben ({target_parquet}): {exc}")
            continue

        print(f"Geschrieben: {target_parquet} ({len(df)} Zeilen) aus '{raw_path.name}'")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bereitet rohe TGA-.txt-Dateien auf und schreibt *_results.parquet."
    )
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--samples",
        nargs="+",
        default=None,
        help="Optionale Rohdatei-Namen (mit oder ohne .txt), space-separated.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_dir = args.input_dir or DEFAULT_INPUT_DIR
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR

    if STORAGE_CONTEXT is not None and ensure_storage_dirs is not None:
        print(f"Storage mode: {STORAGE_CONTEXT.mode} | root: {STORAGE_CONTEXT.storage_root}")
        ensure_storage_dirs(STORAGE_CONTEXT)

    process_samples(input_dir, output_dir, force=args.force, samples=args.samples)


if __name__ == "__main__":
    main()
