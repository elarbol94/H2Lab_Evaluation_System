"""
Minimaler Ersatz für project_paths.py.

EMI_calculation.py importiert beim Start `ensure_storage_dirs` und
`get_storage_context`, um Standard-Ordner (raw_data/, processed_data/, ...)
zu bestimmen. In H2Lab werden input_dir/output_dir aber bei JEDEM Aufruf
explizit über --input-dir/--output-dir gesetzt (siehe main.py:
fuehre_emi_berechnung_aus). Die Werte hier unten sind daher nur
Platzhalter/Defaults, falls --input-dir/--output-dir mal fehlen sollten -
sie werden im normalen H2Lab-Betrieb nicht verwendet.

Diese Datei muss im selben Ordner liegen wie EMI_calculation.py.

Struktur unterhalb von STORAGE_ROOT (= der EMI-Ordner des Projekts):

    EMI/
      data/
        raw_data/         <- rohe Proben-Ordner (.dat-Dateien)
        processed_data/   <- von EMI_calculation.py erzeugte *_results.parquet
      outputs/             <- z.B. exportierte Diagramme

"data" bündelt also raw_data + processed_data, "outputs" liegt separat
daneben - das entspricht der Ordnerwahl "data" vs. "outputs" im EMI-Ordner.
"""

from pathlib import Path

# Fallback-Root für diesen Projektordner (WeTransfer-Kopie von
# H2Lab_PUB_25_9 Lime Addition in EAFD Recycling). Wird nur benutzt, wenn
# main.py ausnahmsweise ohne --input-dir/--output-dir aufruft - im
# normalen H2Lab-Betrieb werden die Pfade ja explizit übergeben.
STORAGE_ROOT = Path(
    r"C:\Users\marty\Desktop\wetransfer_h2lab_pub_25_9-lime-addition-in-eafd-recycling_2026-07-08_0739"
    r"\H2Lab_PUB_25_9 Lime Addition in EAFD Recycling\EMI"
)


class StorageContext:
    def __init__(self, storage_root):
        self.storage_root = Path(storage_root)
        self.raw_data_dir = self.storage_root / "data" / "raw_data"
        self.parquet_data_dir = self.storage_root / "data" / "processed_data"
        self.outputs_dir = self.storage_root / "outputs"
        self.mode = "H2Lab-Fallback"


def get_storage_context():
    return StorageContext(STORAGE_ROOT)


def ensure_storage_dirs(context=None):
    context = context or get_storage_context()
    for ordner in (context.raw_data_dir, context.parquet_data_dir, context.outputs_dir):
        Path(ordner).mkdir(parents=True, exist_ok=True)
    return context