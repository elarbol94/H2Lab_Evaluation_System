"""
Minimaler Ersatz für project_paths.py.

EMI_calculation.py / TGA_calculation.py importieren beim Start
`ensure_storage_dirs` und `get_storage_context`, um Standard-Ordner zu
bestimmen. In H2Lab werden input_dir/output_dir aber bei JEDEM Aufruf
explizit über --input-dir/--output-dir gesetzt (siehe main.py).
Die Werte hier sind daher nur Platzhalter/Defaults.

Struktur pro Methode unterhalb von PROJECT_BASE:

    <Methode>/
      data/
        raw_data/         <- Rohdaten (.dat-Ordner bzw. .txt-Dateien)
        processed_data/   <- *_results.parquet
      outputs/
        diagramm/         <- exportierte Diagramm-Bilder (später befüllt)

Hinweis: "diagramm" (deutsch, doppel-m) - MUSS ident zu main.py sein, das
überall "outputs/diagramm" anlegt/erwartet (siehe main.py:
erstelle_versuchs_struktur() und diagramm_ordner_fuer()). Ein einzelnes
"diagram" hier würde main.py nie finden und einen zusätzlichen,
funktionslosen Ordner erzeugen.
"""

from pathlib import Path

PROJECT_BASE = Path(
    r"C:\Users\marty\Desktop\wetransfer_h2lab_pub_25_9-lime-addition-in-eafd-recycling_2026-07-08_0739"
    r"\H2Lab_PUB_25_9 Lime Addition in EAFD Recycling"
)

METHOD_STORAGE_ROOTS = {
    "EMI": PROJECT_BASE / "EMI",
    "TGA": PROJECT_BASE / "TGA",
}


class StorageContext:
    def __init__(self, storage_root, method="EMI"):
        self.method = str(method or "EMI").upper()
        self.storage_root = Path(storage_root)
        self.raw_data_dir = self.storage_root / "data" / "raw_data"
        self.parquet_data_dir = self.storage_root / "data" / "processed_data"
        self.outputs_dir = self.storage_root / "outputs" / "diagramm"
        self.mode = f"H2Lab-Fallback-{self.method}"


def get_storage_context(method="EMI"):
    methode = str(method or "EMI").upper()
    root = METHOD_STORAGE_ROOTS.get(methode, METHOD_STORAGE_ROOTS["EMI"])
    return StorageContext(root, methode)


def ensure_storage_dirs(context=None):
    context = context or get_storage_context()
    for ordner in (context.raw_data_dir, context.parquet_data_dir, context.outputs_dir):
        Path(ordner).mkdir(parents=True, exist_ok=True)
    return context