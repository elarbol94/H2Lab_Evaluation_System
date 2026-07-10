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
"""

from pathlib import Path


class StorageContext:
    def __init__(self, storage_root):
        self.storage_root = Path(storage_root)
        self.raw_data_dir = self.storage_root / "raw_data"
        self.parquet_data_dir = self.storage_root / "processed_data"
        self.outputs_dir = self.storage_root / "outputs"
        self.mode = "H2Lab-Fallback"


def get_storage_context():
    # Fallback-Root: Ordner, in dem dieses Skript liegt.
    # Wird nur benutzt, wenn main.py ausnahmsweise ohne --input-dir/--output-dir aufruft.
    return StorageContext(Path(__file__).resolve().parent)


def ensure_storage_dirs(context=None):
    context = context or get_storage_context()
    for ordner in (context.raw_data_dir, context.parquet_data_dir, context.outputs_dir):
        Path(ordner).mkdir(parents=True, exist_ok=True)
    return context