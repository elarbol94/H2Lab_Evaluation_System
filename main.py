import customtkinter as ctk
import os
import re
import sys
import subprocess
import threading
import json
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
import shutil
from tkinter import messagebox, colorchooser
from datetime import datetime

# ============================================================
# EDS-FILTER-PARSER (Neue Feature: C+O > 15 Syntax)
# ============================================================

class EDSFilterParser:
    """Parst und evaluiert EDS-Filterausdrücke wie 'C+O > 15'"""
    
    PATTERN = r'([><=!]+)\s*([\d.]+)'
    VALID_ELEMENTS = {
        'H', 'C', 'N', 'O', 'F', 'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl',
        'K', 'Ca', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn',
        'Br', 'Ag', 'Sn', 'I', 'Pb', 'Au'
    }
    
    @classmethod
    def parse(cls, filter_string):
        """
        Parse Filterstring zu auswertbarer Funktion
        z.B. "C+O > 15" -> (evaluate_func, None)
        Returns: (eval_fn, error_msg)
        """
        if not filter_string or not filter_string.strip():
            return None, "Filter ist leer"
        
        filter_string = filter_string.strip()
        match = re.search(cls.PATTERN, filter_string)
        if not match:
            return None, "Format: 'C+O > 15' oder '(Fe+Ni) >= 20'"
        
        operator = match.group(1)
        try:
            threshold = float(match.group(2))
        except ValueError:
            return None, "Schwellwert muss eine Zahl sein"
        
        left_part = filter_string[:match.start()].strip()
        elements = cls._extract_elements(left_part)
        
        if not elements:
            return None, f"Keine Elemente in '{left_part}'"
        
        unknown = [e for e in elements if e not in cls.VALID_ELEMENTS]
        if unknown:
            return None, f"Unbekannte Elemente: {', '.join(unknown)}"
        
        def evaluate(element_dict):
            summe = sum(element_dict.get(elem, 0) for elem in elements)
            if operator == '>':
                return summe > threshold
            elif operator == '>=':
                return summe >= threshold
            elif operator == '<':
                return summe < threshold
            elif operator == '<=':
                return summe <= threshold
            elif operator == '==':
                return abs(summe - threshold) < 0.01
            elif operator == '!=':
                return abs(summe - threshold) >= 0.01
            return False
        
        return evaluate, None
    
    @classmethod
    def _extract_elements(cls, expression):
        """Extrahiert Elementnamen aus 'C+O' oder '(Fe+Ni)'"""
        expression = expression.replace('(', '').replace(')', '')
        parts = re.split(r'[+\-]', expression)
        elements = []
        for part in parts:
            part = part.strip()
            if part and part.isalpha() and len(part) <= 2:
                formatted = part[0].upper() + part[1:].lower() if len(part) > 1 else part.upper()
                if formatted in cls.VALID_ELEMENTS:
                    elements.append(formatted)
        return elements
    
    @classmethod
    def validiere(cls, filter_string):
        """Nur validieren, returns: (ist_gueltig, nachricht)"""
        func, error = cls.parse(filter_string)
        if error:
            return False, error
        return True, "✓ Filter ist gültig"

# --- KONFIGURATION ---
def _ermittle_basis_pfad():
    """Liest Dateipfad aus VS Codes settings.json oder aus der Umgebung.

    Ein Eintrag in den VS-Code-Einstellungen ist keine Betriebssystem-
    Umgebungsvariable und steht deshalb in einer normal gestarteten
    PowerShell nicht automatisch in ``os.environ``. Die VS-Code-Einstellung
    hat Vorrang, damit eine alte Umgebungsvariable nicht versehentlich auf
    einen einzelnen Projekt-Unterordner zeigt.
    """
    appdata = os.environ.get("APPDATA", "").strip()
    einstellungsdateien = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vscode", "settings.json"),
    ]
    if appdata:
        einstellungsdateien.extend([
            os.path.join(appdata, "Code", "User", "settings.json"),
            os.path.join(appdata, "Code - Insiders", "User", "settings.json"),
        ])

    # settings.json darf JSONC-Kommentare und abschliessende Kommata
    # enthalten. Daher nur den benoetigten Stringwert gezielt auslesen.
    muster = re.compile(r'["\']Dateipfad["\']\s*:\s*("(?:\\.|[^"\\])*")')
    for einstellungspfad in einstellungsdateien:
        try:
            with open(einstellungspfad, "r", encoding="utf-8-sig") as datei:
                inhalt = datei.read()
            treffer = muster.search(inhalt)
            if not treffer:
                continue
            wert = json.loads(treffer.group(1)).strip()
            if wert:
                return os.path.normpath(os.path.expandvars(os.path.expanduser(wert)))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue

    umgebungswert = os.environ.get("Dateipfad", "").strip()
    if umgebungswert:
        return os.path.normpath(os.path.expandvars(os.path.expanduser(umgebungswert)))
    return ""


BASIS_PFAD = _ermittle_basis_pfad()
if not BASIS_PFAD:
    raise RuntimeError(
        "'Dateipfad' wurde weder als Umgebungsvariable noch in den "
        "VS-Code-Einstellungen gefunden."
    )
MUL_TURKIS = "#008c96"
MUL_DUNKEL = "#0a2a2d"

# --- Plot Settings: Farb-Combobox (Diagramm 1/2, statt Freitext-Hexfeld) ---
# Anzeige-Name -> Hex. "Custom..." zeigt zusaetzlich ein Hex-Eingabefeld.
PLOT_FARBOPTIONEN = {
    "Türkis (Standard)": MUL_TURKIS,
    "Blau": "#1f77b4",
    "Orange": "#ff7f0e",
    "Grün": "#2ca02c",
    "Rot": "#d62728",
    "Schwarz": "#000000",
    "Grau": "#7f7f7f",
    "Custom...": None,
}
PLOT_FARBOPTIONEN_LABELS = list(PLOT_FARBOPTIONEN.keys())


def plot_farb_label_zu_hex(label, custom_hex_fallback):
    """Loest eine Farb-Combobox-Auswahl zu einem Hex-Code auf. Bei
    'Custom...' wird custom_hex_fallback (Inhalt des Hex-Eingabefelds)
    verwendet."""
    hex_wert = PLOT_FARBOPTIONEN.get(label)
    if hex_wert is None:
        return (custom_hex_fallback or MUL_TURKIS).strip() or MUL_TURKIS
    return hex_wert


def plot_farb_hex_zu_label(hex_wert):
    """Umgekehrte Zuordnung Hex -> Combobox-Label (fuer Dialog-Vorbelegung).
    Unbekannte Hex-Werte -> 'Custom...'."""
    for label, wert in PLOT_FARBOPTIONEN.items():
        if wert is not None and wert.lower() == str(hex_wert or "").lower():
            return label
    return "Custom..."


# --- Plot Settings: Diagrammstil-Presets ("Darstellung" -> Diagrammstil) ---
# Jeder Preset setzt eine Gruppe von Formatierungswerten auf einmal; der
# Nutzer kann danach weiterhin einzelne Werte manuell überschreiben.
PLOT_STIL_PRESETS = {
    "Classic": {
        "hintergrund_diagramm": "#ffffff", "hintergrund_figure": "#ffffff",
        "gitter_anzeigen": True, "minor_grid_anzeigen": False,
        "obere_achse_ausblenden": True, "rechte_achse_ausblenden": True,
        "tick_richtung": "out", "linienstil": "-",
    },
    "Publication": {
        "hintergrund_diagramm": "#ffffff", "hintergrund_figure": "#ffffff",
        "gitter_anzeigen": False, "minor_grid_anzeigen": False,
        "obere_achse_ausblenden": True, "rechte_achse_ausblenden": True,
        "tick_richtung": "in", "linienstil": "-",
    },
    "Origin": {
        "hintergrund_diagramm": "#ffffff", "hintergrund_figure": "#ffffff",
        "gitter_anzeigen": True, "minor_grid_anzeigen": True,
        "obere_achse_ausblenden": False, "rechte_achse_ausblenden": False,
        "tick_richtung": "in", "linienstil": "-",
    },
    "Dark": {
        "hintergrund_diagramm": "#1e1e1e", "hintergrund_figure": "#1e1e1e",
        "gitter_anzeigen": True, "minor_grid_anzeigen": False,
        "obere_achse_ausblenden": True, "rechte_achse_ausblenden": True,
        "tick_richtung": "out", "linienstil": "-",
    },
    "Minimal": {
        "hintergrund_diagramm": "#ffffff", "hintergrund_figure": "#ffffff",
        "gitter_anzeigen": False, "minor_grid_anzeigen": False,
        "obere_achse_ausblenden": True, "rechte_achse_ausblenden": True,
        "tick_richtung": "out", "linienstil": "-",
    },
}
PLOT_STIL_NAMEN = list(PLOT_STIL_PRESETS.keys())

# Tick-Richtung: Anzeige-Label (Dialog) -> matplotlib-Wert
PLOT_TICK_RICHTUNG_LABEL_ZU_WERT = {"Innen": "in", "Außen": "out", "Beides": "inout"}
PLOT_TICK_RICHTUNG_WERT_ZU_LABEL = {v: k for k, v in PLOT_TICK_RICHTUNG_LABEL_ZU_WERT.items()}

# Legendenposition: Anzeige-Label -> matplotlib "loc". "unter Achse" ist ein
# Sonderwert (siehe zeichne_ergebnis_plot_tga): die Legende wird dabei per
# bbox_to_anchor UNTERHALB der Achse platziert, statt per "loc" IN der Achse.
PLOT_LEGENDE_UNTER_ACHSE = "unter Achse"
PLOT_LEGENDE_LABEL_ZU_LOC = {
    PLOT_LEGENDE_UNTER_ACHSE: "unter Achse",
    "oben rechts": "upper right", "oben links": "upper left",
    "unten rechts": "lower right", "unten links": "lower left",
}
PLOT_LEGENDE_LOC_ZU_LABEL = {v: k for k, v in PLOT_LEGENDE_LABEL_ZU_LOC.items()}

# --- Plot Settings: "Anwenden auf"-Auswahl (Diagrammstil/Farbe/Schrift/
# Linien/Legende koennen fuer Links (Diagramm 1), Rechts (Diagramm 2) oder
# Beide gleichzeitig gesetzt werden) ---
PLOT_SEITEN_OPTIONEN = ["Beide", "Links", "Rechts"]

# Linienstil: Anzeige-Symbol -> matplotlib-Wert
PLOT_LINIENSTIL_LABEL_ZU_WERT = {"────": "-", "- - -": "--", "····": ":", "-·-·": "-."}
PLOT_LINIENSTIL_WERT_ZU_LABEL = {v: k for k, v in PLOT_LINIENSTIL_LABEL_ZU_WERT.items()}

# Pfad zu EMI_calculation.py/TGA_calculation.py (die echten Berechnungen von
# raw_data zu processed_data). Wird als eigener Subprozess gestartet, NICHT im
# main.py-Prozess importiert - so bleibt die GUI responsiv und fehlende
# Pakete/Abstürze im Berechnungs-Skript reißen die App nicht mit runter.
# Wenn ein Eintrag None ist, wird automatisch gesucht:
#   1) neben dieser main.py
#   2) unter <main.py-Ordner>/<Methode>/data_preparation/<Methode>_calculation.py
# Falls ein Skript wo ganz anders liegt, hier den vollen Pfad eintragen, z.B.:
# BERECHNUNGS_SKRIPT_PFADE["EMI"] = r"C:\Users\aaron\...\EMI\data_preparation\EMI_calculation.py"
BERECHNUNGS_SKRIPT_PFADE = {
    "EMI": None,
    "TGA": None,
}
# Methoden, für die es bereits eine echte raw_data -> processed_data
# Berechnung gibt (Subprozess-Aufruf). Alle anderen METHODEN (s.u.) zeigen
# weiterhin nur den Platzhalter-"Berechnen"-Check.
BERECHNUNGS_FAEHIGE_METHODEN = ("EMI", "TGA")

# --- Filter-Klassen aus helper/Filter.py (fuer die Glaettung der
# Reaktionskinetik im Ergebnisse-Tab TGA, siehe Settings-Dialog dort) ---
# Der "helper"-Ordner kann je nach Rechner/Aufbau an unterschiedlichen
# Stellen relativ zu main.py liegen. Es werden daher mehrere Kandidaten
# probiert UND jeder Kandidat wird konkret auf eine vorhandene Filter.py
# geprueft (nicht nur auf den blossen Ordnernamen "helper") - so schlaegt
# der Import auch dann nicht fehl, wenn irgendwo ein "helper"-Ordner OHNE
# Filter.py existiert (z.B. eine unvollstaendige Kopie).
_MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
_HELPER_KANDIDATEN = [
    _MAIN_DIR,
    os.path.dirname(_MAIN_DIR),
    os.path.dirname(os.path.dirname(_MAIN_DIR)),
    BASIS_PFAD,
    os.path.dirname(BASIS_PFAD) if BASIS_PFAD else None,
]
_HELPER_SUCHPROTOKOLL = []  # fuer Diagnose im Settings-Dialog, falls Import scheitert
_HELPER_GEFUNDEN = None

for _kandidat in _HELPER_KANDIDATEN:
    if not _kandidat:
        continue
    _helper_ordner = os.path.join(_kandidat, "helper")
    _filter_datei = os.path.join(_helper_ordner, "Filter.py")
    if os.path.isfile(_filter_datei):
        _HELPER_SUCHPROTOKOLL.append(f"OK    {_filter_datei}")
        if _HELPER_GEFUNDEN is None:
            _HELPER_GEFUNDEN = _kandidat
            if _kandidat not in sys.path:
                sys.path.insert(0, _kandidat)
    elif os.path.isdir(_helper_ordner):
        _HELPER_SUCHPROTOKOLL.append(f"FEHLT Filter.py in {_helper_ordner} (Ordner existiert, Datei nicht)")
    else:
        _HELPER_SUCHPROTOKOLL.append(f"FEHLT {_helper_ordner} (Ordner existiert nicht)")

try:
    if _HELPER_GEFUNDEN is None:
        raise ImportError(
            "Kein 'helper'-Ordner mit Filter.py gefunden. Gepruefte Orte:\n"
            + "\n".join(_HELPER_SUCHPROTOKOLL)
        )
    from helper.Filter import (
        ButterworthFilter,
        SavitzkyGolayFilter,
        ExponentialMovingAverage,
        MedianFilter,
        GaussianFilter,
        RollingAverage,
    )
    _FILTER_IMPORT_FEHLER = None
except Exception as _filter_exc:
    ButterworthFilter = SavitzkyGolayFilter = ExponentialMovingAverage = None
    MedianFilter = GaussianFilter = RollingAverage = None
    _FILTER_IMPORT_FEHLER = str(_filter_exc)

# Auswahl-Optionen fuer den Kinetik-Filter (rechtes Diagramm) im
# Settings-Dialog des TGA-Ergebnisse-Tabs.
TGA_FILTER_OPTIONEN = [
    "Kein Filter",
    "Butterworth",
    "Savitzky-Golay",
    "Exponentielles gleitendes Mittel",
    "Median",
    "Gaussian",
    "Gleitender Mittelwert",
]

# Je Filtertyp: Liste von (zustand-Schluessel, Anzeige-Label, Default-Text).
# Wird im Settings-Dialog dynamisch passend zum gewaehlten Filtertyp
# eingeblendet (siehe oeffne_settings/_baue_filter_parameter_felder).
TGA_FILTER_PARAMETER = {
    "Butterworth": [
        ("filter_butter_cutoff", "Cutoff (0 - 0.4, kleiner = staerker glaetten)", "0.05"),
        ("filter_butter_order", "Ordnung", "2"),
    ],
    "Savitzky-Golay": [
        ("filter_savgol_window", "Fensterlaenge (ungerade)", "15"),
        ("filter_savgol_polyorder", "Polynomgrad", "2"),
    ],
    "Exponentielles gleitendes Mittel": [
        ("filter_ema_alpha", "Alpha (0 - 1, kleiner = staerker glaetten)", "0.3"),
    ],
    "Median": [
        ("filter_median_kernel", "Fenstergroesse (ungerade)", "9"),
    ],
    "Gaussian": [
        ("filter_gauss_sigma", "Sigma", "1.0"),
    ],
    "Gleitender Mittelwert": [
        ("filter_rollavg_fenster", "Fenstergroesse (Punkte)", "10"),
    ],
}

# Spalten aus <versuch>_results.parquet (siehe TGA_calculation.py:
# _process_file), die im Plot Settings-Dialog des TGA-Ergebnisse-Tabs pro
# Diagramm als x-value/y-value auswaehlbar sind. Liste von
# (Spaltenname, Anzeige-Label).
TGA_ERGEBNIS_SPALTEN = [
    ("temperature_C", "Temperatur"),
    ("time_min", "Zeit [min]"),
    ("dm_filtered_pct", "Masse % (gefiltert)"),
    ("m_filtered_mg", "Masse abs (gefiltert)"),
    ("dmdt_filtered_pctmin", "Reaktionskinetik"),
    ("dmdt_filtered_mgmin", "Reaktionskinetik absolut"),
]
TGA_ERGEBNIS_SPALTEN_LABELS = [label for _spalte, label in TGA_ERGEBNIS_SPALTEN]
TGA_ERGEBNIS_LABEL_ZU_SPALTE = {label: spalte for spalte, label in TGA_ERGEBNIS_SPALTEN}
TGA_ERGEBNIS_SPALTE_ZU_LABEL = {spalte: label for spalte, label in TGA_ERGEBNIS_SPALTEN}
TGA_ACHSEN_LABELS = {
    "Temperatur": "Temperatur [°C]",
    "Zeit [min]": "Zeit [min]",
    "Masse % (gefiltert)": "relative Masse [%]",
    "Masse abs (gefiltert)": "absolute Masse [mg]",
    "Reaktionskinetik": "relative Reaktionskinetik [%/min]",
    "Reaktionskinetik absolut": "absoluter Reaktionskinetik [mg/min]",
}


def tga_achsen_label_fuer_spalte(spalte):
    """Liefert die einheitliche Achsenbeschriftung der gewählten Datenreihe."""
    label = TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(spalte, spalte)
    return TGA_ACHSEN_LABELS.get(label, str(label))

# ------------------------------------------------------------------
# SEM: Elemente/Operatoren fuer die Filter-Sektion im Rohdaten-Tab
# (siehe baue_rohdaten_tab_sem). Elementkuerzel muessen zu den Dateinamen
# der Elementkarten-TIFs passen ("Montaged Map Data-<Element> At#...tif" -
# siehe _sem_lade_elementkarten). Liste bei Bedarf um weitere im Labor
# gemessene Elemente ergaenzen.
SEM_FILTER_ELEMENTE = (
    "Al", "C", "Ca", "Cl", "Cr", "Cu", "F", "Fe", "K", "Mg", "Mn",
    "Na", "Ni", "O", "P", "Pb", "S", "Si", "Ti", "V", "Zn",
)
SEM_FILTER_OPERATOREN = ("<", "<=", ">", ">=")
# Ein Default-Filter, analog zum in der Aufgabenstellung genannten Beispiel "C < 30".
# "elemente" ist eine LISTE (nicht nur ein einzelnes Element) - ist mehr als
# ein Element eingetragen, werden deren Anteile aufsummiert, bevor mit
# "operator"/"wert" verglichen wird (z.B. "elemente": ["C", "O"], ">", 15
# => Filter "C+O > 15 %").
SEM_FILTER_STANDARD_LISTE = [{"elemente": ["C"], "operator": "<", "wert": 30.0, "aktiv": True}]

# Default-Farbpalette fuer die Element-Faerbung im Ergebnisse-Tab (siehe
# baue_ergebnisse_tab_sem) - wird der Reihe nach an neu auftauchende Elemente
# vergeben (zyklisch, falls mehr Elemente als Farben vorhanden sind). Danach
# frei per Farbauswahl-Dialog aenderbar, Auswahl bleibt projektweit gespeichert.
SEM_ELEMENT_FARBPALETTE = (
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9a6324", "#800000", "#aaffc3", "#000075",
)

# --- Element-Ansicht im Ergebnisse-Tab (siehe baue_ergebnisse_tab_sem):
# statt eines Overlays aus mehreren einfarbigen Elementkarten wird GENAU
# EINE Elementkarte gezeigt (per Dropdown auswaehlbar), eingefaerbt ueber
# eine echte Farbskala (Colormap) auf Basis der rohen 16-Bit-Grauwerte des
# TIFs (0 - 65535): niedrige Werte -> dunkle/kalte Farbe, hohe Werte ->
# helle/warme Farbe. "viridis" ist dafuer gut geeignet (dunkles Violett bei
# 0 bis helles Gelb bei 1) und zusaetzlich farbenblind-freundlich.
SEM_FARBSKALA_COLORMAP = "viridis"

# Modi fuer die Obergrenze der Farbnormierung (siehe
# _sem_baue_element_farbbild). Aendert NUR die Darstellung, nicht die
# Rohdaten/Pixelgeometrie:
#   "linear" - Obergrenze = tatsaechliches Maximum der Karte.
#   "p99"    - Obergrenze = 99. Perzentil der Karte (robuster gegen
#              einzelne sehr helle Ausreisser, mehr Kontrast im Rest).
SEM_FARBSKALIERUNG_OPTIONEN = ("linear", "p99")
SEM_FARBSKALIERUNG_LABELS = {"linear": "Linear", "p99": "p99"}
SEM_FARBSKALIERUNG_LABEL_ZU_WERT = {v: k for k, v in SEM_FARBSKALIERUNG_LABELS.items()}

# Rohwert-Obergrenze eines 16-Bit-Graustufenbilds (2^16 - 1) - Basis fuer die
# %-Beschriftung der Farbskalen-Legende (siehe baue_ergebnisse_tab_sem):
# ein Kartenwert wird als Prozent DIESES festen Maximums ausgedrueckt, nicht
# als Prozent des jeweiligen Karten-eigenen Maximums. Dadurch reicht die
# Legende nur bis zu dem Prozentwert, der tatsaechlich in der Karte
# vorkommt (x_max/SEM_16BIT_MAXIMUM), statt immer kuenstlich bis 100 % zu
# gehen - und Karten unterschiedlicher Elemente/Versuche bleiben ueber die
# %-Achse vergleichbar.
SEM_16BIT_MAXIMUM = 65535.0

# Optional: anderer Python-Interpreter für die EMI-Berechnung (falls HSMTools
# in einer eigenen venv installiert ist, nicht in der venv der GUI-App).
# Kann alternativ auch als Umgebungsvariable HSMTOOLS_PYTHON gesetzt werden.
# Beispiel: r"C:\Users\aaron\hsmtools_venv\Scripts\python.exe"
HSMTOOLS_PYTHON_PFAD = None

# --- GOOGLE SHEET (Projekt-Übersicht) ---
# Jeder Tab (Arbeitsblatt) im Spreadsheet entspricht einem Projekt.
# Verbindung per API-Key (kein OAuth-Login, kein Service-Account).
# Voraussetzung: das Sheet muss per Link freigegeben sein
# ("Jeder mit dem Link kann ansehen" - Freigeben-Button oben rechts im Sheet).
GOOGLE_SHEET_ID = "1RPvd3op6mLaXlxFn1zhpFwGavBmBMe17IU8Yux7-3iE"
# ACHTUNG: diesen Key in der Google Cloud Console auf "Google Sheets API"
# und idealerweise auf deine IP einschränken, da er hier im Klartext steht.
GOOGLE_API_KEY = "AIzaSyBZCV--EcDDNCjOSMXhvLeBaaes2X7DhK8"

# Methoden, die pro Staub/Versuch existieren.
# Hinweis: ersetzt "REM" durch "TGA" gemäß Aarons Ablauf-Beschreibung.
# Falls REM weiterhin gebraucht wird, hier einfach ergänzen/anpassen.
METHODEN = ["EMI", "TGA", "SEM"]

# Zusätzliche Ordner, die nach Projekten durchsucht werden (z.B. entpackte
# WeTransfer-Ordner, die noch nicht nach BASIS_PFAD verschoben wurden).
# Jeder direkte Unterordner hier wird als eigenes Projekt zum Zählen der
# Versuche verwendet (Zuordnung erfolgt per Namens-Abgleich zum Sheet-Tab).
EXTRA_DATENQUELLEN = [
    r"C:\Users\marty\Desktop\wetransfer_h2lab_pub_25_9-lime-addition-in-eafd-recycling_2026-07-08_0739",
]

class LaborApp(ctk.CTk):
    # Feste Länge des SEM-Maßstabsbalkens in Mikrometer (unabhängig vom
    # Zoomstand). Kalibrierung erfolgt über "mikrometer_pro_pixel"
    # (Standard: 0.84427 µm/Pixel, siehe Rohdaten-Tab).
    #
    # BERECHNUNG DES MASSTABSBALKENS (gemäß Dokumentation):
    # =====================================================
    # 1. Gewünschte Maßstabslänge: 1000 µm = 1 mm (MASSSTABSBALKEN_LAENGE_UM)
    # 2. Pixelgröße: 0.84427 µm/Pixel (aus H5OINA oder manuell gesetzt)
    # 3. Formel: Pixel = µm ÷ µm_pro_Pixel
    # 4. Berechnung: 1000 µm ÷ 0.84427 µm/Pixel = 1184.45 Pixel
    #
    # Beim Zoomen im Browser:
    # - Die physikalische Beschriftung bleibt "1 mm"
    # - Die Pixel-Länge wird mit Zoom-Faktor multipliziert
    # - Das Verhältnis zwischen Balken und Bild bleibt korrekt
    MASSSTABSBALKEN_LAENGE_UM = 1000

    def __init__(self):
        super().__init__()
        self.title("MUL - H2Lab Staub-System")
        self.geometry("600x800")
        # Immer Dark Mode - unabhaengig vom System-Theme des Nutzers (nicht
        # "System", da das bei hellem OS-Theme auf Light umschalten wuerde).
        ctk.set_appearance_mode("Dark")

        # Bekanntes CustomTkinter-Problem: die interne DPI-Scaling-Prüfschleife
        # (check_dpi_scaling / update, per self.after() geplant) läuft weiter,
        # auch nachdem das Fenster mit dem X geschlossen wurde -> danach
        # "invalid command name ... (after script)" im Terminal. Fix laut
        # CustomTkinter-Doku: beim Schließen ZUERST quit(), DANN destroy().
        self.protocol("WM_DELETE_WINDOW", self.beim_schliessen)

        self.aktuelle_sheet_daten = None  # Zeilen aus dem Datenblatt des aktuell gewählten Projekts
        self.ergebnisse_cache = {}  # {Methode: (header, gefilterte_zeilen)} - vorberechnet pro Projektwechsel
        # {versuch_pfad: (um_pro_px oder None, fehlermeldung oder None)} -
        # Cache fuer die aus der H5OINA-Datei gelesene Pixelkalibrierung,
        # damit die (teure) H5-Datei nicht bei jedem Redraw/Zoom neu
        # eingelesen wird (siehe _sem_ermittle_um_pro_pixel).
        self._sem_h5oina_kalibrierung_cache = {}

        self.grid_columnconfigure(0, weight=1)

        # status_label MUSS vor dem Projekt-Dropdown existieren, da
        # get_projekte() -> hole_projekte_aus_spreadsheet() bereits
        # Statusmeldungen darauf setzen kann.
        self.status_label = ctk.CTkLabel(self, text="System bereit.", text_color="white")
        self.status_label.pack(side="bottom", pady=10)

        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(pady=20, padx=20, fill="both", expand=True)

        # --- Projektauswahl (immer sichtbar, ganz oben) ---
        ctk.CTkLabel(self.main_frame, text="Projekt auswählen:", font=("Arial", 14, "bold")).pack(pady=(0, 5))
        self.projekt_menu = ctk.CTkOptionMenu(
            self.main_frame,
            values=self.get_projekte() or ["--- keine Projekte ---"],
            command=self.on_projekt_wechsel,
            fg_color=MUL_TURKIS,
        )
        self.projekt_menu.pack(pady=(0, 20))

        # --- Container für die Methoden-Übersicht (wird bei Projektwechsel neu befüllt) ---
        self.uebersicht_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.uebersicht_frame.pack(fill="both", expand=True)

        # Beim Start direkt die Übersicht für das erste Projekt laden
        erstes_projekt = self.projekt_menu.get()
        if erstes_projekt and "---" not in erstes_projekt:
            self.on_projekt_wechsel(erstes_projekt)

    # ------------------------------------------------------------------
    # SAUBERES BEENDEN
    # ------------------------------------------------------------------
    def beim_schliessen(self):
        """
        Wird bei Klick auf das X des Hauptfensters aufgerufen statt direkt
        destroy(). quit() beendet zuerst die Tkinter-Mainloop sauber, DANACH
        erst destroy() - verhindert das bekannte CustomTkinter-Nachlauf-
        Problem, bei dem intern noch geplante after()-Aufrufe (DPI-Scaling-
        Check etc.) nach dem Zerstören des Fensters "invalid command name"
        ins Terminal werfen.
        """
        try:
            self.quit()
        finally:
            self.destroy()

    # ------------------------------------------------------------------
    # GOOGLE SHEET (Projektliste)
    # ------------------------------------------------------------------
    def _status(self, text, farbe="white"):
        """Setzt die Statuszeile, falls sie bereits existiert (defensiv, s. __init__-Reihenfolge)."""
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.configure(text=text, text_color=farbe)

    def hole_projekte_aus_spreadsheet(self):
        """
        Liest alle Tab-/Arbeitsblattnamen aus dem Google Sheet über die
        offizielle Sheets-REST-API mit einem API-Key (Sheet muss per Link
        freigegeben sein: "Jeder mit dem Link kann ansehen").
        Jeder Tab = ein Projekt. Neue Tabs im Sheet tauchen automatisch
        beim nächsten Öffnen der App im Dropdown auf.
        """
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}"
            f"?key={GOOGLE_API_KEY}&fields=sheets.properties.title"
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                daten = json.loads(response.read().decode("utf-8"))
            tabs = [sheet["properties"]["title"] for sheet in daten.get("sheets", [])]
            self._status(f"{len(tabs)} Projekte aus Google Sheet geladen.", "#00ff88")
            return tabs
        except urllib.error.HTTPError as e:
            fehlertext = e.read().decode("utf-8", errors="ignore")
            messagebox.showerror(
                "Google Sheet Fehler",
                f"HTTP {e.code} beim Laden der Projektliste.\n\n"
                f"Häufigste Ursache: Sheet nicht per Link freigegeben.\n\nDetails:\n{fehlertext}",
            )
            return None
        except Exception as e:
            messagebox.showerror("Google Sheet Fehler", f"Konnte Projektliste nicht laden:\n{e}")
            return None

    # ------------------------------------------------------------------
    # PROJEKT-SWITCHING
    # ------------------------------------------------------------------
    def baue_lokale_projekt_pfad_map(self):
        """
        Baut eine Zuordnung {lokaler Ordnername: Root-Ordner} auf - nur zum
        Auffinden der raw_data-Ordner, NICHT mehr die Quelle für das Dropdown
        (das kommt jetzt aus dem Google Sheet, siehe hole_projekte_aus_spreadsheet).

        Falls EXTRA_DATENQUELLEN befüllt ist, werden AUSSCHLIESSLICH die dort
        gefundenen Projekt-Unterordner verwendet (BASIS_PFAD wird ignoriert).
        Ist EXTRA_DATENQUELLEN leer, wird stattdessen BASIS_PFAD durchsucht.
        """
        pfad_map = {}

        if EXTRA_DATENQUELLEN:
            for quelle in EXTRA_DATENQUELLEN:
                if not os.path.exists(quelle):
                    continue
                for d in os.listdir(quelle):
                    voll = os.path.join(quelle, d)
                    if os.path.isdir(voll) and not d.startswith("."):
                        pfad_map[d] = voll
        elif os.path.exists(BASIS_PFAD):
            for d in os.listdir(BASIS_PFAD):
                voll = os.path.join(BASIS_PFAD, d)
                if os.path.isdir(voll) and not d.startswith("."):
                    pfad_map[d] = voll

        return pfad_map

    def get_projekte(self):
        """
        Projektliste für das Dropdown. Kommt primär aus dem Google Sheet
        (ein Tab = ein Projekt). Falls das Sheet nicht erreichbar ist,
        wird ersatzweise auf die lokalen Ordner zurückgefallen.
        """
        sheet_projekte = self.hole_projekte_aus_spreadsheet()
        if sheet_projekte is not None:
            return sheet_projekte
        return sorted(self.baue_lokale_projekt_pfad_map().keys())

    def hole_sheet_daten(self, projekt):
        """
        Liest alle Zeilen aus dem Arbeitsblatt (Tab) im Spreadsheet, dessen
        Name exakt dem gewählten Projekt entspricht ("Datenblatt unten im
        Spreadsheet"). Gibt eine Liste von Zeilen zurück, Zeile 0 = Kopfzeile.
        None, wenn das Blatt nicht gefunden/gelesen werden konnte.
        """
        blatt_name = urllib.parse.quote(projekt, safe="")
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}"
            f"/values/{blatt_name}?key={GOOGLE_API_KEY}"
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                daten = json.loads(response.read().decode("utf-8"))
            zeilen = daten.get("values", [])
            return zeilen
        except urllib.error.HTTPError as e:
            fehlertext = e.read().decode("utf-8", errors="ignore")
            self._status(f"Datenblatt '{projekt}' nicht lesbar (HTTP {e.code}).", "#ff5555")
            print(f"[Sheet-Fehler] {projekt}: {fehlertext}")
            return None
        except Exception as e:
            self._status(f"Datenblatt '{projekt}' nicht lesbar: {e}", "#ff5555")
            return None

    def normalisiere_id(self, text):
        """Für tolerante ID-Vergleiche: Groß-/Kleinschreibung, Leerzeichen und
        Sonderzeichen werden ignoriert (z.B. 'M2602270903 ' == 'm2602-270903')."""
        return "".join(ch.lower() for ch in str(text) if ch.isalnum())

    def finde_header_zeile(self, gesuchte_spalte="Messung", max_zeilen_pruefen=5):
        """
        Manche Sheets (wie dieses) haben ZWEI Kopfzeilen: Zeile 1 = Kategorien
        ("EMI Settings"), Zeile 2 = die echten Spaltennamen ("Messung", ...).
        Sucht innerhalb der ersten paar Zeilen die, die 'gesuchte_spalte'
        enthält, und gibt (zeilen_index, header_liste) zurück - oder
        (None, None), falls nirgends gefunden.
        """
        if not self.aktuelle_sheet_daten:
            return None, None
        for i, zeile in enumerate(self.aktuelle_sheet_daten[:max_zeilen_pruefen]):
            for zelle in zeile:
                if str(zelle).strip().lower() == gesuchte_spalte.lower():
                    return i, zeile
        return None, None

    def hole_emi_parameter_fuer_versuch(self, versuch_name):
        """
        Sucht im aktuell geladenen Sheet-Datenblatt (self.aktuelle_sheet_daten)
        die Zeile, deren "Messung"-Spalte zum gegebenen Versuchsnamen passt
        (toleranter Abgleich), und liest Temperaturverlauf/Gas/Durchfluss
        (EMI-Settings-Bereich) sowie Material/Kommentar (aus dem allgemeinen
        Metadaten-Bereich derselben Zeile) aus.
        Gibt ein Dict zurück, oder None, wenn keine passende Zeile gefunden wurde.
        """
        header_index, header = self.finde_header_zeile("Messung")
        if header is None:
            return None

        spalten_index = {}
        # "Messung" zuerst finden (eindeutig)
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() == "messung":
                spalten_index["Messung"] = i
                break

        if "Messung" not in spalten_index:
            return None

        # Material/Kommentar stehen VOR "Messung" im Metadaten-Bereich der Zeile -
        # einmalig im Header vorhanden, daher über die ganze Zeile suchbar.
        for name in ("material", "comment (lime addition)"):
            for i, spalte in enumerate(header):
                if str(spalte).strip().lower() == name:
                    spalten_index[name] = i
                    break

        # Temperaturverlauf/Gas/Durchfluss NUR in den Spalten NACH "Messung"
        # suchen - es gibt z.B. eine zweite Spalte "Gas" bei TGA Settings,
        # die sonst fälschlich gefunden würde.
        m_idx = spalten_index["Messung"]
        for name in ("Temperaturverlauf", "Gas", "Durchfluss"):
            for i in range(m_idx + 1, len(header)):
                if str(header[i]).strip().lower() == name.lower():
                    spalten_index[name] = i
                    break

        ziel_normalisiert = self.normalisiere_id(versuch_name)
        for zeile in self.aktuelle_sheet_daten[header_index + 1:]:
            if len(zeile) > m_idx and self.normalisiere_id(zeile[m_idx]) == ziel_normalisiert:
                def _wert(spalten_name):
                    idx = spalten_index.get(spalten_name)
                    return zeile[idx] if idx is not None and len(zeile) > idx else ""

                return {
                    "temperaturverlauf": _wert("Temperaturverlauf"),
                    "gas": _wert("Gas"),
                    "durchfluss": _wert("Durchfluss"),
                    "material": _wert("material"),
                    "kommentar": _wert("comment (lime addition)"),
                }

        return None

    def _tga_versuchsname_fuer_sheet(self, eintrag):
        """
        TGA-Rohdateien heißen '<Folgenummer>_<Name>.txt' (z.B. '1986_RT1.txt'),
        wobei '<Name>' der manuell am Gerät eingegebene Versuchsname ist - das
        ist vermutlich auch der Wert in der "Messung"-Spalte im Sheet (nicht
        die Folgenummer). Muss ident zur gleichnamigen Logik in
        TGA_calculation.py sein (_versuchsname_aus_dateiname).
        """
        stem = os.path.splitext(eintrag)[0]
        match = re.match(r"^\d+_(.+)$", stem)
        return match.group(1) if match else stem

    def hole_tga_parameter_fuer_versuch(self, versuch_name):
        """
        TGA-Gegenstück zu hole_emi_parameter_fuer_versuch: sucht im Sheet die
        Zeile zur "Messung"-ID und liest:
          - Material/Kommentar (allgemeiner Metadaten-Bereich, VOR "Messung")
          - CaO-/Kalk-Zugabe (für die interne EAFD-Basis-Umrechnung, siehe
            _tga_auf_eafd_basis - bleibt intern, wird nicht extra im
            Metadaten-Tab angezeigt)
          - TGA-spezifische Settings-Spalten NACH "Messung" (TGA-ID, Gas,
            Tmax, Operator, mass_loss_scale_%) - analog zu Temperaturverlauf/
            Gas/Durchfluss bei EMI, damit keine gleichnamige Spalte aus
            einem anderen Methoden-Block fälschlich gefunden wird.
        Alle Spaltennamen werden tolerant gesucht (mehrere Schreibweisen je
        Zielspalte) - falls euer Sheet anders beschriftet ist, hier die
        passende Variante in den jeweiligen 'kandidaten'-Tupeln ergänzen.
        Gibt ein Dict zurück, oder None, wenn keine passende Zeile gefunden
        wurde.
        """
        header_index, header = self.finde_header_zeile("Messung")
        if header is None:
            return None

        spalten_index = {}
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() == "messung":
                spalten_index["Messung"] = i
                break
        if "Messung" not in spalten_index:
            return None

        for name in ("material", "comment (lime addition)"):
            for i, spalte in enumerate(header):
                if str(spalte).strip().lower() == name:
                    spalten_index[name] = i
                    break

        # CaO-/Kalk-Zugabe-Spalte tolerant suchen (Name im Sheet evtl. anders -
        # bei Bedarf hier ergänzen).
        cao_kandidaten = ("cao", "cao %", "cao [%]", "cao-zugabe", "lime", "lime addition", "lime m-%", "lime %")
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() in cao_kandidaten:
                spalten_index["cao"] = i
                break

        # TGA-spezifische Settings-Spalten NUR in den Spalten NACH "Messung"
        # suchen (siehe Docstring oben) - je Zielspalte mehrere tolerante
        # Namensvarianten.
        m_idx = spalten_index["Messung"]
        tga_settings_kandidaten = {
            "tga_id": ("tga id", "tga-id", "tga_id", "tgaid"),
            "gas": ("gas",),
            "tmax": ("tmax", "t max", "t_max", "t-max"),
            "operator": ("operator", "bediener"),
            "mass_loss_scale_pct": (
                "mass_loss_scale_%", "mass loss scale %", "mass loss scale",
                "massloss scale %", "mass_loss_scale",
            ),
        }
        for ziel_name, kandidaten in tga_settings_kandidaten.items():
            for i in range(m_idx + 1, len(header)):
                if str(header[i]).strip().lower() in kandidaten:
                    spalten_index[ziel_name] = i
                    break

        ziel_normalisiert = self.normalisiere_id(versuch_name)
        for zeile in self.aktuelle_sheet_daten[header_index + 1:]:
            if len(zeile) > m_idx and self.normalisiere_id(zeile[m_idx]) == ziel_normalisiert:
                def _wert(spalten_name):
                    idx = spalten_index.get(spalten_name)
                    return zeile[idx] if idx is not None and len(zeile) > idx else ""

                cao_text = str(_wert("cao")).strip().replace(",", ".").replace("%", "")
                try:
                    cao_pct = float(cao_text) if cao_text else None
                except ValueError:
                    cao_pct = None

                return {
                    "material": _wert("material"),
                    "kommentar": _wert("comment (lime addition)"),
                    "cao_pct": cao_pct,
                    "tga_id": _wert("tga_id"),
                    "gas": _wert("gas"),
                    "tmax": _wert("tmax"),
                    "operator": _wert("operator"),
                    "mass_loss_scale_pct": _wert("mass_loss_scale_pct"),
                }

        return None

    def hole_sem_parameter_fuer_versuch(self, versuch_name):
        """
        SEM-Gegenstück zu hole_emi_parameter_fuer_versuch / hole_tga_parameter_fuer_versuch.

        WICHTIGER UNTERSCHIED zu EMI/TGA: dort wird über die Spalte "Messung"
        gematcht (M-Code, z.B. "M2602270903"). Die SEM-Rohdaten-Ordner sind
        aber nicht so benannt, sondern wie die allgemeine Proben-"id" im
        Metadaten-Bereich ganz am Anfang der Zeile (z.B. "RT1", "RT63",
        "RT74" - siehe wetransfer-Lieferung: Ordner "RT63", "RT74"). Deshalb
        wird hier über die Spalte "id" gematcht, NICHT über "Messung".
        "Messung" wird nur noch benutzt, um (wie bei EMI/TGA) die Kopfzeile
        im Sheet zu finden (dort zuverlässig vorhanden).

        Liest:
          - Material/Kommentar (allgemeiner Metadaten-Bereich, wie bei EMI/TGA)
          - SEM-spezifische Settings-Spalten aus dem Block "SEM Preperation" /
            "SEM Evaluation" (NUR in den Spalten NACH "Messung" gesucht,
            analog zu TGA, damit keine gleichnamige Spalte aus einem anderen
            Methoden-Block fälschlich gefunden wird):
            SEM id, Box N°, embedded by, polished by, overview by, detail by
        Alle Spaltennamen werden tolerant gesucht. Gibt ein Dict zurück,
        oder None, wenn keine passende Zeile gefunden wurde.
        """
        header_index, header = self.finde_header_zeile("Messung")
        if header is None:
            return None

        spalten_index = {}
        # Match-Spalte: die allgemeine "id"-Spalte (NICHT "Messung" - siehe
        # Docstring). Exakter Vergleich auf "id", damit "Project id",
        # "Box ID", "SEM  id" etc. nicht fälschlich treffen.
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() == "id":
                spalten_index["id"] = i
                break
        if "id" not in spalten_index:
            return None

        for name in ("material", "comment (lime addition)"):
            for i, spalte in enumerate(header):
                if str(spalte).strip().lower() == name:
                    spalten_index[name] = i
                    break

        # "Messung" brauchen wir nur, um NACH dieser Spalte zu suchen (SEM-
        # Block kommt im Sheet nach "EMI Settings"/"Messung") - analog zum
        # TGA-Vorgehen.
        m_idx = None
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() == "messung":
                m_idx = i
                break
        if m_idx is None:
            m_idx = spalten_index["id"]

        sem_settings_kandidaten = {
            "sem_id": ("sem id", "sem  id", "sem-id", "semid"),
            "box_n": ("box n°", "box no", "box nr", "box nr.", "box number", "box n"),
            "embedded_by": ("embedded by",),
            "polished_by": ("polished by",),
            "overview_by": ("overview by",),
            "detail_by": ("detail by",),
        }
        for ziel_name, kandidaten in sem_settings_kandidaten.items():
            for i in range(m_idx + 1, len(header)):
                if str(header[i]).strip().lower() in kandidaten:
                    spalten_index[ziel_name] = i
                    break

        ziel_normalisiert = self.normalisiere_id(versuch_name)
        id_idx = spalten_index["id"]
        for zeile in self.aktuelle_sheet_daten[header_index + 1:]:
            if len(zeile) > id_idx and self.normalisiere_id(zeile[id_idx]) == ziel_normalisiert:
                def _wert(spalten_name):
                    idx = spalten_index.get(spalten_name)
                    return zeile[idx] if idx is not None and len(zeile) > idx else ""

                return {
                    "material": _wert("material"),
                    "kommentar": _wert("comment (lime addition)"),
                    "sem_id": _wert("sem_id"),
                    "box_n": _wert("box_n"),
                    "embedded_by": _wert("embedded_by"),
                    "polished_by": _wert("polished_by"),
                    "overview_by": _wert("overview_by"),
                    "detail_by": _wert("detail_by"),
                }

        return None

    def on_projekt_wechsel(self, projekt_name):
        """Wird aufgerufen, sobald im Dropdown ein anderes Projekt gewählt wird."""
        self._status(f"Projekt '{projekt_name}' wird geladen ...", "#ffff00")

        # Datenblatt (Tab) des Projekts aus dem Spreadsheet laden
        self.aktuelle_sheet_daten = self.hole_sheet_daten(projekt_name)

        if self.aktuelle_sheet_daten is not None:
            header_index, _ = self.finde_header_zeile("Messung")
            # Falls keine "Messung"-Kopfzeile gefunden wurde, defensiv von 1
            # Kopfzeile ausgehen, damit die Anzeige nicht negativ/falsch wird.
            erste_datenzeile = (header_index + 1) if header_index is not None else 1
            anzahl_zeilen = max(len(self.aktuelle_sheet_daten) - erste_datenzeile, 0)
            self._status(
                f"Projekt '{projekt_name}' geladen ({anzahl_zeilen} Zeilen im Datenblatt).",
                "#00ff88",
            )

        # Ergebnisse pro Methode JETZT einmal berechnen und cachen, damit ein
        # Tab-Klick später nur noch ein Cache-Lookup ist (kein erneutes
        # Scannen/Filtern -> keine Ladeverzögerung beim Öffnen des Tabs).
        self.ergebnisse_cache = {}
        for methode in METHODEN:
            self.ergebnisse_cache[methode] = self.berechne_ergebnisse_fuer_methode(projekt_name, methode)

        self.baue_methoden_uebersicht(projekt_name)

    def berechne_ergebnisse_fuer_methode(self, projekt, methode):
        """
        Filtert die Sheet-Zeilen des Projekts auf die, deren "Messung"-ID zu
        einem raw_data-Eintrag dieser Methode passt (toleranter ID-Abgleich,
        s. normalisiere_id). Gibt (header, gefilterte_zeilen) zurück, oder
        None, falls kein Sheet-Datenblatt geladen ist.
        """
        if not self.aktuelle_sheet_daten or len(self.aktuelle_sheet_daten) < 1:
            return None

        header = self.aktuelle_sheet_daten[0]
        alle_zeilen = self.aktuelle_sheet_daten[1:]

        messung_index = 0
        for i, spalte in enumerate(header):
            if str(spalte).strip().lower() == "messung":
                messung_index = i
                break

        lokale_ids_normalisiert = set()
        for _staub, eintrag, _pfad in self.liste_versuche(projekt, methode):
            basisname = os.path.splitext(eintrag)[0]
            lokale_ids_normalisiert.add(self.normalisiere_id(basisname))

        gefilterte_zeilen = [
            z for z in alle_zeilen
            if len(z) > messung_index and self.normalisiere_id(z[messung_index]) in lokale_ids_normalisiert
        ]

        return (header, gefilterte_zeilen)

    def get_projekt_root(self, projekt):
        """
        Gibt den lokalen Root-Ordner für ein Projekt zurück, um Versuche zu zählen.

        Der Projektname kommt vom Sheet-Tab und muss nicht 1:1 zum lokalen
        Ordnernamen passen (z.B. Tab "H2Lab_INT_26_1 bliblablu" vs. Ordner
        "H2Lab_INT_26_1"). Reihenfolge der Zuordnung:
          1) exakte Übereinstimmung
          2) lokaler Ordnername ist Präfix des Projektnamens
          3) Projektname ist Präfix des lokalen Ordnernamens
        Wird nichts gefunden (z.B. Projekt existiert nur im Sheet, noch
        keine lokalen Rohdaten), wird ein nicht-existenter Pfad zurückgegeben
        -> Versuchszahl ist dann einfach 0.
        """
        pfad_map = self.baue_lokale_projekt_pfad_map()

        if projekt in pfad_map:
            return pfad_map[projekt]

        for ordner_name, pfad in pfad_map.items():
            if ordner_name.lower().startswith(projekt.lower()) or projekt.lower().startswith(ordner_name.lower()):
                return pfad

        return os.path.join(BASIS_PFAD, projekt)  # existiert evtl. nicht -> 0 Versuche

    def finde_raw_data_ordner(self, projekt, methode):
        """
        Sucht NUR an den bekannten, erwarteten Stellen nach dem raw_data-Ordner
        einer Methode - keine unbeschränkte Rekursion über den ganzen Baum,
        damit keine zufälligen/doppelten raw_data-Ordner aus tieferen
        Unterverzeichnissen mitgezählt werden.

        Unterstützte Strukturen (relativ zum Projekt-Root):
          1) <Methode>/raw_data                         (z.B. WeTransfer-Ordner)
          2) <Methode>/data/raw_data                     (Standard BASIS_PFAD-Struktur ohne Staub)
          3) <Staub>/<Methode>/raw_data
          4) <Staub>/<Methode>/data/raw_data              (Standard BASIS_PFAD-Struktur mit Staub)
        """
        root = self.get_projekt_root(projekt)
        treffer = []
        if not os.path.isdir(root):
            return treffer

        kandidaten_relativ_pfade = [
            os.path.join(methode, "raw_data"),
            os.path.join(methode, "data", "raw_data"),
        ]

        # Ebene 1 + 2: direkt unter Projekt-Root
        for rel in kandidaten_relativ_pfade:
            pfad = os.path.join(root, rel)
            if os.path.isdir(pfad):
                treffer.append(pfad)

        # Ebene 3 + 4: eine Staub-Ebene dazwischen
        for eintrag in os.listdir(root):
            unterordner = os.path.join(root, eintrag)
            if not os.path.isdir(unterordner) or eintrag.startswith("."):
                continue
            if eintrag.lower() == methode.lower():
                continue  # das war bereits Ebene 1/2, kein zusätzlicher Staub-Level
            for rel in kandidaten_relativ_pfade:
                pfad = os.path.join(unterordner, rel)
                if os.path.isdir(pfad):
                    treffer.append(pfad)

        return treffer

    def zaehle_versuche(self, projekt, methode):
        """
        Zählt alle Versuche für eine Methode (rekursiv, s. finde_raw_data_ordner).
        Jeder direkte Eintrag in raw_data zählt als ein Versuch -
        egal ob es eine einzelne Datei ist oder ein Unterordner pro Versuch.
        """
        anzahl = 0
        for raw_data_pfad in self.finde_raw_data_ordner(projekt, methode):
            anzahl += len(os.listdir(raw_data_pfad))
        return anzahl

    def liste_versuche(self, projekt, methode):
        """
        Liefert Liste von (staub, eintragsname, vollpfad) für alle Versuche einer Methode.
        Jeder direkte Eintrag in raw_data (Datei ODER Unterordner) zählt als ein Versuch.
        "staub" wird aus dem Pfad-Teil vor der Methode abgeleitet, falls vorhanden,
        sonst wird der Projektname als Platzhalter verwendet.
        """
        root = self.get_projekt_root(projekt)
        ergebnisse = []
        for raw_data_pfad in self.finde_raw_data_ordner(projekt, methode):
            rel = os.path.relpath(raw_data_pfad, root)
            teile = rel.split(os.sep)
            # alles vor dem Methoden-Ordner als "Staub"-Label verwenden (leer -> Projektname)
            idx = [t.lower() for t in teile].index(methode.lower())
            staub_label = os.sep.join(teile[:idx]) if idx > 0 else projekt

            for eintrag in sorted(os.listdir(raw_data_pfad)):
                voller_pfad = os.path.join(raw_data_pfad, eintrag)
                ergebnisse.append((staub_label, eintrag, voller_pfad))
        return ergebnisse

    # ------------------------------------------------------------------
    # UI: METHODEN-ÜBERSICHT (EMI | TGA | SEM mit Versuchsanzahl)
    # ------------------------------------------------------------------
    def baue_methoden_uebersicht(self, projekt_name):
        # alten Inhalt löschen
        for widget in self.uebersicht_frame.winfo_children():
            widget.destroy()

        if not projekt_name or "---" in projekt_name:
            return

        ctk.CTkLabel(
            self.uebersicht_frame,
            text=f"Übersicht: {projekt_name}",
            font=("Arial", 16, "bold"),
        ).pack(pady=(10, 20))

        karten_frame = ctk.CTkFrame(self.uebersicht_frame, fg_color="transparent")
        karten_frame.pack(pady=(0, 20))

        for i, methode in enumerate(METHODEN):
            anzahl = self.zaehle_versuche(projekt_name, methode)
            karte = ctk.CTkButton(
                karten_frame,
                text=f"{methode}\n({anzahl} Versuche)",
                width=150,
                height=80,
                fg_color=MUL_DUNKEL,
                hover_color=MUL_TURKIS,
                border_width=1,
                border_color=MUL_TURKIS,
                font=("Arial", 13, "bold"),
                command=lambda m=methode: self.oeffne_methoden_detail(projekt_name, m),
            )
            karte.grid(row=0, column=i, padx=10)

    # ------------------------------------------------------------------
    # DETAILANSICHT EINER METHODE
    # ------------------------------------------------------------------
    def oeffne_methoden_detail(self, projekt, methode):
        # Ordnerstruktur sicherstellen: falls für diese Methode NOCH KEIN
        # raw_data-Ordner existiert (weder alte "raw_data"- noch neue
        # "data/raw_data"-Struktur), legen wir die neue Standardstruktur
        # <Methode>/data/raw_data + data/processed_data + outputs/diagramm
        # an. Existiert bereits irgendeine Struktur, wird NICHTS angefasst -
        # das bestehende EMI-Setup bleibt unverändert. Gilt gleichermaßen
        # für EMI, TGA und zukünftige Methoden, da "methode" generisch ist.
        if not self.finde_raw_data_ordner(projekt, methode):
            self.erstelle_versuchs_struktur(projekt, "", methode)

        top = ctk.CTkToplevel(self)
        # Fenster erst UNSICHTBAR aufbauen und ganz am Ende (nachdem alle
        # Tabs/Tabellen fertig geladen und gezeichnet sind) anzeigen. Sonst
        # sieht man - v.a. weil das Fenster gleich maximiert wird - waehrend
        # des (teils laengeren) Ladens kurz ein leeres weisses Fenster.
        top.withdraw()
        top.title(f"{projekt} – {methode}")
        top.minsize(900, 600)
        top.resizable(True, True)

        ctk.CTkLabel(top, text=f"{methode} – {projekt}", font=("Arial", 16, "bold")).pack(pady=(15, 10))

        tabs = ctk.CTkTabview(top)
        tabs.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        tab_metadaten = tabs.add("Metadaten")
        tab_rohdaten = tabs.add("Rohdaten")
        tab_ergebnisse = tabs.add("Ergebnisse")
        self._ergebnisse_tabs = getattr(self, "_ergebnisse_tabs", {})
        self._ergebnisse_tabs[(projekt, methode)] = tab_ergebnisse

        self.baue_metadaten_tab(tab_metadaten, projekt, methode)
        self.baue_rohdaten_tab(tab_rohdaten, projekt, methode)
        self.baue_ergebnisse_tab(tab_ergebnisse, projekt, methode)

        # Erst JETZT (Inhalt fertig) das Fenster maximiert einblenden, statt
        # nur 85% der Bildschirmgröße - man muss also nicht mehr manuell per
        # Doppelklick/Ziehen maximieren, und es gibt keinen weissen
        # "Leerlauf-Blitz" mehr davor.
        top.update_idletasks()
        try:
            # Windows (und manche Linux-Fenstermanager): "zoomed" = normales
            # maximiertes Fenster (mit Titelleiste/Rand), KEIN randloser
            # Fullscreen-Modus.
            top.state("zoomed")
        except Exception:
            try:
                # Linux (manche Fenstermanager, z.B. viele GTK-basierte WMs)
                top.attributes("-zoomed", True)
            except Exception:
                # Letzter Fallback, falls weder "zoomed" noch "-zoomed"
                # unterstützt wird: Fenstergröße/-position manuell auf die
                # volle Bildschirmgröße setzen.
                bildschirm_breite = top.winfo_screenwidth()
                bildschirm_hoehe = top.winfo_screenheight()
                top.geometry(f"{bildschirm_breite}x{bildschirm_hoehe}+0+0")
        top.deiconify()
        top.lift()
        top.focus_force()

    # ------------------------------------------------------------------
    # .dat-AUSWERTUNG (Erhitzungsmikroskop-Datenbank pro Versuch)
    # ------------------------------------------------------------------
    def finde_dat_datei(self, versuch_pfad):
        """Sucht die .dat-Datei (SQLite-DB des Geräts) in/an einem Versuchseintrag."""
        if os.path.isfile(versuch_pfad) and versuch_pfad.lower().endswith(".dat"):
            return versuch_pfad
        if os.path.isdir(versuch_pfad):
            for datei in os.listdir(versuch_pfad):
                if datei.lower().endswith(".dat"):
                    return os.path.join(versuch_pfad, datei)
        return None

    def lese_versuch_metadaten(self, versuch_pfad):
        """
        Liest die .dat-SQLite-Datenbank eines Versuchs (Erhitzungsmikroskop)
        aus und gibt ein Dict mit Metadaten, Temperaturverlauf und
        charakteristischen Temperaturen zurück. None bei Fehler/keine .dat-Datei.
        """
        dat_pfad = self.finde_dat_datei(versuch_pfad)
        if not dat_pfad:
            return None

        try:
            conn = sqlite3.connect(f"file:{dat_pfad}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("""
                SELECT msmi.Name, msmi.Material, msmi.Date, msmi.User, msmi.DeviceID,
                       msmi.MethodForm, msmi.MethodName, g.Name AS Gruppe,
                       ms.Start_T, ms.End_T, ms.Norm, ms.AnzahlBilder, ms.AnzahlMesswerte,
                       ms.ErweichenNach
                FROM MeasurementSeriesMetaInfo msmi
                JOIN MeasurementSeries ms ON ms.MeasID = msmi.MeasID
                LEFT JOIN Groups g ON g.GroupID = msmi.GroupID
            """)
            grunddaten = dict(cur.fetchone())

            cur.execute("SELECT Temp, Rate, Dwell, SeqNr FROM HPSequenz ORDER BY SeqNr")
            temperaturverlauf = [dict(z) for z in cur.fetchall()]

            cur.execute("""
                SELECT Sinterbeginn_Temp, Sinterpunkt_Temp, Schrumpfungsstart_Temp,
                       Erweichen_Temp, Hemisphaerisch_Temp, Fliessen_Temp
                FROM MeasurementFeature
            """)
            merkmale_row = cur.fetchone()
            merkmale = dict(merkmale_row) if merkmale_row else {}

            conn.close()

            return {
                "grunddaten": grunddaten,
                "temperaturverlauf": temperaturverlauf,
                "merkmale": merkmale,
            }
        except Exception as e:
            print(f"[.dat Fehler] {dat_pfad}: {e}")
            return None

    # ------------------------------------------------------------------
    # PROCESSED_DATA / "Berechnen"
    # ------------------------------------------------------------------
    def processed_data_ordner_fuer(self, raw_data_ordner):
        """Spiegelt einen raw_data-Ordner nach processed_data (…/data/raw_data -> …/data/processed_data)."""
        processed_ordner = raw_data_ordner.replace(
            os.sep + "raw_data", os.sep + "processed_data"
        )
        if processed_ordner == raw_data_ordner:  # falls "raw_data" nicht exakt im Pfad vorkam
            processed_ordner = os.path.join(os.path.dirname(raw_data_ordner), "processed_data")
        return processed_ordner

    def processed_data_pfad_fuer(self, raw_data_ordner, eintrag):
        """
        Spiegelt den Pfad eines raw_data-Eintrags (Datei oder Ordner) nach
        processed_data (…/data/raw_data/X -> …/data/processed_data/X).
        Erzeugt NICHTS - reine Pfad-Berechnung für den Existenz-Check.
        """
        return os.path.join(self.processed_data_ordner_fuer(raw_data_ordner), eintrag)

    def _sanitiere_versuchsnamen(self, name):
        """Muss ident zu _sanitize_name() in EMI_calculation.py sein, damit wir
        denselben Ausgabe-Dateinamen vorhersagen können (Leerzeichen -> _)."""
        return str(name or "").strip().replace(" ", "_")

    def ist_versuch_verarbeitet(self, raw_data_ordner, eintrag, methode=None):
        """
        Prüft, ob ein Versuch bereits verarbeitet wurde.
        Bei EMI/TGA: sucht die vom jeweiligen *_calculation.py-Skript erzeugte
        "<sanitierter_name>_results.parquet"-Datei in processed_data.
        Bei anderen Methoden (noch kein Berechnungs-Skript vorhanden):
        Platzhalter-Check, ob irgendwas mit demselben Namen existiert.
        """
        processed_ordner = self.processed_data_ordner_fuer(raw_data_ordner)
        if methode in BERECHNUNGS_FAEHIGE_METHODEN:
            versuch_name = os.path.splitext(eintrag)[0]
            ziel = os.path.join(processed_ordner, f"{self._sanitiere_versuchsnamen(versuch_name)}_results.parquet")
        else:
            ziel = os.path.join(processed_ordner, eintrag)
        return os.path.exists(ziel)

    # ------------------------------------------------------------------
    # Berechnung (echte Skripte: EMI_calculation.py / TGA_calculation.py)
    # Läuft bewusst NICHT im main.py-Prozess: wird als eigener Python-
    # Subprozess gestartet (wie in der .md als CLI-Aufruf beschrieben), in
    # einem Hintergrund-Thread, damit die GUI währenddessen nicht einfriert
    # und Abstürze/fehlende Pakete im Berechnungs-Skript die App nicht
    # mitreißen.
    # ------------------------------------------------------------------
    def finde_berechnung_skript(self, methode):
        """
        Sucht "<Methode>_calculation.py" in dieser Reihenfolge:
          1) BERECHNUNGS_SKRIPT_PFADE[methode] (falls gesetzt)
          2) neben main.py
          3) <main.py-Ordner>/<Methode>/data_preparation/<Methode>_calculation.py
        Gibt den Pfad zurück oder wirft FileNotFoundError.
        """
        skript_name = f"{methode}_calculation.py"
        skript_ordner = os.path.dirname(os.path.abspath(__file__))
        kandidaten = []
        pfad_override = BERECHNUNGS_SKRIPT_PFADE.get(methode)
        if pfad_override:
            kandidaten.append(pfad_override)
        kandidaten.append(os.path.join(skript_ordner, skript_name))
        kandidaten.append(os.path.join(skript_ordner, methode, "data_preparation", skript_name))

        gefundener_pfad = next((p for p in kandidaten if p and os.path.isfile(p)), None)
        if not gefundener_pfad:
            raise FileNotFoundError(
                f"{skript_name} nicht gefunden. Gesucht an:\n"
                + "\n".join(kandidaten)
                + f"\n\nEntweder das Skript dort ablegen oder BERECHNUNGS_SKRIPT_PFADE['{methode}'] "
                "am Kopf von main.py auf den vollen Pfad setzen."
            )
        return gefundener_pfad

    def fuehre_berechnung_aus(self, methode, unverarbeitete_versuche, log_zeile_callback):
        """
        Startet "<Methode>_calculation.py" als eigenen Python-Subprozess (CLI) -
        NICHT im main.py-Prozess. Läuft in DIESEM Aufruf synchron
        (blockierend), wird daher von starte_berechnung() immer in einem
        Hintergrund-Thread aufgerufen, nie direkt im GUI-Thread.
        Ruft NUR für die übergebenen, noch unverarbeiteten Versuche auf
        (--samples, kein --force) - bestehende processed_data-Dateien
        anderer Versuche bleiben unangetastet. Gruppiert nach raw_data-
        Ordner, da das Skript einen ganzen Ordner voller Sample-Unterordner
        bzw. Rohdateien pro Aufruf erwartet.

        log_zeile_callback(text) wird für JEDE Ausgabezeile des Subprozesses
        sofort aufgerufen (live), damit man im UI sieht, dass/was gerade
        passiert - statt stumm auf das Ende zu warten.

        Gibt None bei Erfolg zurück, sonst einen Fehlertext.
        """
        try:
            skript_pfad = self.finde_berechnung_skript(methode)
        except Exception as e:
            return str(e)

        python_interpreter = os.environ.get("HSMTOOLS_PYTHON") or HSMTOOLS_PYTHON_PFAD or sys.executable

        gruppen = {}
        for staub, eintrag, voller_pfad in unverarbeitete_versuche:
            raw_data_ordner = os.path.dirname(voller_pfad)
            gruppen.setdefault(raw_data_ordner, []).append(eintrag)

        for raw_data_ordner, sample_namen in gruppen.items():
            output_ordner = self.processed_data_ordner_fuer(raw_data_ordner)
            os.makedirs(output_ordner, exist_ok=True)

            befehl = [
                python_interpreter,
                "-u",  # unbuffered stdout -> print()-Zeilen kommen sofort an, nicht erst am Ende gebündelt
                skript_pfad,
                "--input-dir", raw_data_ordner,
                "--output-dir", output_ordner,
                "--samples", *sample_namen,
            ]
            log_zeile_callback(f"$ {' '.join(befehl)}")

            try:
                prozess = subprocess.Popen(
                    befehl,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # beides zusammen -> eine Zeitleiste, nichts geht verloren
                    text=True,
                    bufsize=1,  # Zeilenweise gepuffert -> Zeilen kommen live an, nicht erst am Ende
                )
            except Exception as e:
                return f"Konnte {methode}_calculation.py nicht starten ('{raw_data_ordner}'):\n{e}"

            gesammelte_ausgabe = []
            for zeile in prozess.stdout:
                zeile = zeile.rstrip("\n")
                gesammelte_ausgabe.append(zeile)
                log_zeile_callback(zeile)

            returncode = prozess.wait(timeout=3600)
            if returncode != 0:
                ausgabe = "\n".join(gesammelte_ausgabe[-40:]) or "(keine Ausgabe)"
                return f"{methode}_calculation.py meldete einen Fehler (Exit-Code {returncode}) in '{raw_data_ordner}':\n\n{ausgabe}"

        return None

    def oeffne_berechnungs_log_fenster(self, titel):
        """Öffnet ein kleines Fenster mit Live-Log-Textbox für die laufende Berechnung."""
        log_fenster = ctk.CTkToplevel(self)
        log_fenster.title(titel)
        log_fenster.geometry("760x480")
        log_fenster.attributes("-topmost", True)

        ctk.CTkLabel(
            log_fenster, text=titel, font=("Arial", 14, "bold")
        ).pack(pady=(10, 5))

        log_textbox = ctk.CTkTextbox(log_fenster, width=730, height=400, font=("Consolas", 11))
        log_textbox.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        log_textbox.configure(state="disabled")

        return log_fenster, log_textbox

    def starte_berechnung(self, projekt, methode, rohdaten_frame):
        """
        'Berechnen'-Button. Bei EMI/TGA: startet die echte Berechnung
        (<Methode>_calculation.py) als eigenen Subprozess in einem
        Hintergrund-Thread, mit Live-Log-Fenster - die GUI bleibt
        währenddessen bedienbar und man SIEHT, dass/was gerade passiert.
        Bei anderen Methoden (noch kein Skript vorhanden): reiner
        Platzhalter-Check wie bisher.
        """
        versuche = self.liste_versuche(projekt, methode)
        anzahl_gesamt = len(versuche)

        unverarbeitete = [
            (staub, eintrag, voller_pfad)
            for staub, eintrag, voller_pfad in versuche
            if not self.ist_versuch_verarbeitet(os.path.dirname(voller_pfad), eintrag, methode)
        ]

        if methode not in BERECHNUNGS_FAEHIGE_METHODEN:
            anzahl_verarbeitet = anzahl_gesamt - len(unverarbeitete)
            self._status(
                f"Geprüft: {anzahl_verarbeitet}/{anzahl_gesamt} Versuche bereits in processed_data.",
                "#00ff88",
            )
            for widget in rohdaten_frame.winfo_children():
                widget.destroy()
            self.baue_rohdaten_tab(rohdaten_frame, projekt, methode)
            return

        if not unverarbeitete:
            self._status(f"Alle {methode}-Versuche bereits in processed_data vorhanden.", "#00ff88")
            for widget in rohdaten_frame.winfo_children():
                widget.destroy()
            self.baue_rohdaten_tab(rohdaten_frame, projekt, methode)
            return

        self._status(f"Berechne {len(unverarbeitete)} {methode}-Versuch(e) im Hintergrund ...", "#ffff00")
        log_fenster, log_textbox = self.oeffne_berechnungs_log_fenster(
            f"{methode}-Berechnung läuft: {projekt} ({len(unverarbeitete)} Versuch(e))"
        )

        def log_anhaengen(text):
            # Fenster/Textbox kann inzwischen geschlossen worden sein (User hat
            # das Log-Fenster zugemacht, während die Berechnung im Hintergrund
            # noch läuft) - dann NICHT mehr versuchen, sie zu beschreiben,
            # sonst TclError "invalid command name ...". Die Berechnung selbst
            # (Subprozess) läuft davon unbeeindruckt im Hintergrund weiter.
            if not log_textbox.winfo_exists():
                return
            try:
                log_textbox.configure(state="normal")
                log_textbox.insert("end", text + "\n")
                log_textbox.see("end")
                log_textbox.configure(state="disabled")
            except Exception:
                pass

        def hintergrund_arbeit():
            def live_zeile(zeile):
                # Kommt aus dem Hintergrund-Thread -> Textbox-Update in den GUI-Thread verlagern.
                self.after(0, lambda z=zeile: log_anhaengen(z))

            fehler = self.fuehre_berechnung_aus(methode, unverarbeitete, live_zeile)
            self.after(0, lambda: fertig_im_gui_thread(fehler))

        def fertig_im_gui_thread(fehler):
            if fehler:
                log_anhaengen(f"\n--- FEHLER ---\n{fehler}")
                messagebox.showerror(f"{methode}-Berechnung fehlgeschlagen", fehler)
                self._status(f"{methode}-Berechnung fehlgeschlagen (siehe Fehlermeldung/Log).", "#ff5555")
            else:
                jetzt_verarbeitet = anzahl_gesamt - sum(
                    1 for _s, e, p in versuche
                    if not self.ist_versuch_verarbeitet(os.path.dirname(p), e, methode)
                )
                log_anhaengen(f"\n--- FERTIG ({jetzt_verarbeitet}/{anzahl_gesamt} verarbeitet) ---")
                self._status(
                    f"{methode}-Berechnung abgeschlossen ({jetzt_verarbeitet}/{anzahl_gesamt} verarbeitet).",
                    "#00ff88",
                )
            # Auch hier: Log-Fenster und Rohdaten-Frame können vom User
            # inzwischen geschlossen worden sein -> vor jedem Zugriff prüfen.
            if log_fenster.winfo_exists():
                try:
                    log_fenster.attributes("-topmost", False)
                except Exception:
                    pass
            if rohdaten_frame.winfo_exists():
                for widget in rohdaten_frame.winfo_children():
                    widget.destroy()
                self.baue_rohdaten_tab(rohdaten_frame, projekt, methode)
                # Ergebnisse-Cache sofort neu aufbauen
                self.ergebnisse_cache[methode] = self.berechne_ergebnisse_fuer_methode(
                    projekt, methode
                )
                tab = getattr(self, "_ergebnisse_tabs", {}).get((projekt, methode))

                if tab and tab.winfo_exists():
                    for widget in tab.winfo_children():
                        widget.destroy()
                    self.baue_ergebnisse_tab(tab, projekt, methode)
                    
        threading.Thread(target=hintergrund_arbeit, daemon=True).start()

    # ------------------------------------------------------------------
    # TAB: METADATEN (vormals "Ergebnisse")
    # ------------------------------------------------------------------
    def baue_metadaten_tab(self, parent, projekt, methode):
        """
        Zeigt ALLE Versuche aus raw_data mit den Werten, die der Benutzer im
        Google-Sheet-Datenblatt einträgt (Spalten "Temperaturverlauf", "Gas",
        "Durchfluss" im EMI-Settings-Bereich, verknüpft über die Spalte
        "Messung" = Ordnername). Charakteristische Temperaturen (SIN/SST/ST/
        DT/HT/FT) o.ä. werden hier NICHT berechnet/angezeigt - das kommt
        später über ein separates Auswerte-Script.
        """
        versuche = self.liste_versuche(projekt, methode)

        if not versuche:
            ctk.CTkLabel(parent, text="Keine Versuche in raw_data gefunden.").pack(pady=20)
            return

        scroll = ctk.CTkScrollableFrame(parent, width=1100, height=750)
        scroll.pack(padx=5, pady=5, fill="both", expand=True)

        if methode == "EMI":
            spalten = ["Versuch", "Material", "Kommentar (Lime addition)", "Temperaturverlauf", "Gas", "Durchfluss"]
        elif methode == "TGA":
            spalten = [
                "Versuch", "Material", "Kommentar (Lime addition)", "TGA ID",
                "Gas", "Tmax", "Operator", "Mass Loss Scale [%]", "CaO [%]",
            ]
        elif methode == "SEM":
            spalten = [
                "Versuch", "Material", "Kommentar (Lime addition)", "SEM ID",
                "Box N°", "embedded by", "polished by", "overview by", "detail by",
            ]
        else:
            spalten = ["Versuch", "Info"]

        # Spalten gleichmäßig über die volle (jetzt größere) Fensterbreite verteilen
        for spalte_index in range(len(spalten)):
            scroll.grid_columnconfigure(spalte_index, weight=1, minsize=140)

        for spalte_index, titel in enumerate(spalten):
            ctk.CTkLabel(
                scroll, text=titel, font=("Arial", 12, "bold"),
            ).grid(row=0, column=spalte_index, padx=10, pady=(0, 10), sticky="w")

        for zeilen_index, (staub, eintrag, voller_pfad) in enumerate(versuche, start=1):
            versuch_name = os.path.splitext(eintrag)[0]

            if methode == "EMI":
                emi_parameter = self.hole_emi_parameter_fuer_versuch(versuch_name)
                if emi_parameter:
                    werte = [
                        versuch_name,
                        emi_parameter["material"],
                        emi_parameter["kommentar"],
                        emi_parameter["temperaturverlauf"],
                        emi_parameter["gas"],
                        emi_parameter["durchfluss"],
                    ]
                else:
                    werte = [versuch_name, "-", "-", "-", "-", "-"]
            elif methode == "TGA":
                sheet_name = self._tga_versuchsname_fuer_sheet(eintrag)
                tga_parameter = self.hole_tga_parameter_fuer_versuch(sheet_name)
                if tga_parameter:
                    werte = [
                        versuch_name,
                        tga_parameter["material"],
                        tga_parameter["kommentar"],
                        tga_parameter["tga_id"],
                        tga_parameter["gas"],
                        tga_parameter["tmax"],
                        tga_parameter["operator"],
                        tga_parameter["mass_loss_scale_pct"],
                        tga_parameter["cao_pct"] if tga_parameter["cao_pct"] is not None else "-",
                    ]
                else:
                    werte = [versuch_name, "-", "-", "-", "-", "-", "-", "-", "-"]
            elif methode == "SEM":
                # SEM-Rohdaten-Ordner heißen wie die allgemeine Proben-"id"
                # im Sheet (z.B. "RT74"), keine Nummer-Präfix/Suffix-Logik
                # wie bei TGA nötig - versuch_name direkt verwenden.
                sem_parameter = self.hole_sem_parameter_fuer_versuch(versuch_name)
                if sem_parameter:
                    werte = [
                        versuch_name,
                        sem_parameter["material"],
                        sem_parameter["kommentar"],
                        sem_parameter["sem_id"],
                        sem_parameter["box_n"],
                        sem_parameter["embedded_by"],
                        sem_parameter["polished_by"],
                        sem_parameter["overview_by"],
                        sem_parameter["detail_by"],
                    ]
                else:
                    werte = [versuch_name, "-", "-", "-", "-", "-", "-", "-", "-"]
            else:
                werte = [versuch_name, "Für diese Methode noch keine Sheet-Anbindung."]

            for spalte_index, wert in enumerate(werte):
                ctk.CTkLabel(
                    scroll, text=str(wert or "-"), font=("Arial", 11),
                    anchor="w", justify="left", wraplength=220,
                ).grid(row=zeilen_index, column=spalte_index, padx=10, pady=5, sticky="w")

    # ------------------------------------------------------------------
    # TAB: ROHDATEN
    # ------------------------------------------------------------------
    def baue_rohdaten_tab(self, parent, projekt, methode):
        """Zeigt die Datei-/Ordnerliste aus raw_data (ohne Verarbeitet-Häkchen)
        und einen 'Berechnen'-Button unten rechts."""
        if methode == "TGA":
            self.baue_rohdaten_tab_tga(parent, projekt, methode)
            return
        if methode == "SEM":
            self.baue_rohdaten_tab_sem(parent, projekt, methode)
            return

        versuche = self.liste_versuche(projekt, methode)

        if not versuche:
            ctk.CTkLabel(parent, text="Keine lokalen Rohdaten gefunden.").pack(pady=20)
            return

        kopfzeile = ctk.CTkFrame(parent, fg_color="transparent")
        kopfzeile.pack(fill="x", padx=5, pady=(5, 0))
        ctk.CTkLabel(kopfzeile, text="Versuche", font=("Arial", 12, "bold")).pack(side="left", padx=5)
        ctk.CTkLabel(kopfzeile, text="Processed", font=("Arial", 12, "bold")).pack(side="right", padx=20)

        # Button-Leiste ZUERST mit side="bottom" packen, damit ihr Platz fest
        # reserviert ist, BEVOR die (ggf. große) Scrollbar-Fläche gepackt wird -
        # sonst kann eine hohe height=... im CTkScrollableFrame den Button
        # nach unten aus dem sichtbaren Bereich drücken.
        button_zeile = ctk.CTkFrame(parent, fg_color="transparent")
        button_zeile.pack(side="bottom", fill="x", padx=5, pady=8)
        ctk.CTkButton(
            button_zeile,
            text="Alle Berechnen",
            fg_color=MUL_TURKIS,
            command=lambda: self.starte_berechnung(projekt, methode, parent),
        ).pack(side="right")

        scroll = ctk.CTkScrollableFrame(parent, width=1100, height=650)
        scroll.pack(padx=5, pady=(5, 0), fill="both", expand=True)

        for staub, eintrag, voller_pfad in versuche:
            zeile = ctk.CTkFrame(scroll, fg_color="transparent")
            zeile.pack(fill="x", pady=3)
            symbol = "📁" if os.path.isdir(voller_pfad) else "📄"
            ctk.CTkLabel(zeile, text=f"{symbol} [{staub}] {eintrag}", anchor="w").pack(
                side="left", padx=5
            )
            verarbeitet = self.ist_versuch_verarbeitet(os.path.dirname(voller_pfad), eintrag, methode)
            ctk.CTkLabel(
                zeile,
                text="✓" if verarbeitet else "",
                font=("Arial", 14, "bold"),
                text_color="#00ff88",
                width=80,
                anchor="e",
            ).pack(side="right", padx=20)

    # ------------------------------------------------------------------
    # DIAGRAMM-EINSTELLUNGEN (Titel/Labels/Achsenbereiche) - dauerhaft
    # ------------------------------------------------------------------
    def _pfad_diagramm_einstellungen(self, projekt, methode):
        """Eine JSON-Datei pro Projekt+Methode im Projekt-Root, versteckt (führender Punkt)."""
        root = self.get_projekt_root(projekt)
        return os.path.join(root, f".diagramm_einstellungen_{methode}.json")

    def lade_diagramm_einstellungen(self, projekt, methode, standard):
        """
        Lädt gespeicherte Diagramm-Einstellungen von der Platte und legt sie
        über die Standardwerte (Fallback, falls Datei fehlt/fehlerhaft oder
        neue Felder seit dem letzten Speichern dazugekommen sind).
        """
        pfad = self._pfad_diagramm_einstellungen(projekt, methode)
        zustand = dict(standard)
        try:
            with open(pfad, "r", encoding="utf-8") as f:
                gespeichert = json.load(f)
            for schluessel, wert in gespeichert.items():
                if schluessel in zustand:
                    zustand[schluessel] = wert
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return zustand

    def speichere_diagramm_einstellungen(self, projekt, methode, zustand):
        """Schreibt die aktuellen Diagramm-Einstellungen auf die Platte (bleiben nach Neustart erhalten)."""
        pfad = self._pfad_diagramm_einstellungen(projekt, methode)
        speicherbar = {k: v for k, v in zustand.items() if k != "versuch_name" and not str(k).startswith("_")}
        try:
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(speicherbar, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[Diagramm-Einstellungen speichern Fehler] {pfad}: {e}")

    # ------------------------------------------------------------------
    # DIAGRAMM-DARSTELLUNG PRO VERSUCH (Ergebnisse-Tabs EMI/TGA): jedes
    # Diagramm jedes einzelnen Versuchs behaelt seine eigene Darstellung
    # (Titel, Achsen, Farbe, Schrift, ...) - analog zum bereits bestehenden
    # Muster fuer die Rohdaten-Filter-Einstellungen (siehe oben). Eigene
    # Ablagedatei, damit sie NICHT mit der aelteren, projektweiten
    # ".diagramm_einstellungen_<methode>.json" kollidiert; diese bleibt als
    # Fallback/Ausgangsbasis fuer Versuche ohne eigene Einstellungen aktiv.
    # ------------------------------------------------------------------
    def _pfad_diagramm_einstellungen_versuch(self, projekt, methode):
        root = self.get_projekt_root(projekt)
        return os.path.join(root, f".diagramm_einstellungen_{methode}_versuche.json")

    def _lade_diagramm_einstellungen_versuch_datei(self, projekt, methode):
        pfad = self._pfad_diagramm_einstellungen_versuch(projekt, methode)
        try:
            with open(pfad, "r", encoding="utf-8") as f:
                daten = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"versuche": {}}
        if isinstance(daten, dict):
            daten.setdefault("versuche", {})
            return daten
        return {"versuche": {}}

    def _speichere_diagramm_einstellungen_versuch_datei(self, projekt, methode, daten):
        pfad = self._pfad_diagramm_einstellungen_versuch(projekt, methode)
        try:
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(daten, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[Diagramm-Einstellungen (pro Versuch) speichern Fehler] {pfad}: {e}")

    def lade_diagramm_einstellungen_fuer_versuch(self, projekt, methode, versuch_schluessel, standard):
        """
        Laedt die Darstellungs-Einstellungen (Titel, Achsen, Farbe, Schrift,
        Wertebereiche, ...) fuer EINEN einzelnen Versuch. Prioritaet
        (niedrig -> hoch): eingebaute Standardwerte < aeltere projektweite
        Datei (Migrations-/Erstfall-Basis, z.B. von vor der Umstellung auf
        pro-Versuch-Einstellungen) < individuell fuer DIESEN Versuch
        gespeicherte Werte.
        """
        zustand = dict(standard)
        alte_gemeinsame = self.lade_diagramm_einstellungen(projekt, methode, {})
        zustand.update({k: v for k, v in alte_gemeinsame.items() if k in zustand})
        daten = self._lade_diagramm_einstellungen_versuch_datei(projekt, methode)
        if versuch_schluessel:
            zustand.update(daten.get("versuche", {}).get(versuch_schluessel, {}))
        return zustand

    def speichere_diagramm_einstellungen_fuer_versuch(self, projekt, methode, versuch_schluessel, zustand):
        """Speichert die aktuelle Darstellung NUR fuer diesen einen Versuch."""
        if not versuch_schluessel:
            return
        daten = self._lade_diagramm_einstellungen_versuch_datei(projekt, methode)
        speicherbar = {k: v for k, v in zustand.items() if k != "versuch_name" and not str(k).startswith("_")}
        daten["versuche"][versuch_schluessel] = speicherbar
        self._speichere_diagramm_einstellungen_versuch_datei(projekt, methode, daten)

    def speichere_diagramm_einstellungen_fuer_alle(self, projekt, methode, versuch_schluessel_liste, zustand):
        """
        Uebernimmt die aktuelle Darstellung fuer ALLE uebergebenen Versuche
        (Button 'Fuer alle Versuche uebernehmen') - ueberschreibt dabei auch
        bereits individuell abweichend gesetzte Werte dieser Versuche.
        Zusaetzlich wird der Zustand als neuer projektweiter "Standard"
        (aeltere, gemeinsame Datei) gespeichert, damit spaeter neu hinzu-
        kommende Versuche (noch ohne eigene Einstellung) ebenfalls damit
        starten.
        """
        speicherbar = {k: v for k, v in zustand.items() if k != "versuch_name" and not str(k).startswith("_")}
        self.speichere_diagramm_einstellungen(projekt, methode, zustand)
        daten = self._lade_diagramm_einstellungen_versuch_datei(projekt, methode)
        for versuch_schluessel in versuch_schluessel_liste:
            if versuch_schluessel:
                daten["versuche"][versuch_schluessel] = dict(speicherbar)
        self._speichere_diagramm_einstellungen_versuch_datei(projekt, methode, daten)

    # ------------------------------------------------------------------
    # ROHDATEN-VORSCHAU (TGA): Filter-1/Filter-2 Einstellungen - eigene
    # Ablagedatei, damit sie NICHT mit den Ergebnisse-Tab-Einstellungen
    # kollidiert (speichere_diagramm_einstellungen() ueberschreibt die
    # komplette Datei mit dem jeweils uebergebenen zustand-Dict).
    # ------------------------------------------------------------------
    def _pfad_rohdaten_filter_einstellungen(self, projekt, methode):
        root = self.get_projekt_root(projekt)
        return os.path.join(root, f".rohdaten_filter_einstellungen_{methode}.json")

    def versuch_schluessel_rohdaten_filter(self, projekt, voller_pfad):
        """
        Eindeutiger, stabiler Schluessel fuer EINEN Versuch (Datei/Ordner in
        raw_data), unter dem seine individuellen Filter-Einstellungen in der
        JSON-Datei abgelegt werden. Relativ zum Projekt-Root (nicht der
        absolute Pfad), damit die Zuordnung auch nach einem Verschieben des
        Projekt-Ordners (z.B. anderer Rechner/Pfad) erhalten bleibt.
        """
        root = self.get_projekt_root(projekt)
        try:
            return os.path.relpath(voller_pfad, root).replace("\\", "/")
        except ValueError:
            # z.B. unterschiedliche Laufwerksbuchstaben unter Windows
            return os.path.basename(voller_pfad)

    def _lade_rohdaten_filter_datei(self, projekt, methode):
        """
        Liest die komplette Rohdaten-Filter-Einstellungsdatei fuer
        projekt+methode ein. Struktur (NEU, pro Versuch getrennt):

            {
              "standard": {...zuletzt per "Fuer alle uebernehmen" gesetzte
                           Werte; Fallback fuer Versuche OHNE eigene
                           gespeicherte Einstellungen...},
              "versuche": {
                  "<versuch_schluessel>": {...individuelle Einstellungen
                                            genau dieses Versuchs...},
                  ...
              }
            }

        Migration: Dateien aus der ALTEN Version (vor Umstellung auf
        pro-Versuch-Einstellungen) enthalten noch KEIN "versuche"-Feld,
        sondern direkt die flachen Filter-Schluessel/Werte auf oberster
        Ebene. Diese werden automatisch als "standard" uebernommen, damit
        bereits gespeicherte Werte beim ersten Start nach dem Update nicht
        verloren gehen (sie gelten dann als Ausgangswerte fuer alle
        Versuche, bis diese individuell veraendert werden).
        """
        pfad = self._pfad_rohdaten_filter_einstellungen(projekt, methode)
        try:
            with open(pfad, "r", encoding="utf-8") as f:
                rohdaten = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"standard": {}, "versuche": {}}

        if isinstance(rohdaten, dict) and ("versuche" in rohdaten or "standard" in rohdaten):
            rohdaten.setdefault("standard", {})
            rohdaten.setdefault("versuche", {})
            return rohdaten

        # Alte, flache Datei -> als "standard" fuer alle Versuche interpretieren.
        return {"standard": rohdaten if isinstance(rohdaten, dict) else {}, "versuche": {}}

    def _speichere_rohdaten_filter_datei(self, projekt, methode, daten):
        pfad = self._pfad_rohdaten_filter_einstellungen(projekt, methode)
        try:
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(daten, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[Rohdaten-Filter-Einstellungen speichern Fehler] {pfad}: {e}")

    def lade_rohdaten_filter_einstellungen_fuer_versuch(self, projekt, methode, versuch_schluessel, standard):
        """
        Laedt die Filter-Einstellungen fuer EINEN einzelnen Versuch.
        Prioritaet (niedrig -> hoch): Programm-Defaults (standard) <
        gespeicherter projektweiter "standard" (zuletzt per "Fuer alle
        uebernehmen" gesetzt) < individuell fuer DIESEN Versuch gespeicherte
        Werte.
        """
        daten = self._lade_rohdaten_filter_datei(projekt, methode)
        zustand = dict(standard)
        zustand.update(daten.get("standard", {}))
        zustand.update(daten.get("versuche", {}).get(versuch_schluessel, {}))
        return zustand

    def speichere_rohdaten_filter_einstellungen_fuer_versuch(self, projekt, methode, versuch_schluessel, zustand):
        """Speichert die aktuellen Filter-Einstellungen NUR fuer diesen einen Versuch (Button 'Uebernehmen')."""
        daten = self._lade_rohdaten_filter_datei(projekt, methode)
        daten["versuche"][versuch_schluessel] = dict(zustand)
        self._speichere_rohdaten_filter_datei(projekt, methode, daten)

    def speichere_rohdaten_filter_einstellungen_fuer_alle(self, projekt, methode, versuch_schluessel_liste, zustand):
        """
        Uebernimmt die aktuell im Panel eingestellten Filter-Einstellungen
        fuer ALLE uebergebenen Versuche (Button 'Fuer alle uebernehmen') -
        ueberschreibt dabei auch bereits individuell abweichend gesetzte
        Werte dieser Versuche. Zusaetzlich wird der Zustand als neuer
        projektweiter "standard" gemerkt, damit spaeter neu hinzukommende
        Versuche (noch keine eigene Einstellung) ebenfalls damit starten.
        """
        daten = self._lade_rohdaten_filter_datei(projekt, methode)
        daten["standard"] = dict(zustand)
        for versuch_schluessel in versuch_schluessel_liste:
            daten["versuche"][versuch_schluessel] = dict(zustand)
        self._speichere_rohdaten_filter_datei(projekt, methode, daten)

    def _lade_rohdaten_dataframe_tga(self, voller_pfad):
        """
        Liest eine rohe TGA-.txt-Datei (Time(s), Temperature(...C), Delta
        m(mg), ...) DIREKT ein - fuer die Live-Vorschau im Rohdaten-Tab,
        BEVOR "Berechnen" gedrueckt wurde. Portierung der Kernlogik aus
        TGA_calculation.py (_get_weight_mg / _read_raw_dataframe /
        _find_column), ohne Abhaengigkeit von project_paths.py.

        Gibt ein pandas DataFrame mit den Spalten time_min, temperature_C,
        dm_original_mg zurueck (chronologisch sortiert), oder None bei
        Fehlern (fehlende Spalten, kein Startgewicht, kein gueltiges
        Rohdaten-Format, o.ae.).
        """
        import re
        import pandas as pd

        if not os.path.isfile(voller_pfad):
            return None

        gewicht_pattern = re.compile(r"Weight:\s*([0-9]+\.?[0-9]*)\s*mg", re.IGNORECASE)
        startgewicht = None
        header_zeile = None
        try:
            with open(voller_pfad, encoding="ISO-8859-1") as f:
                for i, zeile in enumerate(f):
                    if zeile.startswith("#"):
                        treffer = gewicht_pattern.search(zeile)
                        if treffer:
                            startgewicht = float(treffer.group(1))
                    elif zeile.startswith("Time(s)"):
                        header_zeile = i
                        break
        except OSError:
            return None

        if not startgewicht or header_zeile is None:
            return None

        try:
            raw = pd.read_csv(
                voller_pfad, delimiter=",", header=header_zeile, encoding="unicode_escape"
            )
        except Exception as e:
            print(f"[Rohdaten-Vorschau] Konnte {voller_pfad} nicht lesen: {e}")
            return None

        def _finde_spalte(spalten, *teilstrings):
            for spalte in spalten:
                klein = str(spalte).lower()
                if all(s.lower() in klein for s in teilstrings):
                    return spalte
            return None

        zeit_spalte = _finde_spalte(raw.columns, "time")
        temp_spalte = _finde_spalte(raw.columns, "temperat")
        dm_spalte = _finde_spalte(raw.columns, "delta", "m")
        if not (zeit_spalte and temp_spalte and dm_spalte):
            return None

        try:
            df = pd.DataFrame({
                "time_min": raw[zeit_spalte].astype(float) / 60.0,
                "temperature_C": raw[temp_spalte].astype(float),
                "dm_original_mg": raw[dm_spalte].astype(float),
            }).dropna(subset=["time_min", "temperature_C", "dm_original_mg"])
        except Exception as e:
            print(f"[Rohdaten-Vorschau] Ungueltige Werte in {voller_pfad}: {e}")
            return None

        if df.empty:
            return None
        return df.sort_values("time_min").reset_index(drop=True)

    def _rohdaten_filter_parameter_ansicht(self, zustand, praefix):
        """
        Liefert ein Dict mit den (NICHT praefigierten) Basis-Schluesseln aus
        TGA_FILTER_PARAMETER, dessen Werte aus den PRAEFIGIERTEN
        zustand-Eintraegen (z.B. "f1_filter_butter_cutoff") gelesen werden -
        passend zur Signatur von _baue_filter_objekt().
        """
        ansicht = {}
        for felder in TGA_FILTER_PARAMETER.values():
            for schluessel, _label, _default in felder:
                ansicht[schluessel] = zustand.get(f"{praefix}_{schluessel}")
        return ansicht

    def _baue_filter_objekt(self, typ, parameter):
        """
        Baut ein Filter-Objekt aus helper/Filter.py fuer den gegebenen
        Typnamen (siehe TGA_FILTER_OPTIONEN). `parameter` ist ein Dict mit
        den NICHT praefigierten Basis-Schluesseln aus TGA_FILTER_PARAMETER
        (z.B. {"filter_butter_cutoff": 0.05, "filter_butter_order": 2}).

        Gibt None zurueck bei "Kein Filter", fehlendem Import (siehe
        _FILTER_IMPORT_FEHLER) oder ungueltigen Werten - der Aufrufer soll
        dann die ungefilterten Werte anzeigen.
        """
        if typ == "Kein Filter" or ButterworthFilter is None:
            return None
        try:
            if typ == "Butterworth":
                return ButterworthFilter(
                    cutoff=float(parameter.get("filter_butter_cutoff", 0.05)),
                    order=int(parameter.get("filter_butter_order", 2)),
                    time_unit="sec",
                )
            if typ == "Savitzky-Golay":
                fenster = int(parameter.get("filter_savgol_window", 15))
                if fenster % 2 == 0:
                    fenster += 1
                return SavitzkyGolayFilter(
                    window_length=fenster,
                    polyorder=int(parameter.get("filter_savgol_polyorder", 2)),
                )
            if typ == "Exponentielles gleitendes Mittel":
                return ExponentialMovingAverage(alpha=float(parameter.get("filter_ema_alpha", 0.3)))
            if typ == "Median":
                kernel = int(parameter.get("filter_median_kernel", 9))
                if kernel % 2 == 0:
                    kernel += 1
                return MedianFilter(kernel_size=kernel)
            if typ == "Gaussian":
                return GaussianFilter(sigma=float(parameter.get("filter_gauss_sigma", 1.0)))
            if typ == "Gleitender Mittelwert":
                return RollingAverage(sampling_rate=int(parameter.get("filter_rollavg_fenster", 10)))
        except Exception as e:
            print(f"[Filter-Aufbau] Ungueltige Einstellungen ({typ}): {e}")
            return None
        return None

    def zeichne_rohdaten_vorschau_tga(self, voller_pfad, fig, achsen, canvas, zustand, zoom_zustand=None):
        """
        Zeichnet die 2x2 Live-Vorschau im TGA-Rohdaten-Tab NEU:
            oben:  Filter 1 auf die Massenkurve (dm)      - roh | gefiltert
            unten: Filter 2 auf die Reaktionskinetik (dm/dt) - roh | gefiltert
        dm/dt wird dabei (wie im echten TGA_calculation.py-Pipeline-Schritt)
        aus der BEREITS mit Filter 1 geglaetteten dm-Kurve abgeleitet, bevor
        Filter 2 zusaetzlich die Kinetik glaettet.

        `zoom_zustand` (optional): Dict mit "x_min"/"x_max" - falls durch
        die Zoom-Lupe (siehe _aktiviere_zoom_lupe) ein Zeitbereich aktiv
        ausgewaehlt ist, wird dieser nach dem Neuzeichnen wieder auf alle
        4 Diagramme angewendet, damit ein Filterwechsel oder ein Wechsel
        des Versuchs den aktuellen Zoom nicht verwirft.
        """
        import numpy as np
        import pandas as pd

        ax_f1_roh, ax_f1_gefiltert, ax_f2_roh, ax_f2_gefiltert = achsen
        for ax in achsen:
            ax.clear()

        df = self._lade_rohdaten_dataframe_tga(voller_pfad)
        if df is None or df.empty:
            for ax in achsen:
                ax.text(
                    0.5, 0.5, "Konnte Rohdaten nicht laden.",
                    ha="center", va="center", transform=ax.transAxes,
                )
            canvas.draw_idle()
            return

        zeit = df["time_min"].to_numpy()
        dm_roh = df["dm_original_mg"].to_numpy()

        f1_typ = zustand.get("f1_filter_typ", "Kein Filter")
        f1_filter = self._baue_filter_objekt(
            f1_typ, self._rohdaten_filter_parameter_ansicht(zustand, "f1")
        )
        if f1_filter is not None:
            try:
                dm_gefiltert = f1_filter(df["time_min"], pd.Series(dm_roh)).to_numpy()
            except Exception as e:
                print(f"[Rohdaten-Filter-1] Fehlgeschlagen, zeige ungefiltert: {e}")
                dm_gefiltert = dm_roh.copy()
        else:
            dm_gefiltert = dm_roh.copy()

        dmdt_roh = (
            pd.Series(dm_gefiltert).diff() / pd.Series(zeit).diff()
        ).bfill().to_numpy()

        f2_typ = zustand.get("f2_filter_typ", "Kein Filter")
        f2_filter = self._baue_filter_objekt(
            f2_typ, self._rohdaten_filter_parameter_ansicht(zustand, "f2")
        )
        if f2_filter is not None:
            try:
                x_index = pd.Series(np.arange(len(dmdt_roh), dtype=float))
                dmdt_gefiltert = f2_filter(x_index, pd.Series(dmdt_roh)).to_numpy()
            except Exception as e:
                print(f"[Rohdaten-Filter-2] Fehlgeschlagen, zeige ungefiltert: {e}")
                dmdt_gefiltert = dmdt_roh.copy()
        else:
            dmdt_gefiltert = dmdt_roh.copy()

        ax_f1_roh.plot(zeit, dm_roh, color=MUL_TURKIS, linewidth=1.0)
        ax_f1_roh.set_title(f"Filter 1: {f1_typ} (Rohdaten)")
        ax_f1_roh.set_xlabel("Zeit [min]")
        ax_f1_roh.set_ylabel("Delta m [mg]")
        ax_f1_roh.grid(True, alpha=0.3)

        ax_f1_gefiltert.plot(zeit, dm_gefiltert, color=MUL_TURKIS, linewidth=1.5)
        ax_f1_gefiltert.set_title(f"Filter 1: {f1_typ} (gefiltert)")
        ax_f1_gefiltert.set_xlabel("Zeit [min]")
        ax_f1_gefiltert.set_ylabel("Delta m [mg]")
        ax_f1_gefiltert.grid(True, alpha=0.3)

        ax_f2_roh.plot(zeit, dmdt_roh, color=MUL_TURKIS, linewidth=1.0)
        ax_f2_roh.axhline(0, linestyle="--", linewidth=0.5, color="grey")
        ax_f2_roh.set_title(f"Filter 2: {f2_typ} (Rohdaten)")
        ax_f2_roh.set_xlabel("Zeit [min]")
        ax_f2_roh.set_ylabel("dm/dt [mg/min]")
        ax_f2_roh.grid(True, alpha=0.3)

        ax_f2_gefiltert.plot(zeit, dmdt_gefiltert, color=MUL_TURKIS, linewidth=1.5)
        ax_f2_gefiltert.axhline(0, linestyle="--", linewidth=0.5, color="grey")
        ax_f2_gefiltert.set_title(f"Filter 2: {f2_typ} (gefiltert)")
        ax_f2_gefiltert.set_xlabel("Zeit [min]")
        ax_f2_gefiltert.set_ylabel("dm/dt [mg/min]")
        ax_f2_gefiltert.grid(True, alpha=0.3)

        if zoom_zustand is not None:
            self._wende_zoom_an(achsen, zoom_zustand)

        # Grosszuegigerer Abstand zwischen den 4 Diagrammen (Standard-
        # tight_layout() ohne Padding wirkt zu eng beieinander).
        fig.tight_layout(w_pad=3.0, h_pad=3.0, pad=1.5)
        canvas.draw_idle()

    # ------------------------------------------------------------------
    # ZOOM-LUPE fuer die 2x2 Live-Vorschau (TGA-Rohdaten-Tab): Rechteck
    # aufziehen zoomt den Zeitbereich synchron in allen 4 Diagrammen,
    # Doppelklick setzt den Zoom zurueck.
    # ------------------------------------------------------------------
    def _wende_zoom_an(self, achsen, zoom_zustand):
        """
        Wendet den in `zoom_zustand` ("x_min"/"x_max", None = kein Zoom)
        gehaltenen Zeitbereich auf alle uebergebenen Achsen an. Die
        x-Achse wird dabei fuer ALLE Diagramme identisch gesetzt (gleiche
        Zeitbasis); die y-Achse wird pro Diagramm SEPARAT auf die im
        gezoomten Zeitbereich tatsaechlich sichtbaren Werte skaliert
        ("dynamisch angepasst"), da Filter 1 (dm) und Filter 2 (dm/dt)
        voellig unterschiedliche Wertebereiche haben.
        """
        import numpy as np

        x_min, x_max = zoom_zustand.get("x_min"), zoom_zustand.get("x_max")
        for ax in achsen:
            if x_min is None or x_max is None:
                ax.relim()
                ax.autoscale(enable=True, axis="both")
                continue
            ax.set_xlim(x_min, x_max)
            y_werte = []
            for linie in ax.get_lines():
                xdata = np.asarray(linie.get_xdata())
                ydata = np.asarray(linie.get_ydata())
                if xdata.size == 0:
                    continue
                maske = (xdata >= x_min) & (xdata <= x_max)
                if maske.any():
                    y_werte.append(ydata[maske])
            if not y_werte:
                continue
            alle_y = np.concatenate(y_werte)
            alle_y = alle_y[~np.isnan(alle_y)]
            if alle_y.size == 0:
                continue
            y_min, y_max = float(alle_y.min()), float(alle_y.max())
            spanne = (y_max - y_min) or max(abs(y_max), 1.0)
            puffer = spanne * 0.08
            ax.set_ylim(y_min - puffer, y_max + puffer)

    def _dynamische_figsize(self, frame, spalten, zeilen, mindest_breite_px=650, mindest_hoehe_px=420,
                             breite_anteil=1.0, hoehe_anteil=1.0, dpi=100):
        """
        Berechnet eine an den tatsächlich verfügbaren Platz (Frame- bzw.
        ersatzweise Bildschirmgröße) angepasste figsize, statt eine fixe
        Größe zu erzwingen - dadurch bekommen die Diagramme auf kleinen
        Laptop-Bildschirmen genug Platz zueinander und wirken auf großen
        Monitoren nicht winzig/eng zusammengequetscht.
        """
        try:
            frame.update_idletasks()
            breite_px = frame.winfo_width()
            hoehe_px = frame.winfo_height()
        except Exception:
            breite_px = hoehe_px = 0

        # Frame evtl. noch nicht "gemappt" (winfo liefert dann 1 oder 0) ->
        # auf Bildschirmgröße als Ersatz zurückfallen, damit der allererste
        # Diagrammaufbau nicht winzig gerät.
        if breite_px <= 1:
            breite_px = int(self.winfo_screenwidth() * 0.55)
        if hoehe_px <= 1:
            hoehe_px = int(self.winfo_screenheight() * 0.6)

        breite_px = max(int(breite_px * breite_anteil), mindest_breite_px)
        hoehe_px = max(int(hoehe_px * hoehe_anteil), mindest_hoehe_px)

        # Nach oben absichern: auf sehr großen Bildschirmen sollen die
        # Diagramme nicht unnötig riesig (und damit die Schrift winzig
        # relativ zur Fläche) werden.
        max_breite = max(self.winfo_screenwidth() - 200, mindest_breite_px)
        max_hoehe = max(self.winfo_screenheight() - 200, mindest_hoehe_px)
        breite_px = min(breite_px, max_breite)
        hoehe_px = min(hoehe_px, max_hoehe)

        return (breite_px / dpi, hoehe_px / dpi)

    def _aktiviere_zoom_lupe(self, fig, achsen, canvas, zoom_zustand):
        """
        Macht die 2x2-Vorschau per Maus zoombar ("Lupe"):
          - Klick + Ziehen in einem der 4 Diagramme zieht ein rechteckiges
            Auswahlfeld auf. Beim Loslassen wird der ueberstrichene
            Zeitbereich (x-Achse) auf ALLE 4 Diagramme uebertragen (siehe
            _wende_zoom_an) - die y-Achse jedes Diagramms passt sich dabei
            automatisch an die im gewaehlten Zeitbereich sichtbaren Werte
            an.
          - Doppelklick (egal in welchem Diagramm) setzt den Zoom wieder
            auf die volle Ansicht zurueck.
        `zoom_zustand` wird von aussen (zeichne_rohdaten_vorschau_tga)
        mitgelesen, damit ein Filter-/Versuchswechsel den aktuell aktiven
        Zoom nicht verwirft.
        """
        import matplotlib.patches as patches

        ziehen = {"achse": None, "start_x": None, "start_y": None, "rechteck": None}

        def _zuruecksetzen():
            zoom_zustand["x_min"] = None
            zoom_zustand["x_max"] = None
            self._wende_zoom_an(achsen, zoom_zustand)
            fig.tight_layout(w_pad=3.0, h_pad=3.0, pad=1.5)
            canvas.draw_idle()

        def _on_press(event):
            if event.dblclick:
                _zuruecksetzen()
                return
            if event.button != 1 or event.inaxes not in achsen or event.xdata is None:
                return
            ziehen["achse"] = event.inaxes
            ziehen["start_x"] = event.xdata
            ziehen["start_y"] = event.ydata
            rechteck = patches.Rectangle(
                (event.xdata, event.ydata), 0, 0,
                fill=True, facecolor=MUL_TURKIS, alpha=0.15,
                edgecolor=MUL_TURKIS, linewidth=1.0, linestyle="--",
            )
            event.inaxes.add_patch(rechteck)
            ziehen["rechteck"] = rechteck
            canvas.draw_idle()

        def _on_motion(event):
            if ziehen["achse"] is None or ziehen["rechteck"] is None:
                return
            if event.inaxes != ziehen["achse"] or event.xdata is None or event.ydata is None:
                return
            start_x, start_y = ziehen["start_x"], ziehen["start_y"]
            ziehen["rechteck"].set_xy((min(start_x, event.xdata), min(start_y, event.ydata)))
            ziehen["rechteck"].set_width(abs(event.xdata - start_x))
            ziehen["rechteck"].set_height(abs(event.ydata - start_y))
            canvas.draw_idle()

        def _on_release(event):
            achse = ziehen["achse"]
            rechteck = ziehen["rechteck"]
            start_x = ziehen["start_x"]
            ziehen["achse"] = None
            ziehen["rechteck"] = None
            if rechteck is not None:
                rechteck.remove()
            if achse is None or start_x is None:
                canvas.draw_idle()
                return
            end_x = event.xdata if (event.inaxes == achse and event.xdata is not None) else start_x
            # Zu kleines/versehentliches Ziehen (praktisch ein einfacher
            # Klick) ignorieren, statt auf einen Mini-Zeitbereich zu zoomen.
            achsen_breite = achse.get_xlim()[1] - achse.get_xlim()[0]
            if achsen_breite <= 0 or abs(end_x - start_x) < achsen_breite * 0.01:
                canvas.draw_idle()
                return
            zoom_zustand["x_min"] = min(start_x, end_x)
            zoom_zustand["x_max"] = max(start_x, end_x)
            self._wende_zoom_an(achsen, zoom_zustand)
            fig.tight_layout(w_pad=3.0, h_pad=3.0, pad=1.5)
            canvas.draw_idle()

        canvas.mpl_connect("button_press_event", _on_press)
        canvas.mpl_connect("motion_notify_event", _on_motion)
        canvas.mpl_connect("button_release_event", _on_release)

        # Kleiner Hinweis in der Ecke der Figure (bleibt beim Neuzeichnen
        # der Achsen erhalten, da er auf der Figure selbst sitzt, nicht
        # auf einer der 4 Achsen, die bei jedem Neuzeichnen geleert werden).
        fig.text(
            0.005, 0.005,
            "🔍 Ziehen = Zoom (in allen 4 Diagrammen) · Doppelklick = zuruecksetzen",
            fontsize=7, color="grey", ha="left", va="bottom",
        )

    # ------------------------------------------------------------------
    # LAYOUT-HELFER: ziehbare Trennleiste (Griff) zwischen zwei Spalten +
    # ein-/ausklappbare Seitenspalten - fuer den TGA-Rohdaten-Tab, damit
    # die Versuchsliste links und die Filter-Einstellungen rechts per
    # Maus breiter/schmaler gezogen bzw. komplett ein-/ausgeklappt werden
    # koennen (mehr Platz fuer lange Versuchsnamen bzw. fuer die
    # 2x2-Vorschau in der Mitte).
    # ------------------------------------------------------------------
    def _mache_griff_ziehbar(self, griff, ziel_frame, breite_merker, minimum=90, maximum=650, invertiert=False):
        """
        Bindet Maus-Drag-Events an `griff` (eine schmale CTkFrame-Leiste),
        um die feste Breite von `ziel_frame` live zu veraendern.
        `ziel_frame` MUSS `pack_propagate(False)` gesetzt haben, sonst hat
        `width=...` keine Wirkung.

        `breite_merker` ist ein Dict mit Schluessel "breite", in dem die
        aktuell gueltige (ausgeklappte) Breite mitgefuehrt wird - so kann
        eine Ein-/Ausklapp-Funktion nach dem Wiederausklappen exakt die
        zuletzt gezogene Breite wiederherstellen statt immer auf den
        urspruenglichen Startwert zurueckzuspringen.

        `invertiert=True`, wenn der Griff LINKS vom ziel_frame liegt (z.B.
        rechte Spalte): Ziehen nach links soll dann breiter machen statt
        schmaler.
        """
        start = {"maus_x": 0, "breite": 0}

        def _start(event):
            start["maus_x"] = event.x_root
            start["breite"] = ziel_frame.winfo_width()

        def _ziehen(event):
            delta = event.x_root - start["maus_x"]
            if invertiert:
                delta = -delta
            neue_breite = max(minimum, min(maximum, start["breite"] + delta))
            ziel_frame.configure(width=neue_breite)
            breite_merker["breite"] = neue_breite

        griff.bind("<Button-1>", _start)
        griff.bind("<B1-Motion>", _ziehen)

    def _mache_spalte_einklappbar(self, toggle_button, ziel_frame, inhalt_frame, breite_merker, eingeklappt_text, ausgeklappt_text, breite_eingeklappt=40):
        """
        Macht eine Seitenspalte per Button ein-/ausklappbar: im
        eingeklappten Zustand wird `inhalt_frame` (Versuchsliste bzw.
        Filter-Einstellungen) komplett ausgeblendet und `ziel_frame` auf
        `breite_eingeklappt` verschmaelert - dadurch bekommt die mittlere
        2x2-Vorschau spuerbar mehr Platz. Beim erneuten Ausklappen wird
        exakt die zuletzt (ggf. per Ziehen veraenderte) Breite aus
        `breite_merker["breite"]` wiederhergestellt.
        """
        zustand = {"offen": True}

        def _umschalten():
            if zustand["offen"]:
                inhalt_frame.pack_forget()
                ziel_frame.configure(width=breite_eingeklappt)
                toggle_button.configure(text=ausgeklappt_text)
                zustand["offen"] = False
            else:
                ziel_frame.configure(width=breite_merker["breite"])
                inhalt_frame.pack(fill="both", expand=True)
                toggle_button.configure(text=eingeklappt_text)
                zustand["offen"] = True

        toggle_button.configure(command=_umschalten)
        return _umschalten

    def baue_rohdaten_tab_tga(self, parent, projekt, methode):
        """
        TGA-Gegenstueck zu baue_rohdaten_tab (EMI/generisch): links die
        Versuchsliste (mit "Verarbeitet"-Haekchen wie bisher, per Klick
        waehlbar) + "Berechnen"-Button; in der Mitte eine 2x2-Live-Vorschau
        direkt aus der rohen .txt-Datei (Filter 1 auf die Massenkurve dm,
        Filter 2 auf die daraus abgeleitete Reaktionskinetik dm/dt - siehe
        zeichne_rohdaten_vorschau_tga); rechts die Einstellungen fuer beide
        Filter, komplett eingebettet (kein Popup wie im Ergebnisse-Tab).
        """
        versuche = self.liste_versuche(projekt, methode)
        if not versuche:
            ctk.CTkLabel(parent, text="Keine lokalen Rohdaten gefunden.").pack(pady=20)
            return

        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except ImportError:
            ctk.CTkLabel(
                parent,
                text="matplotlib ist nicht installiert - 'pip install matplotlib' im GUI-Environment noetig.",
                wraplength=560,
            ).pack(pady=20)
            return

        # --- Default-Zustand: fuer Filter 1 UND Filter 2 je alle moeglichen
        # Filterparameter (praefigiert), damit beim Umschalten zwischen
        # Filtertypen die zuletzt eingestellten Werte erhalten bleiben. ---
        zustand_standard = {
            "f1_filter_typ": "Gleitender Mittelwert",
            "f2_filter_typ": "Butterworth",
        }
        for praefix in ("f1", "f2"):
            for felder in TGA_FILTER_PARAMETER.values():
                for schluessel, _label, default_text in felder:
                    try:
                        default_wert = float(default_text)
                    except ValueError:
                        default_wert = default_text
                    zustand_standard[f"{praefix}_{schluessel}"] = default_wert
        # `zustand` wird als EIN Dict-Objekt wiederverwendet (nicht neu
        # zugewiesen), damit alle Closures unten (zeige_parameter,
        # uebernehmen, ...) automatisch den aktuellen Inhalt sehen. Beim
        # Wechsel des ausgewaehlten Versuchs wird der Inhalt in
        # waehle_versuch() per .clear()/.update() ausgetauscht - siehe dort.
        zustand = self.lade_rohdaten_filter_einstellungen_fuer_versuch(
            projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, versuche[0][2]), zustand_standard
        )

        haupt_layout = ctk.CTkFrame(parent, fg_color="transparent")
        haupt_layout.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Linke Spalte: Versuchsliste (mit Haekchen) + Berechnen ---
        # Breite ist per Griff (rechts daneben) mit der Maus ziehbar und
        # per Pfeil-Button in der Kopfzeile komplett einklappbar - damit
        # lange Versuchsnamen, die vorher abgeschnitten wurden, bei Bedarf
        # vollstaendig sichtbar gemacht werden koennen.
        #
        # WICHTIG: Die Kopfzeile enthaelt NUR den Einklapp-Button und bleibt
        # IMMER in voller Groesse sichtbar (auch im eingeklappten Zustand) -
        # vorher teilten sich "Versuche"/"Verarb."-Beschriftung und der
        # Button dieselbe schmale Kopfzeile, wodurch beim Einklappen (Breite
        # 40px) BEIDE abgeschnitten wurden und der Button nicht mehr
        # antippbar/sichtbar war. Die Titelzeile mit "Versuche"/"Verarb."
        # steckt jetzt zusammen mit der Liste in inhalt_links und wird beim
        # Einklappen sauber mit ausgeblendet statt nur abgeschnitten.
        linke_breite_merker = {"breite": 280}
        linke_spalte = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=linke_breite_merker["breite"])
        linke_spalte.pack(side="left", fill="y", padx=(0, 0))
        linke_spalte.pack_propagate(False)

        kopfzeile = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        kopfzeile.pack(side="top", fill="x", padx=5, pady=(0, 5))
        links_einklapp_btn = ctk.CTkButton(kopfzeile, text="◀", width=32, fg_color="transparent", border_width=1)
        links_einklapp_btn.pack(side="left")

        # Alles ausser dem Einklapp-Button steckt in inhalt_links, damit es
        # beim Einklappen der Spalte als Ganzes sauber ausgeblendet wird.
        inhalt_links = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        inhalt_links.pack(fill="both", expand=True)

        titelzeile_links = ctk.CTkFrame(inhalt_links, fg_color="transparent")
        titelzeile_links.pack(side="top", fill="x", padx=5, pady=(0, 5))
        ctk.CTkLabel(titelzeile_links, text="Versuche", font=("Arial", 12, "bold")).pack(side="left", padx=5)
        ctk.CTkLabel(titelzeile_links, text="Verarb.", font=("Arial", 12, "bold")).pack(side="right", padx=5)

        # Button-Leiste zuerst mit side="bottom" packen (siehe Kommentar in
        # baue_rohdaten_tab), damit sie beim Scrollen nicht verschwindet.
        button_zeile = ctk.CTkFrame(inhalt_links, fg_color="transparent")
        button_zeile.pack(side="bottom", fill="x", padx=5, pady=8)
        ctk.CTkButton(
            button_zeile,
            text="Alle Berechnen",
            fg_color=MUL_TURKIS,
            command=lambda: self.starte_berechnung(projekt, methode, parent),
        ).pack(fill="x")

        scroll = ctk.CTkScrollableFrame(inhalt_links, width=240)
        scroll.pack(padx=0, pady=(0, 5), fill="both", expand=True)

        # Ziehbarer Griff zwischen Versuchsliste und der mittleren Vorschau.
        griff_links = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_links.pack(side="left", fill="y", padx=(4, 8))
        self._mache_griff_ziehbar(griff_links, linke_spalte, linke_breite_merker, minimum=90, maximum=650, invertiert=False)
        self._mache_spalte_einklappbar(
            links_einklapp_btn, linke_spalte, inhalt_links, linke_breite_merker,
            eingeklappt_text="◀", ausgeklappt_text="▶", breite_eingeklappt=48,
        )

        ausgewaehlter_pfad = {"wert": None}
        zeilen_frames = []
        # Wird weiter unten (nach dem Aufbau der Filter-Dropdowns/-Felder)
        # auf die echte Funktion gesetzt, die Dropdown+Eingabefelder mit
        # dem aktuellen `zustand` synchronisiert. Bis dahin (Aufbau der
        # Versuchsliste oben) ist sie noch None.
        aktualisiere_filter_panel = {"fn": None}

        def waehle_versuch(voller_pfad):
            ausgewaehlter_pfad["wert"] = voller_pfad
            for rahmen, pfad in zeilen_frames:
                rahmen.configure(fg_color=MUL_TURKIS if pfad == voller_pfad else "transparent")

            # --- Individuelle Filter-Einstellungen DIESES Versuchs laden ---
            # `zustand` bleibt dasselbe Dict-Objekt (siehe Kommentar oben) -
            # nur sein Inhalt wird ausgetauscht, damit alle bereits
            # gebundenen Closures (Eingabefelder, uebernehmen(), ...)
            # automatisch die neuen Werte sehen.
            neuer_zustand = self.lade_rohdaten_filter_einstellungen_fuer_versuch(
                projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad), zustand_standard
            )
            zustand.clear()
            zustand.update(neuer_zustand)
            if aktualisiere_filter_panel["fn"] is not None:
                aktualisiere_filter_panel["fn"]()
            zeichne()

        for staub, eintrag, voller_pfad in versuche:
            zeile = ctk.CTkFrame(scroll, fg_color="transparent", cursor="hand2")
            zeile.pack(fill="x", pady=2)
            # Haekchen ZUERST (side="right") packen, damit ihm sein Platz
            # am rechten Rand fest reserviert wird, BEVOR der Namens-Label
            # (der bei langen Versuchsnamen viel breiter sein moechte als
            # verfuegbar) den Rest der Zeile fuellt. Vorher wurde das
            # Haekchen bei langen Namen aus dem sichtbaren Bereich gedrueckt.
            verarbeitet = self.ist_versuch_verarbeitet(os.path.dirname(voller_pfad), eintrag, methode)
            haekchen = ctk.CTkLabel(
                zeile, text="✓" if verarbeitet else "", font=("Arial", 13, "bold"),
                text_color="#00ff88", width=30, anchor="center",
            )
            haekchen.pack(side="right", padx=5)
            symbol = "📁" if os.path.isdir(voller_pfad) else "📄"
            # Nur der Dateiname (z.B. "1986_RT1.txt") statt "[Projektname] Dateiname" -
            # der lange Projekt-/Staub-Präfix brachte hier keinen Mehrwert und
            # sorgte nur dafür, dass die eigentliche Versuchsnummer (Folgenummer_
            # Versuchsname, z.B. 1986_RT1, 1987_RT2, ...) abgeschnitten wurde.
            label = ctk.CTkLabel(zeile, text=f"{symbol} {eintrag}", anchor="w")
            label.pack(side="left", padx=5, fill="x", expand=True)
            zeilen_frames.append((zeile, voller_pfad))
            for widget in (zeile, label):
                widget.bind("<Button-1>", lambda _e, p=voller_pfad: waehle_versuch(p))

        # --- Mitte: 2x2 Live-Vorschau ---
        mitte = ctk.CTkFrame(haupt_layout, fg_color="transparent")
        mitte.pack(side="left", fill="both", expand=True, padx=(0, 0))

        # Größe folgt dem tatsächlich verfügbaren Platz in der Mitte-Spalte
        # (bzw. ersatzweise dem Bildschirm) statt einer fixen Größe - so
        # bekommen die 4 Diagramme auf kleinen Bildschirmen mehr Luft
        # zueinander und auf großen Bildschirmen mehr Fläche.
        figsize = self._dynamische_figsize(mitte, 2, 2, mindest_breite_px=650, mindest_hoehe_px=500)
        fig, achsen_grid = plt.subplots(2, 2, figsize=figsize)
        achsen = (achsen_grid[0, 0], achsen_grid[0, 1], achsen_grid[1, 0], achsen_grid[1, 1])
        canvas = FigureCanvasTkAgg(fig, master=mitte)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # Zoom-Lupe: Rechteck ziehen zoomt den Zeitbereich synchron in
        # allen 4 Diagrammen, Doppelklick setzt zurueck (siehe
        # _aktiviere_zoom_lupe/_wende_zoom_an). zoom_zustand wird an
        # zeichne_rohdaten_vorschau_tga durchgereicht, damit ein
        # Filter-/Versuchswechsel den aktiven Zoom nicht verwirft.
        zoom_zustand = {"x_min": None, "x_max": None}
        self._aktiviere_zoom_lupe(fig, achsen, canvas, zoom_zustand)

        def zeichne():
            pfad = ausgewaehlter_pfad["wert"]
            if not pfad:
                return
            self.zeichne_rohdaten_vorschau_tga(pfad, fig, achsen, canvas, zustand, zoom_zustand)

        # --- Rechts: Filter-Einstellungen (Filter 1 + Filter 2), komplett
        # eingebettet (kein Popup). Auesserer Container ist ein einfacher
        # Frame mit fester (per Griff ziehbarer) Breite; darin liegt eine
        # eigene Kopfzeile (Titel + Einklapp-Button) und darunter die
        # eigentliche Scroll-Flaeche mit den Filterblöcken, die beim
        # Einklappen komplett ausgeblendet wird.
        rechte_breite_merker = {"breite": 320}
        griff_rechts = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_rechts.pack(side="left", fill="y", padx=(8, 4))

        rechte_container = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=rechte_breite_merker["breite"])
        rechte_container.pack(side="left", fill="y")
        rechte_container.pack_propagate(False)

        rechte_kopfzeile = ctk.CTkFrame(rechte_container, fg_color="transparent")
        rechte_kopfzeile.pack(side="top", fill="x", padx=(5, 0), pady=(0, 5))
        rechts_einklapp_btn = ctk.CTkButton(
            rechte_kopfzeile, text="▶", width=32, fg_color="transparent", border_width=1
        )
        rechts_einklapp_btn.pack(side="left")

        rechte_spalte = ctk.CTkScrollableFrame(rechte_container, fg_color="transparent")
        rechte_spalte.pack(fill="both", expand=True)

        ctk.CTkLabel(
            rechte_spalte, text="Filter-Einstellungen", font=("Arial", 14, "bold")
        ).pack(fill="x", padx=10, pady=(0, 10))

        self._mache_griff_ziehbar(
            griff_rechts, rechte_container, rechte_breite_merker, minimum=140, maximum=650, invertiert=True
        )
        self._mache_spalte_einklappbar(
            rechts_einklapp_btn, rechte_container, rechte_spalte, rechte_breite_merker,
            eingeklappt_text="▶", ausgeklappt_text="◀", breite_eingeklappt=48,
        )

        if _FILTER_IMPORT_FEHLER:
            ctk.CTkLabel(
                rechte_spalte,
                text=(
                    "Filter aus helper/Filter.py konnten nicht geladen werden:\n"
                    f"{_FILTER_IMPORT_FEHLER}"
                ),
                text_color="#ff8080", anchor="w", justify="left", wraplength=270,
            ).pack(fill="x", padx=10, pady=(0, 10))

        filter_dropdown_werte = TGA_FILTER_OPTIONEN if not _FILTER_IMPORT_FEHLER else ["Kein Filter"]
        filter_eingabe_widgets = {"f1": {}, "f2": {}}
        filter_dropdowns = {}
        # merkt je Praefix ("f1"/"f2") die zeige_parameter()-Funktion, damit
        # aktualisiere_filter_panel() (siehe unten, beim Versuchswechsel)
        # Dropdown-Auswahl + Eingabefelder komplett neu aus `zustand`
        # aufbauen kann.
        zeige_parameter_je_praefix = {}

        def baue_filter_block(praefix, ueberschrift_text):
            ctk.CTkFrame(rechte_spalte, height=2, fg_color=("gray75", "gray30")).pack(
                fill="x", padx=10, pady=(5, 10)
            )
            ctk.CTkLabel(
                rechte_spalte, text=ueberschrift_text, font=("Arial", 13, "bold"), anchor="w"
            ).pack(fill="x", padx=10, pady=(0, 5))
            parameter_frame = ctk.CTkFrame(rechte_spalte, fg_color="transparent")

            def zeige_parameter(*_):
                for kind in parameter_frame.winfo_children():
                    kind.destroy()
                parameter_frame.pack(fill="x", padx=0, pady=(0, 5))
                aktueller_typ = filter_dropdowns[praefix].get()
                felder_definition = TGA_FILTER_PARAMETER.get(aktueller_typ, [])
                widgets_fuer_typ = {}
                for schluessel, label_text, _default in felder_definition:
                    ctk.CTkLabel(parameter_frame, text=f"{label_text}:", anchor="w").pack(
                        fill="x", padx=10
                    )
                    eingabe = ctk.CTkEntry(parameter_frame)
                    eingabe.insert(0, str(zustand.get(f"{praefix}_{schluessel}", "")))
                    eingabe.pack(fill="x", padx=10, pady=(2, 6))
                    # ENTER-Event binden, um "uebernehmen()" aufzurufen (Lambda fängt das Event-Argument ab)
                    eingabe.bind("<Return>", lambda event: uebernehmen())
                    widgets_fuer_typ[schluessel] = eingabe
                # NUR die Eingabefelder des GERADE sichtbaren Filtertyps
                # merken (nicht anhaengen!) - alte Eintraege fuer vorher
                # gewaehlte Typen wurden oben bereits zerstoert
                # (kind.destroy()) und wuerden in uebernehmen() beim
                # Auslesen (entry.get()) eine Exception werfen, die den
                # Rest von uebernehmen() (inkl. Titel-Update) stillschweigend
                # abbricht. Deshalb hier IMMER ersetzen statt akkumulieren.
                filter_eingabe_widgets[praefix] = widgets_fuer_typ

                # Live-Vorschau: Titel (und Kurven) sofort an den neu
                # gewaehlten Filtertyp anpassen, nicht erst nach Klick auf
                # "Uebernehmen". Die Zahlenparameter selbst werden weiterhin
                # erst durch "Uebernehmen" persistiert.
                zustand[f"{praefix}_filter_typ"] = aktueller_typ
                zeichne()

            dropdown = ctk.CTkOptionMenu(
                rechte_spalte, values=filter_dropdown_werte, fg_color=MUL_TURKIS,
                command=zeige_parameter,
            )
            aktueller_gespeicherter_typ = zustand.get(f"{praefix}_filter_typ", "Kein Filter")
            dropdown.set(
                aktueller_gespeicherter_typ
                if aktueller_gespeicherter_typ in filter_dropdown_werte else "Kein Filter"
            )
            dropdown.pack(fill="x", padx=10, pady=(0, 5))
            filter_dropdowns[praefix] = dropdown
            zeige_parameter_je_praefix[praefix] = zeige_parameter
            zeige_parameter()

        baue_filter_block("f1", "Filter 1 (Massenkurve dm)")
        baue_filter_block("f2", "Filter 2 (Reaktionskinetik dm/dt)")

        def als_zahl_oder_none(text):
            text = text.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None

        def _uebernimm_eingabefelder_in_zustand():
            """Liest Dropdown-Auswahl + Eingabefelder von Filter 1/2 aus und schreibt sie in `zustand`."""
            for praefix in ("f1", "f2"):
                zustand[f"{praefix}_filter_typ"] = filter_dropdowns[praefix].get()
                for schluessel, entry in filter_eingabe_widgets[praefix].items():
                    wert = als_zahl_oder_none(entry.get())
                    if wert is not None:
                        zustand[f"{praefix}_{schluessel}"] = wert

        def uebernehmen():
            """Speichert die aktuellen Filter-Einstellungen NUR fuer den gerade ausgewaehlten Versuch."""
            _uebernimm_eingabefelder_in_zustand()
            pfad = ausgewaehlter_pfad["wert"]
            if pfad:
                schluessel = self.versuch_schluessel_rohdaten_filter(projekt, pfad)
                self.speichere_rohdaten_filter_einstellungen_fuer_versuch(projekt, methode, schluessel, zustand)
            zeichne()

        def fuer_alle_uebernehmen():
            """
            Uebernimmt die aktuell im Panel eingestellten Filter-Einstellungen
            fuer ALLE Versuche dieser Methode (ueberschreibt dabei auch
            bereits individuell abweichend gesetzte Werte anderer Versuche) -
            nach Rueckfrage, da das nicht ohne weiteres rueckgaengig zu
            machen ist.
            """
            if not messagebox.askyesno(
                "Für alle übernehmen",
                "Die aktuellen Filter-Einstellungen werden für ALLE "
                f"{len(versuche)} Versuche dieser Methode übernommen und "
                "überschreiben dabei auch bereits individuell abweichend "
                "eingestellte Werte einzelner Versuche.\n\n"
                "Fortfahren?",
            ):
                return
            _uebernimm_eingabefelder_in_zustand()
            alle_schluessel = [
                self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad)
                for _staub, _eintrag, voller_pfad in versuche
            ]
            self.speichere_rohdaten_filter_einstellungen_fuer_alle(projekt, methode, alle_schluessel, zustand)
            zeichne()

        def _aktualisiere_filter_panel_impl():
            """
            Synchronisiert Dropdown-Auswahl + Eingabefelder von Filter 1/2
            mit dem aktuellen Inhalt von `zustand` - wird beim Wechsel des
            ausgewaehlten Versuchs aufgerufen (siehe waehle_versuch()),
            NACHDEM `zustand` bereits mit den individuellen Werten des neu
            gewaehlten Versuchs befuellt wurde.
            """
            for praefix in ("f1", "f2"):
                aktueller_typ = zustand.get(f"{praefix}_filter_typ", "Kein Filter")
                if aktueller_typ not in filter_dropdown_werte:
                    aktueller_typ = "Kein Filter"
                filter_dropdowns[praefix].set(aktueller_typ)
                # baut die Eingabefelder fuer den (ggf. neuen) Filtertyp neu
                # auf und befuellt sie direkt aus `zustand`.
                zeige_parameter_je_praefix[praefix]()

        # Platzhalter (siehe oben, vor der Versuchsliste) jetzt mit der
        # echten Funktion befuellen - waehle_versuch() ruft ab jetzt bei
        # jedem Versuchswechsel aktualisiere_filter_panel["fn"]() auf.
        aktualisiere_filter_panel["fn"] = _aktualisiere_filter_panel_impl

        button_leiste_filter = ctk.CTkFrame(rechte_spalte, fg_color="transparent")
        button_leiste_filter.pack(fill="x", padx=10, pady=(10, 15))
        ctk.CTkButton(
            button_leiste_filter, text="Übernehmen", fg_color=MUL_TURKIS, command=uebernehmen
        ).pack(fill="x")
        ctk.CTkButton(
            button_leiste_filter,
            text="Für alle übernehmen",
            fg_color="transparent",
            border_width=1,
            border_color=MUL_TURKIS,
            command=fuer_alle_uebernehmen,
        ).pack(fill="x", pady=(8, 0))

        # --- Ersten Versuch automatisch auswaehlen ---
        waehle_versuch(versuche[0][2])

    # ------------------------------------------------------------------
    # SEM: Rohdaten laden (Elementkarten-TIFs), normieren, filtern,
    # Cluster-Umrisse berechnen - siehe baue_rohdaten_tab_sem fuer die UI.
    # ------------------------------------------------------------------
    def _sem_finde_h5oina_datei(self, versuch_pfad):
        """
        Sucht im Versuchsordner (rekursiv) nach einer H5OINA/HDF5-Datei mit
        der Kalibrierungs-Metadaten (X Step/Y Step). Portiert aus
        SEM/filtering.py:locate_sample_h5oina_file.
        Gibt (pfad_oder_None, fehlermeldung_oder_None) zurueck.
        """
        if os.path.isfile(versuch_pfad):
            suchordner = os.path.dirname(versuch_pfad)
        else:
            suchordner = versuch_pfad
        if not os.path.isdir(suchordner):
            return None, "Versuchsordner nicht gefunden"

        endungen = {".h5oina", ".h5", ".hdf5", ".hdf"}
        kandidaten = []
        for wurzel, _unterordner, dateien in os.walk(suchordner):
            for datei in dateien:
                if os.path.splitext(datei)[1].lower() in endungen:
                    kandidaten.append(os.path.join(wurzel, datei))
        if not kandidaten:
            return None, "keine H5OINA-Datei gefunden"

        bevorzugt = [
            p for p in kandidaten
            if "oina" in os.path.basename(p).lower() or p.lower().endswith(".h5oina")
        ]
        pool = bevorzugt or kandidaten
        if len(pool) > 1:
            namen = ", ".join(os.path.basename(p) for p in pool[:3])
            return None, f"mehrere H5OINA-Kandidaten gefunden: {namen}"
        return pool[0], None

    @staticmethod
    def _sem_h5_normalisiere_key(name):
        return re.sub(r"[^a-z0-9]+", "", str(name).lower())

    @classmethod
    def _sem_h5_einheit_aus_key(cls, name):
        """Erkennt die Einheit anhand des Metadaten-Schlüsselnamens (z.B.
        'PixelSizeUm', 'XStepNm', ...) und gibt den Umrechnungsfaktor nach
        µm zurück. Portiert aus SEM/filtering.py:_unit_scale_from_key."""
        key = cls._sem_h5_normalisiere_key(name)
        if not key:
            return None
        if any(tok in key for tok in ("umperpixel", "micronperpixel", "micronsperpixel", "micrometerperpixel", "micrometersperpixel")):
            return 1.0
        if any(tok in key for tok in ("pixelsizeum", "pixelwidthum", "xstepum", "xscaleum", "stepsizeum")):
            return 1.0
        if any(tok in key for tok in ("pixelsizenm", "pixelwidthnm", "xstepnm", "xscalenm", "stepsizenm")):
            return 1e-3
        if any(tok in key for tok in ("pixelsizemm", "pixelwidthmm", "xstepmm", "xscalemm", "stepsizemm")):
            return 1000.0
        if any(tok in key for tok in ("pixelsizem", "pixelwidthm", "xstepm", "xscalem", "stepsizem")):
            return 1e6
        return None

    @classmethod
    def _sem_h5_einheit_aus_text(cls, einheit):
        """Erkennt die Einheit anhand eines Text-Attributs (z.B. 'µm',
        'nm', ...). Portiert aus SEM/filtering.py:_unit_scale_from_unit_text."""
        if isinstance(einheit, bytes):
            try:
                einheit = einheit.decode("utf-8", errors="ignore")
            except Exception:
                return None
        key = cls._sem_h5_normalisiere_key(str(einheit))
        if key in {"um", "micron", "microns", "micrometer", "micrometers"}:
            return 1.0
        if key in {"nm", "nanometer", "nanometers"}:
            return 1e-3
        if key in {"mm", "millimeter", "millimeters"}:
            return 1000.0
        if key in {"m", "meter", "meters"}:
            return 1e6
        return None

    @staticmethod
    def _sem_h5_normalisiere_skalar(wert):
        """Wandelt einen H5-Datensatzwert (Zahl, 1-elementiges Array oder
        Zahlen-String) in einen einzelnen positiven float um, sonst None.
        Portiert aus SEM/filtering.py:_normalize_h5_scalar."""
        import numpy as np

        if isinstance(wert, bytes):
            try:
                wert = wert.decode("utf-8", errors="ignore")
            except Exception:
                return None
        if isinstance(wert, str):
            treffer = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", wert)
            if not treffer:
                return None
            wert = treffer.group(0)
        try:
            array = np.asarray(wert)
        except Exception:
            return None
        if array.size != 1:
            return None
        try:
            skalar = float(array.reshape(-1)[0])
        except (TypeError, ValueError):
            return None
        if not np.isfinite(skalar) or skalar <= 0:
            return None
        return skalar

    def _sem_h5_lese_wert_und_einheit(self, handle, datensatz_pfad):
        try:
            datensatz = handle[datensatz_pfad]
        except Exception:
            return None, None
        wert = None
        einheit_skalierung = None
        try:
            wert = self._sem_h5_normalisiere_skalar(datensatz[()])
        except Exception:
            wert = None
        try:
            einheit_skalierung = self._sem_h5_einheit_aus_text(datensatz.attrs.get("Unit"))
        except Exception:
            einheit_skalierung = None
        return wert, einheit_skalierung

    def _sem_h5_lese_step_paar_um(self, handle, x_pfad, y_pfad):
        """Liest X/Y Step, rechnet in µm um und mittelt - verwirft das
        Ergebnis, wenn X/Y-Schrittweite um mehr als 5% voneinander
        abweichen (rechteckige statt quadratische Pixel). Portiert aus
        SEM/filtering.py:_read_h5_step_pair_um."""
        x_wert, x_skalierung = self._sem_h5_lese_wert_und_einheit(handle, x_pfad)
        y_wert, y_skalierung = self._sem_h5_lese_wert_und_einheit(handle, y_pfad)
        if x_wert is None or y_wert is None or x_skalierung is None or y_skalierung is None:
            return None, None
        x_um = float(x_wert) * float(x_skalierung)
        y_um = float(y_wert) * float(y_skalierung)
        if x_um <= 0.0 or y_um <= 0.0:
            return None, None
        rel_diff = abs(x_um - y_um) / max(x_um, y_um)
        if rel_diff > 0.05:
            return None, "widersprüchliche Pixelgrößen-Metadaten in der H5OINA-Datei (X/Y weichen >5% ab)"
        return float(0.5 * (x_um + y_um)), None

    def _sem_lese_h5oina_um_pro_pixel(self, pfad, quelle="auto"):
        """
        Liest die Mikrometer/Pixel-Kalibrierung aus einer H5OINA-Datei.
        WICHTIG: EDS-Elementkarten und das Electron-Image(Backscatter)-Bild
        haben in H5OINA-Dateien i.d.R. UNTERSCHIEDLICHE Pixelaufloesungen
        (die EDS-Karte ist meist deutlich groeber gebinnt als das hochaufgeloeste
        Electron Image, auch wenn beide dasselbe physische Sichtfeld zeigen).
        `quelle` legt fest, welcher Header-Pfad verwendet werden soll:
          - "eds":      nur /1/EDS/Header/X|Y Step (fuer Elementkarten-TIFs)
          - "electron": nur /1/Electron Image/Header/X|Y Step (fuer Backscatter)
          - "auto":     wie bisher - erst EDS, dann Electron Image, dann
                        generischer Attribut-Scan (nur sinnvoll, wenn nicht
                        bekannt ist, welcher Bildtyp kalibriert werden soll).
        Portiert aus SEM/filtering.py:read_h5oina_um_per_pixel.
        Gibt (um_pro_pixel_oder_None, fehlermeldung_oder_None) zurueck.
        """
        import numpy as np
        try:
            import h5py
        except ImportError:
            return None, "h5py ist nicht installiert ('pip install h5py')"

        if quelle == "eds":
            pfad_paare = (("/1/EDS/Header/X Step", "/1/EDS/Header/Y Step"),)
        elif quelle == "electron":
            pfad_paare = (("/1/Electron Image/Header/X Step", "/1/Electron Image/Header/Y Step"),)
        else:
            pfad_paare = (
                ("/1/EDS/Header/X Step", "/1/EDS/Header/Y Step"),
                ("/1/Electron Image/Header/X Step", "/1/Electron Image/Header/Y Step"),
            )

        try:
            with h5py.File(pfad, "r") as handle:
                for x_pfad, y_pfad in pfad_paare:
                    treffer, fehler = self._sem_h5_lese_step_paar_um(handle, x_pfad, y_pfad)
                    if treffer is not None:
                        return treffer, None
                    if fehler is not None:
                        return None, fehler

                if quelle != "auto":
                    # Fuer eine explizit angeforderte Quelle (eds/electron)
                    # NICHT auf den generischen Attribut-Scan zurueckfallen -
                    # der wuerde nicht zwischen EDS- und Electron-Image-
                    # Aufloesung unterscheiden koennen und koennte den
                    # jeweils falschen (weil vom anderen Bildtyp stammenden)
                    # Wert liefern.
                    bezeichnung = "EDS" if quelle == "eds" else "Electron Image"
                    return None, f"keine {bezeichnung}-Pixelgrößen-Metadaten in der H5OINA-Datei gefunden"

                skalar_werte = []

                def sammle_attribute(prefix, obj):
                    try:
                        attribute = obj.attrs.items()
                    except Exception:
                        return
                    for name, roh in attribute:
                        wert = self._sem_h5_normalisiere_skalar(roh)
                        if wert is not None:
                            skalar_werte.append((f"{prefix}/@{name}", wert))

                def besuche(name, obj):
                    prefix = f"/{name}" if name else "/"
                    sammle_attribute(prefix, obj)
                    if getattr(obj, "shape", None) is not None:
                        try:
                            if int(np.prod(obj.shape)) == 1:
                                wert = self._sem_h5_normalisiere_skalar(obj[()])
                                if wert is not None:
                                    skalar_werte.append((prefix, wert))
                        except Exception:
                            return

                sammle_attribute("/", handle)
                handle.visititems(besuche)
        except Exception as exc:
            return None, f"H5OINA-Datei nicht lesbar: {type(exc).__name__}: {exc}"

        treffer = []
        for name, wert in skalar_werte:
            skalierung = self._sem_h5_einheit_aus_key(name)
            if skalierung is not None:
                treffer.append(wert * skalierung)
        eindeutige_treffer = sorted({round(wert, 9) for wert in treffer if wert > 0})
        if len(eindeutige_treffer) == 1:
            return float(eindeutige_treffer[0]), None
        if len(eindeutige_treffer) > 1:
            return None, "widersprüchliche Pixelgrößen-Metadaten in der H5OINA-Datei"
        return None, "keine Pixelgrößen-Metadaten in der H5OINA-Datei gefunden"

    def _sem_ermittle_um_pro_pixel(self, versuch_pfad, manueller_wert, quelle="auto"):
        """
        Ermittelt die Mikrometer/Pixel-Kalibrierung fuer den Maßstabsbalken
        EINES BESTIMMTEN BILDTYPS (`quelle`: "eds" fuer Elementkarten,
        "electron" fuer das Backscatter-/Electron-Image, "auto" wenn der
        Bildtyp nicht bekannt/egal ist):
        1. Bevorzugt wird der reale, probenspezifische Wert aus der
           H5OINA-Datei des Versuchsordners gelesen (X/Y Step-Mittelwert
           DES JEWEILIGEN HEADERS), analog zur SEM-Web-App (siehe
           SEM/filtering.py). EDS-Elementkarten und Electron-Image haben
           i.d.R. unterschiedliche Pixelaufloesungen trotz gleichem
           Sichtfeld - deshalb NIE einen fuer den einen Bildtyp gelesenen
           Wert fuer den anderen wiederverwenden. Das Ergebnis wird pro
           (Versuchsordner, Quelle) zwischengespeichert (H5-Datei wird nur
           einmal gelesen, nicht bei jedem Redraw/Zoom).
        2. Ist keine (eindeutige) H5OINA-Datei vorhanden/lesbar oder fehlt
           der jeweilige Header, wird auf den manuell im Rohdaten-Tab
           eingestellten Wert zurueckgefallen (Kalibrierungsfeld
           "mikrometer_pro_pixel") - anders als die Web-App (die dann GAR
           KEINEN Balken zeigt), damit die Desktop-App auch ohne H5OINA-
           Datei weiter nutzbar bleibt.
        Gibt (um_pro_pixel, herkunft) zurueck, mit herkunft in
        {"h5oina", "manuell"}.
        """
        cache_schluessel = (versuch_pfad, quelle)
        if cache_schluessel in self._sem_h5oina_kalibrierung_cache:
            um_pro_px, fehler = self._sem_h5oina_kalibrierung_cache[cache_schluessel]
        else:
            h5oina_pfad, fehler_suche = self._sem_finde_h5oina_datei(versuch_pfad)
            if h5oina_pfad is None:
                um_pro_px, fehler = None, fehler_suche
            else:
                um_pro_px, fehler = self._sem_lese_h5oina_um_pro_pixel(h5oina_pfad, quelle=quelle)
            self._sem_h5oina_kalibrierung_cache[cache_schluessel] = (um_pro_px, fehler)
            if fehler:
                print(f"[SEM Maßstab] {versuch_pfad} ({quelle}): automatische Kalibrierung nicht möglich ({fehler}) - verwende manuellen Wert.")

        if um_pro_px:
            return um_pro_px, "h5oina"
        return manueller_wert, "manuell"

    def _sem_finde_tif_ordner(self, versuch_pfad):
        """
        Sucht den Ordner mit den Elementkarten-TIFs zu einem SEM-Versuch.
        Erwartete Struktur (siehe wetransfer-Lieferung): <Versuch>/TIF/*.tif
        Falls kein "TIF"-Unterordner existiert, wird der Versuchsordner
        selbst durchsucht (Fallback, falls die TIFs direkt dort liegen).
        """
        if os.path.isfile(versuch_pfad):
            return os.path.dirname(versuch_pfad)
        for eintrag in os.listdir(versuch_pfad):
            if eintrag.lower() == "tif" and os.path.isdir(os.path.join(versuch_pfad, eintrag)):
                return os.path.join(versuch_pfad, eintrag)
        return versuch_pfad

    def _sem_finde_backscatter_pfad(self, versuch_pfad):
        """
        Sucht ein Backscatter-/Übersichtsbild fuer das "Ausgangsbild" (linkes
        Diagramm) - bevorzugt den vom sem_filter_app-Cache erzeugten
        registrierten Backscatter-TIF (.sem_filter_cache/...), sonst ein
        TIF mit "backscatter" im Dateinamen im TIF-Ordner.
        """
        cache_ordner = os.path.join(versuch_pfad, ".sem_filter_cache")
        if os.path.isdir(cache_ordner):
            for eintrag in sorted(os.listdir(cache_ordner)):
                if eintrag.lower().endswith(".tif") and "backscatter" in eintrag.lower():
                    return os.path.join(cache_ordner, eintrag)
        tif_ordner = self._sem_finde_tif_ordner(versuch_pfad)
        if os.path.isdir(tif_ordner):
            for eintrag in sorted(os.listdir(tif_ordner)):
                if eintrag.lower().endswith(".tif") and "backscatter" in eintrag.lower():
                    return os.path.join(tif_ordner, eintrag)
        return None

    def _sem_lade_elementkarten(self, versuch_pfad):
        """
        Liest alle Elementkarten-TIFs eines SEM-Versuchs ein
        ("Montaged Map Data-<Element> At#...tif") und gibt ein Dict
        Element -> 2D-numpy-Array zurueck. Das "EDS Layered Image" (RGB-
        Komposit, kein Elementkanal) wird separat als moegliches
        Ausgangsbild zurueckgegeben, nicht als Element.
        Gibt (elementkarten_dict, eds_layered_pfad) zurueck.
        """
        from PIL import Image
        import numpy as np

        tif_ordner = self._sem_finde_tif_ordner(versuch_pfad)
        elementkarten = {}
        eds_layered_pfad = None
        if not os.path.isdir(tif_ordner):
            return elementkarten, eds_layered_pfad

        element_muster = re.compile(r"Data-([A-Za-z]{1,2})\s*At", re.IGNORECASE)
        for eintrag in sorted(os.listdir(tif_ordner)):
            if not eintrag.lower().endswith(".tif"):
                continue
            pfad = os.path.join(tif_ordner, eintrag)
            if "eds layered image" in eintrag.lower():
                eds_layered_pfad = pfad
                continue
            treffer = element_muster.search(eintrag)
            if not treffer:
                continue
            element = treffer.group(1).capitalize()
            try:
                with Image.open(pfad) as bild:
                    elementkarten[element] = np.asarray(bild, dtype=np.float64)
            except Exception as e:
                print(f"[SEM TIF Fehler] {pfad}: {e}")
        return elementkarten, eds_layered_pfad

    def _sem_lade_ausgangsbild(self, versuch_pfad, elementkarten, eds_layered_pfad):
        """
        Waehlt/laedt das Bild fuer das linke ("Ausgangsbild"-)Diagramm:
        bevorzugt Backscatter-Übersicht, sonst das EDS-Layered-Komposit,
        sonst (Fallback) die Summe aller Elementkarten als Graustufenbild.
        Gibt (bild_array, ist_farbig, kalibrierung_quelle) zurueck, oder
        (None, False, "electron"). `kalibrierung_quelle` ist "electron" fuer
        das (hoeher aufgeloeste) Backscatter-/Electron-Image, sonst "eds" -
        WICHTIG fuer den Maßstabsbalken, da EDS-Elementkarten und Electron
        Image i.d.R. unterschiedliche µm/Pixel-Aufloesungen haben (siehe
        _sem_lese_h5oina_um_pro_pixel).
        """
        from PIL import Image
        import numpy as np

        backscatter_pfad = self._sem_finde_backscatter_pfad(versuch_pfad)
        for pfad, ist_kandidat_farbig, kalibrierung_quelle in (
            (backscatter_pfad, False, "electron"),
            (eds_layered_pfad, True, "eds"),
        ):
            if not pfad:
                continue
            try:
                with Image.open(pfad) as bild:
                    array = np.asarray(bild)
                return array, (array.ndim == 3), kalibrierung_quelle
            except Exception as e:
                print(f"[SEM Ausgangsbild Fehler] {pfad}: {e}")

        if elementkarten:
            summe = np.sum(np.stack(list(elementkarten.values()), axis=0), axis=0)
            return summe, False, "eds"
        return None, False, "electron"

    def _sem_normalisiere_elementkarten(self, elementkarten):
        """
        Normiert die Elementkarten auf Prozent-Basis je Pixel (Summe aller
        Elemente an diesem Pixel = 100%), damit unterschiedliche
        Gesamt-Signalstaerken zwischen Versuchen vergleichbar werden
        ("Größe normiert, damit vergleichbar") - Portierung der Kernidee aus
        SEM/filtering.py:compute_normalized_element_maps, ohne die dortigen
        Zusatzabhaengigkeiten (cv2/zarr/h5py).
        """
        import numpy as np

        if not elementkarten:
            return {}
        gesamt = np.sum(np.stack(list(elementkarten.values()), axis=0), axis=0)
        normiert = {}
        for element, karte in elementkarten.items():
            werte = np.zeros_like(karte, dtype=np.float64)
            np.divide(karte * 100.0, gesamt, out=werte, where=gesamt > 0.0)
            werte[~np.isfinite(werte)] = 0.0
            normiert[element] = werte
        return normiert

    def _sem_normalisiere_filter_liste(self, filter_liste):
        """
        Wandelt evtl. noch im ALTEN Format ("element": "C") gespeicherte
        Filter in das neue Format ("elemente": ["C", ...]) um, damit auch
        aeltere gespeicherte Rohdaten-Filter-Einstellungen weiter funktionieren.
        """
        normalisiert = []
        for eintrag in filter_liste or []:
            elemente = eintrag.get("elemente")
            if not elemente:
                einzel = eintrag.get("element")
                elemente = [einzel] if einzel else ["C"]
            normalisiert.append({
                "elemente": list(elemente),
                "operator": eintrag.get("operator", "<"),
                "wert": eintrag.get("wert", 0),
                "aktiv": eintrag.get("aktiv", True),
            })
        return normalisiert

    def _sem_wende_filter_an(self, karten, filter_liste):
        """
        Wendet die (aktiven) Schwellwert-Filter der Filter-Sektion nacheinander
        per UND auf die Elementkarten an (z.B. "C < 30" AND "O > 5" ...) und
        liefert die finale boolesche Maske zurueck. Analog zur in
        SEM/FILTERING_EXPLANATION.md beschriebenen Logik.

        Ein Filter kann sich auf MEHRERE Elemente gleichzeitig beziehen
        (eintrag["elemente"] = ["C", "O"]) - deren Anteile werden dann pro
        Pixel aufsummiert, bevor mit operator/wert verglichen wird (Beispiel
        "C+O > 15" -> ueberall wo C-Anteil + O-Anteil > 15 % ist).
        """
        import numpy as np

        if not karten:
            return None
        form = next(iter(karten.values())).shape
        maske = np.ones(form, dtype=bool)
        for eintrag in self._sem_normalisiere_filter_liste(filter_liste):
            if not eintrag.get("aktiv", True):
                continue
            elemente = [e for e in eintrag.get("elemente", []) if e in karten]
            if not elemente:
                continue
            summenkarte = np.sum(np.stack([karten[e] for e in elemente], axis=0), axis=0)
            operator = eintrag.get("operator", "<")
            try:
                wert = float(eintrag.get("wert", 0))
            except (TypeError, ValueError):
                continue
            if operator == "<":
                vergleich = summenkarte < wert
            elif operator == "<=":
                vergleich = summenkarte <= wert
            elif operator == ">":
                vergleich = summenkarte > wert
            elif operator == ">=":
                vergleich = summenkarte >= wert
            else:
                continue
            maske &= vergleich
        return maske

    def _sem_berechne_cluster(self, maske):
        """
        Clustert die gefilterten (maske=True) Pixel ueber
        zusammenhaengende-Komponenten-Labeling (scipy.ndimage.label) - ein
        Standard-Clusteralgorithmus fuer räumliche Regionen. Gibt
        (labels_array, anzahl_cluster) zurueck; (None, 0) falls scipy fehlt
        oder die Maske leer ist.
        """
        try:
            from scipy import ndimage
        except ImportError:
            return None, 0
        if maske is None or not maske.any():
            return None, 0
        labels, anzahl = ndimage.label(maske)
        return labels, anzahl

    def _sem_filtere_kleine_cluster(self, labels, anzahl_cluster, mindestgroesse=3, nachbarschaft_px=5):
        """
        Blendet einzelne, isolierte Cluster mit weniger als `mindestgroesse`
        Pixeln aus, SOFERN sich in ihrer unmittelbaren Nachbarschaft
        (Umkreis von `nachbarschaft_px` Pixeln) KEIN groesserer Cluster
        (>= mindestgroesse) befindet. Kleine Cluster direkt neben/nahe an
        einem groesseren Cluster bleiben erhalten (vermutlich Teil derselben
        realen Struktur, z.B. deren ausgefranster Rand); typischerweise
        isoliertes Rauschen (kleiner Cluster, weit von allem anderen
        entfernt) wird dagegen ausgeblendet.

        Gibt eine boolesche Maske zurueck (gleiche Form wie `labels`):
        True = dieser Pixel gehoert zu einem Cluster, der sichtbar bleiben
        soll. Bei anzahl_cluster == 0 oder labels is None -> passende
        All-False-Maske bzw. None.
        """
        import numpy as np
        from scipy import ndimage

        if labels is None:
            return None
        if anzahl_cluster == 0:
            return np.zeros(labels.shape, dtype=bool)

        cluster_ids = np.arange(1, anzahl_cluster + 1)
        groessen = ndimage.sum(np.ones_like(labels), labels, index=cluster_ids)

        grosse_ids = cluster_ids[groessen >= mindestgroesse]
        kleine_ids = cluster_ids[groessen < mindestgroesse]

        sichtbar = np.isin(labels, grosse_ids)
        if kleine_ids.size == 0:
            return sichtbar

        radius = max(1, int(round(nachbarschaft_px)))
        struktur = np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)
        grosse_maske = sichtbar
        grosse_umgebung = (
            ndimage.binary_dilation(grosse_maske, structure=struktur)
            if grosse_maske.any() else np.zeros_like(grosse_maske)
        )

        for label_id in kleine_ids:
            cluster_maske = labels == label_id
            # Kleiner Cluster bleibt sichtbar, wenn er (teilweise) in der
            # unmittelbaren Umgebung eines groesseren Clusters liegt.
            if np.any(cluster_maske & grosse_umgebung):
                sichtbar |= cluster_maske

        return sichtbar

    def _sem_aktiviere_scrollrad_pan(self, canvas, achsen, toolbar):
        """
        Aktiviert fuer eine SEM-Karten-Canvas:
          - Gedrueckt gehaltenes Scroll-Rad (mittlere Maustaste) + Ziehen:
            verschiebt den Kartenausschnitt (Pan), unabhaengig vom
            Toolbar-Pan-Werkzeug (das ueber die linke Maustaste laeuft und
            dort bereits fuer die Punkt-/Rechteck-Markierung gebraucht wird).
          - Doppelklick auf das Scroll-Rad: setzt den Zoom auf die zuletzt
            per toolbar.push_current() hinterlegte Vollansicht zurueck
            (aequivalent zum "Home"-Knopf der Toolbar).

        `achsen`: Iterable der Achsen, die beim Pan SYNCHRON verschoben
        werden sollen (bei mehreren Diagrammen nebeneinander, z.B.
        Rohdaten-Tab: Ausgangsbild + gefiltertes Bild). Die Verschiebung
        wird pro Achse ueber deren eigene transData umgerechnet, damit
        unterschiedlich gezoomte/skalierte Achsen trotzdem korrekt und
        synchron mitwandern.
        """
        achsen = tuple(achsen)
        pan_status = {"aktiv": False, "start_px": None, "start_xlim": {}, "start_ylim": {}}

        def _mittlere_maus_runter(event):
            if event.button != 2:
                return
            if event.dblclick:
                # Doppelklick auf dem Scroll-Rad -> Zoom zuruecksetzen.
                pan_status["aktiv"] = False
                try:
                    toolbar.home()
                except Exception:
                    pass
                canvas.draw_idle()
                return
            if event.inaxes not in achsen or event.x is None or event.y is None:
                return
            pan_status["aktiv"] = True
            pan_status["start_px"] = (event.x, event.y)
            pan_status["start_xlim"] = {a: a.get_xlim() for a in achsen}
            pan_status["start_ylim"] = {a: a.get_ylim() for a in achsen}

        def _mittlere_maus_bewegt(event):
            if not pan_status["aktiv"] or event.x is None or event.y is None:
                return
            start_px = pan_status["start_px"]
            for achse in achsen:
                inv = achse.transData.inverted()
                start_data_x, start_data_y = inv.transform(start_px)
                aktuell_data_x, aktuell_data_y = inv.transform((event.x, event.y))
                dx = start_data_x - aktuell_data_x
                dy = start_data_y - aktuell_data_y
                xlim0 = pan_status["start_xlim"][achse]
                ylim0 = pan_status["start_ylim"][achse]
                achse.set_xlim(xlim0[0] + dx, xlim0[1] + dx)
                achse.set_ylim(ylim0[0] + dy, ylim0[1] + dy)
            canvas.draw_idle()

        def _mittlere_maus_los(event):
            if event.button != 2:
                return
            pan_status["aktiv"] = False

        canvas.mpl_connect("button_press_event", _mittlere_maus_runter)
        canvas.mpl_connect("motion_notify_event", _mittlere_maus_bewegt)
        canvas.mpl_connect("button_release_event", _mittlere_maus_los)

    def _sem_aktualisiere_massstabsbalken(self, ax, um_pro_px):
        """
        Zeichnet/aktualisiert den Maßstabsbalken in `ax` mit einer FESTEN
        Länge von MASSSTABSBALKEN_LAENGE_UM (Standard: 1000 µm), umgerechnet
        über die Kalibrierung `um_pro_px` (Mikrometer/Pixel, Standard:
        0.84427 µm/Pixel, vom Nutzer editierbar im Rohdaten-Tab) in die
        entsprechende Pixel-Breite. Bei um_pro_px <= 0 wird nur der
        vorherige Balken entfernt.

        Vorherige Balken-Elemente (Linie + Text) werden VOR dem Neuzeichnen
        entfernt (in ax._sem_massstab_artists zwischengespeichert) - sonst
        wuerden sich bei jedem Zoom/Pan-Schritt immer mehr Balken uebereinander
        stapeln.
        """
        for artist in getattr(ax, "_sem_massstab_artists", []):
            try:
                artist.remove()
            except Exception:
                pass
        ax._sem_massstab_artists = []

        if not um_pro_px or um_pro_px <= 0:
            return

        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        sichtbare_breite_px = abs(xlim[1] - xlim[0])
        sichtbare_hoehe_px = abs(ylim[1] - ylim[0])
        if sichtbare_breite_px <= 0:
            return

        # Feste Balkenlaenge von 1000 Mikrometer (statt automatisch
        # gewaehlter "schoener" Rundungszahl basierend auf dem sichtbaren
        # Ausschnitt). Bei Bedarf ueber MASSSTABSBALKEN_LAENGE_UM anpassbar.
        #
        # === MASSTABSBALKEN-BERECHNUNG (nach Dokumentation - Schritt 6) ===
        # Gewünschte Länge: 1000 µm (= 1 mm)
        # Pixelgröße: 0.84427 µm/Pixel (aus H5OINA oder manuell)
        # Berechnung: balken_px = balken_um / um_pro_px
        #            balken_px = 1000 µm / 0.84427 µm/Pixel
        #            balken_px ≈ 1184.45 Pixel
        balken_px, balken_um = self._sem_berechne_massstabsbalken_pixel(um_pro_px)
        if balken_px is None or balken_um is None:
            return

        x_min = min(xlim)
        rand_x = x_min + sichtbare_breite_px * 0.05
        rand_y = sichtbare_hoehe_px * 0.08
        # Bei imshow ist die y-Achse i.d.R. invertiert (0 oben, Bildhoehe
        # unten) - "unten im Bild" liegt dann bei max(ylim), sonst bei
        # min(ylim). Balken + Beschriftung entsprechend dazu ausrichten.
        invertiert = ylim[0] > ylim[1]
        if invertiert:
            y_balken = max(ylim) - rand_y
            text_y = y_balken - sichtbare_hoehe_px * 0.025
            va = "bottom"
        else:
            y_balken = min(ylim) + rand_y
            text_y = y_balken + sichtbare_hoehe_px * 0.025
            va = "top"

        linie, = ax.plot(
            [rand_x, rand_x + balken_px], [y_balken, y_balken],
            color="black", linewidth=3, solid_capstyle="butt", zorder=5,
        )
        beschriftung = (
            f"{balken_um / 1000:g} mm"
            if balken_um >= 1000 and balken_um % 1000 == 0
            else f"{balken_um:g} µm"
        )
        text = ax.text(
            rand_x + balken_px / 2, text_y, beschriftung,
            color="black", fontsize=9, fontweight="bold", ha="center", va=va, zorder=5,
            path_effects=self._sem_massstab_texteffekt(),
        )
        ax._sem_massstab_artists = [linie, text]

    def _sem_berechne_massstabsbalken_pixel(self, um_pro_px):
        """
        Berechnet die Pixel-Breite des Maßstabsbalkens nach der
        offiziellen Dokumentation (Schritt 6):

        Formel: L_Pixel = L_µm ÷ s_µm/Pixel

        Args:
            um_pro_px (float): Pixelgröße in Mikrometer/Pixel
                               (z.B. 0.84427 µm/Pixel aus H5OINA)

        Returns:
            tuple: (balken_px, balken_um)
                   - balken_px: Pixel-Breite, z.B. 1184.45 Pixel
                   - balken_um: Mikrometer-Länge, z.B. 1000 µm

        Beispiel:
            >>> app = LaborApp()
            >>> px, um = app._sem_berechne_massstabsbalken_pixel(0.84427)
            >>> print(f"Balken: {um} µm = {px:.0f} Pixel")
            Balken: 1000 µm = 1184 Pixel

        Dokumentation:
            - L_µm = 1000 µm (MASSSTABSBALKEN_LAENGE_UM, feste Länge)
            - s_µm/Pixel = 0.84427 µm/Pixel (aus H5OINA oder manuell)
            - L_Pixel = 1000 / 0.84427 = 1184.45 Pixel
        """
        if not um_pro_px or um_pro_px <= 0:
            return None, None

        balken_um = self.MASSSTABSBALKEN_LAENGE_UM
        balken_px = balken_um / um_pro_px

        return balken_px, balken_um

    @staticmethod
    def _sem_kalibrierung_fuer_vorschaubreite(um_pro_px, referenz_breite, vorschau_breite):
        """Uebertraegt die Layered-Image-Kalibrierung auf ein Vorschauarray.

        Matplotlib zeichnet Rohdaten-Elementkarten in deren nativer Breite
        (z.B. 512 Pixel), waehrend die physikalische Kalibrierung fuer das
        EDS Layered Image (z.B. 8192 Pixel) hinterlegt ist. Damit der Balken
        in beiden Ansichten denselben Anteil am Sichtfeld einnimmt, muss ein
        Vorschaupixel entsprechend mehr Mikrometer repraesentieren.
        """
        try:
            um_pro_px = float(um_pro_px)
            referenz_breite = float(referenz_breite)
            vorschau_breite = float(vorschau_breite)
        except (TypeError, ValueError):
            return um_pro_px
        if um_pro_px <= 0 or referenz_breite <= 0 or vorschau_breite <= 0:
            return um_pro_px
        return um_pro_px * referenz_breite / vorschau_breite

    def _sem_massstab_texteffekt(self):
        # Duenne weisse Kontur um die schwarze Maßstabs-Beschriftung, damit
        # sie auch auf dunklem Bildhintergrund lesbar bleibt.
        try:
            import matplotlib.patheffects as pe
            return [pe.withStroke(linewidth=2, foreground="white")]
        except ImportError:
            return []

    def zeichne_rohdaten_vorschau_sem(self, voller_pfad, fig, achsen, canvas, zustand, aktuelle_daten=None):
        """
        Zeichnet die 2 Diagramme des SEM-Rohdaten-Tabs neu: links das
        Ausgangsbild, rechts das gefilterte Bild inkl. Cluster-Umrissen
        (falls aktiviert). `zustand` enthaelt die Filter-Sektion
        (zustand["filter"]), sowie "normieren", "cluster_anzeigen" und
        "mikrometer_pro_pixel" (fuer den Maßstabsbalken).

        `aktuelle_daten` (optional): Dict, in dem die auf Prozent normierten
        Elementkarten zwischengespeichert werden (aktuelle_daten["karten_normiert"]),
        damit ein Klick auf ein Pixel (siehe baue_rohdaten_tab_sem) die
        Elementzusammensetzung an genau dieser Stelle nachschlagen kann, ohne
        die TIFs ein weiteres Mal von der Platte zu lesen.
        """
        import numpy as np

        ax_links, ax_rechts = achsen
        ax_links.clear()
        ax_rechts.clear()

        elementkarten, eds_layered_pfad = self._sem_lade_elementkarten(voller_pfad)
        ausgangsbild, ist_farbig, kalibrierung_quelle = self._sem_lade_ausgangsbild(voller_pfad, elementkarten, eds_layered_pfad)

        ax_links.set_title("Ausgangsbild")
        if ausgangsbild is not None:
            if ist_farbig:
                ax_links.imshow(ausgangsbild)
            else:
                ax_links.imshow(ausgangsbild, cmap="gray")
        else:
            ax_links.text(0.5, 0.5, "Keine Rohdaten (TIF) gefunden", ha="center", va="center")
        ax_links.set_xticks([])
        ax_links.set_yticks([])

        ax_rechts.set_title("Gefiltert" + (" + Cluster-Umrisse" if zustand.get("cluster_anzeigen", True) else ""))
        if not elementkarten:
            if aktuelle_daten is not None:
                aktuelle_daten["karten_normiert"] = None
            ax_rechts.text(0.5, 0.5, "Keine Elementkarten (TIF) gefunden", ha="center", va="center")
            ax_rechts.set_xticks([])
            ax_rechts.set_yticks([])
            canvas.draw()
            return

        karten_prozent = self._sem_normalisiere_elementkarten(elementkarten)
        if aktuelle_daten is not None:
            # IMMER die %-normierten Karten fuer den Pixel-Klick-Popup
            # zwischenspeichern (unabhaengig vom "normieren"-Haekchen, das
            # nur die Filter-Anzeige betrifft) - die Zusammensetzung an
            # einem Pixel will man ja immer in % sehen.
            aktuelle_daten["karten_normiert"] = karten_prozent
        karten = karten_prozent if zustand.get("normieren", True) else elementkarten
        maske = self._sem_wende_filter_an(karten, zustand.get("filter", []))

        # Der schwarze Hintergrund AUSSERHALB der Probe (Vakuum/kein
        # Material) hat ueberall ein Rohsignal von 0 - bei den normierten
        # %-Karten wird das dann ebenfalls ueberall zu 0 %, wodurch ein
        # Filter wie "C < 30" dort faelschlich IMMER zutrifft und der
        # Clusteralgorithmus den gesamten Hintergrund als (einen riesigen)
        # Cluster erkennt -> Umriss liegt dann rund um die Probe statt nur
        # um die tatsaechlich gefilterten Bereiche. Deshalb hier explizit
        # ausschliessen: nur Pixel MIT Rohsignal (= echte Probenflaeche)
        # duerfen ueberhaupt in die Maske/den Cluster-Umriss.
        probe_maske = np.sum(np.stack(list(elementkarten.values()), axis=0), axis=0) > 0.0
        if maske is not None:
            maske = maske & probe_maske

        # --- "Kleine Cluster ausblenden": isolierte Cluster < 3 Pixel (ohne
        # groesseren Cluster in der unmittelbaren Naehe) werden HIER schon
        # aus der Maske entfernt - VOR dem Einfaerben/Anzeigen - damit sie
        # tatsaechlich aus dem Bild verschwinden (nicht nur aus dem
        # Cluster-Umriss). Laeuft unabhaengig davon, ob die Umrisse selbst
        # angezeigt werden (siehe "cluster_anzeigen").
        # SPEICHERE MASKE FUER MAUS-AUSBLENDUNG (Rechtsklick auf kleine Cluster). ---
        zustand["_aktuelle_maske"] = maske.copy() if maske is not None else None
        
        if maske is not None and zustand.get("kleine_cluster_ausblenden", False):
            _kb_labels, _kb_anzahl = self._sem_berechne_cluster(maske)
            _kb_sichtbar = self._sem_filtere_kleine_cluster(_kb_labels, _kb_anzahl)
            if _kb_sichtbar is not None:
                maske = maske & _kb_sichtbar

        if ausgangsbild is not None and ist_farbig:
            basis_grau = np.mean(np.asarray(ausgangsbild, dtype=np.float64)[..., :3], axis=-1)
        elif ausgangsbild is not None:
            basis_grau = np.asarray(ausgangsbild, dtype=np.float64)
        else:
            basis_grau = np.sum(np.stack(list(elementkarten.values()), axis=0), axis=0)

        # Hintergrund (keine Probe) soll WEISS erscheinen statt schwarz -
        # dafuer die Achsenflaeche weiss faerben und den Hintergrund im
        # Graustufenbild auf NaN setzen (wird dann transparent, die weisse
        # Achsenflaeche scheint durch).
        ax_rechts.set_facecolor("white")
        basis_grau_anzeige = np.array(basis_grau, dtype=np.float64, copy=True)
        if probe_maske.shape == basis_grau.shape:
            basis_grau_anzeige[~probe_maske] = np.nan

        gefiltert_anzeige = np.array(basis_grau, dtype=np.float64, copy=True)
        if maske is not None and maske.shape == basis_grau.shape:
            gefiltert_anzeige[~maske] = np.nan
        ax_rechts.imshow(basis_grau_anzeige, cmap="gray", alpha=0.35)
        ax_rechts.imshow(gefiltert_anzeige, cmap="viridis")

        anzahl_cluster = 0
        if maske is not None and zustand.get("cluster_anzeigen", True):
            # `maske` ist an dieser Stelle bereits um evtl. ausgeblendete
            # kleine Cluster bereinigt (siehe oben) - der Umriss zeigt also
            # automatisch nur noch die tatsaechlich sichtbaren Cluster.
            _labels, anzahl_cluster = self._sem_berechne_cluster(maske)
            try:
                # Umrisse aller (auch mehrerer getrennter) Cluster in einem
                # Zug: contour() zeichnet an der 0.5-Hoehenlinie die Grenze
                # jeder zusammenhaengenden True-Region der Maske.
                ax_rechts.contour(
                    maske.astype(np.float64), levels=[0.5], colors="red", linewidths=1.2
                )
            except Exception as e:
                print(f"[SEM Umriss Fehler] {e}")

        retained_pct = float(np.mean(maske)) * 100.0 if maske is not None else 0.0
        ax_rechts.set_xlabel(f"behalten: {retained_pct:.1f} % | Cluster: {anzahl_cluster}")
        ax_rechts.set_xticks([])
        ax_rechts.set_yticks([])

        manueller_wert = zustand.get("mikrometer_pro_pixel", 0.0)
        eds_um_pro_px, _ = self._sem_ermittle_um_pro_pixel(
            voller_pfad, manueller_wert, quelle="eds"
        )
        # Referenzbreite immer direkt aus dem EDS-Layered-TIFF im TIF-Ordner
        # lesen. Die beiden Rohdatenachsen verwenden dagegen ihre jeweiligen
        # Arraybreiten (Backscatter bzw. 512px-Elementkarte). Ohne diese
        # Umrechnung wurde ein 1-mm-Balken fast ueber die gesamte Vorschau
        # gezeichnet, obwohl er im 8192px-Ergebnisbild korrekt war.
        referenz_breite = None
        if eds_layered_pfad:
            try:
                from PIL import Image
                with Image.open(eds_layered_pfad) as eds_bild:
                    referenz_breite = eds_bild.size[0]
            except Exception as exc:
                print(f"[SEM Maßstab] TIFF-Breite nicht lesbar ({eds_layered_pfad}): {exc}")
        if not referenz_breite:
            referenz_breite = ausgangsbild.shape[1]

        links_um_pro_px = self._sem_kalibrierung_fuer_vorschaubreite(
            eds_um_pro_px, referenz_breite, ausgangsbild.shape[1]
        )
        referenzkarte = next(iter(elementkarten.values()))
        rechts_um_pro_px = self._sem_kalibrierung_fuer_vorschaubreite(
            eds_um_pro_px, referenz_breite, referenzkarte.shape[1]
        )

        for ax_massstab, achsen_um_pro_px in (
            (ax_links, links_um_pro_px),
            (ax_rechts, rechts_um_pro_px),
        ):
            # Vollansicht (Grenzen direkt nach dem Neuzeichnen, bevor
            # irgendein Pan/Zoom passiert ist) fuer den Doppelklick-Reset
            # merken (siehe _on_press/_vollansicht_setzen in
            # baue_rohdaten_tab_sem).
            ax_massstab._sem_vollansicht = (ax_massstab.get_xlim(), ax_massstab.get_ylim())
            self._sem_aktualisiere_massstabsbalken(ax_massstab, achsen_um_pro_px)

        fig.tight_layout()
        canvas.draw()

    def baue_rohdaten_tab_sem(self, parent, projekt, methode):
        """
        SEM-Gegenstueck zu baue_rohdaten_tab_tga: links die Versuchsliste,
        in der Mitte 2 Diagramme nebeneinander (links Ausgangsbild, rechts
        gefiltertes Bild mit Cluster-Umrissen), rechts die Filter-Sektion
        (dynamische Liste von Schwellwert-Filtern wie "C < 30", per UND
        kombiniert - siehe SEM_FILTER_ELEMENTE/_sem_wende_filter_an) sowie
        Schalter fuer Normierung und Cluster-Umrisse. Einstellungen werden
        wie bei TGA pro Versuch gespeichert (lade_/speichere_
        rohdaten_filter_einstellungen_fuer_versuch).
        """
        versuche = self.liste_versuche(projekt, methode)
        if not versuche:
            ctk.CTkLabel(parent, text="Keine lokalen Rohdaten gefunden.").pack(pady=20)
            return

        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
            import tkinter as tk
            import numpy as np
        except ImportError:
            ctk.CTkLabel(
                parent,
                text="matplotlib ist nicht installiert - 'pip install matplotlib' im GUI-Environment noetig.",
                wraplength=560,
            ).pack(pady=20)
            return

        zustand_standard = {
            "filter": [dict(f) for f in SEM_FILTER_STANDARD_LISTE],
            "normieren": True,
            "cluster_anzeigen": True,
            "kleine_cluster_ausblenden": False,
            "mikrometer_pro_pixel": 0.84427,
        }
        zustand = self.lade_rohdaten_filter_einstellungen_fuer_versuch(
            projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, versuche[0][2]), zustand_standard
        )
        if not zustand.get("filter"):
            zustand["filter"] = [dict(f) for f in SEM_FILTER_STANDARD_LISTE]
        zustand["filter"] = self._sem_normalisiere_filter_liste(zustand["filter"])

        haupt_layout = ctk.CTkFrame(parent, fg_color="transparent")
        haupt_layout.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Linke Spalte: Versuchsliste (analog TGA, ohne Verarb.-Haekchen -
        # fuer SEM gibt es noch kein *_calculation.py-Skript) ---
        linke_breite_merker = {"breite": 260}
        linke_spalte = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=linke_breite_merker["breite"])
        linke_spalte.pack(side="left", fill="y", padx=(0, 0))
        linke_spalte.pack_propagate(False)

        kopfzeile = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        kopfzeile.pack(side="top", fill="x", padx=5, pady=(0, 5))
        links_einklapp_btn = ctk.CTkButton(kopfzeile, text="◀", width=32, fg_color="transparent", border_width=1)
        links_einklapp_btn.pack(side="left")

        inhalt_links = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        inhalt_links.pack(fill="both", expand=True)

        ctk.CTkLabel(inhalt_links, text="Versuche", font=("Arial", 12, "bold")).pack(
            side="top", anchor="w", padx=10, pady=(0, 5)
        )
        scroll = ctk.CTkScrollableFrame(inhalt_links, width=220)
        scroll.pack(padx=0, pady=(0, 5), fill="both", expand=True)

        griff_links = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_links.pack(side="left", fill="y", padx=(4, 8))
        self._mache_griff_ziehbar(griff_links, linke_spalte, linke_breite_merker, minimum=90, maximum=650, invertiert=False)
        self._mache_spalte_einklappbar(
            links_einklapp_btn, linke_spalte, inhalt_links, linke_breite_merker,
            eingeklappt_text="◀", ausgeklappt_text="▶", breite_eingeklappt=48,
        )

        ausgewaehlter_pfad = {"wert": None}
        zeilen_frames = []
        aktualisiere_filter_panel = {"fn": None}

        def waehle_versuch(voller_pfad):
            ausgewaehlter_pfad["wert"] = voller_pfad
            for zeile, pfad in zeilen_frames:
                zeile.configure(fg_color=MUL_TURKIS if pfad == voller_pfad else "transparent")
            zustand.clear()
            zustand.update(
                self.lade_rohdaten_filter_einstellungen_fuer_versuch(
                    projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad), zustand_standard
                )
            )
            if not zustand.get("filter"):
                zustand["filter"] = [dict(f) for f in SEM_FILTER_STANDARD_LISTE]
            zustand["filter"] = self._sem_normalisiere_filter_liste(zustand["filter"])
            markierungen.clear()
            if aktualisiere_filter_panel["fn"]:
                aktualisiere_filter_panel["fn"]()
            zeichne()

        for _staub, eintrag, voller_pfad in versuche:
            zeile = ctk.CTkFrame(scroll, fg_color="transparent")
            zeile.pack(fill="x", pady=2)
            label = ctk.CTkLabel(zeile, text=os.path.splitext(eintrag)[0], anchor="w")
            label.pack(side="left", padx=5, fill="x", expand=True)
            zeilen_frames.append((zeile, voller_pfad))
            for widget in (zeile, label):
                widget.bind("<Button-1>", lambda _e, p=voller_pfad: waehle_versuch(p))

        # --- Mitte: 2 Diagramme nebeneinander (Ausgangsbild | gefiltert) ---
        mitte = ctk.CTkFrame(haupt_layout, fg_color="transparent")
        mitte.pack(side="left", fill="both", expand=True, padx=(0, 0))

        # Toolbar (Home/Zurueck/Vor/Pan/Zoom) UNTER den Diagrammen, damit man
        # ein versehentlich mit der Maus verschobenes Bild ueber den
        # "Home"-Knopf wieder schoen in die Mitte/Vollansicht zurueckholen
        # kann - analog zur Ergebnisse-Tab-Toolbar. NavigationToolbar2Tk
        # braucht ein "echtes" Tk-Widget als Master, daher tk.Frame statt
        # CTkFrame fuer den Toolbar-Container.
        toolbar_frame = tk.Frame(mitte)
        toolbar_frame.pack(side="bottom", fill="x")

        figsize = self._dynamische_figsize(mitte, 2, 1, mindest_breite_px=650, mindest_hoehe_px=420)
        fig, achsen_zeile = plt.subplots(1, 2, figsize=figsize)
        achsen = (achsen_zeile[0], achsen_zeile[1])
        canvas = FigureCanvasTkAgg(fig, master=mitte)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
        toolbar.update()

        aktuelle_daten = {"karten_normiert": None}
        markierungen = []

        def zeichne():
            pfad = ausgewaehlter_pfad["wert"]
            if not pfad:
                return
            self.zeichne_rohdaten_vorschau_sem(pfad, fig, achsen, canvas, zustand, aktuelle_daten=aktuelle_daten)
            _zeichne_markierungen()
            canvas.draw()
            # WICHTIG: "Home"-Knopf (Haus-Symbol) der Toolbar reparieren.
            # toolbar.update() ALLEIN reicht nicht - es LEERT nur den
            # Verlaufsspeicher (Zurueck/Vor), setzt aber KEINEN neuen
            # Home-Punkt. Ohne den anschliessenden push_current() ist der
            # Verlauf danach leer, und der Haus-Knopf springt ins Leere
            # (passiert nichts). push_current() legt die gerade gezeichnete
            # Vollansicht als neuen Ausgangspunkt ab, auf den "Home" dann
            # zuverlaessig zurueckspringt.
            toolbar.update()
            toolbar.push_current()



        # --- Strg + Mausrad: beide Diagramme SYNCHRON zoomen (um den
        # Cursor herum), damit man Ausgangsbild und gefiltertes Bild
        # gemeinsam vergleichend reinzoomen kann. Ohne gedrueckte Strg-Taste
        # passiert nichts, damit normales Scrollen der Seite nicht gestoert wird. ---
        def _strg_taste_gedrueckt(event):
            """
            Robuste Erkennung, ob Strg/Ctrl/Cmd bei Scroll-Event gedrueckt war.
            Unterstuetzt mehrere Event-Systeme mit Fallbacks:
            - tkinter: guiEvent.state & 0x0004
            - wxPython: event.ControlDown()
            - Qt: event.modifiers() & Qt.ControlModifier
            - Generisch: event.key String-Analyse
            """
            # Methode 1: tkinter guiEvent.state
            gui_event = getattr(event, "guiEvent", None)
            if gui_event is not None:
                zustand_bits = getattr(gui_event, "state", 0)
                try:
                    if bool(int(zustand_bits) & 0x0004):  # Strg-Taste unter tkinter
                        return True
                except (TypeError, ValueError):
                    pass
            
            # Methode 2: wxPython
            if hasattr(event, "ControlDown"):
                try:
                    if event.ControlDown():
                        return True
                except Exception:
                    pass
            
            # Methode 3: Qt
            if hasattr(event, "modifiers"):
                try:
                    # Versuche Qt zu importieren, falls verfuegbar
                    try:
                        from matplotlib.backends.qt_compat import QtCore
                        if event.modifiers() & QtCore.Qt.ControlModifier:
                            return True
                    except ImportError:
                        pass
                except Exception:
                    pass
            
            # Methode 4: event.key String-Analyse (manche Matplotlib-Versionen)
            key = getattr(event, "key", None)
            if key is not None and "control" in str(key).lower():
                return True
            
            return False

        def _synchroner_zoom(event):
            ziel_achse = event.inaxes
            if ziel_achse not in achsen:
                return
            
            # Robuste Strg-Taste-Erkennung (mit Fallbacks fuer verschiedene Systeme)
            if not _strg_taste_gedrueckt(event):
                return
            
            if event.button == "up":
                faktor = 0.85  # Vergroessern (Hineinzoomen)
            elif event.button == "down":
                faktor = 1.0 / 0.85  # Verkleinern (Herauszoomen)
            else:
                return
            
            xlim0, ylim0 = ziel_achse.get_xlim(), ziel_achse.get_ylim()
            breite0, hoehe0 = xlim0[1] - xlim0[0], ylim0[1] - ylim0[0]
            
            # Relative Position der Maus in der Achse (0.0 - 1.0)
            if event.xdata is not None and event.ydata is not None:
                rel_x = (event.xdata - xlim0[0]) / breite0 if breite0 else 0.5
                rel_y = (event.ydata - ylim0[0]) / hoehe0 if hoehe0 else 0.5
            else:
                rel_x, rel_y = 0.5, 0.5
            
            # Zoom auf BEIDEN Achsen synchron anwenden
            for achse in achsen:
                xlim, ylim = achse.get_xlim(), achse.get_ylim()
                neue_breite = (xlim[1] - xlim[0]) * faktor
                neue_hoehe = (ylim[1] - ylim[0]) * faktor
                mitte_x = xlim[0] + rel_x * (xlim[1] - xlim[0])
                mitte_y = ylim[0] + rel_y * (ylim[1] - ylim[0])
                achse.set_xlim(mitte_x - rel_x * neue_breite, mitte_x + (1 - rel_x) * neue_breite)
                achse.set_ylim(mitte_y - rel_y * neue_hoehe, mitte_y + (1 - rel_y) * neue_hoehe)
            canvas.draw_idle()

        canvas.mpl_connect("scroll_event", _synchroner_zoom)

        # --- Gedrueckt gehaltenes Scroll-Rad: Kartenausschnitt verschieben;
        # Doppelklick auf dem Scroll-Rad: Zoom zuruecksetzen (siehe
        # _sem_aktiviere_scrollrad_pan). Beide Achsen (Ausgangsbild +
        # gefiltertes Bild) wandern dabei synchron mit. ---
        self._sem_aktiviere_scrollrad_pan(canvas, achsen, toolbar)

        def _werte_am_pixel(x, y):
            karten = aktuelle_daten["karten_normiert"]
            if not karten:
                return None
            beispiel = next(iter(karten.values()))
            if not (0 <= y < beispiel.shape[0] and 0 <= x < beispiel.shape[1]):
                return None
            return {element: float(karte[y, x]) for element, karte in karten.items()}

        def _durchschnitt_im_bereich(x0, y0, x1, y1):
            """Mittelwert jedes Elements ueber ein rechteckiges Pixel-Gebiet
            (fuer die Rechteck-Auswahl per Ziehen). Koordinaten werden
            automatisch auf die Bildgrenzen begrenzt."""
            karten = aktuelle_daten["karten_normiert"]
            if not karten:
                return None
            beispiel = next(iter(karten.values()))
            hoehe_bild, breite_bild = beispiel.shape[0], beispiel.shape[1]
            xa, xb = sorted((int(round(x0)), int(round(x1))))
            ya, yb = sorted((int(round(y0)), int(round(y1))))
            xa = max(0, min(xa, breite_bild - 1))
            xb = max(0, min(xb, breite_bild - 1))
            ya = max(0, min(ya, hoehe_bild - 1))
            yb = max(0, min(yb, hoehe_bild - 1))
            if xb < xa or yb < ya:
                return None
            return (
                {element: float(np.mean(karte[ya:yb + 1, xa:xb + 1])) for element, karte in karten.items()},
                (xa, ya, xb, yb),
            )

        def _markierung_box_inhalt(marker, index):
            """Zeilen + grobe Box-Groesse (in Punkten) fuer die
            Zusammensetzungs-Box einer Markierung - wird sowohl beim
            Zeichnen (Position der Box/des "x"-Knopfes) als auch beim
            Klick-Hittest auf den "x"-Knopf gebraucht, damit beide exakt
            dieselbe Geometrie annehmen."""
            top_werte = sorted(marker["werte"].items(), key=lambda kv: -kv[1])[:5]
            zeilen = [f"{element}: {wert:.1f}%" for element, wert in top_werte if wert > 0.0]
            if marker.get("typ") == "rechteck":
                kopf = f"Ø{index}  ({marker['breite']}×{marker['hoehe']} px)"
            else:
                kopf = f"M{index}"
            alle_zeilen = [kopf] + (zeilen if zeilen else ["keine Elemente > 0 %"])
            breite_pts = 16 + max(len(z) for z in alle_zeilen) * 5.5
            hoehe_pts = 14 + len(alle_zeilen) * 12.5
            return zeilen, breite_pts, hoehe_pts

        def _x_knopf_offset_pts(marker, index):
            _zeilen, breite_pts, hoehe_pts = _markierung_box_inhalt(marker, index)
            return (16 + breite_pts - 10, 16 + hoehe_pts - 8)

        def _x_knopf_display_pos(marker, index):
            """Aktuelle Bildschirm-Position (Pixel) des Mini-'x'-Knopfes
            EINER Markierung - IMMER frisch aus dem aktuellen Zoom/Pan-
            Zustand berechnet (ax.transData), damit der Hittest auch nach
            Verschieben/Zoomen ohne Neuzeichnen noch stimmt."""
            off_x_pts, off_y_pts = _x_knopf_offset_pts(marker, index)
            anker_disp = marker["ax"].transData.transform((marker["x"], marker["y"]))
            px_je_pt = fig.dpi / 72.0
            return (anker_disp[0] + off_x_pts * px_je_pt, anker_disp[1] + off_y_pts * px_je_pt)

        def _klick_auf_x_knopf(event):
            for index, marker in enumerate(markierungen, start=1):
                if marker["ax"] is not event.inaxes:
                    continue
                bx, by = _x_knopf_display_pos(marker, index)
                if (event.x - bx) ** 2 + (event.y - by) ** 2 <= 11 ** 2:
                    return marker
            return None

        def _zeichne_markierungen():
            """Zeichnet fuer jede gesetzte Markierung entweder einen kleinen
            nummerierten Kreis am Pixel (einzelner Klick) oder ein
            gestricheltes Rechteck (Rechteck-Auswahl per Ziehen), jeweils
            mit einer Box mit der (Durchschnitts-)Elementzusammensetzung
            DIREKT DANEBEN im jeweiligen Bild - inkl. Mini-"x"-Knopf oben
            rechts zum gezielten Entfernen genau dieser einen Markierung."""
            for index, marker in enumerate(markierungen, start=1):
                ax_ziel = marker["ax"]
                if marker.get("typ") == "rechteck":
                    rect_patch = mpatches.Rectangle(
                        (marker["x0"], marker["y0"]),
                        marker["x1"] - marker["x0"], marker["y1"] - marker["y0"],
                        fill=False, edgecolor="white", linewidth=1.6, linestyle="--", zorder=5,
                    )
                    ax_ziel.add_patch(rect_patch)
                    ax_ziel.annotate(
                        str(index), (marker["x0"], marker["y0"]), color="white", fontsize=9, fontweight="bold",
                        xytext=(3, -3), textcoords="offset points", ha="left", va="top",
                    )
                else:
                    ax_ziel.plot(marker["x"], marker["y"], marker="o", markersize=9,
                                 markerfacecolor="none", markeredgecolor="white", markeredgewidth=2)
                    ax_ziel.annotate(
                        str(index), (marker["x"], marker["y"]), color="white", fontsize=9, fontweight="bold",
                        ha="center", va="center",
                    )
                zeilen, _breite_pts, _hoehe_pts = _markierung_box_inhalt(marker, index)
                kopf = f"Ø{index}" if marker.get("typ") == "rechteck" else f"M{index}"
                box_text = kopf + "\n" + ("\n".join(zeilen) if zeilen else "keine Elemente > 0 %")
                ax_ziel.annotate(
                    box_text,
                    xy=(marker["x"], marker["y"]), xycoords="data",
                    xytext=(16, 16), textcoords="offset points",
                    fontsize=8, color="black", ha="left", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=MUL_TURKIS, alpha=0.92),
                    arrowprops=dict(arrowstyle="->", color=MUL_TURKIS, lw=1.2),
                    zorder=6,
                )
                x_knopf_offset = _x_knopf_offset_pts(marker, index)
                ax_ziel.annotate(
                    "✕",
                    xy=(marker["x"], marker["y"]), xycoords="data",
                    xytext=x_knopf_offset, textcoords="offset points",
                    fontsize=8, color="white", fontweight="bold", ha="center", va="center",
                    bbox=dict(boxstyle="circle,pad=0.22", fc="#c0392b", ec="white", lw=1),
                    zorder=7,
                )

        # --- Klick = einzelner Pixel, Ziehen (Rechteck aufziehen) =
        # Durchschnitts-Elementaranalyse ueber die Auswahl. Unterschieden
        # wird per Maus-runter/-bewegt/-los statt nur einem einzelnen
        # Klick-Event, damit ein kurzer Klick weiterhin wie bisher einen
        # Punkt setzt, ein Ziehen aber die neue Rechteck-Auswahl ausloest.
        # RECHTSKLICK auf kleine Cluster: entfernt isolierte Cluster automatisch ---
        auswahl = {"aktiv": False, "achse": None, "start_data": None, "start_disp": None, "vorschau": None}
        ZIEH_SCHWELLE_PX = 6  # Mindestbewegung in Bildschirm-Pixeln fuer "Ziehen" statt "Klick"
        
        # Maus-basiertes Cluster-Ausblenden: Rechtsklick auf kleine Cluster
        def _rechtsklick_cluster_ausblenden(event):
            """Rechtsklick auf kleine Cluster entfernt diese automatisch"""
            if event.button != 3:  # Button 3 = Rechtsklick
                return
            if event.inaxes not in achsen or event.xdata is None or event.ydata is None:
                return
            
            # Pixel an dieser Position ermitteln
            karten = aktuelle_daten["karten_normiert"]
            if not karten:
                return
            beispiel = next(iter(karten.values()))
            x, y = int(round(event.xdata)), int(round(event.ydata))
            if not (0 <= y < beispiel.shape[0] and 0 <= x < beispiel.shape[1]):
                return
            
            # Cluster-Label an dieser Position finden
            maske = zustand.get("_aktuelle_maske")
            if maske is None:
                return
            
            labels, anzahl_cluster = self._sem_berechne_cluster(maske)
            if labels is None or anzahl_cluster == 0:
                return
            
            cluster_label = labels[y, x]
            if cluster_label == 0:  # Kein Cluster an dieser Position
                return
            
            # Größe dieses Clusters prüfen
            groessen = (labels == cluster_label).sum()
            if groessen >= 3:
                # Nicht klein genug - nicht entfernen
                return
            
            # KLEINE CLUSTER ENTFERNEN: Maske updaten
            kleine_cluster_var.set(True)  # Checkbox aktivieren
            zustand["kleine_cluster_ausblenden"] = True
            
            # Neu zeichnen
            zeichne()

        def _auswahl_vorschau_entfernen():
            if auswahl["vorschau"] is not None:
                try:
                    auswahl["vorschau"].remove()
                except Exception:
                    pass
                auswahl["vorschau"] = None

        def _maus_runter(event):
            if event.button != 1:
                return
            # Waehrend die Toolbar-Lupe/Pan aktiv ist, gehoert der Klick/Zug
            # zum Zoomen/Verschieben - dann KEINE Markierung setzen/entfernen.
            if getattr(toolbar, "mode", ""):
                return
            # Klick auf den Mini-"x"-Knopf einer bestehenden Box? Das MUSS
            # VOR dem inaxes-Check passieren: die Box (und damit der
            # "x"-Knopf) kann ueber den Bildrand hinaus in den Bereich
            # AUSSERHALB der Achsen hineinragen.
            treffer = _klick_auf_x_knopf(event)
            if treffer is not None:
                markierungen.remove(treffer)
                zeichne()
                return
            if event.inaxes not in achsen or event.xdata is None or event.ydata is None:
                return
            canvas.get_tk_widget().focus_set()
            auswahl["aktiv"] = True
            auswahl["achse"] = event.inaxes
            auswahl["start_data"] = (event.xdata, event.ydata)
            auswahl["start_disp"] = (event.x, event.y)

        def _maus_bewegt(event):
            if not auswahl["aktiv"] or event.inaxes != auswahl["achse"] or event.xdata is None or event.ydata is None:
                return
            x0, y0 = auswahl["start_data"]
            _auswahl_vorschau_entfernen()
            patch = mpatches.Rectangle(
                (min(x0, event.xdata), min(y0, event.ydata)),
                abs(event.xdata - x0), abs(event.ydata - y0),
                fill=False, edgecolor="white", linewidth=1.2, linestyle="--", zorder=8,
            )
            auswahl["achse"].add_patch(patch)
            auswahl["vorschau"] = patch
            canvas.draw_idle()

        def _maus_los(event):
            if not auswahl["aktiv"]:
                return
            auswahl["aktiv"] = False
            _auswahl_vorschau_entfernen()
            achse = auswahl["achse"]
            start_disp = auswahl["start_disp"]
            start_data = auswahl["start_data"]
            if event.inaxes != achse or event.xdata is None or event.ydata is None:
                canvas.draw_idle()
                return
            bewegt_px = ((event.x - start_disp[0]) ** 2 + (event.y - start_disp[1]) ** 2) ** 0.5
            if bewegt_px < ZIEH_SCHWELLE_PX:
                # Kurzer Klick (kaum Bewegung) -> wie bisher ein einzelner Punkt.
                x = int(round(event.xdata))
                y = int(round(event.ydata))
                werte = _werte_am_pixel(x, y)
                if werte is None:
                    canvas.draw_idle()
                    return
                markierungen.append({"typ": "punkt", "ax": achse, "x": x, "y": y, "werte": werte})
            else:
                # Rechteck aufgezogen -> Durchschnitts-Elementaranalyse ueber
                # die Auswahl. Box wird an der oberen rechten Ecke verankert.
                ergebnis = _durchschnitt_im_bereich(start_data[0], start_data[1], event.xdata, event.ydata)
                if ergebnis is None:
                    canvas.draw_idle()
                    return
                werte, (xa, ya, xb, yb) = ergebnis
                markierungen.append({
                    "typ": "rechteck", "ax": achse,
                    "x": xb, "y": ya,
                    "x0": xa, "y0": ya, "x1": xb, "y1": yb,
                    "breite": max(xb - xa + 1, 1), "hoehe": max(yb - ya + 1, 1),
                    "werte": werte,
                })
            zeichne()

        def _bei_taste(event):
            # ESC schliesst die zuletzt gesetzte Markierung (zusaetzlich zum
            # Mini-"x"-Knopf direkt an der Box).
            if event.key == "escape" and markierungen:
                markierungen.pop()
                zeichne()

        canvas.mpl_connect("button_press_event", _maus_runter)
        canvas.mpl_connect("motion_notify_event", _maus_bewegt)
        canvas.mpl_connect("button_release_event", _maus_los)
        canvas.mpl_connect("button_press_event", _rechtsklick_cluster_ausblenden)
        canvas.mpl_connect("key_press_event", _bei_taste)

        # --- Rechts: Filter-Sektion (dynamische Liste) + Normierung/Cluster ---
        rechte_breite_merker = {"breite": 340}
        griff_rechts = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_rechts.pack(side="left", fill="y", padx=(8, 4))

        rechte_container = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=rechte_breite_merker["breite"])
        rechte_container.pack(side="left", fill="y")
        rechte_container.pack_propagate(False)

        rechte_kopfzeile = ctk.CTkFrame(rechte_container, fg_color="transparent")
        rechte_kopfzeile.pack(side="top", fill="x", padx=(5, 0), pady=(0, 5))
        rechts_einklapp_btn = ctk.CTkButton(
            rechte_kopfzeile, text="▶", width=32, fg_color="transparent", border_width=1
        )
        rechts_einklapp_btn.pack(side="left")

        rechte_spalte = ctk.CTkScrollableFrame(rechte_container, fg_color="transparent")
        rechte_spalte.pack(fill="both", expand=True)

        ctk.CTkLabel(
            rechte_spalte, text="Filter-Sektion", font=("Arial", 14, "bold")
        ).pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(
            rechte_spalte,
            text="Schwellwert-Filter auf normierte Elementanteile, z.B. C < 30. "
                 "Mehrere Elemente pro Zeile ergeben eine SUMME, z.B. C+O > 15. "
                 "Zeilen werden per UND kombiniert (alle aktiven Filter muessen zutreffen).",
            font=("Arial", 10), text_color=("gray30", "gray70"),
            anchor="w", justify="left", wraplength=290,
        ).pack(fill="x", padx=10, pady=(0, 10))

        self._mache_griff_ziehbar(
            griff_rechts, rechte_container, rechte_breite_merker, minimum=200, maximum=650, invertiert=True
        )
        self._mache_spalte_einklappbar(
            rechts_einklapp_btn, rechte_container, rechte_spalte, rechte_breite_merker,
            eingeklappt_text="▶", ausgeklappt_text="◀", breite_eingeklappt=48,
        )

        filter_liste_frame = ctk.CTkFrame(rechte_spalte, fg_color="transparent")
        filter_liste_frame.pack(fill="x", padx=0, pady=(0, 5))

        def _uebernimm_zeilen_in_zustand():
            """Liest die aktuellen Filter-Zeilen-Widgets in zustand['filter'] ein."""
            neue_liste = []
            for eintrag in filter_zeilen_widgets:
                try:
                    wert = float(eintrag["wert_entry"].get().strip().replace(",", "."))
                except ValueError:
                    wert = eintrag["daten"].get("wert", 0)
                neue_liste.append({
                    "elemente": list(eintrag["elemente_liste"]) or ["C"],
                    "operator": eintrag["operator_dropdown"].get(),
                    "wert": wert,
                    "aktiv": bool(eintrag["aktiv_var"].get()),
                })
            zustand["filter"] = neue_liste

        filter_zeilen_widgets = []

        def baue_filter_zeilen():
            for kind in filter_liste_frame.winfo_children():
                kind.destroy()
            filter_zeilen_widgets.clear()

            for index, eintrag in enumerate(zustand.get("filter", [])):
                zeile = ctk.CTkFrame(filter_liste_frame, fg_color=("gray90", "gray20"))
                zeile.pack(fill="x", padx=10, pady=4)

                kopfzeile_filter = ctk.CTkFrame(zeile, fg_color="transparent")
                kopfzeile_filter.pack(fill="x", padx=6, pady=(4, 2))

                aktiv_var = ctk.BooleanVar(value=eintrag.get("aktiv", True))
                ctk.CTkCheckBox(kopfzeile_filter, text="", variable=aktiv_var, width=20).pack(side="left", padx=(0, 2))

                operator_dropdown = ctk.CTkOptionMenu(
                    kopfzeile_filter, values=list(SEM_FILTER_OPERATOREN), width=55, fg_color=MUL_TURKIS
                )
                operator_dropdown.set(eintrag.get("operator", "<"))
                operator_dropdown.pack(side="left", padx=2)

                wert_entry = ctk.CTkEntry(kopfzeile_filter, width=60)
                wert_entry.insert(0, str(eintrag.get("wert", 0)))
                wert_entry.pack(side="left", padx=2)
                wert_entry.bind("<Return>", lambda _e: uebernehmen())
                ctk.CTkLabel(kopfzeile_filter, text="%", font=("Arial", 11)).pack(side="left", padx=(0, 4))

                def _entfernen(i=index):
                    _uebernimm_zeilen_in_zustand()
                    del zustand["filter"][i]
                    baue_filter_zeilen()
                    uebernehmen()

                ctk.CTkButton(
                    kopfzeile_filter, text="✕", width=28, fg_color="transparent",
                    text_color=("black", "white"), hover_color="#aa3333", command=_entfernen,
                ).pack(side="right", padx=(2, 0))

                # --- Element-"Chips": ein Filter kann sich auf MEHRERE
                # Elemente gleichzeitig beziehen (Summe), z.B. C + O. ---
                elemente_liste = list(eintrag.get("elemente") or [eintrag.get("element", "C")])
                if not elemente_liste:
                    elemente_liste = ["C"]

                chips_zeile = ctk.CTkFrame(zeile, fg_color="transparent")
                chips_zeile.pack(fill="x", padx=6, pady=(0, 6))

                def _chips_neu_zeichnen(chips_zeile=chips_zeile, elemente_liste=elemente_liste):
                    for kind in chips_zeile.winfo_children():
                        kind.destroy()
                    for i, elem in enumerate(elemente_liste):
                        if i > 0:
                            ctk.CTkLabel(chips_zeile, text="+", font=("Arial", 11, "bold")).pack(side="left", padx=(2, 2))
                        chip = ctk.CTkFrame(chips_zeile, fg_color=MUL_TURKIS, corner_radius=5)
                        chip.pack(side="left", padx=1)
                        ctk.CTkLabel(chip, text=elem, text_color="white", font=("Arial", 11, "bold")).pack(
                            side="left", padx=(6, 2), pady=2
                        )

                        def _element_entfernen(i=i, chips_zeile=chips_zeile, elemente_liste=elemente_liste):
                            if len(elemente_liste) > 1:
                                elemente_liste.pop(i)
                                _chips_neu_zeichnen(chips_zeile, elemente_liste)
                                uebernehmen()

                        ctk.CTkButton(
                            chip, text="✕", width=16, height=16, fg_color="transparent",
                            text_color="white", hover_color="#aa3333", font=("Arial", 9),
                            command=_element_entfernen,
                        ).pack(side="left", padx=(0, 4), pady=2)

                    verbleibend = [e for e in SEM_FILTER_ELEMENTE if e not in elemente_liste]
                    hinzufuegen_dropdown = ctk.CTkOptionMenu(
                        chips_zeile, values=verbleibend or ["–"], width=90,
                        # WICHTIG: CTkOptionMenu erlaubt hier (anders als
                        # CTkButton) KEIN fg_color="transparent" - das
                        # crasht mit "ValueError: transparency is not
                        # allowed for this attribute". Stattdessen ein
                        # neutraler Grauton passend zur uebrigen Filter-UI.
                        fg_color=("gray80", "gray25"), button_color=MUL_TURKIS,
                    )
                    hinzufuegen_dropdown.set("+ Element")

                    def _element_hinzufuegen(gewaehlt, chips_zeile=chips_zeile, elemente_liste=elemente_liste):
                        if gewaehlt and gewaehlt != "–" and gewaehlt not in elemente_liste:
                            elemente_liste.append(gewaehlt)
                            _chips_neu_zeichnen(chips_zeile, elemente_liste)
                            uebernehmen()

                    hinzufuegen_dropdown.configure(command=_element_hinzufuegen)
                    hinzufuegen_dropdown.pack(side="left", padx=(4, 0))

                _chips_neu_zeichnen()

                filter_zeilen_widgets.append({
                    "daten": eintrag, "aktiv_var": aktiv_var,
                    "elemente_liste": elemente_liste, "operator_dropdown": operator_dropdown,
                    "wert_entry": wert_entry,
                })

        def _filter_hinzufuegen():
            _uebernimm_zeilen_in_zustand()
            zustand["filter"].append({"elemente": ["O"], "operator": ">", "wert": 5.0, "aktiv": True})
            baue_filter_zeilen()

        ctk.CTkButton(
            rechte_spalte, text="+ Filter hinzufügen", fg_color="transparent",
            border_width=1, border_color=MUL_TURKIS, command=_filter_hinzufuegen,
        ).pack(fill="x", padx=10, pady=(0, 15))

        ctk.CTkFrame(rechte_spalte, height=2, fg_color=("gray75", "gray30")).pack(fill="x", padx=10, pady=(0, 10))

        normieren_var = ctk.BooleanVar(value=zustand.get("normieren", True))
        ctk.CTkCheckBox(
            rechte_spalte, text="Elementanteile normieren (Summe = 100 %, vergleichbar)",
            variable=normieren_var,
        ).pack(fill="x", padx=10, pady=(0, 8))

        cluster_var = ctk.BooleanVar(value=zustand.get("cluster_anzeigen", True))
        ctk.CTkCheckBox(
            rechte_spalte, text="Umrisse via Clusteralgorithmus anzeigen",
            variable=cluster_var,
        ).pack(fill="x", padx=10, pady=(0, 4))

        # Kleine Cluster ausblenden: untergeordnete Checkbox unter Cluster-Umrisse
        kleine_cluster_var = ctk.BooleanVar(value=zustand.get("kleine_cluster_ausblenden", False))
        kleine_cluster_checkbox = ctk.CTkCheckBox(
            rechte_spalte, text="kleine Cluster ausblenden (< 3 px, isoliert)",
            variable=kleine_cluster_var,
        )
        kleine_cluster_checkbox.pack(fill="x", padx=30, pady=(0, 15))
        # Hinweis: padx=30 rueckt diese Checkbox 20 Pixel weiter nach rechts ein,
        # um sie visuell als Unteroption der "Cluster-Umrisse"-Checkbox zu kennzeichnen

        def uebernehmen():
            _uebernimm_zeilen_in_zustand()
            zustand["normieren"] = bool(normieren_var.get())
            zustand["cluster_anzeigen"] = bool(cluster_var.get())
            zustand["kleine_cluster_ausblenden"] = bool(kleine_cluster_var.get())
            pfad = ausgewaehlter_pfad["wert"]
            if pfad:
                self.speichere_rohdaten_filter_einstellungen_fuer_versuch(
                    projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, pfad), zustand
                )
            zeichne()

        def fuer_alle_uebernehmen():
            if not messagebox.askyesno(
                "Für alle übernehmen",
                "Die aktuelle Filter-Sektion wird für ALLE "
                f"{len(versuche)} Versuche dieser Methode übernommen und "
                "überschreibt dabei auch bereits individuell abweichend "
                "eingestellte Werte einzelner Versuche.\n\nFortfahren?",
            ):
                return
            _uebernimm_zeilen_in_zustand()
            zustand["normieren"] = bool(normieren_var.get())
            zustand["cluster_anzeigen"] = bool(cluster_var.get())
            zustand["kleine_cluster_ausblenden"] = bool(kleine_cluster_var.get())
            alle_schluessel = [
                self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad)
                for _staub, _eintrag, voller_pfad in versuche
            ]
            self.speichere_rohdaten_filter_einstellungen_fuer_alle(projekt, methode, alle_schluessel, zustand)
            zeichne()

        def _aktualisiere_filter_panel_impl():
            normieren_var.set(zustand.get("normieren", True))
            cluster_var.set(zustand.get("cluster_anzeigen", True))
            kleine_cluster_var.set(zustand.get("kleine_cluster_ausblenden", False))
            baue_filter_zeilen()

        aktualisiere_filter_panel["fn"] = _aktualisiere_filter_panel_impl

        button_leiste_filter = ctk.CTkFrame(rechte_spalte, fg_color="transparent")
        button_leiste_filter.pack(fill="x", padx=10, pady=(0, 15))
        ctk.CTkButton(
            button_leiste_filter, text="Übernehmen", fg_color=MUL_TURKIS, command=uebernehmen
        ).pack(fill="x")
        ctk.CTkButton(
            button_leiste_filter,
            text="Für alle übernehmen",
            fg_color="transparent",
            border_width=1,
            border_color=MUL_TURKIS,
            command=fuer_alle_uebernehmen,
        ).pack(fill="x", pady=(8, 0))

        baue_filter_zeilen()

        # --- Ersten Versuch automatisch auswaehlen ---
        waehle_versuch(versuche[0][2])

    # ------------------------------------------------------------------
    # TAB: ERGEBNISSE (Höhenverlauf + Form pro Versuch, aus processed_data)
    # ------------------------------------------------------------------
    def baue_ergebnisse_tab(self, parent, projekt, methode):
        """
        Zeigt pro (bereits verarbeitetem) Versuch den Höhenverlauf
        (sample_height_px über Temperature) und den Schmelzverlauf (alle
        Konturen übereinander, nach Temperatur eingefärbt) aus der
        zugehörigen <versuch>_results.parquet-Datei.

        Layout: linke Seitenleiste mit Versuchsauswahl (Anzeige als
        "Material – Kommentar" statt nur der M...-Nummer) + Settings-Button
        zum Umbenennen der Achsenbeschriftungen; rechts die Diagramme.

        Figure/Canvas werden nur EINMAL erzeugt - beim Versuchswechsel wird
        nur der Achseninhalt neu gezeichnet (ax.clear() + canvas.draw()),
        nicht das ganze Widget neu aufgebaut. Das verhindert den sichtbaren
        "Sprung"/das Ruckeln beim Umschalten zwischen Versuchen.
        """
        if methode == "TGA":
            self.baue_ergebnisse_tab_tga(parent, projekt, methode)
            return

        if methode == "SEM":
            self.baue_ergebnisse_tab_sem(parent, projekt, methode)
            return

        if methode != "EMI":
            ctk.CTkLabel(
                parent, text="Ergebnisdarstellung ist aktuell nur für EMI und TGA verfügbar."
            ).pack(pady=20)
            return

        versuche = self.liste_versuche(projekt, methode)
        verarbeitete_versuche = [
            (staub, eintrag, voller_pfad)
            for staub, eintrag, voller_pfad in versuche
            if self.ist_versuch_verarbeitet(os.path.dirname(voller_pfad), eintrag, methode)
        ]

        if not verarbeitete_versuche:
            ctk.CTkLabel(
                parent, text="Noch keine verarbeiteten Versuche (siehe Rohdaten-Tab -> Berechnen)."
            ).pack(pady=20)
            return

        try:
            import matplotlib as mpl
            import matplotlib.pyplot as plt
            import matplotlib.cm as cm
            import matplotlib.colors as mcolors
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            import numpy as np
        except ImportError:
            ctk.CTkLabel(
                parent,
                text="matplotlib/numpy ist nicht installiert - 'pip install matplotlib numpy' im GUI-Environment nötig.",
                wraplength=560,
            ).pack(pady=20)
            return

        # Zustand, der über Versuchswechsel & Settings-Dialog hinweg erhalten
        # bleibt UND dauerhaft auf der Platte gespeichert wird (siehe
        # lade_/speichere_diagramm_einstellungen), also auch nach dem
        # Schließen und Neustarten des Programms erhalten bleibt.
        # x_min/x_max/y_min/y_max = None bedeutet "automatisch" (an Daten anpassen).
        # farbskala_min/farbskala_max = None bedeutet "automatisch" (folgt
        # x_min/x_max des Höhenverlaufs links, siehe unten).
        zustand_standard = {
            "versuch_name": None,
            # Diagramme nebeneinander (horizontal) oder untereinander (vertical).
            "diagramm_layout": "horizontal",
            "title_hoehe": "Höhenverlauf",
            "label_x": "Temperature [°C]",
            "label_y": "Sample Height [% of initial]",
            "x_min": 600,
            "x_max": None,
            "y_min": None,
            "y_max": None,
            "title_form": "Schmelzverlauf (alle Konturen, nach Temperatur eingefärbt)",
            "label_x_form": "x [px]",
            "label_y_form": "y [px]",
            "y_min_form": None,
            "y_max_form": None,
            "farbskala_min": None,
            "farbskala_max": None,
            # --- Darstellung (Linie, Schrift, Hintergrund) ---
            "linienfarbe": MUL_TURKIS,
            "linienbreite": 2,
            "schriftgroesse_titel": 13,
            "schriftgroesse_achsen": 11,
            "hintergrund_figure": "#ffffff",
        }
        zustand = self.lade_diagramm_einstellungen(projekt, methode, zustand_standard)

        # Fallback-Grenzen der Farbskala, falls weder x_min/x_max links noch
        # eine explizite Farbskala-Einstellung vorhanden sind.
        FARBSKALA_MIN_STANDARD = 200
        FARBSKALA_MAX_STANDARD = 1600

        def farbskala_grenzen():
            """
            Effektive Farbskala-Grenzen: explizite Einstellung hat Vorrang,
            sonst folgt die Skala automatisch dem X-Achsen-Bereich links
            (Höhenverlauf), sonst der Standard-Fallback.
            """
            vmin = zustand["farbskala_min"]
            if vmin is None:
                vmin = zustand["x_min"] if zustand["x_min"] is not None else FARBSKALA_MIN_STANDARD
            vmax = zustand["farbskala_max"]
            if vmax is None:
                vmax = zustand["x_max"] if zustand["x_max"] is not None else FARBSKALA_MAX_STANDARD
            return vmin, vmax

        # --- Anzeige-Text (Material – Kommentar) <-> tatsächlicher Versuchsname ---
        anzeige_zu_name = {}
        anzeige_werte = []
        for _s, eintrag, _p in verarbeitete_versuche:
            versuch_name = os.path.splitext(eintrag)[0]
            parameter = self.hole_emi_parameter_fuer_versuch(versuch_name)
            if parameter and (parameter.get("material") or parameter.get("kommentar")):
                material = parameter.get("material") or "-"
                kommentar = parameter.get("kommentar") or "-"
                anzeige = f"{material} – {kommentar}"
            else:
                anzeige = versuch_name
            if anzeige in anzeige_zu_name:  # Eindeutigkeit sicherstellen
                anzeige = f"{anzeige} [{versuch_name}]"
            anzeige_zu_name[anzeige] = versuch_name
            anzeige_werte.append(anzeige)

        # --- Layout: Seitenleiste links, Diagramme rechts ---
        haupt_layout = ctk.CTkFrame(parent, fg_color="transparent")
        haupt_layout.pack(fill="both", expand=True, padx=10, pady=10)

        seitenleiste = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=170)
        seitenleiste.pack(side="left", fill="y", padx=(0, 15))
        seitenleiste.pack_propagate(False)

        ctk.CTkLabel(seitenleiste, text="Versuch:", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        plot_frame = ctk.CTkFrame(haupt_layout, fg_color="transparent")
        plot_frame.pack(side="left", fill="both", expand=True)

        # Kleine Button-Leiste OBERHALB der Diagramme (nicht über die
        # Zeichenfläche gelegt - MUSS vor dem Canvas gepackt werden, damit
        # sie garantiert sichtbar oberhalb bleibt statt vom Canvas
        # überdeckt/verdrängt zu werden). Zwei gleich breite Spalten, damit
        # der linke Button über dem linken und der rechte über dem rechten
        # Diagramm landet.
        button_leiste = ctk.CTkFrame(plot_frame, fg_color="transparent")
        button_leiste.pack(side="top", fill="x")
        button_leiste.grid_columnconfigure(0, weight=1)
        button_leiste.grid_columnconfigure(1, weight=1)

        # Figure/Canvas EINMAL erzeugen (siehe Docstring oben). Größe folgt
        # dem tatsächlich verfügbaren Platz im Diagramm-Bereich statt einer
        # fixen Größe - so bekommen die beiden Diagramme auf kleinen
        # Bildschirmen mehr Luft zueinander und auf großen mehr Fläche.
        figsize = self._dynamische_figsize(plot_frame, 1, 2, mindest_breite_px=700, mindest_hoehe_px=420)
        fig, (ax_hoehe, ax_form) = plt.subplots(1, 2, figsize=figsize)
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        cmap = mpl.colormaps["jet"]
        vmin_start, vmax_start = farbskala_grenzen()
        sm = cm.ScalarMappable(cmap=cmap, norm=mcolors.Normalize(vmin=vmin_start, vmax=vmax_start))
        sm.set_array([])
        farbskala = fig.colorbar(sm, ax=ax_form, shrink=0.85)

        def _eintrag_info_fuer(versuch_name):
            return next(
                (s, e, p) for s, e, p in verarbeitete_versuche
                if os.path.splitext(e)[0] == versuch_name
            )

        def _versuch_schluessel(versuch_name):
            """Eindeutiger Schluessel dieses Versuchs, unter dem seine
            individuelle Diagramm-Darstellung gespeichert wird."""
            if not versuch_name:
                return None
            try:
                _s, _e, voller_pfad = _eintrag_info_fuer(versuch_name)
            except StopIteration:
                return versuch_name
            return self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad)

        def _speichern():
            """Speichert die aktuelle Darstellung NUR fuer den gerade
            angezeigten Versuch - Aenderungen wirken sich damit nicht mehr
            automatisch auf andere Versuche aus (siehe Button 'Fuer alle
            Versuche uebernehmen', falls vorhanden, fuer den Fall, dass
            wirklich ALLE Versuche auf einmal aktualisiert werden sollen)."""
            self.speichere_diagramm_einstellungen_fuer_versuch(
                projekt, methode, _versuch_schluessel(zustand.get("versuch_name")), zustand,
            )

        def zeichne():
            versuch_name = zustand["versuch_name"]
            if not versuch_name:
                return
            eintrag_info = _eintrag_info_fuer(versuch_name)
            # Für den Download-Button gemerkt: der tatsächliche raw_data-
            # Ordner dieses Versuchs, um daraus den Ausgabe-Ordner zu
            # spiegeln (siehe diagramm_ordner_fuer).
            zustand["_aktueller_raw_data_ordner"] = os.path.dirname(eintrag_info[2])
            # Farbskala VOR dem Zeichnen aktualisieren, falls sich x_min/x_max
            # links oder eine explizite Farbskala-Einstellung geändert haben.
            vmin, vmax = farbskala_grenzen()
            sm.set_norm(mcolors.Normalize(vmin=vmin, vmax=vmax))
            farbskala.update_normal(sm)
            self.zeichne_ergebnis_plot(
                eintrag_info, fig, ax_hoehe, ax_form, canvas, sm, farbskala, zustand, np, mcolors
            )

        def setze_layout(layout, speichern=True):
            """Ersetzt Figure und Canvas vollständig (inkl. Farbskala), damit
            keine alten Achsen überlappen. Größe richtet sich weiterhin nach
            dem tatsächlich verfügbaren Platz."""
            nonlocal fig, canvas, ax_hoehe, ax_form, farbskala
            zustand["diagramm_layout"] = layout
            canvas.get_tk_widget().destroy()
            plt.close(fig)
            if layout == "vertical":
                # Bei zwei übereinanderliegenden Plots darf die Figure nicht
                # die komplette (oft sehr breite) Fensterbreite einnehmen -
                # Breite wird daher an der Höhe orientiert.
                vfigsize = self._dynamische_figsize(
                    plot_frame, 1, 2, mindest_breite_px=520, mindest_hoehe_px=680, breite_anteil=0.75,
                )
                fig, (ax_hoehe, ax_form) = plt.subplots(2, 1, figsize=vfigsize)
            else:
                figsize = self._dynamische_figsize(plot_frame, 1, 2, mindest_breite_px=700, mindest_hoehe_px=420)
                fig, (ax_hoehe, ax_form) = plt.subplots(1, 2, figsize=figsize)
            canvas = FigureCanvasTkAgg(fig, master=plot_frame)
            canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
            farbskala = fig.colorbar(sm, ax=ax_form, shrink=0.85)
            zeichne()
            if speichern:
                _speichern()

        def speichere_diagrammausschnitt(ax, dateiname_teil):
            """
            Speichert NUR den Bereich von ax (also entweder Höhenverlauf oder
            Schmelzverlauf, nicht beide zusammen) als PNG, gespiegelt vom
            raw_data-Ordner des aktuellen Versuchs nach outputs/diagramm
            (siehe diagramm_ordner_fuer - wird bei Bedarf angelegt).
            """
            versuch_name = zustand["versuch_name"]
            raw_data_ordner = zustand.get("_aktueller_raw_data_ordner")
            if not versuch_name or not raw_data_ordner:
                return
            try:
                ziel_ordner = self.diagramm_ordner_fuer(raw_data_ordner)
                zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
                dateiname = f"{self._sanitiere_versuchsnamen(versuch_name)}_{dateiname_teil}_{zeitstempel}.png"
                ziel_pfad = os.path.join(ziel_ordner, dateiname)
                # Nur die Bounding-Box der jeweiligen Achse exportieren (inkl.
                # Titel/Beschriftung), nicht die ganze Figure mit beiden Plots.
                canvas.draw()
                bbox = ax.get_tightbbox(canvas.get_renderer()).transformed(fig.dpi_scale_trans.inverted())
                fig.savefig(ziel_pfad, dpi=200, bbox_inches=bbox)
                self._status(f"Diagramm gespeichert: {ziel_pfad}", "#00ff88")
            except Exception as e:
                messagebox.showerror("Download-Fehler", f"Konnte Diagramm nicht speichern:\n{e}")

        def lade_hoehenverlauf_herunter():
            speichere_diagrammausschnitt(ax_hoehe, "hoehenverlauf")

        def lade_schmelzverlauf_herunter():
            speichere_diagrammausschnitt(ax_form, "schmelzverlauf")

        # Zwei kleine runde Download-Buttons in der Leiste über den
        # Diagrammen: linker Button über dem linken (Höhenverlauf), rechter
        # Button über dem rechten Diagramm (Schmelzverlauf) - "↓" statt
        # Emoji "⬇", da Emoji je nach Schriftart nur als Fragezeichen
        # dargestellt wird.
        download_button_links = ctk.CTkButton(
            button_leiste, text="↓", width=30, height=30, corner_radius=15,
            fg_color=MUL_TURKIS, hover_color=MUL_DUNKEL, font=("Arial", 14, "bold"),
            command=lade_hoehenverlauf_herunter,
        )
        download_button_links.grid(row=0, column=0, sticky="e", padx=(0, 6), pady=(0, 4))

        download_button_rechts = ctk.CTkButton(
            button_leiste, text="↓", width=30, height=30, corner_radius=15,
            fg_color=MUL_TURKIS, hover_color=MUL_DUNKEL, font=("Arial", 14, "bold"),
            command=lade_schmelzverlauf_herunter,
        )
        download_button_rechts.grid(row=0, column=1, sticky="e", padx=(6, 0), pady=(0, 4))

        def zeige_versuch(anzeige):
            versuch_name = anzeige_zu_name.get(anzeige, anzeige)
            layout_backup = zustand.get("diagramm_layout", "horizontal")
            neuer_zustand = self.lade_diagramm_einstellungen_fuer_versuch(
                projekt, methode, _versuch_schluessel(versuch_name), zustand_standard,
            )
            neuer_zustand["versuch_name"] = versuch_name
            neuer_zustand["diagramm_layout"] = layout_backup
            zustand.clear()
            zustand.update(neuer_zustand)
            zeichne()

        def als_zahl_oder_none(text):
            text = text.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None

        def live_kopplung(entry, label_widget, praefix, suffix=""):
            """
            Aktualisiert label_widget bei jedem Tastendruck in entry live mit
            dessen aktuellem Text (z.B. Überschrift "Rechtes Diagramm (<Titel>)"
            oder "Y-Achse (<Beschriftung>) von/bis:" - folgt sofort dem, was
            man in das jeweilige Beschriftungsfeld eintippt).
            """
            def aktualisieren(_event=None):
                wert = entry.get().strip() or "..."
                label_widget.configure(text=f"{praefix}{wert}{suffix}")
            entry.bind("<KeyRelease>", aktualisieren)

        def oeffne_darstellung():
            """Separater Dialog für die Anordnung der beiden Diagramme."""
            dialog = ctk.CTkToplevel(self)
            dialog.title("Darstellung")
            dialog.geometry("360x180")
            dialog.minsize(320, 160)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()

            inhalt = ctk.CTkFrame(dialog, fg_color="transparent")
            inhalt.pack(fill="both", expand=True, padx=15, pady=15)
            ctk.CTkLabel(inhalt, text="Anordnung der Diagramme:", anchor="w").pack(fill="x", pady=(0, 8))
            layout_labels = {
                "Horizontal (nebeneinander)": "horizontal",
                "Vertikal (übereinander)": "vertical",
            }
            layout_werte = {wert: label for label, wert in layout_labels.items()}

            def _layout_gewaehlt(_=None):
                setze_layout(layout_labels.get(layout_dropdown.get(), "horizontal"))

            layout_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(layout_labels), fg_color=MUL_TURKIS,
                command=_layout_gewaehlt,
            )
            layout_dropdown.set(layout_werte.get(
                zustand.get("diagramm_layout", "horizontal"), "Horizontal (nebeneinander)"
            ))
            layout_dropdown.pack(fill="x")
            dialog.bind("<Return>", _layout_gewaehlt)

        def oeffne_settings():
            """
            # Plot Settings enthält bewusst nur die Daten-Auswahl. Alle
            # Stil- und Layout-Optionen liegen im separaten Darstellung-Button.
            dialog = ctk.CTkToplevel(self)
            dialog.title("Plot Settings")
            dialog.geometry("420x420")
            dialog.minsize(380, 320)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()

            tabs_kurz = ctk.CTkTabview(dialog, fg_color="transparent")
            tabs_kurz.pack(fill="both", expand=True, padx=5, pady=(10, 0))
            tab1_kurz = tabs_kurz.add("Diagramm 1")
            tab2_kurz = tabs_kurz.add("Diagramm 2")

            def _diagramm_felder(parent_tab, titel_key, x_key, y_key):
                inhalt_kurz = ctk.CTkFrame(parent_tab, fg_color="transparent")
                inhalt_kurz.pack(fill="both", expand=True, padx=10, pady=10)
                ctk.CTkLabel(inhalt_kurz, text="Titel:", anchor="w").pack(fill="x")
                titel = ctk.CTkEntry(inhalt_kurz)
                titel.insert(0, zustand[titel_key])
                titel.pack(fill="x", pady=(2, 12))
                ctk.CTkLabel(inhalt_kurz, text="x-value:", anchor="w").pack(fill="x")
                x_value = ctk.CTkOptionMenu(inhalt_kurz, values=TGA_ERGEBNIS_SPALTEN_LABELS, fg_color=MUL_TURKIS)
                x_value.set(TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand[x_key], TGA_ERGEBNIS_SPALTEN_LABELS[0]))
                x_value.pack(fill="x", pady=(2, 12))
                ctk.CTkLabel(inhalt_kurz, text="y-value:", anchor="w").pack(fill="x")
                y_value = ctk.CTkOptionMenu(inhalt_kurz, values=TGA_ERGEBNIS_SPALTEN_LABELS, fg_color=MUL_TURKIS)
                y_value.set(TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand[y_key], TGA_ERGEBNIS_SPALTEN_LABELS[0]))
                y_value.pack(fill="x", pady=(2, 12))
                return titel, x_value, y_value

            titel1, x1, y1 = _diagramm_felder(tab1_kurz, "title_masse", "diagramm1_x_spalte", "diagramm1_y_spalte")
            titel2, x2, y2 = _diagramm_felder(tab2_kurz, "title_kinetik", "diagramm2_x_spalte", "diagramm2_y_spalte")

            def _uebernehmen_kurz():
                zustand["title_masse"] = titel1.get().strip() or zustand["title_masse"]
                zustand["title_kinetik"] = titel2.get().strip() or zustand["title_kinetik"]
                zustand["diagramm1_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[x1.get()]
                zustand["diagramm1_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[y1.get()]
                zustand["diagramm2_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[x2.get()]
                zustand["diagramm2_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[y2.get()]
                zeichne()
                self.speichere_diagramm_einstellungen(projekt, methode, zustand)

            def _live_plot_settings(_=None):
                # x/y-Auswahl und Titel ohne zusätzlichen Klick übernehmen.
                _uebernehmen_kurz()

            for auswahl in (x1, y1, x2, y2):
                auswahl.configure(command=_live_plot_settings)
            for titel_feld in (titel1, titel2):
                titel_feld.bind("<KeyRelease>", _live_plot_settings)
                titel_feld.bind("<FocusOut>", _live_plot_settings)

            ctk.CTkButton(dialog, text="Übernehmen", fg_color=MUL_TURKIS, command=_uebernehmen_kurz).pack(pady=10)
            dialog.bind("<Return>", lambda _event: _uebernehmen_kurz())
            """

            dialog = ctk.CTkToplevel(self)
            dialog.title("Diagramm-Einstellungen")
            dialog.geometry("480x720")
            dialog.minsize(420, 420)
            dialog.resizable(True, True)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()

            def uebernehmen():
                zustand["title_hoehe"] = eingabe_titel_hoehe.get().strip() or zustand["title_hoehe"]
                zustand["label_y"] = eingabe_y_label.get().strip() or zustand["label_y"]
                zustand["label_x"] = eingabe_x_label.get().strip() or zustand["label_x"]
                zustand["x_min"] = als_zahl_oder_none(eingabe_x_min.get())
                zustand["x_max"] = als_zahl_oder_none(eingabe_x_max.get())
                zustand["y_min"] = als_zahl_oder_none(eingabe_y_min.get())
                zustand["y_max"] = als_zahl_oder_none(eingabe_y_max.get())

                zustand["title_form"] = eingabe_titel_form.get().strip() or zustand["title_form"]
                zustand["label_x_form"] = eingabe_x_label_form.get().strip() or zustand["label_x_form"]
                zustand["label_y_form"] = eingabe_y_label_form.get().strip() or zustand["label_y_form"]
                zustand["y_min_form"] = als_zahl_oder_none(eingabe_y_min_form.get())
                zustand["y_max_form"] = als_zahl_oder_none(eingabe_y_max_form.get())
                zustand["farbskala_min"] = als_zahl_oder_none(eingabe_farbskala_min.get())
                zustand["farbskala_max"] = als_zahl_oder_none(eingabe_farbskala_max.get())

                neue_linienfarbe = eingabe_linienfarbe.get().strip()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}", neue_linienfarbe):
                    zustand["linienfarbe"] = neue_linienfarbe
                zustand["linienbreite"] = als_zahl_oder_none(eingabe_linienbreite.get()) or zustand["linienbreite"]
                zustand["schriftgroesse_titel"] = als_zahl_oder_none(eingabe_schrift_titel.get()) or zustand["schriftgroesse_titel"]
                zustand["schriftgroesse_achsen"] = als_zahl_oder_none(eingabe_schrift_achsen.get()) or zustand["schriftgroesse_achsen"]
                neuer_hintergrund = eingabe_hintergrund.get().strip()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}", neuer_hintergrund):
                    zustand["hintergrund_figure"] = neuer_hintergrund

                zeichne()
                canvas.draw_idle()
                _speichern()
                self._status("Darstellung automatisch gespeichert.", "#00ff88")

            # --- Live anwenden: Werte gelten sofort im Diagramm, OHNE dass
            # unten auf "Übernehmen" geklickt werden muss. Enter bestätigt
            # sofort (ohne Debounce), Tippen/Fokuswechsel mit kurzer
            # Verzögerung, damit nicht bei jedem einzelnen Tastendruck neu
            # gezeichnet wird.
            auto_update_auftrag = {"id": None}

            def _sofort_anwenden(*_args):
                if auto_update_auftrag["id"] is not None:
                    try:
                        dialog.after_cancel(auto_update_auftrag["id"])
                    except Exception:
                        pass
                auto_update_auftrag["id"] = dialog.after(30, uebernehmen)

            def _enter_anwenden(_event=None):
                uebernehmen()
                return "break"

            # WICHTIG: Button-Leiste ZUERST mit side="bottom" packen, damit ihr
            # Platz fest reserviert ist - sonst kann zu viel Inhalt darüber
            # (wie beim "Berechnen"-Button vorher) den Button aus dem
            # sichtbaren Bereich drücken und er wirkt "kaputt"/unklickbar.
            button_zeile = ctk.CTkFrame(dialog, fg_color="transparent")
            button_zeile.pack(side="bottom", fill="x", pady=10)
            ctk.CTkButton(
                button_zeile, text="Übernehmen & Vorschau aktualisieren", fg_color=MUL_TURKIS, command=uebernehmen,
            ).pack()
            dialog.bind("<Return>", _enter_anwenden)

            inhalt = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
            inhalt.pack(fill="both", expand=True, padx=5, pady=(10, 0))

            def _hex_feld(parent, label_text, wert, breite=110, platzhalter="#ffffff"):
                """Textfeld + 🎨-Knopf: öffnet den System-Farbwähler; sobald
                dort auf 'OK' gedrückt wird, wird die Farbe SOFORT im
                Diagramm übernommen (kein zusätzlicher Klick auf
                'Übernehmen' nötig)."""
                zeile = ctk.CTkFrame(parent, fg_color="transparent")
                zeile.pack(fill="x", padx=10, pady=(0, 10))
                ctk.CTkLabel(zeile, text=label_text, width=breite, anchor="w").pack(side="left")
                eingabe = ctk.CTkEntry(zeile, placeholder_text=platzhalter)
                eingabe.insert(0, "" if wert is None else str(wert))
                eingabe.pack(side="left", padx=(5, 5), fill="x", expand=True)

                def waehle_farbe():
                    start = eingabe.get().strip() or platzhalter
                    eltern_fenster = zeile.winfo_toplevel()
                    war_topmost = False
                    try:
                        war_topmost = bool(eltern_fenster.attributes("-topmost"))
                        if war_topmost:
                            eltern_fenster.attributes("-topmost", False)
                    except Exception:
                        pass
                    try:
                        _rgb, hex_code = colorchooser.askcolor(
                            color=start, title="Farbe wählen", parent=eltern_fenster,
                        )
                    except Exception:
                        hex_code = None
                    finally:
                        if war_topmost:
                            try:
                                eltern_fenster.attributes("-topmost", True)
                                eltern_fenster.lift()
                            except Exception:
                                pass
                    if hex_code:
                        eingabe.delete(0, "end")
                        eingabe.insert(0, hex_code)
                        uebernehmen()

                ctk.CTkButton(
                    zeile, text="🎨", width=32, fg_color=MUL_DUNKEL, hover_color=MUL_TURKIS, command=waehle_farbe,
                ).pack(side="left")
                return eingabe

            # --- Linkes Diagramm (Höhenverlauf) ---
            header_links = ctk.CTkLabel(
                inhalt, text=f"Linkes Diagramm ({zustand['title_hoehe']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_links.pack(fill="x", padx=10, pady=(5, 5))

            ctk.CTkLabel(inhalt, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_hoehe = ctk.CTkEntry(inhalt)
            eingabe_titel_hoehe.insert(0, zustand["title_hoehe"])
            eingabe_titel_hoehe.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_titel_hoehe, header_links, "Linkes Diagramm (", ")")

            ctk.CTkLabel(inhalt, text="Y-Achsen-Beschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label = ctk.CTkEntry(inhalt)
            eingabe_y_label.insert(0, zustand["label_y"])
            eingabe_y_label.pack(fill="x", padx=10, pady=(2, 10))

            ctk.CTkLabel(
                inhalt, text="X-Achsen-Beschriftung (gilt auch für Farbskala rechts):", anchor="w",
            ).pack(fill="x", padx=10)
            eingabe_x_label = ctk.CTkEntry(inhalt)
            eingabe_x_label.insert(0, zustand["label_x"])
            eingabe_x_label.pack(fill="x", padx=10, pady=(2, 10))

            bereich_zeile_x = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_x.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(bereich_zeile_x, text="X-Achse von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_x_min = ctk.CTkEntry(bereich_zeile_x, placeholder_text="z.B. 600")
            eingabe_x_min.insert(0, "" if zustand["x_min"] is None else str(zustand["x_min"]))
            eingabe_x_min.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_x_max = ctk.CTkEntry(bereich_zeile_x, placeholder_text="auto")
            eingabe_x_max.insert(0, "" if zustand["x_max"] is None else str(zustand["x_max"]))
            eingabe_x_max.pack(side="left", padx=(5, 0), fill="x", expand=True)

            bereich_zeile_y = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_y.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(bereich_zeile_y, text="Y-Achse von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_y_min = ctk.CTkEntry(bereich_zeile_y, placeholder_text="auto")
            eingabe_y_min.insert(0, "" if zustand["y_min"] is None else str(zustand["y_min"]))
            eingabe_y_min.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_y_max = ctk.CTkEntry(bereich_zeile_y, placeholder_text="auto")
            eingabe_y_max.insert(0, "" if zustand["y_max"] is None else str(zustand["y_max"]))
            eingabe_y_max.pack(side="left", padx=(5, 0), fill="x", expand=True)

            # --- Trennlinie ---
            ctk.CTkFrame(inhalt, height=2, fg_color=("gray75", "gray30")).pack(fill="x", padx=10, pady=(5, 15))

            # --- Rechtes Diagramm (Schmelzverlauf) ---
            header_rechts = ctk.CTkLabel(
                inhalt, text=f"Rechtes Diagramm ({zustand['title_form']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_rechts.pack(fill="x", padx=10, pady=(0, 5))

            ctk.CTkLabel(inhalt, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_form = ctk.CTkEntry(inhalt)
            eingabe_titel_form.insert(0, zustand["title_form"])
            eingabe_titel_form.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_titel_form, header_rechts, "Rechtes Diagramm (", ")")

            ctk.CTkLabel(inhalt, text="X-Achsen-Beschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_x_label_form = ctk.CTkEntry(inhalt)
            eingabe_x_label_form.insert(0, zustand["label_x_form"])
            eingabe_x_label_form.pack(fill="x", padx=10, pady=(2, 10))

            ctk.CTkLabel(inhalt, text="Y-Achsen-Beschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label_form = ctk.CTkEntry(inhalt)
            eingabe_y_label_form.insert(0, zustand["label_y_form"])
            eingabe_y_label_form.pack(fill="x", padx=10, pady=(2, 10))

            bereich_zeile_y_form = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_y_form.pack(fill="x", padx=10, pady=(0, 10))
            label_bereich_y_form = ctk.CTkLabel(
                bereich_zeile_y_form, text=f"Y-Achse ({zustand['label_y_form']}) von/bis:", width=110, anchor="w",
            )
            label_bereich_y_form.pack(side="left")
            eingabe_y_min_form = ctk.CTkEntry(bereich_zeile_y_form, placeholder_text="auto")
            eingabe_y_min_form.insert(0, "" if zustand["y_min_form"] is None else str(zustand["y_min_form"]))
            eingabe_y_min_form.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_y_max_form = ctk.CTkEntry(bereich_zeile_y_form, placeholder_text="auto")
            eingabe_y_max_form.insert(0, "" if zustand["y_max_form"] is None else str(zustand["y_max_form"]))
            eingabe_y_max_form.pack(side="left", padx=(5, 0), fill="x", expand=True)
            live_kopplung(eingabe_y_label_form, label_bereich_y_form, "Y-Achse (", ") von/bis:")

            bereich_zeile_farbskala = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_farbskala.pack(fill="x", padx=10, pady=(0, 5))
            ctk.CTkLabel(bereich_zeile_farbskala, text="Farbskala von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_farbskala_min = ctk.CTkEntry(bereich_zeile_farbskala, placeholder_text="auto (= X-Achse von)")
            eingabe_farbskala_min.insert(0, "" if zustand["farbskala_min"] is None else str(zustand["farbskala_min"]))
            eingabe_farbskala_min.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_farbskala_max = ctk.CTkEntry(bereich_zeile_farbskala, placeholder_text="auto (= X-Achse bis)")
            eingabe_farbskala_max.insert(0, "" if zustand["farbskala_max"] is None else str(zustand["farbskala_max"]))
            eingabe_farbskala_max.pack(side="left", padx=(5, 0), fill="x", expand=True)

            ctk.CTkLabel(
                inhalt,
                text=(
                    "Der Schmelzverlauf rechts zeigt automatisch nur die Konturen im\n"
                    "gewählten X-Achsen-Bereich links. Die Farbskala folgt automatisch\n"
                    "demselben Bereich (X-Achse von/bis), kann hier aber auch abweichend\n"
                    "fest eingestellt werden - leer lassen für \"automatisch\"."
                ),
                text_color=("gray40", "gray70"), font=("Arial", 10), justify="left",
            ).pack(fill="x", padx=10, pady=(0, 10))

            # --- Trennlinie ---
            ctk.CTkFrame(inhalt, height=2, fg_color=("gray75", "gray30")).pack(fill="x", padx=10, pady=(5, 15))

            # --- Darstellung: Farbe, Linienbreite, Schrift, Hintergrund ---
            ctk.CTkLabel(inhalt, text="Darstellung", anchor="w", font=("Arial", 13, "bold")).pack(
                fill="x", padx=10, pady=(0, 5)
            )
            eingabe_linienfarbe = _hex_feld(inhalt, "Linienfarbe:", zustand.get("linienfarbe", MUL_TURKIS))

            zeile_linienbreite = ctk.CTkFrame(inhalt, fg_color="transparent")
            zeile_linienbreite.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(zeile_linienbreite, text="Linienbreite:", width=110, anchor="w").pack(side="left")
            eingabe_linienbreite = ctk.CTkEntry(zeile_linienbreite)
            eingabe_linienbreite.insert(0, str(zustand.get("linienbreite", 2)))
            eingabe_linienbreite.pack(side="left", padx=(5, 0), fill="x", expand=True)

            zeile_schrift_titel = ctk.CTkFrame(inhalt, fg_color="transparent")
            zeile_schrift_titel.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(zeile_schrift_titel, text="Schriftgröße Titel:", width=110, anchor="w").pack(side="left")
            eingabe_schrift_titel = ctk.CTkEntry(zeile_schrift_titel)
            eingabe_schrift_titel.insert(0, str(zustand.get("schriftgroesse_titel", 13)))
            eingabe_schrift_titel.pack(side="left", padx=(5, 0), fill="x", expand=True)

            zeile_schrift_achsen = ctk.CTkFrame(inhalt, fg_color="transparent")
            zeile_schrift_achsen.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(zeile_schrift_achsen, text="Schriftgröße Achsen:", width=110, anchor="w").pack(side="left")
            eingabe_schrift_achsen = ctk.CTkEntry(zeile_schrift_achsen)
            eingabe_schrift_achsen.insert(0, str(zustand.get("schriftgroesse_achsen", 11)))
            eingabe_schrift_achsen.pack(side="left", padx=(5, 0), fill="x", expand=True)

            eingabe_hintergrund = _hex_feld(inhalt, "Hintergrund:", zustand.get("hintergrund_figure", "#ffffff"))

            # Alle Eingabefelder gelten sofort: Enter bestätigt ohne
            # Verzögerung, Tippen/Fokuswechsel mit kurzer Verzögerung (siehe
            # _sofort_anwenden oben) - kein Klick auf "Übernehmen" nötig.
            for eingabe in (
                eingabe_titel_hoehe, eingabe_y_label, eingabe_x_label, eingabe_x_min, eingabe_x_max,
                eingabe_y_min, eingabe_y_max, eingabe_titel_form, eingabe_x_label_form, eingabe_y_label_form,
                eingabe_y_min_form, eingabe_y_max_form, eingabe_farbskala_min, eingabe_farbskala_max,
                eingabe_linienfarbe, eingabe_linienbreite, eingabe_schrift_titel, eingabe_schrift_achsen,
                eingabe_hintergrund,
            ):
                eingabe.bind("<KeyRelease>", _sofort_anwenden)
                eingabe.bind("<FocusOut>", _sofort_anwenden)
                eingabe.bind("<Return>", _enter_anwenden)

        dropdown = ctk.CTkOptionMenu(
            seitenleiste, values=anzeige_werte, command=zeige_versuch, fg_color=MUL_TURKIS,
        )
        dropdown.pack(fill="x", pady=(0, 15))

        ctk.CTkButton(
            seitenleiste, text="Settings", fg_color=MUL_DUNKEL, command=oeffne_settings,
        ).pack(fill="x")

        zeige_versuch(anzeige_werte[0])

    def lade_ergebnis_dataframe(self, eintrag, voller_pfad):
        """Lädt <versuch>_results.parquet aus processed_data als pandas DataFrame, oder None."""
        raw_data_ordner = os.path.dirname(voller_pfad)
        processed_ordner = self.processed_data_ordner_fuer(raw_data_ordner)
        versuch_name = os.path.splitext(eintrag)[0]
        parquet_pfad = os.path.join(
            processed_ordner, f"{self._sanitiere_versuchsnamen(versuch_name)}_results.parquet"
        )
        if not os.path.exists(parquet_pfad):
            return None
        try:
            import polars as pl
            return pl.read_parquet(parquet_pfad).to_pandas()
        except ImportError:
            try:
                import pandas as pd
                return pd.read_parquet(parquet_pfad)
            except Exception as e:
                print(f"[Ergebnis-Parquet Fehler] {parquet_pfad}: {e}")
                return None
        except Exception as e:
            print(f"[Ergebnis-Parquet Fehler] {parquet_pfad}: {e}")
            return None

    def zeichne_ergebnis_plot(
        self, eintrag_info, fig, ax_hoehe, ax_form, canvas, sm, farbskala, zustand, np, mcolors
    ):
        """
        Zeichnet Höhenverlauf (links) und Schmelzverlauf (rechts, alle
        Konturen nach Temperatur eingefärbt) für einen Versuch NEU in die
        bereits bestehenden Achsen (ax.clear() statt neues Canvas-Widget) -
        das ist der Schlüssel gegen den Lade-"Sprung" beim Versuchswechsel.
        """
        staub, eintrag, voller_pfad = eintrag_info
        df = self.lade_ergebnis_dataframe(eintrag, voller_pfad)

        ax_hoehe.clear()
        ax_form.clear()

        if df is None or df.empty:
            ax_hoehe.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            ax_form.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            canvas.draw_idle()
            return

        # --- Höhenverlauf ---
        if "Temperature" in df.columns and "sample_height_px" in df.columns:
            gueltig = df.dropna(subset=["sample_height_px"]).sort_values("Temperature")
            if not gueltig.empty:
                referenz_hoehe = gueltig["sample_height_px"].iloc[0]
                if referenz_hoehe:
                    hoehe_rel_pct = gueltig["sample_height_px"] / referenz_hoehe * 100
                    ax_hoehe.plot(
                        gueltig["Temperature"], hoehe_rel_pct,
                        color=zustand.get("linienfarbe", MUL_TURKIS) or MUL_TURKIS,
                        linewidth=zustand.get("linienbreite", 2) or 2,
                    )
        schriftgroesse_titel = zustand.get("schriftgroesse_titel", 13) or 13
        schriftgroesse_achsen = zustand.get("schriftgroesse_achsen", 11) or 11
        ax_hoehe.set_xlabel(zustand["label_x"], fontsize=schriftgroesse_achsen)
        ax_hoehe.set_ylabel(zustand["label_y"], fontsize=schriftgroesse_achsen)
        ax_hoehe.set_title(zustand["title_hoehe"], fontsize=schriftgroesse_titel)
        ax_hoehe.set_xlim(left=zustand["x_min"], right=zustand["x_max"])
        ax_hoehe.set_ylim(bottom=zustand["y_min"], top=zustand["y_max"])
        ax_hoehe.grid(True, alpha=0.3)


        # --- Form: kompletter Schmelzverlauf, alle Konturen übereinander,
        # nach Temperatur eingefärbt (wie bei den bunten Referenzplots).
        # Zeigt nur Konturen im selben X-Achsen-Bereich wie der Höhenverlauf
        # links - Farbskala rechts bleibt dabei aber IMMER fix. ---
        if (
            "contour_x" in df.columns
            and "contour_y" in df.columns
            and "Temperature" in df.columns
        ):
            gueltige = df.dropna(subset=["contour_x", "contour_y", "Temperature"]).copy()
            gueltige = gueltige[
                gueltige["contour_x"].apply(lambda w: w is not None and len(w) > 0)
            ]
            if zustand["x_min"] is not None:
                gueltige = gueltige[gueltige["Temperature"] >= zustand["x_min"]]
            if zustand["x_max"] is not None:
                gueltige = gueltige[gueltige["Temperature"] <= zustand["x_max"]]
        else:
            gueltige = None

        if gueltige is not None and not gueltige.empty:
            gueltige = gueltige.sort_values("Temperature")

            # Auf max. ~60 Konturen ausdünnen, sonst wird der Plot zu voll/langsam
            max_konturen = 60
            if len(gueltige) > max_konturen:
                indizes = np.linspace(0, len(gueltige) - 1, max_konturen).round().astype(int)
                gueltige = gueltige.iloc[sorted(set(indizes))]

            norm = sm.norm  # fix (siehe FARBSKALA_MIN/MAX in baue_ergebnisse_tab)
            cmap = sm.get_cmap()

            # Gemeinsame Grundlinie (unterste Kante über alle Konturen), damit
            # jede Form bis zur Probenhalter-Linie "gefüllt" wird.
            grundlinie = max(np.nanmax(np.asarray(cy, dtype=float)) for cy in gueltige["contour_y"])

            # Von kalt -> heiß zeichnen: kühle (hohe, schmale) Formen liegen
            # unten im Stapel, heißere (geschmolzene, breitere) werden zuletzt
            # darüber gezeichnet - Ränder der kühleren Formen bleiben als
            # dünne Farbringe sichtbar.
            for _, zeile in gueltige.iterrows():
                cx = np.asarray(zeile["contour_x"], dtype=float)
                cy = np.asarray(zeile["contour_y"], dtype=float)
                reihenfolge = np.argsort(cx)
                cx, cy = cx[reihenfolge], cy[reihenfolge]
                farbe = cmap(norm(zeile["Temperature"]))
                ax_form.fill_between(cx, cy, grundlinie, color=farbe, linewidth=0)

            ax_form.invert_yaxis()
            ax_form.set_aspect("equal", adjustable="box")

            # Y-Achse (px) rechts optional fest einstellbar - "von" = kleinerer
            # Pixelwert (oben im Bild), "bis" = größerer Pixelwert (unten im
            # Bild), passend zur invertierten Achse. Fehlt einer der beiden
            # Werte, bleibt dieser bei der automatischen Grenze.
            if zustand["y_min_form"] is not None or zustand["y_max_form"] is not None:
                aktuell_unten, aktuell_oben = ax_form.get_ylim()
                unten = zustand["y_max_form"] if zustand["y_max_form"] is not None else aktuell_unten
                oben = zustand["y_min_form"] if zustand["y_min_form"] is not None else aktuell_oben
                ax_form.set_ylim(bottom=unten, top=oben)
        elif gueltige is not None:
            ax_form.text(
                0.5, 0.5, "Keine Kontur im gewählten Bereich.", ha="center", va="center",
                transform=ax_form.transAxes,
            )

        # Nur die Beschriftung der Farbskala ist änderbar - die
        # Werte/Skalierung (norm) selbst bleiben fix (siehe oben).
        farbskala.set_label(zustand["label_x"], fontsize=schriftgroesse_achsen)

        ax_form.set_xlabel(zustand["label_x_form"], fontsize=schriftgroesse_achsen)
        ax_form.set_ylabel(zustand["label_y_form"], fontsize=schriftgroesse_achsen)
        ax_form.set_title(zustand["title_form"], fontsize=schriftgroesse_titel)
        ax_form.grid(True, alpha=0.2)

        # Gemeinsamer Figure-Hintergrund (wie im Darstellung-Dialog gewählt).
        hintergrund_figure = zustand.get("hintergrund_figure")
        if hintergrund_figure:
            fig.patch.set_facecolor(hintergrund_figure)
            fig.patch.set_alpha(1.0)

        # Grosszuegigerer Abstand zwischen den beiden Diagrammen (Standard-
        # tight_layout() ohne Padding wirkt zu eng beieinander).
        fig.tight_layout(w_pad=3.5, pad=1.5)
        canvas.draw_idle()

    # ------------------------------------------------------------------
    # TAB: ERGEBNISSE (SEM) - Elementkarten durchklicken, je Element eigene
    # frei waehlbare Farbe (Farbauswahl-Dialog), als Overlay uebereinander.
    # ------------------------------------------------------------------
    def _sem_farbe_hex_zu_rgb(self, hex_wert):
        """'#rrggbb' -> (r, g, b) als Floats 0..1."""
        hex_wert = str(hex_wert or "#ffffff").lstrip("#")
        if len(hex_wert) != 6:
            hex_wert = "ffffff"
        return tuple(int(hex_wert[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _sem_sichere_hex_farbe(self, wert, fallback="#ffffff"):
        """
        Liefert IMMER einen gueltigen Hex-Farbstring ("#rrggbb") zurueck.
        Faengt insbesondere "transparent" ab (das z.B. bei CTkButton fuer
        hover_color/border_color NICHT erlaubt ist und sonst mit
        "ValueError: transparency is not allowed for this attribute"
        abstuerzt) sowie None/leere/kaputte Werte, z.B. aus einer aelteren
        gespeicherten diagramm_einstellungen.json.
        """
        if isinstance(wert, str):
            kandidat = wert.strip()
            if kandidat.startswith("#") and len(kandidat) in (4, 7):
                return kandidat
        return fallback

    def _sem_stelle_element_zustand_sicher(self, zustand, elemente):
        """
        Ergaenzt in zustand['element_farben']/['element_sichtbar'] fehlende
        Eintraege fuer neu aufgetauchte Elemente (Default-Farbe zyklisch aus
        SEM_ELEMENT_FARBPALETTE, standardmaessig sichtbar=True). Bereits
        vorhandene (z.B. vom Nutzer geaenderte) Eintraege bleiben unangetastet.
        """
        zustand.setdefault("element_farben", {})
        zustand.setdefault("element_sichtbar", {})
        for element in elemente:
            if element not in zustand["element_farben"]:
                index = len(zustand["element_farben"]) % len(SEM_ELEMENT_FARBPALETTE)
                zustand["element_farben"][element] = SEM_ELEMENT_FARBPALETTE[index]
            if element not in zustand["element_sichtbar"]:
                zustand["element_sichtbar"][element] = True

    def _sem_baue_element_farbbild(self, karte, element, zustand, karten_prozent=None, pixel_maske=None,
                                    probe_maske=None):
        """
        Baut das farbige Bild fuer GENAU EINE Elementkarte mittels einer
        echten Farbskala (Colormap).

          1. Pixelwert = tatsaechlicher, quantifizierter Element-Prozentwert
             (`karten_prozent[element]`), NICHT der rohe 16-Bit-Grauwert -
             so zeigt die Skala echte %-Werte (z.B. "8.3 % Sauerstoff")
             statt einer Naeherung auf Basis des 16-Bit-Wertebereichs.
          2. Normierung auf [0, 1]: n(x) = clip((x - x_min) / (x_max - x_min), 0, 1)
             mit x_min/x_max NUR aus den Pixeln berechnet, die sich
             innerhalb der Probe befinden (`probe_maske`/`pixel_maske`) -
             der Hintergrund ausserhalb der Probe (der ueberall 0 % ist)
             darf die Skala nicht verfaelschen.
          3. Zuordnung des normierten Werts zu SEM_FARBSKALA_COLORMAP:
             niedrige Werte -> dunkle/kalte Farbe, hohe Werte -> helle/
             warme Farbe.

        Modus `zustand["farbskalierung"]` (siehe SEM_FARBSKALIERUNG_OPTIONEN)
        bestimmt NUR die Obergrenze x_max der Normierung - die zugrunde
        liegenden Prozentwerte bleiben in beiden Faellen unveraendert:
          "linear" - x_max = tatsaechliches Maximum des Elements INNERHALB
                     DER PROBE. Das ist genau der hoechste in der Probe
                     vorkommende %-Wert dieses Elements und steht dann
                     ganz oben an der Farbskala.
          "p99"    - x_max = 99. Perzentil innerhalb der Probe. Die
                     obersten ~1% der Pixelwerte werden auf die Endfarbe
                     gekappt, der restliche (relevante) Wertebereich wird
                     staerker ueber die Farbskala verteilt -> schwaechere
                     raeumliche Strukturen werden deutlicher sichtbar.

        `mindestanteil` (optional, wie zuvor): Pixel, an denen dieses
        Element WENIGER als den eingestellten Prozentsatz zur lokalen
        Zusammensetzung beitraegt, werden auf die dunkelste
        Farbskalenfarbe gesetzt (== "kein/kaum Signal hier").

        `pixel_maske` (optional): boolesche Maske (Rohdaten-Tab-Filter) -
        ist sie gesetzt, werden NUR die gefilterten Pixel eingefaerbt; alle
        anderen erscheinen weiss, analog zum Rohdaten-Tab. Sie wird -
        zusammen mit `probe_maske`, falls `pixel_maske` fehlt - auch fuer
        die Berechnung von x_min/x_max verwendet.

        Gibt (rgb_bild, x_min, x_max, x_max_linear) zurueck; x_min/x_max/
        x_max_linear sind ECHTE Element-Prozentwerte (0-100) und werden fuer
        die Farbskalen-Legende (Colorbar) im Diagramm gebraucht. x_max_linear
        ist IMMER das tatsaechliche Maximum innerhalb der Probe (unabhaengig
        vom gewaehlten Modus) - im "p99"-Modus braucht die Colorbar diesen
        Wert zusaetzlich zu x_max (= p99-Wert) fuer die Anzeige oberhalb der
        Unterbrechung (siehe baue_ergebnisse_tab_sem/zeichne). Im "linear"-
        Modus ist x_max_linear == x_max. Bei leerer/konstanter Karte:
        (None, 0.0, 0.0, 0.0).
        """
        import numpy as np
        import matplotlib as mpl

        if karte is None or karte.size == 0:
            return None, 0.0, 0.0, 0.0

        prozent_karte = karten_prozent.get(element) if karten_prozent else None
        # Fallback auf die rohen Grauwerte, falls (aus irgendeinem Grund)
        # keine quantifizierte %-Karte fuer dieses Element vorliegt.
        basis_karte = prozent_karte if prozent_karte is not None else karte

        maske_fuer_skala = pixel_maske if pixel_maske is not None else probe_maske
        if maske_fuer_skala is not None and maske_fuer_skala.shape == basis_karte.shape and maske_fuer_skala.any():
            werte_in_probe = basis_karte[maske_fuer_skala]
        else:
            # Keine gueltige Probe-Maske vorhanden -> auf die gesamte
            # Karte ausweichen, statt mit einer leeren Auswahl abzustuerzen.
            werte_in_probe = basis_karte.reshape(-1)

        x_min = float(np.min(werte_in_probe))
        x_max_linear = float(np.max(werte_in_probe))
        if zustand.get("farbskalierung", "p99") == "linear":
            x_max = x_max_linear
        else:
            x_max = float(np.percentile(werte_in_probe, 99))
        if x_max <= x_min:
            x_max = x_min + 1.0
        if x_max_linear <= x_min:
            x_max_linear = x_min + 1.0

        normiert = np.clip((basis_karte - x_min) / (x_max - x_min), 0.0, 1.0)

        mindestanteil = float(zustand.get("mindestanteil", 0.0) or 0.0)
        if mindestanteil > 0.0 and karten_prozent is not None and element in karten_prozent:
            normiert = np.where(karten_prozent[element] >= mindestanteil, normiert, 0.0)

        colormap = mpl.colormaps[SEM_FARBSKALA_COLORMAP]
        rgb_bild = np.asarray(colormap(normiert))[..., :3].copy()

        if pixel_maske is not None and pixel_maske.shape == karte.shape:
            rgb_bild[~pixel_maske] = 1.0

        return rgb_bild, x_min, x_max, x_max_linear

    def baue_ergebnisse_tab_sem(self, parent, projekt, methode):
        """
        SEM-Gegenstueck zu baue_ergebnisse_tab (EMI)/baue_ergebnisse_tab_tga:
        links Versuchsauswahl, in der Mitte GENAU EINE Elementkarte
        (per Dropdown rechts auswaehlbar), eingefaerbt ueber eine echte
        Farbskala/Colormap samt Legende (Colorbar) - siehe
        _sem_baue_element_farbbild. Rechts: Dropdown zur Elementauswahl
        sowie ein Linear/p99-Umschalter fuer die Obergrenze der
        Farbnormierung (siehe SEM_FARBSKALIERUNG_OPTIONEN). Auswahl/
        Skalierung sind projektweit (nicht pro Versuch) gespeichert, wie
        die uebrigen Diagramm-Einstellungen (lade_/speichere_
        diagramm_einstellungen).
        """
        versuche = self.liste_versuche(projekt, methode)
        if not versuche:
            ctk.CTkLabel(parent, text="Keine lokalen Rohdaten gefunden.").pack(pady=20)
            return

        try:
            import matplotlib as mpl
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            import matplotlib.cm as mcm
            from matplotlib.colors import Normalize
            from matplotlib.ticker import PercentFormatter
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
            import tkinter as tk
            import numpy as np
        except ImportError:
            ctk.CTkLabel(
                parent,
                text="matplotlib ist nicht installiert - 'pip install matplotlib' im GUI-Environment noetig.",
                wraplength=560,
            ).pack(pady=20)
            return

        zustand_standard = {
            "element_farben": {}, "element_sichtbar": {}, "mindestanteil": 0.0,
            "ausgewaehltes_element": None, "farbskalierung": "p99",
        }
        zustand = self.lade_diagramm_einstellungen(projekt, methode, zustand_standard)
        # Absichern gegen kaputte/veraltete gespeicherte Werte (z.B. "transparent"
        # oder leere Strings) - siehe _sem_sichere_hex_farbe.
        for _element, _farbe in list(zustand.get("element_farben", {}).items()):
            zustand["element_farben"][_element] = self._sem_sichere_hex_farbe(_farbe)
        if zustand.get("farbskalierung") not in SEM_FARBSKALIERUNG_OPTIONEN:
            zustand["farbskalierung"] = "p99"

        haupt_layout = ctk.CTkFrame(parent, fg_color="transparent")
        haupt_layout.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Linke Spalte: Versuchsliste (analog Rohdaten-Tab) ---
        linke_breite_merker = {"breite": 260}
        linke_spalte = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=linke_breite_merker["breite"])
        linke_spalte.pack(side="left", fill="y", padx=(0, 0))
        linke_spalte.pack_propagate(False)

        kopfzeile = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        kopfzeile.pack(side="top", fill="x", padx=5, pady=(0, 5))
        links_einklapp_btn = ctk.CTkButton(kopfzeile, text="◀", width=32, fg_color="transparent", border_width=1)
        links_einklapp_btn.pack(side="left")

        inhalt_links = ctk.CTkFrame(linke_spalte, fg_color="transparent")
        inhalt_links.pack(fill="both", expand=True)

        ctk.CTkLabel(inhalt_links, text="Versuche", font=("Arial", 12, "bold")).pack(
            side="top", anchor="w", padx=10, pady=(0, 5)
        )
        scroll = ctk.CTkScrollableFrame(inhalt_links, width=220)
        scroll.pack(padx=0, pady=(0, 5), fill="both", expand=True)

        griff_links = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_links.pack(side="left", fill="y", padx=(4, 8))
        self._mache_griff_ziehbar(griff_links, linke_spalte, linke_breite_merker, minimum=90, maximum=650, invertiert=False)
        self._mache_spalte_einklappbar(
            links_einklapp_btn, linke_spalte, inhalt_links, linke_breite_merker,
            eingeklappt_text="◀", ausgeklappt_text="▶", breite_eingeklappt=48,
        )

        ausgewaehlter_pfad = {"wert": None}
        zeilen_frames = []
        aktualisiere_element_panel = {"fn": None}
        # Normierte Elementkarten (Prozent je Pixel) des GERADE angezeigten
        # Versuchs - fuer den Maus-Hover-Readout (_bei_maus_bewegung) gecacht,
        # damit beim reinen Bewegen der Maus NICHT jedes Mal alle TIFs neu
        # von der Platte gelesen werden muessen.
        aktuelle_daten = {"karten_normiert": None}
        # "gezeichnet" merkt sich, ob im aktuellen Diagramm schon ein Bild
        # steht (fuer den allerersten Zeichenaufruf eines Versuchs duerfen
        # die noch auf (0,1) stehenden Default-Achsenlimits NICHT als "Zoom"
        # missverstanden werden). Die eigentlichen Zoom-Grenzen werden
        # IMMER frisch per ax.get_xlim()/get_ylim() aus dem Diagramm selbst
        # gelesen (siehe zeichne()) statt aus einem separaten, potenziell
        # veralteten Zwischenspeicher - dadurch bleibt der Zoom-Ausschnitt
        # beim Umschalten von Element-Sichtbarkeit/Farbe/Filter-Schieberegler
        # ("Alle an"/"Alle aus" etc.) zuverlaessig erhalten und "springt"
        # nicht mehr auf die Vollansicht zurueck.
        bild_status = {"gezeichnet": False}
        # Markierungen ("Pins"), die der Nutzer im Bild gesetzt hat, um
        # einzelne Stellen zu vergleichen (siehe _markierungs_klick /
        # Markierungs-Liste im rechten Panel). Jede Markierung merkt sich
        # Pixel-Koordinate + die Element-%-Werte an dieser Stelle.
        markierungen = []

        # --- Mitte: 1 grosses Overlay-Diagramm + Zoom-Werkzeugleiste +
        # Filter-Schieberegler + Marker-Werkzeug + Pixel-Werte-Anzeige (Hover) ---
        mitte = ctk.CTkFrame(haupt_layout, fg_color="transparent")
        mitte.pack(side="left", fill="both", expand=True, padx=(0, 0))

        # Untere Leiste (Hover-Readout + Filter/Marker-Zeile + Zoom-Toolbar)
        # ZUERST mit side="bottom" packen, damit ihr Platz reserviert ist,
        # bevor das (expandierende) Diagramm gepackt wird. Packreihenfolge
        # von unten nach oben: Hover-Zeile ganz unten, darueber die
        # Filter/Marker-Zeile, darueber die Matplotlib-Toolbar, direkt
        # unter dem Diagramm.
        untere_leiste = ctk.CTkFrame(mitte, fg_color="transparent")
        untere_leiste.pack(side="bottom", fill="x")

        # --- Hover-Readout: Pixel-Koordinate + je Element ein Farbkaestchen
        # (identisch zur Overlay-Farbe) + Prozentwert, statt nur Fliesstext -
        # so sieht man auf einen Blick, welche Farbe im Bild zu welchem
        # Element/Wert gehoert.
        #
        # WICHTIG: die Zeile darf bei JEDER Mausbewegung neu befuellt werden
        # (das passiert sehr oft) - wuerden die Widgets dabei jedes Mal
        # zerstoert und neu erzeugt, aendert sich kurzzeitig ihre Breite und
        # das ganze Diagramm "hüpft"/flackert. Daher: feste Hoehe fuer den
        # Rahmen + ein fester Pool an wiederverwendeten "Slot"-Widgets, die
        # nur per .configure() aktualisiert (nicht neu gebaut) werden. ---
        hover_frame = ctk.CTkFrame(untere_leiste, fg_color=("gray92", "gray17"), height=34)
        hover_frame.pack(side="bottom", fill="x", padx=8, pady=(4, 6))
        hover_frame.pack_propagate(False)

        hover_koord_label = ctk.CTkLabel(
            hover_frame, text="Pixel-Werte: Maus über das Bild bewegen",
            anchor="w", font=("Arial", 11, "bold"), width=230,
        )
        hover_koord_label.pack(side="left", padx=(8, 10), pady=5)

        _HOVER_SLOT_ANZAHL = 12  # reicht fuer alle ueblichen SEM-Elementlisten
        hover_slots = []
        for _ in range(_HOVER_SLOT_ANZAHL):
            slot = ctk.CTkFrame(hover_frame, fg_color="transparent")
            swatch = ctk.CTkLabel(slot, text="", width=12, height=12, fg_color="#ffffff", corner_radius=3)
            swatch.pack(side="left", padx=(0, 4))
            text = ctk.CTkLabel(slot, text="", font=("Arial", 11), width=68, anchor="w")
            text.pack(side="left")
            hover_slots.append({"frame": slot, "swatch": swatch, "text": text, "sichtbar": False})

        def _hover_zuruecksetzen():
            hover_koord_label.configure(text="Pixel-Werte: Maus über das Bild bewegen")
            for slot in hover_slots:
                if slot["sichtbar"]:
                    slot["frame"].pack_forget()
                    slot["sichtbar"] = False

        def _hover_anzeigen(x, y, werte_mit_farbe):
            hover_koord_label.configure(text=f"Pixel ({x}, {y}):")
            anzahl = min(len(werte_mit_farbe), _HOVER_SLOT_ANZAHL)
            for index, slot in enumerate(hover_slots):
                if index < anzahl:
                    element, wert, farbe = werte_mit_farbe[index]
                    slot["swatch"].configure(fg_color=farbe)
                    slot["text"].configure(text=f"{element}: {wert:.1f}%")
                    if not slot["sichtbar"]:
                        slot["frame"].pack(side="left", padx=(0, 12), pady=5)
                        slot["sichtbar"] = True
                elif slot["sichtbar"]:
                    slot["frame"].pack_forget()
                    slot["sichtbar"] = False
            if anzahl == 0:
                hover_koord_label.configure(text=f"Pixel ({x}, {y}):  alle Elemente ~0 %")

        _hover_zuruecksetzen()


        # --- Filter-Zeile: Mindestanteil-Schwelle (nur Pixel anzeigen, an
        # denen ein Element mindestens X % der lokalen Zusammensetzung
        # ausmacht). ---
        filter_zeile = ctk.CTkFrame(untere_leiste, fg_color="transparent")
        filter_zeile.pack(side="bottom", fill="x", padx=8, pady=(2, 0))

        ctk.CTkLabel(filter_zeile, text="Mindestanteil-Filter:", font=("Arial", 11)).pack(side="left", padx=(0, 6))
        mindestanteil_wert_label = ctk.CTkLabel(filter_zeile, text="0 %", font=("Arial", 11), width=42)

        def _mindestanteil_geaendert(neuer_wert):
            zustand["mindestanteil"] = round(float(neuer_wert), 1)
            mindestanteil_wert_label.configure(text=f"{zustand['mindestanteil']:.0f} %")
            self.speichere_diagramm_einstellungen(projekt, methode, zustand)
            zeichne()

        mindestanteil_slider = ctk.CTkSlider(
            filter_zeile, from_=0, to=90, number_of_steps=90, width=180, command=_mindestanteil_geaendert,
        )
        mindestanteil_slider.set(float(zustand.get("mindestanteil", 0.0) or 0.0))
        mindestanteil_slider.pack(side="left", padx=(0, 6))
        mindestanteil_wert_label.configure(text=f"{float(zustand.get('mindestanteil', 0.0) or 0.0):.0f} %")
        mindestanteil_wert_label.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(
            filter_zeile,
            text="→ blendet Pixel aus, an denen ein Element weniger als diesen Anteil hat",
            font=("Arial", 10), text_color=("gray30", "gray70"),
        ).pack(side="left", padx=(0, 16))

        # NavigationToolbar2Tk braucht ein "echtes" Tk-Widget als Master -
        # daher ein normales tk.Frame statt CTkFrame fuer den Toolbar-Container.
        toolbar_frame = tk.Frame(untere_leiste)
        toolbar_frame.pack(side="bottom", fill="x")

        figsize = self._dynamische_figsize(mitte, 1, 1, mindest_breite_px=500, mindest_hoehe_px=420)
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        canvas = FigureCanvasTkAgg(fig, master=mitte)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        # Merkt sich die Colorbar-Achse(n) der Farbskalen-Legende zwischen
        # zwei zeichne()-Aufrufen, damit sie vor jedem Neuzeichnen sauber
        # entfernt werden (statt sich bei jedem Aufruf zu verdoppeln). Im
        # p99-Modus sind es ZWEI Achsen (Hauptskala + kleines Segment fuer
        # den echten Maximalwert oberhalb der Unterbrechung, siehe zeichne()).
        farbskala_status = {"achsen": []}

        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
        toolbar.update()

        def _werte_am_pixel(x, y):
            karten = aktuelle_daten["karten_normiert"]
            if not karten:
                return None
            beispiel_karte = next(iter(karten.values()))
            anzeige_h, anzeige_w = aktuelle_daten.get("anzeige_shape", beispiel_karte.shape[:2])
            if not (0 <= y < anzeige_h and 0 <= x < anzeige_w):
                return None
            # Maus-/Markerkoordinaten liegen in den echten Pixelkoordinaten
            # des EDS Layered Image (z.B. 8192x5376). Fuer den Datenzugriff
            # auf die Elementkarten (z.B. 512x336) proportional umrechnen.
            karten_x = min(int(x * beispiel_karte.shape[1] / anzeige_w), beispiel_karte.shape[1] - 1)
            karten_y = min(int(y * beispiel_karte.shape[0] / anzeige_h), beispiel_karte.shape[0] - 1)
            werte = sorted(
                ((element, float(karte[karten_y, karten_x])) for element, karte in karten.items()),
                key=lambda kv: -kv[1],
            )
            return [
                (element, wert, zustand.get("element_farben", {}).get(element, "#ffffff"))
                for element, wert in werte if wert >= 0.05
            ]

        def _bei_maus_bewegung(event):
            if event.inaxes != ax or event.xdata is None or event.ydata is None:
                return
            x = int(round(event.xdata))
            y = int(round(event.ydata))
            werte = _werte_am_pixel(x, y)
            if werte is None:
                return
            _hover_anzeigen(x, y, werte)

        def _bei_maus_verlassen(_event):
            _hover_zuruecksetzen()

        def _durchschnitt_im_bereich(x0, y0, x1, y1):
            """Mittelwert jedes Elements ueber ein rechteckiges Pixel-Gebiet
            (fuer die Rechteck-Auswahl per Ziehen). Koordinaten werden
            automatisch auf die Bildgrenzen begrenzt."""
            karten = aktuelle_daten["karten_normiert"]
            if not karten:
                return None
            beispiel_karte = next(iter(karten.values()))
            hoehe_bild, breite_bild = beispiel_karte.shape[0], beispiel_karte.shape[1]
            anzeige_h, anzeige_w = aktuelle_daten.get("anzeige_shape", beispiel_karte.shape[:2])
            anzeige_xa, anzeige_xb = sorted((int(round(x0)), int(round(x1))))
            anzeige_ya, anzeige_yb = sorted((int(round(y0)), int(round(y1))))
            anzeige_xa = max(0, min(anzeige_xa, anzeige_w - 1))
            anzeige_xb = max(0, min(anzeige_xb, anzeige_w - 1))
            anzeige_ya = max(0, min(anzeige_ya, anzeige_h - 1))
            anzeige_yb = max(0, min(anzeige_yb, anzeige_h - 1))
            xa = min(int(anzeige_xa * breite_bild / anzeige_w), breite_bild - 1)
            xb = min(int(anzeige_xb * breite_bild / anzeige_w), breite_bild - 1)
            ya = min(int(anzeige_ya * hoehe_bild / anzeige_h), hoehe_bild - 1)
            yb = min(int(anzeige_yb * hoehe_bild / anzeige_h), hoehe_bild - 1)
            if xb < xa or yb < ya:
                return None
            return (
                {element: float(np.mean(karte[ya:yb + 1, xa:xb + 1])) for element, karte in karten.items()},
                (anzeige_xa, anzeige_ya, anzeige_xb, anzeige_yb),
            )

        def _markierung_box_inhalt(marker, index):
            """Zeilen + grobe Box-Groesse (in Punkten) fuer die
            Zusammensetzungs-Box einer Markierung - wird sowohl beim
            Zeichnen (Position der Box/des "x"-Knopfes) als auch beim
            Klick-Hittest auf den "x"-Knopf gebraucht, damit beide exakt
            dieselbe Geometrie annehmen."""
            top_werte = sorted(marker["werte"].items(), key=lambda kv: -kv[1])[:5]
            zeilen = [f"{element}: {wert:.1f}%" for element, wert in top_werte if wert >= 0.05]
            if marker.get("typ") == "rechteck":
                kopf = f"Ø{index}  ({marker['breite']}×{marker['hoehe']} px)"
            else:
                kopf = f"M{index}"
            alle_zeilen = [kopf] + (zeilen if zeilen else ["alle Elemente ~0 %"])
            breite_pts = 16 + max(len(z) for z in alle_zeilen) * 5.5
            hoehe_pts = 14 + len(alle_zeilen) * 12.5
            return zeilen, breite_pts, hoehe_pts

        def _x_knopf_offset_pts(marker, index):
            _zeilen, breite_pts, hoehe_pts = _markierung_box_inhalt(marker, index)
            return (16 + breite_pts - 10, 16 + hoehe_pts - 8)

        def _x_knopf_display_pos(marker, index):
            """Aktuelle Bildschirm-Position (Pixel) des Mini-'x'-Knopfes
            EINER Markierung - IMMER frisch aus dem aktuellen Zoom/Pan-
            Zustand berechnet (ax.transData), damit der Hittest auch nach
            Verschieben/Zoomen ohne Neuzeichnen noch stimmt."""
            off_x_pts, off_y_pts = _x_knopf_offset_pts(marker, index)
            anker_disp = ax.transData.transform((marker["x"], marker["y"]))
            px_je_pt = fig.dpi / 72.0
            return (anker_disp[0] + off_x_pts * px_je_pt, anker_disp[1] + off_y_pts * px_je_pt)

        def _klick_auf_x_knopf(event):
            for index, marker in enumerate(markierungen, start=1):
                bx, by = _x_knopf_display_pos(marker, index)
                if (event.x - bx) ** 2 + (event.y - by) ** 2 <= 11 ** 2:
                    return marker
            return None

        # --- Klick = einzelner Pixel, Ziehen (Rechteck aufziehen) =
        # Durchschnitts-Elementaranalyse ueber die Auswahl. Unterschieden
        # wird per Maus-runter/-bewegt/-los statt nur einem einzelnen
        # Klick-Event, damit ein kurzer Klick weiterhin wie bisher einen
        # Punkt setzt, ein Ziehen aber die neue Rechteck-Auswahl ausloest. ---
        auswahl = {"aktiv": False, "start_data": None, "start_disp": None, "vorschau": None}
        ZIEH_SCHWELLE_PX = 6  # Mindestbewegung in Bildschirm-Pixeln fuer "Ziehen" statt "Klick"

        def _auswahl_vorschau_entfernen():
            if auswahl["vorschau"] is not None:
                try:
                    auswahl["vorschau"].remove()
                except Exception:
                    pass
                auswahl["vorschau"] = None

        def _maus_runter(event):
            if event.button != 1:
                return
            # Waehrend die Toolbar-Lupe/Pan aktiv ist, gehoert der Klick/Zug
            # zum Zoomen/Verschieben - dann KEINE Markierung setzen/entfernen.
            if getattr(toolbar, "mode", ""):
                return
            # Klick auf den Mini-"x"-Knopf einer bestehenden Box? Das MUSS
            # VOR dem inaxes-Check passieren: die Box (und damit der
            # "x"-Knopf) kann ueber den Bildrand hinaus in den Bereich
            # AUSSERHALB der Achse hineinragen.
            treffer = _klick_auf_x_knopf(event)
            if treffer is not None:
                markierungen.remove(treffer)
                zeichne()
                return
            if event.inaxes != ax or event.xdata is None or event.ydata is None:
                return
            canvas.get_tk_widget().focus_set()
            auswahl["aktiv"] = True
            auswahl["start_data"] = (event.xdata, event.ydata)
            auswahl["start_disp"] = (event.x, event.y)

        def _maus_bewegt_auswahl(event):
            if not auswahl["aktiv"] or event.inaxes != ax or event.xdata is None or event.ydata is None:
                return
            x0, y0 = auswahl["start_data"]
            _auswahl_vorschau_entfernen()
            patch = mpatches.Rectangle(
                (min(x0, event.xdata), min(y0, event.ydata)),
                abs(event.xdata - x0), abs(event.ydata - y0),
                fill=False, edgecolor="white", linewidth=1.2, linestyle="--", zorder=8,
            )
            ax.add_patch(patch)
            auswahl["vorschau"] = patch
            canvas.draw_idle()

        def _maus_los(event):
            if not auswahl["aktiv"]:
                return
            auswahl["aktiv"] = False
            _auswahl_vorschau_entfernen()
            start_disp = auswahl["start_disp"]
            start_data = auswahl["start_data"]
            if event.inaxes != ax or event.xdata is None or event.ydata is None:
                canvas.draw_idle()
                return
            bewegt_px = ((event.x - start_disp[0]) ** 2 + (event.y - start_disp[1]) ** 2) ** 0.5
            if bewegt_px < ZIEH_SCHWELLE_PX:
                # Kurzer Klick (kaum Bewegung) -> wie bisher ein einzelner Punkt.
                x = int(round(event.xdata))
                y = int(round(event.ydata))
                werte = _werte_am_pixel(x, y)
                if werte is None:
                    canvas.draw_idle()
                    return
                markierungen.append({
                    "typ": "punkt", "x": x, "y": y,
                    "werte": {element: wert for element, wert, _farbe in werte},
                })
            else:
                # Rechteck aufgezogen -> Durchschnitts-Elementaranalyse ueber
                # die Auswahl. Box wird an der oberen rechten Ecke verankert.
                ergebnis = _durchschnitt_im_bereich(start_data[0], start_data[1], event.xdata, event.ydata)
                if ergebnis is None:
                    canvas.draw_idle()
                    return
                werte, (xa, ya, xb, yb) = ergebnis
                markierungen.append({
                    "typ": "rechteck",
                    "x": xb, "y": ya,
                    "x0": xa, "y0": ya, "x1": xb, "y1": yb,
                    "breite": max(xb - xa + 1, 1), "hoehe": max(yb - ya + 1, 1),
                    "werte": werte,
                })
            zeichne()

        def _bei_taste(event):
            # ESC schliesst die zuletzt gesetzte Markierung (zusaetzlich zum
            # Mini-"x"-Knopf direkt an der Box).
            if event.key == "escape" and markierungen:
                markierungen.pop()
                zeichne()

        canvas.mpl_connect("motion_notify_event", _bei_maus_bewegung)
        canvas.mpl_connect("axes_leave_event", _bei_maus_verlassen)
        canvas.mpl_connect("button_press_event", _maus_runter)
        canvas.mpl_connect("motion_notify_event", _maus_bewegt_auswahl)
        canvas.mpl_connect("button_release_event", _maus_los)
        canvas.mpl_connect("key_press_event", _bei_taste)

        # --- Strg + Mausrad: Overlay-Diagramm um den Cursor herum zoomen
        # (gleiches Prinzip wie im Rohdaten-Tab, hier nur 1 Achse statt 2).
        # Ohne gedrueckte Strg-Taste passiert nichts, damit normales
        # Scrollen der Seite/des Panels nicht gestoert wird. ---
        def _strg_gedrueckt_ergebnisse(event):
            gui_event = getattr(event, "guiEvent", None)
            zustand_bits = getattr(gui_event, "state", 0)
            try:
                return bool(int(zustand_bits) & 0x0004)
            except (TypeError, ValueError):
                return False

        def _strg_scroll_zoom(event):
            if event.inaxes != ax or not _strg_gedrueckt_ergebnisse(event):
                return
            if event.button == "up":
                faktor = 0.85
            elif event.button == "down":
                faktor = 1.0 / 0.85
            else:
                return
            xlim0, ylim0 = ax.get_xlim(), ax.get_ylim()
            breite0, hoehe0 = xlim0[1] - xlim0[0], ylim0[1] - ylim0[0]
            rel_x = (event.xdata - xlim0[0]) / breite0 if breite0 else 0.5
            rel_y = (event.ydata - ylim0[0]) / hoehe0 if hoehe0 else 0.5
            neue_breite = breite0 * faktor
            neue_hoehe = hoehe0 * faktor
            mitte_x = xlim0[0] + rel_x * breite0
            mitte_y = ylim0[0] + rel_y * hoehe0
            ax.set_xlim(mitte_x - rel_x * neue_breite, mitte_x + (1 - rel_x) * neue_breite)
            ax.set_ylim(mitte_y - rel_y * neue_hoehe, mitte_y + (1 - rel_y) * neue_hoehe)
            canvas.draw_idle()

        canvas.mpl_connect("scroll_event", _strg_scroll_zoom)

        # --- Gedrueckt gehaltenes Scroll-Rad: Kartenausschnitt verschieben;
        # Doppelklick auf dem Scroll-Rad: Zoom zuruecksetzen (siehe
        # _sem_aktiviere_scrollrad_pan). ---
        self._sem_aktiviere_scrollrad_pan(canvas, (ax,), toolbar)

        def zeichne(zoom_beibehalten=True):
            pfad = ausgewaehlter_pfad["wert"]
            # Zoom-Ausschnitt IMMER frisch direkt vom Diagramm lesen (nicht
            # aus einem separaten Zwischenspeicher) - das ist die einzige
            # Quelle, die garantiert nicht veraltet sein kann. Nur beim
            # allerersten Zeichnen eines Versuchs (bild_status["gezeichnet"]
            # noch False) stehen hier die Matplotlib-Default-Limits (0,1),
            # die duerfen NICHT als Zoom uebernommen werden.
            kann_zoom_erhalten = zoom_beibehalten and bild_status["gezeichnet"]
            vorherige_xlim = ax.get_xlim() if kann_zoom_erhalten else None
            vorherige_ylim = ax.get_ylim() if kann_zoom_erhalten else None
            ax.clear()
            ax.set_xticks([])
            ax.set_yticks([])
            if not pfad:
                aktuelle_daten["karten_normiert"] = None
                bild_status["gezeichnet"] = False
                canvas.draw()
                return
            elementkarten, eds_pfad = self._sem_lade_elementkarten(pfad)
            if not elementkarten:
                aktuelle_daten["karten_normiert"] = None
                bild_status["gezeichnet"] = False
                ax.text(0.5, 0.5, "Keine Elementkarten (TIF) gefunden", ha="center", va="center")
                canvas.draw()
                return
            self._sem_stelle_element_zustand_sicher(zustand, elementkarten.keys())
            # Fuer den Hover-Readout IMMER die auf Prozent normierten Karten
            # nutzen (unabhaengig von der Overlay-Visualisierung unten), da
            # das die tatsaechliche Element-% ist, die man ablesen will (z.B.
            # "wie viel % Zink befindet sich an dieser Stelle").
            karten_prozent = self._sem_normalisiere_elementkarten(elementkarten)
            aktuelle_daten["karten_normiert"] = karten_prozent

            # Zeichenflaeche immer aus den echten Abmessungen des EDS Layered
            # Image im TIF-Ordner bestimmen. Das Overlay bleibt als
            # 512x336-Array speicherschonend und wird von imshow auf diese
            # 8192x5376-Pixelkoordinaten abgebildet.
            beispiel_karte = next(iter(elementkarten.values()))
            anzeige_h, anzeige_w = beispiel_karte.shape[:2]
            if eds_pfad:
                try:
                    from PIL import Image
                    with Image.open(eds_pfad) as eds_bild:
                        anzeige_w, anzeige_h = eds_bild.size
                except Exception as exc:
                    print(f"[SEM Maßstab] TIFF-Abmessungen nicht lesbar ({eds_pfad}): {exc}")
            aktuelle_daten["anzeige_shape"] = (anzeige_h, anzeige_w)

            # --- Nur die im Rohdaten-Tab GEFILTERTEN Pixel anzeigen: die
            # dort eingestellten Schwellwert-Filter (z.B. "C < 30") werden
            # hier erneut angewendet, alles was NICHT durchkommt (inkl. dem
            # schwarzen Hintergrund ausserhalb der Probe) wird ausgeblendet
            # (weiss statt eingefaerbt) - analog zum Rohdaten-Tab. ---
            rohdaten_zustand = self.lade_rohdaten_filter_einstellungen_fuer_versuch(
                projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, pfad),
                {
                    "filter": [dict(f) for f in SEM_FILTER_STANDARD_LISTE],
                    "normieren": True,
                    "mikrometer_pro_pixel": 0.84427,
                },
            )
            karten_fuer_filter = karten_prozent if rohdaten_zustand.get("normieren", True) else elementkarten
            pixel_maske = self._sem_wende_filter_an(karten_fuer_filter, rohdaten_zustand.get("filter", []))
            probe_maske = np.sum(np.stack(list(elementkarten.values()), axis=0), axis=0) > 0.0
            if pixel_maske is not None:
                pixel_maske = pixel_maske & probe_maske

            # --- Element-Auswahl: per Dropdown im rechten Panel gewaehlt
            # (zustand["ausgewaehltes_element"]). Ist noch keins gewaehlt
            # oder das gespeicherte Element gibt es im aktuellen Versuch
            # nicht (z.B. Versuchswechsel), faellt automatisch das
            # haeufigste Element (hoechster mittlerer %-Anteil) als
            # Default heran. ---
            aktives_element = zustand.get("ausgewaehltes_element")
            if aktives_element not in elementkarten:
                if karten_prozent:
                    aktives_element = max(
                        karten_prozent.keys(), key=lambda e: float(np.nanmean(karten_prozent[e]))
                    )
                else:
                    aktives_element = next(iter(elementkarten.keys()))
                zustand["ausgewaehltes_element"] = aktives_element

            # Vorherige Colorbar-Achse(n) entfernen, bevor neue gezeichnet
            # werden - sonst haeufen sich bei jedem zeichne()-Aufruf weitere
            # Colorbars an.
            for _alte_achse in farbskala_status["achsen"]:
                try:
                    _alte_achse.remove()
                except Exception:
                    pass
            farbskala_status["achsen"] = []

            bild, x_min, x_max, x_max_linear = self._sem_baue_element_farbbild(
                elementkarten[aktives_element], aktives_element, zustand,
                karten_prozent=karten_prozent, pixel_maske=pixel_maske, probe_maske=probe_maske,
            )
            ax.set_facecolor("white")
            if bild is not None:
                ax.imshow(
                    bild,
                    extent=(-0.5, anzeige_w - 0.5, anzeige_h - 0.5, -0.5),
                    interpolation="nearest",
                )
                # --- Farbskalen-Legende (Colorbar): zeigt, welcher
                # tatsaechliche Element-Prozentwert (nicht mehr die rohe
                # 16-Bit-Grauwert-Naeherung) welcher Farbe entspricht.
                # x_min/x_max kommen bereits als ECHTE %-Werte aus
                # _sem_baue_element_farbbild und sind auf die Pixel
                # INNERHALB DER PROBE beschraenkt - im Linear-Modus steht
                # so oben an der Skala genau der hoechste in der Probe
                # vorkommende %-Wert dieses Elements. ---
                skalierungs_label = SEM_FARBSKALIERUNG_LABELS.get(
                    zustand.get("farbskalierung", "p99"), "p99"
                )
                divider = make_axes_locatable(ax)
                platzhalter_cax = divider.append_axes("right", size="4%", pad=0.12)

                ist_p99 = zustand.get("farbskalierung", "p99") == "p99" and x_max_linear > x_max
                if not ist_p99:
                    # --- Normalfall (Linear-Modus, oder p99 ohne
                    # abgeschnittene Ausreisser): EINE durchgehende
                    # Colorbar wie bisher. ---
                    mappable = mcm.ScalarMappable(
                        norm=Normalize(vmin=x_min, vmax=x_max), cmap=SEM_FARBSKALA_COLORMAP
                    )
                    colorbar = fig.colorbar(mappable, cax=platzhalter_cax)
                    colorbar.set_label(f"{aktives_element}-Anteil in % – {skalierungs_label}", fontsize=9)
                    # WICHTIG: matplotlib waehlt die Tick-Positionen sonst
                    # automatisch "rund" (z.B. 0/10/20/...%) - der hoechste
                    # tatsaechlich vorkommende Wert (x_max) landet dadurch
                    # meist NICHT direkt am oberen Rand der Skala, sondern
                    # etwas darunter. Hier stattdessen ein festes Set von
                    # Ticks setzen, das x_min UND x_max garantiert
                    # einschliesst -> der hoechste Wert steht immer ganz
                    # oben mit eigener Beschriftung.
                    ticks = np.linspace(x_min, x_max, 6)
                    colorbar.set_ticks(ticks)
                    colorbar.ax.yaxis.set_major_formatter(PercentFormatter())
                    colorbar.ax.tick_params(labelsize=8)
                    farbskala_status["achsen"] = [platzhalter_cax]
                else:
                    # --- p99-Modus MIT abgeschnittenen Ausreissern:
                    # unterbrochene Colorbar. Unten (Hauptteil) die normale,
                    # p99-skalierte Farbskala (0 bis p99-Wert) - deren
                    # oberer Rand ist automatisch bereits die Endfarbe der
                    # Colormap. Oben (kleines Segment, gleiche Endfarbe)
                    # steht NUR der tatsaechliche lineare Maximalwert.
                    # Dazwischen ein sichtbarer "Bruch" (Unterbrechung), wie
                    # bei einer klassischen unterbrochenen Achse. Vor und
                    # nach der Unterbrechung hat die Skala dieselbe Farbe
                    # (die Endfarbe der Colormap). ---
                    # WICHTIG: get_position() sofort nach append_axes()
                    # liefert eine falsche (viel zu grosse/verschobene)
                    # Bbox - der AxesDivider berechnet die tatsaechliche,
                    # schmale Position von platzhalter_cax erst waehrend
                    # eines Layout-/Zeichen-Durchlaufs. Deshalb hier zuerst
                    # einen Layout-Durchlauf erzwingen (ohne sichtbares
                    # Zeichnen), damit platz_bbox die ECHTE, schmale
                    # Position/Breite der reservierten Colorbar-Flaeche
                    # enthaelt - sonst landet die Skala viel zu breit und zu
                    # weit links (fast ueber dem ganzen Diagramm) statt als
                    # duenner Streifen ganz rechts.
                    try:
                        fig.draw_without_rendering()
                    except AttributeError:
                        fig.canvas.draw()
                    platz_bbox = platzhalter_cax.get_position()
                    platzhalter_cax.remove()

                    oben_anteil = 0.07   # Hoehenanteil des kleinen Max-Segments
                    luecke_anteil = 0.035  # Hoehenanteil der sichtbaren Unterbrechung
                    haupt_anteil = 1.0 - oben_anteil - luecke_anteil

                    haupt_hoehe = platz_bbox.height * haupt_anteil
                    oben_hoehe = platz_bbox.height * oben_anteil
                    haupt_cax = fig.add_axes([
                        platz_bbox.x0, platz_bbox.y0, platz_bbox.width, haupt_hoehe,
                    ])
                    oben_cax = fig.add_axes([
                        platz_bbox.x0, platz_bbox.y0 + platz_bbox.height - oben_hoehe,
                        platz_bbox.width, oben_hoehe,
                    ])
                    # Aus dem tight_layout()-Management ausnehmen - sonst
                    # wuerde der spaetere fig.tight_layout()-Aufruf die hier
                    # bewusst berechnete Position/Groesse wieder verwerfen.
                    haupt_cax.set_in_layout(False)
                    oben_cax.set_in_layout(False)

                    # Hauptteil: normale p99-Colorbar (x_min bis x_max=p99).
                    mappable = mcm.ScalarMappable(
                        norm=Normalize(vmin=x_min, vmax=x_max), cmap=SEM_FARBSKALA_COLORMAP
                    )
                    colorbar = fig.colorbar(mappable, cax=haupt_cax)
                    colorbar.set_label(f"{aktives_element}-Anteil in % – {skalierungs_label}", fontsize=9)
                    ticks = np.linspace(x_min, x_max, 6)
                    colorbar.set_ticks(ticks)
                    colorbar.ax.yaxis.set_major_formatter(PercentFormatter())
                    colorbar.ax.tick_params(labelsize=8)

                    # Oberes Segment: durchgehend die Endfarbe der Colormap
                    # (dieselbe Farbe wie am oberen Rand des Hauptteils) -
                    # zeigt NUR den echten linearen Maximalwert als
                    # Beschriftung ganz oben.
                    endfarbe = mpl.colormaps[SEM_FARBSKALA_COLORMAP](1.0)
                    oben_cax.set_facecolor(endfarbe)
                    oben_cax.set_xticks([])
                    oben_cax.set_xlim(0, 1)
                    oben_cax.set_ylim(0, 1)
                    oben_cax.yaxis.tick_right()
                    oben_cax.set_yticks([1.0])
                    oben_cax.set_yticklabels([f"{x_max_linear:.1f}%"])
                    oben_cax.tick_params(labelsize=8, length=0)
                    for spine in oben_cax.spines.values():
                        spine.set_visible(True)
                        spine.set_edgecolor("black")
                        spine.set_linewidth(0.8)

                    # Sichtbare Unterbrechung: klassische diagonale
                    # "Bruch"-Striche an der Unterkante des oberen und der
                    # Oberkante des unteren Segments (Standard-Notation fuer
                    # eine unterbrochene Achse/Skala).
                    bruch_kwargs = dict(
                        marker=[(-1, -0.6), (1, 0.6)], markersize=9, linestyle="none",
                        color="black", mec="black", mew=1.1, clip_on=False,
                    )
                    oben_cax.plot([0, 1], [0, 0], transform=oben_cax.transAxes, **bruch_kwargs)
                    haupt_cax.plot([0, 1], [1, 1], transform=haupt_cax.transAxes, **bruch_kwargs)

                    farbskala_status["achsen"] = [haupt_cax, oben_cax]
            ax.set_title(f"{os.path.splitext(os.path.basename(pfad))[0]}  –  Element: {aktives_element}")

            # --- Markierungen: kleiner nummerierter Kreis am Pixel (einzelner
            # Klick) ODER gestricheltes Rechteck (Rechteck-Auswahl per
            # Ziehen) + Box MIT DER (DURCHSCHNITTS-)ELEMENT-ZUSAMMENSETZUNG
            # direkt daneben, die sofort "im Bild" aufpoppt (statt nur in
            # einer Liste rechts) - jede neue Auswahl fuegt eine weitere,
            # eigene Box hinzu. Mini-"x"-Knopf oben rechts an der Box
            # schliesst genau diese eine Markierung wieder (siehe
            # _klick_auf_x_knopf). ---
            for index, marker in enumerate(markierungen, start=1):
                if marker.get("typ") == "rechteck":
                    rect_patch = mpatches.Rectangle(
                        (marker["x0"], marker["y0"]),
                        marker["x1"] - marker["x0"], marker["y1"] - marker["y0"],
                        fill=False, edgecolor="white", linewidth=1.6, linestyle="--", zorder=5,
                    )
                    ax.add_patch(rect_patch)
                    ax.annotate(
                        str(index), (marker["x0"], marker["y0"]), color="white", fontsize=9, fontweight="bold",
                        xytext=(3, -3), textcoords="offset points", ha="left", va="top",
                    )
                else:
                    ax.plot(marker["x"], marker["y"], marker="o", markersize=9,
                            markerfacecolor="none", markeredgecolor="white", markeredgewidth=2)
                    ax.annotate(
                        str(index), (marker["x"], marker["y"]), color="white", fontsize=9, fontweight="bold",
                        ha="center", va="center",
                    )
                zeilen, _breite_pts, _hoehe_pts = _markierung_box_inhalt(marker, index)
                kopf = f"Ø{index}" if marker.get("typ") == "rechteck" else f"M{index}"
                box_text = kopf + "\n" + ("\n".join(zeilen) if zeilen else "alle Elemente ~0 %")
                ax.annotate(
                    box_text,
                    xy=(marker["x"], marker["y"]), xycoords="data",
                    xytext=(16, 16), textcoords="offset points",
                    fontsize=8, color="black", ha="left", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=MUL_TURKIS, alpha=0.92),
                    arrowprops=dict(arrowstyle="->", color=MUL_TURKIS, lw=1.2),
                    zorder=6,
                )
                x_knopf_offset = _x_knopf_offset_pts(marker, index)
                ax.annotate(
                    "✕",
                    xy=(marker["x"], marker["y"]), xycoords="data",
                    xytext=x_knopf_offset, textcoords="offset points",
                    fontsize=8, color="white", fontweight="bold", ha="center", va="center",
                    bbox=dict(boxstyle="circle,pad=0.22", fc="#c0392b", ec="white", lw=1),
                    zorder=7,
                )

            # --- Massstabsbalken in Mikrometer: bevorzugt automatisch aus
            # der H5OINA-Datei des Versuchsordners gelesen, sonst Fallback
            # auf den manuell im Rohdaten-Tab hinterlegten Wert (siehe
            # _sem_ermittle_um_pro_pixel / mikrometer_pro_pixel). ---
            kalibrierung = self.lade_rohdaten_filter_einstellungen_fuer_versuch(
                projekt, methode, self.versuch_schluessel_rohdaten_filter(projekt, pfad),
                {"mikrometer_pro_pixel": 0.84427},
            )
            um_pro_px, _um_pro_px_quelle = self._sem_ermittle_um_pro_pixel(
                pfad, kalibrierung.get("mikrometer_pro_pixel", 0.0), quelle="eds"
            )

            if vorherige_xlim is not None and vorherige_ylim is not None:
                ax.set_xlim(vorherige_xlim)
                ax.set_ylim(vorherige_ylim)
            self._sem_aktualisiere_massstabsbalken(ax, um_pro_px)
            bild_status["gezeichnet"] = True
            fig.tight_layout()
            canvas.draw()
            if not kann_zoom_erhalten:
                # Frischgezeichnetes Bild OHNE uebernommenen Zoom (neuer
                # Versuch bzw. allererstes Zeichnen) = die tatsaechliche
                # "Originalansicht". toolbar.update() ALLEIN reicht nicht -
                # es LEERT nur den Verlaufsspeicher (Zurueck/Vor), setzt
                # aber KEINEN neuen Home-Punkt. Ohne den anschliessenden
                # push_current() ist der Verlauf danach leer, und der
                # Haus-Knopf springt ins Leere (passiert nichts).
                # push_current() legt die gerade gezeichnete Vollansicht
                # als neuen Ausgangspunkt ab. NICHT aufrufen, wenn ein
                # bestehender Zoom beibehalten wurde (kann_zoom_erhalten),
                # sonst wuerde "Home" auf den Zoom statt auf die echte
                # Originalansicht zurueckspringen.
                toolbar.update()
                toolbar.push_current()

        def waehle_versuch(voller_pfad):
            ausgewaehlter_pfad["wert"] = voller_pfad
            for zeile, pfad in zeilen_frames:
                zeile.configure(fg_color=MUL_TURKIS if pfad == voller_pfad else "transparent")
            # Neuer Versuch -> alter Zoom-Ausschnitt UND alte Markierungen
            # machen keinen Sinn mehr (anderes Bild) - beides zuruecksetzen.
            bild_status["gezeichnet"] = False
            markierungen.clear()
            zeichne(zoom_beibehalten=False)
            # ERST zeichnen (laedt aktuelle_daten["karten_normiert"] +
            # element_farben fuer den NEUEN Versuch), DANN die Elementliste
            # bauen - sonst wuerden die Haeufigkeits-Prozentwerte in der
            # Liste noch die Werte des vorherigen Versuchs zeigen.
            if aktualisiere_element_panel["fn"]:
                aktualisiere_element_panel["fn"]()

        for _staub, eintrag, voller_pfad in versuche:
            zeile = ctk.CTkFrame(scroll, fg_color="transparent")
            zeile.pack(fill="x", pady=2)
            label = ctk.CTkLabel(zeile, text=os.path.splitext(eintrag)[0], anchor="w")
            label.pack(side="left", padx=5, fill="x", expand=True)
            zeilen_frames.append((zeile, voller_pfad))
            for widget in (zeile, label):
                widget.bind("<Button-1>", lambda _e, p=voller_pfad: waehle_versuch(p))

        # --- Rechts: Element-Liste ("durchklicken") mit Sichtbar-Checkbox +
        # Farbauswahl je Element ---
        rechte_breite_merker = {"breite": 260}
        griff_rechts = ctk.CTkFrame(haupt_layout, width=6, fg_color=("gray70", "gray25"), cursor="sb_h_double_arrow")
        griff_rechts.pack(side="left", fill="y", padx=(8, 4))

        rechte_container = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=rechte_breite_merker["breite"])
        rechte_container.pack(side="left", fill="y")
        rechte_container.pack_propagate(False)

        rechte_kopfzeile = ctk.CTkFrame(rechte_container, fg_color="transparent")
        rechte_kopfzeile.pack(side="top", fill="x", padx=(5, 0), pady=(0, 5))
        rechts_einklapp_btn = ctk.CTkButton(
            rechte_kopfzeile, text="▶", width=32, fg_color="transparent", border_width=1
        )
        rechts_einklapp_btn.pack(side="left")

        rechte_spalte = ctk.CTkScrollableFrame(rechte_container, fg_color="transparent")
        rechte_spalte.pack(fill="both", expand=True)

        ctk.CTkLabel(
            rechte_spalte, text="Element", font=("Arial", 14, "bold")
        ).pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(
            rechte_spalte,
            text=(
                "Element unten im Dropdown wählen - die Karte wird über eine "
                "Farbskala (Colormap) eingefärbt: dunkel/kalt = niedrige, "
                "hell/warm = hohe Signalintensität. Strg + Mausrad = Diagramm "
                "zoomen. Lupe in der Toolbar: mit linker Maustaste ein Rechteck "
                "aufziehen = reinzoomen, mit rechter Maustaste = rauszoomen."
            ),
            font=("Arial", 10), text_color=("gray30", "gray70"),
            anchor="w", justify="left", wraplength=220,
        ).pack(fill="x", padx=10, pady=(0, 10))

        self._mache_griff_ziehbar(
            griff_rechts, rechte_container, rechte_breite_merker, minimum=180, maximum=500, invertiert=True
        )
        self._mache_spalte_einklappbar(
            rechts_einklapp_btn, rechte_container, rechte_spalte, rechte_breite_merker,
            eingeklappt_text="▶", ausgeklappt_text="◀", breite_eingeklappt=48,
        )

        # --- Sortier-Umschalter: "Häufigkeit" (Standard, absteigend nach
        # mittlerem Flächenanteil %) oder "Alphabetisch". Bestimmt die
        # Reihenfolge der Elemente im Dropdown direkt darunter. Wird
        # projektweit mitgespeichert (wie ausgewaehltes_element/
        # farbskalierung). Bewusst UEBER dem Dropdown platziert, damit
        # sofort klar ist, dass sich die Einstellung auf dessen
        # Element-Reihenfolge bezieht. ---
        SEM_SORTIER_OPTIONEN = ("haeufigkeit", "alphabetisch")
        if zustand.get("element_sortierung") not in SEM_SORTIER_OPTIONEN:
            zustand["element_sortierung"] = "haeufigkeit"

        sortier_zeile = ctk.CTkFrame(rechte_spalte, fg_color="transparent")
        sortier_zeile.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(sortier_zeile, text="Sortieren nach:", font=("Arial", 11)).pack(side="left", padx=(0, 6))

        sortier_button = ctk.CTkButton(sortier_zeile, text="", width=140, fg_color="transparent", border_width=1)

        def _sortier_label():
            return "🔢 Häufigkeit" if zustand["element_sortierung"] == "haeufigkeit" else "🔤 Alphabetisch"

        def _sortierung_umschalten():
            zustand["element_sortierung"] = (
                "alphabetisch" if zustand["element_sortierung"] == "haeufigkeit" else "haeufigkeit"
            )
            sortier_button.configure(text=_sortier_label())
            self.speichere_diagramm_einstellungen(projekt, methode, zustand)
            _baue_element_zeilen()

        sortier_button.configure(text=_sortier_label(), command=_sortierung_umschalten)
        sortier_button.pack(side="left")

        # --- Element-Dropdown: waehlt GENAU EIN Element aus, dessen Karte
        # in der Mitte als Farbskalen-Bild gezeichnet wird (siehe zeichne()/
        # _sem_baue_element_farbbild). Ersetzt die frueheren Mehrfachauswahl-
        # Haekchen samt freier Farbwahl, da nur noch ein Element gleichzeitig
        # ueber die Colormap dargestellt wird. ---
        element_dropdown = ctk.CTkOptionMenu(
            rechte_spalte, values=["–"], fg_color=MUL_TURKIS, dynamic_resizing=False,
        )
        element_dropdown.pack(fill="x", padx=10, pady=(0, 4))

        element_anteil_label = ctk.CTkLabel(
            rechte_spalte, text="", font=("Arial", 11), text_color=("gray30", "gray70"), anchor="w",
        )
        element_anteil_label.pack(fill="x", padx=10, pady=(0, 10))

        # --- Farbskalierung-Umschalter: Linear (Obergrenze = tatsaechliches
        # Maximum) vs. p99 (Obergrenze = 99. Perzentil, kappt Ausreisser fuer
        # mehr Kontrast im relevanten Bereich). Aendert NUR die Obergrenze
        # der Farbnormierung, siehe SEM_FARBSKALIERUNG_OPTIONEN und
        # _sem_baue_element_farbbild. ---
        ctk.CTkFrame(rechte_spalte, height=2, fg_color=("gray75", "gray30")).pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(rechte_spalte, text="Farbskalierung", font=("Arial", 13, "bold")).pack(
            fill="x", padx=10, pady=(0, 3)
        )

        farbskalierung_zeile = ctk.CTkFrame(rechte_spalte, fg_color="transparent")
        farbskalierung_zeile.pack(fill="x", padx=10, pady=(0, 4))

        farbskalierung_button = ctk.CTkButton(
            farbskalierung_zeile, text="", fg_color=MUL_TURKIS, border_width=1,
        )

        def _farbskalierung_label():
            return SEM_FARBSKALIERUNG_LABELS.get(zustand.get("farbskalierung", "p99"), "p99")

        def _farbskalierung_umschalten():
            zustand["farbskalierung"] = (
                "p99" if zustand.get("farbskalierung", "p99") == "linear" else "linear"
            )
            farbskalierung_button.configure(text=f"↔ {_farbskalierung_label()}")
            self.speichere_diagramm_einstellungen(projekt, methode, zustand)
            zeichne()

        farbskalierung_button.configure(text=f"↔ {_farbskalierung_label()}", command=_farbskalierung_umschalten)
        farbskalierung_button.pack(fill="x")

        ctk.CTkLabel(
            rechte_spalte,
            text=(
                "Linear: volles Wertespektrum inkl. Ausreißern (0-Maximum). "
                "p99: obere Grenze = 99. Perzentil, kappt die obersten ~1 % - "
                "macht schwächere Strukturen im Rest der Karte sichtbarer. "
                "Rohdaten und Pixelgeometrie bleiben in beiden Fällen unverändert."
            ),
            font=("Arial", 10), text_color=("gray30", "gray70"),
            anchor="w", justify="left", wraplength=220,
        ).pack(fill="x", padx=10, pady=(4, 10))

        def _element_haeufigkeiten():
            """
            Mittlerer Flächenanteil (%) je Element ueber ALLE Pixel des
            aktuell geladenen Versuchs (aktuelle_daten["karten_normiert"] -
            die auf 100%/Pixel normierten Karten, siehe
            _sem_normalisiere_elementkarten). Liefert {element: mittelwert}.
            Ohne geladene Karten (noch kein Versuch gewaehlt) -> leeres Dict,
            dann wird weiter unten alphabetisch sortiert.
            """
            karten = aktuelle_daten.get("karten_normiert")
            if not karten:
                return {}
            return {element: float(np.nanmean(karte)) for element, karte in karten.items()}

        def _element_im_dropdown_gewaehlt(gewaehltes_element):
            zustand["ausgewaehltes_element"] = gewaehltes_element
            self.speichere_diagramm_einstellungen(projekt, methode, zustand)
            _aktualisiere_anteil_label()
            zeichne()

        def _aktualisiere_anteil_label():
            haeufigkeiten = _element_haeufigkeiten()
            element = zustand.get("ausgewaehltes_element")
            anteil = haeufigkeiten.get(element) if element else None
            element_anteil_label.configure(
                text=f"Ø Flächenanteil: {anteil:.1f} %" if anteil is not None else ""
            )

        def _baue_element_zeilen():
            """
            Befuellt das Element-Dropdown mit den Elementen des aktuell
            geladenen Versuchs (sortiert nach zustand["element_sortierung"])
            und waehlt das in zustand["ausgewaehltes_element"] hinterlegte
            Element an (Fallback: haeufigstes bzw. erstes Element - siehe
            auch der analoge Fallback direkt in zeichne()).
            """
            alle_elemente = list(zustand.get("element_farben", {}).keys())
            if not alle_elemente:
                element_dropdown.configure(values=["–"])
                element_dropdown.set("–")
                element_anteil_label.configure(text="")
                return

            haeufigkeiten = _element_haeufigkeiten()
            if zustand["element_sortierung"] == "haeufigkeit" and haeufigkeiten:
                # Absteigend nach mittlerem %-Anteil; Elemente ohne Wert
                # (sollte praktisch nicht vorkommen) landen ganz hinten.
                elemente = sorted(
                    alle_elemente, key=lambda e: -haeufigkeiten.get(e, -1.0)
                )
            else:
                elemente = sorted(alle_elemente)

            element_dropdown.configure(values=elemente, command=_element_im_dropdown_gewaehlt)

            aktuelles_element = zustand.get("ausgewaehltes_element")
            if aktuelles_element not in elemente:
                aktuelles_element = elemente[0]
                zustand["ausgewaehltes_element"] = aktuelles_element
            element_dropdown.set(aktuelles_element)
            _aktualisiere_anteil_label()

        aktualisiere_element_panel["fn"] = _baue_element_zeilen

        # --- Ersten Versuch automatisch auswaehlen ---
        waehle_versuch(versuche[0][2])

    # ------------------------------------------------------------------
    # TAB: ERGEBNISSE (TGA) - Relative Masse + Reaktionskinetik pro Versuch
    # ------------------------------------------------------------------
    def baue_ergebnisse_tab_tga(self, parent, projekt, methode):
        """
        TGA-Gegenstück zu baue_ergebnisse_tab (EMI): zeigt pro (bereits
        verarbeitetem) Versuch links die Relative Masse und rechts die
        Reaktionskinetik, jeweils über der Temperatur, aus der zugehörigen
        <versuch>_results.parquet-Datei (erzeugt von TGA_calculation.py).

        Ist die CaO-Zugabe für den Versuch im Google Sheet hinterlegt (siehe
        hole_tga_parameter_fuer_versuch), werden beide Kurven automatisch auf
        "% of EAFD" umgerechnet (CaO-Anteil rausgerechnet, siehe
        _tga_auf_eafd_basis) - genau wie in euren Referenzdiagrammen. Ohne
        CaO-Wert bleibt es bei "% of mixture" (Rohwert aus der Waage).

        Gleiches Layout/Bedienkonzept wie bei EMI: Seitenleiste mit
        Versuchsauswahl + Settings-Button (Titel/Achsenbeschriftungen/
        -bereiche editierbar, dauerhaft gespeichert), Download-Buttons pro
        Diagrammausschnitt.
        """
        versuche = self.liste_versuche(projekt, methode)
        verarbeitete_versuche = [
            (staub, eintrag, voller_pfad)
            for staub, eintrag, voller_pfad in versuche
            if self.ist_versuch_verarbeitet(os.path.dirname(voller_pfad), eintrag, methode)
        ]

        if not verarbeitete_versuche:
            ctk.CTkLabel(
                parent, text="Noch keine verarbeiteten Versuche (siehe Rohdaten-Tab -> Berechnen)."
            ).pack(pady=20)
            return

        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except ImportError:
            ctk.CTkLabel(
                parent,
                text="matplotlib ist nicht installiert - 'pip install matplotlib' im GUI-Environment nötig.",
                wraplength=560,
            ).pack(pady=20)
            return

        zustand_standard = {
            "versuch_name": None,
            # --- Diagramm 1 (links, Relative Masse) ---
            "title_masse": "Relative Mass",
            "label_x_masse": "Temperature [°C]",
            "label_y_masse": "Relative Mass\n[% of EAFD]",
            "diagramm1_x_spalte": "temperature_C",
            "diagramm1_y_spalte": "dm_filtered_pct",
            # --- Diagramm 2 (rechts, Reaktionskinetik) ---
            "title_kinetik": "Reaction Kinetics",
            "label_x_kinetik": "Temperature [°C]",
            "label_y_kinetik": "Reaction Kinetics\n[%/min of EAFD]",
            "diagramm2_x_spalte": "temperature_C",
            "diagramm2_y_spalte": "dmdt_filtered_pctmin",

            # ==========================================================
            # Darstellung - jede Gruppe unten ist pro Seite (links/rechts)
            # einzeln einstellbar ODER gemeinsam ueber "Anwenden auf: Beide"
            # (siehe oeffne_settings/_seiten_dropdown). "_links" = Diagramm 1
            # (Relative Masse), "_rechts" = Diagramm 2 (Reaktionskinetik).
            # ==========================================================

            # --- Diagrammstil (Preset: Grid/Achsen/Tick/Hintergrund je Achse) ---
            "diagramm_stil_links": "Classic",
            "diagramm_stil_rechts": "Classic",
            "gitter_anzeigen_links": True,
            "gitter_anzeigen_rechts": True,
            "minor_grid_anzeigen_links": False,
            "minor_grid_anzeigen_rechts": False,
            "obere_achse_ausblenden_links": True,
            "obere_achse_ausblenden_rechts": True,
            "rechte_achse_ausblenden_links": True,
            "rechte_achse_ausblenden_rechts": True,
            "tick_richtung_links": "out",
            "tick_richtung_rechts": "out",
            "tick_laenge_links": 5,
            "tick_laenge_rechts": 5,
            "hintergrund_diagramm_links": "#ffffff",
            "hintergrund_diagramm_rechts": "#ffffff",

            # --- Farbe (Linienfarbe je Diagramm) ---
            "linienfarbe": MUL_TURKIS,      # Diagramm 1 (links)
            "linienfarbe2": MUL_TURKIS,     # Diagramm 2 (rechts)

            # --- Schrift (Schriftgroessen je Diagramm) ---
            "schriftgroesse_titel_links": 13,
            "schriftgroesse_titel_rechts": 13,
            "schriftgroesse_achsen_links": 11,
            "schriftgroesse_achsen_rechts": 11,
            "schriftgroesse_ticks_links": 10,
            "schriftgroesse_ticks_rechts": 10,

            # --- Linien (Breite + Stil je Diagramm) ---
            "linienbreite": 1.5,            # Diagramm 1 (links)
            "linienbreite2": 1.5,           # Diagramm 2 (rechts)
            "linienstil_links": "-",
            "linienstil_rechts": "-",

            # --- Legende (Anzeige je Diagramm, Position gemeinsam) ---
            "legende_anzeigen_links": False,
            "legende_anzeigen_rechts": False,
            "legende_position": PLOT_LEGENDE_UNTER_ACHSE,

            # --- Hintergrund der gesamten Figure (ein gemeinsames Canvas) ---
            "hintergrund_figure": "#ffffff",
            "transparenter_hintergrund": False,

            # --- Export ---
            "export_dpi": 300,
            "export_format": "png",
            "tight_layout": True,
        }
        zustand = self.lade_diagramm_einstellungen(projekt, methode, zustand_standard)

        # --- Anzeige-Text (Material – Kommentar) <-> tatsächlicher Versuchsname ---
        anzeige_zu_name = {}
        anzeige_werte = []
        for _s, eintrag, _p in verarbeitete_versuche:
            versuch_name = os.path.splitext(eintrag)[0]
            sheet_name = self._tga_versuchsname_fuer_sheet(eintrag)
            parameter = self.hole_tga_parameter_fuer_versuch(sheet_name)
            if parameter and (parameter.get("material") or parameter.get("kommentar")):
                material = parameter.get("material") or "-"
                kommentar = parameter.get("kommentar") or "-"
                anzeige = f"{material} – {kommentar}"
            else:
                anzeige = versuch_name
            if anzeige in anzeige_zu_name:
                anzeige = f"{anzeige} [{versuch_name}]"
            anzeige_zu_name[anzeige] = versuch_name
            anzeige_werte.append(anzeige)

        # --- Layout: Seitenleiste links, Diagramme rechts (wie bei EMI) ---
        haupt_layout = ctk.CTkFrame(parent, fg_color="transparent")
        haupt_layout.pack(fill="both", expand=True, padx=10, pady=10)

        seitenleiste = ctk.CTkFrame(haupt_layout, fg_color="transparent", width=170)
        seitenleiste.pack(side="left", fill="y", padx=(0, 15))
        seitenleiste.pack_propagate(False)

        ctk.CTkLabel(seitenleiste, text="Versuch:", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        plot_frame = ctk.CTkFrame(haupt_layout, fg_color="transparent")
        plot_frame.pack(side="left", fill="both", expand=True)

        button_leiste = ctk.CTkFrame(plot_frame, fg_color="transparent")
        button_leiste.pack(side="top", fill="x")
        button_leiste.grid_columnconfigure(0, weight=1)
        button_leiste.grid_columnconfigure(1, weight=1)

        def _subplots_fuer_layout(layout):
            # Die Figure richtet sich nach dem tatsächlich verfügbaren Platz
            # des Ergebnis-Tabs, statt eine fixe Größe zu erzwingen.
            haupt_layout.update_idletasks()
            breite_px = max(plot_frame.winfo_width(), 650)
            hoehe_px = max(plot_frame.winfo_height() - button_leiste.winfo_height(), 480)
            dpi = 100
            if layout == "vertical":
                # Bei zwei übereinanderliegenden Plots darf die Figure nicht
                # die komplette (oft sehr breite) Fensterbreite einnehmen.
                # Die Breite wird deshalb aus der Bildschirmhöhe abgeleitet;
                # so erhalten beide Diagramme genug Höhe statt gestaucht zu
                # wirken.
                bildschirm_hoehe = max(self.winfo_screenheight() - 180, 600)
                hoehe_px = min(max(hoehe_px, 560), bildschirm_hoehe)
                breite_px = min(breite_px, int(hoehe_px * 0.95))
                return plt.subplots(
                    2, 1, figsize=(breite_px / dpi, hoehe_px / dpi),
                    gridspec_kw={"hspace": 0.65},
                )
            return plt.subplots(1, 2, figsize=(breite_px / dpi, hoehe_px / dpi))

        fig, (ax_masse, ax_kinetik) = _subplots_fuer_layout(
            zustand.get("diagramm_layout", "horizontal")
        )
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        def _eintrag_info_fuer(versuch_name):
            return next(
                (s, e, p) for s, e, p in verarbeitete_versuche
                if os.path.splitext(e)[0] == versuch_name
            )

        def _versuch_schluessel(versuch_name):
            """Eindeutiger Schluessel dieses Versuchs, unter dem seine
            individuelle Diagramm-Darstellung gespeichert wird."""
            if not versuch_name:
                return None
            try:
                _s, _e, voller_pfad = _eintrag_info_fuer(versuch_name)
            except StopIteration:
                return versuch_name
            return self.versuch_schluessel_rohdaten_filter(projekt, voller_pfad)

        def _speichern():
            """Speichert die aktuelle Darstellung NUR fuer den gerade
            angezeigten Versuch - Aenderungen an Versuch 1 wirken sich damit
            nicht mehr automatisch auf andere Versuche aus. Siehe
            _speichern_fuer_alle() fuer den Button 'Fuer alle Versuche
            uebernehmen', der bewusst ALLE Versuche auf einmal aktualisiert."""
            self.speichere_diagramm_einstellungen_fuer_versuch(
                projekt, methode, _versuch_schluessel(zustand.get("versuch_name")), zustand,
            )

        def _speichern_fuer_alle():
            """Uebernimmt die aktuelle Darstellung fuer ALLE Versuche dieser
            Methode (Button 'Fuer alle Versuche uebernehmen')."""
            alle_schluessel = [
                _versuch_schluessel(os.path.splitext(eintrag)[0])
                for _s, eintrag, _p in verarbeitete_versuche
            ]
            self.speichere_diagramm_einstellungen_fuer_alle(projekt, methode, alle_schluessel, zustand)

        def zeichne():
            versuch_name = zustand["versuch_name"]
            if not versuch_name:
                return
            eintrag_info = _eintrag_info_fuer(versuch_name)
            zustand["_aktueller_raw_data_ordner"] = os.path.dirname(eintrag_info[2])
            self.zeichne_ergebnis_plot_tga(eintrag_info, fig, ax_masse, ax_kinetik, canvas, zustand)

        def setze_layout(layout, speichern=True):
            """Ersetzt Figure und Canvas vollständig, damit keine alten Achsen überlappen."""
            nonlocal fig, canvas, ax_masse, ax_kinetik
            zustand["diagramm_layout"] = layout
            canvas.get_tk_widget().destroy()
            plt.close(fig)
            fig, (ax_masse, ax_kinetik) = _subplots_fuer_layout(layout)
            canvas = FigureCanvasTkAgg(fig, master=plot_frame)
            canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
            zeichne()
            if speichern:
                _speichern()

        def speichere_diagrammausschnitt(ax, dateiname_teil):
            versuch_name = zustand["versuch_name"]
            raw_data_ordner = zustand.get("_aktueller_raw_data_ordner")
            if not versuch_name or not raw_data_ordner:
                return
            try:
                ziel_ordner = self.diagramm_ordner_fuer(raw_data_ordner)
                zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
                dateiformat = zustand.get("export_format", "png") or "png"
                dpi = int(zustand.get("export_dpi", 300) or 300)
                transparent = bool(zustand.get("transparenter_hintergrund", False))
                dateiname = f"{self._sanitiere_versuchsnamen(versuch_name)}_{dateiname_teil}_{zeitstempel}.{dateiformat}"
                ziel_pfad = os.path.join(ziel_ordner, dateiname)
                canvas.draw()
                bbox = ax.get_tightbbox(canvas.get_renderer()).transformed(fig.dpi_scale_trans.inverted())
                fig.savefig(ziel_pfad, dpi=dpi, bbox_inches=bbox, format=dateiformat, transparent=transparent)
                self._status(f"Diagramm gespeichert: {ziel_pfad}", "#00ff88")
            except Exception as e:
                messagebox.showerror("Download-Fehler", f"Konnte Diagramm nicht speichern:\n{e}")

        def lade_masse_herunter():
            speichere_diagrammausschnitt(ax_masse, "relative_masse")

        def lade_kinetik_herunter():
            speichere_diagrammausschnitt(ax_kinetik, "reaktionskinetik")

        download_button_links = ctk.CTkButton(
            button_leiste, text="↓", width=30, height=30, corner_radius=15,
            fg_color=MUL_TURKIS, hover_color=MUL_DUNKEL, font=("Arial", 14, "bold"),
            command=lade_masse_herunter,
        )
        download_button_links.grid(row=0, column=0, sticky="e", padx=(0, 6), pady=(0, 4))

        download_button_rechts = ctk.CTkButton(
            button_leiste, text="↓", width=30, height=30, corner_radius=15,
            fg_color=MUL_TURKIS, hover_color=MUL_DUNKEL, font=("Arial", 14, "bold"),
            command=lade_kinetik_herunter,
        )
        download_button_rechts.grid(row=0, column=1, sticky="e", padx=(6, 0), pady=(0, 4))

        def zeige_versuch(anzeige):
            versuch_name = anzeige_zu_name.get(anzeige, anzeige)
            # Anordnung (horizontal/vertikal) bleibt beim Versuchswechsel wie
            # aktuell angezeigt bestehen, statt pro Versuch zu "springen".
            layout_backup = zustand.get("diagramm_layout", "horizontal")
            neuer_zustand = self.lade_diagramm_einstellungen_fuer_versuch(
                projekt, methode, _versuch_schluessel(versuch_name), zustand_standard,
            )
            neuer_zustand["versuch_name"] = versuch_name
            neuer_zustand["diagramm_layout"] = layout_backup
            zustand.clear()
            zustand.update(neuer_zustand)
            zeichne()

        def als_zahl_oder_none(text):
            text = text.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None

        def live_kopplung(entry, label_widget=None, praefix="", suffix="", live_chart=None):
            """Bei jedem Tastendruck im Feld: (a) optional Header-Label im
            Dialog aktualisieren, (b) optional 'live_chart(text)' aufrufen,
            das direkt das ECHTE Diagramm (nicht nur den Dialog) anpasst -
            z.B. Titel/Achsenbeschriftung sofort auf dem Canvas nachziehen,
            ohne erst auf "Übernehmen" klicken zu müssen."""
            def aktualisieren(_event=None):
                wert = entry.get().strip()
                if label_widget is not None:
                    label_widget.configure(text=f"{praefix}{wert or '...'}{suffix}")
                if live_chart is not None and wert:
                    try:
                        live_chart(wert)
                        canvas.draw_idle()
                    except Exception:
                        pass
            entry.bind("<KeyRelease>", aktualisieren)

        def _feld(parent, label_text, wert, breite=110, platzhalter=None):
            """Kleine Helper-Zeile 'Label: [Entry]' - spart Wiederholung unten."""
            zeile = ctk.CTkFrame(parent, fg_color="transparent")
            zeile.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(zeile, text=label_text, width=breite, anchor="w").pack(side="left")
            eingabe = ctk.CTkEntry(zeile, placeholder_text=platzhalter)
            eingabe.insert(0, "" if wert is None else str(wert))
            eingabe.pack(side="left", padx=(5, 0), fill="x", expand=True)
            return eingabe

        def _hex_feld(parent, label_text, wert, breite=110, platzhalter="#ffffff", bei_aenderung=None):
            """Wie _feld(), aber mit einem 🎨-Knopf daneben: öffnet den
            System-Farbwähler (Palette + Farbverlauf, per Mauszeiger wählbar)
            und schreibt den gewählten Hex-Code direkt ins Eingabefeld.
            'bei_aenderung' wird (falls angegeben) sofort nach Auswahl im
            Farbwähler aufgerufen, damit die Änderung z.B. live im Diagramm
            sichtbar wird, ohne erst auf "Übernehmen" klicken zu müssen."""
            zeile = ctk.CTkFrame(parent, fg_color="transparent")
            zeile.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(zeile, text=label_text, width=breite, anchor="w").pack(side="left")
            eingabe = ctk.CTkEntry(zeile, placeholder_text=platzhalter)
            eingabe.insert(0, "" if wert is None else str(wert))
            eingabe.pack(side="left", padx=(5, 5), fill="x", expand=True)

            def waehle_farbe():
                start = eingabe.get().strip() or platzhalter
                # WICHTIG: Ohne explizites 'parent=' haengt sich der System-
                # Farbwaehler an das (unsichtbare) Haupt-Root-Fenster von
                # CustomTkinter statt an dieses Plot-Settings-Fenster. Das
                # fuehrte zu dem Bug, dass beim Klick auf die Palette
                # faelschlicherweise das ganze Hauptfenster mitgeschlossen
                # wurde. Mit 'parent=eltern_fenster' gehoert der Farbwaehler
                # sauber zu diesem Dialog (oeffnet direkt daneben/darauf und
                # ist normal per Titelleiste verschiebbar).
                eltern_fenster = zeile.winfo_toplevel()

                # Das Plot-Settings-Fenster ist bewusst "-topmost" (immer im
                # Vordergrund). Der Farbwaehler ist das NICHT - er wuerde
                # sonst dahinter geoeffnet und wirkt wie eingefroren/
                # verschwunden. Daher -topmost kurz deaktivieren, waehrend
                # der Farbwaehler offen ist, und danach wiederherstellen.
                war_topmost = False
                try:
                    war_topmost = bool(eltern_fenster.attributes("-topmost"))
                    if war_topmost:
                        eltern_fenster.attributes("-topmost", False)
                except Exception:
                    pass

                try:
                    _rgb, hex_code = colorchooser.askcolor(
                        color=start, title="Farbe wählen", parent=eltern_fenster,
                    )
                except Exception:
                    hex_code = None
                finally:
                    if war_topmost:
                        try:
                            eltern_fenster.attributes("-topmost", True)
                            eltern_fenster.lift()
                        except Exception:
                            pass

                if hex_code:
                    eingabe.delete(0, "end")
                    eingabe.insert(0, hex_code)
                    if bei_aenderung is not None:
                        bei_aenderung()

            ctk.CTkButton(
                zeile, text="🎨", width=32, fg_color=MUL_DUNKEL, hover_color=MUL_TURKIS,
                command=waehle_farbe,
            ).pack(side="left")
            return eingabe

        def _abschnitt(parent, titel):
            ctk.CTkLabel(parent, text=titel, anchor="w", font=("Arial", 13, "bold")).pack(
                fill="x", padx=10, pady=(14, 5)
            )

        def _seiten_dropdown(parent, label_text="Anwenden auf:"):
            """Zeile 'Anwenden auf: [Beide/Links/Rechts]' - steuert, ob eine
            Formatierungsgruppe (Diagrammstil/Farbe/Schrift/Linien/Achsen/
            Legende/...) für Diagramm 1 (links), Diagramm 2 (rechts) oder
            beide gleichzeitig gesetzt wird."""
            zeile = ctk.CTkFrame(parent, fg_color="transparent")
            zeile.pack(fill="x", padx=10, pady=(0, 6))
            ctk.CTkLabel(zeile, text=label_text, anchor="w").pack(side="left")
            dd = ctk.CTkOptionMenu(zeile, values=PLOT_SEITEN_OPTIONEN, fg_color=MUL_DUNKEL, width=110)
            dd.set("Beide")
            dd.pack(side="right")
            return dd

        def oeffne_darstellung():
            dialog = ctk.CTkToplevel(self)
            dialog.title("Darstellung")
            dialog.geometry("420x560")
            dialog.minsize(360, 380)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()
            inhalt = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
            inhalt.pack(fill="both", expand=True, padx=15, pady=15)
            ctk.CTkLabel(inhalt, text="Anordnung der Diagramme:", anchor="w").pack(fill="x", pady=(0, 8))
            layout_labels = {
                "Horizontal (nebeneinander)": "horizontal",
                "Vertikal (übereinander)": "vertical",
            }
            layout_werte = {wert: label for label, wert in layout_labels.items()}

            def _layout_gewaehlt(_=None):
                setze_layout(layout_labels.get(layout_dropdown.get(), "horizontal"))

            layout_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(layout_labels), fg_color=MUL_TURKIS, command=_layout_gewaehlt,
            )
            layout_dropdown.set(layout_werte.get(
                zustand.get("diagramm_layout", "horizontal"), "Horizontal (nebeneinander)"
            ))
            layout_dropdown.pack(fill="x", pady=(0, 12))

            def abschnitt(text):
                ctk.CTkLabel(inhalt, text=text, anchor="w", font=("Arial", 13, "bold")).pack(
                    fill="x", pady=(10, 4)
                )

            def feld(label, wert):
                ctk.CTkLabel(inhalt, text=label, anchor="w").pack(fill="x")
                eingabe = ctk.CTkEntry(inhalt)
                eingabe.insert(0, str(wert))
                eingabe.pack(fill="x", pady=(2, 8))
                return eingabe

            abschnitt("Anwenden auf")
            seiten_auswahl = {
                "Diagramm 1 (links)": "links",
                "Diagramm 2 (rechts)": "rechts",
                "Beide Diagramme": "beide",
            }
            seite = ctk.CTkOptionMenu(inhalt, values=list(seiten_auswahl), fg_color=MUL_DUNKEL)
            seite.set("Beide Diagramme")
            seite.pack(fill="x", pady=(0, 8))

            abschnitt("Schnellvorlage")
            stil = ctk.CTkOptionMenu(inhalt, values=PLOT_STIL_NAMEN, fg_color=MUL_TURKIS)
            stil.set(zustand.get("diagramm_stil_links", "Classic"))
            stil.pack(fill="x", pady=(0, 8))

            abschnitt("Linie und Schrift")
            farbe = _hex_feld(inhalt, "Linienfarbe:", zustand["linienfarbe"], bei_aenderung=lambda: _anwendung())
            linienbreite = feld("Linienbreite:", zustand["linienbreite"])
            ctk.CTkLabel(inhalt, text="Linienart:", anchor="w").pack(fill="x")
            linienstil = ctk.CTkOptionMenu(inhalt, values=list(PLOT_LINIENSTIL_LABEL_ZU_WERT), fg_color=MUL_TURKIS)
            linienstil.set(PLOT_LINIENSTIL_WERT_ZU_LABEL.get(zustand["linienstil_links"], "────"))
            linienstil.pack(fill="x", pady=(2, 8))
            titelgroesse = feld("Schriftgroesse Titel:", zustand["schriftgroesse_titel_links"])
            achsengroesse = feld("Schriftgroesse Achsen:", zustand["schriftgroesse_achsen_links"])
            tickgroesse = feld("Schriftgroesse Ticks:", zustand["schriftgroesse_ticks_links"])
            titelfarbe = _hex_feld(inhalt, "Titelfarbe:", zustand.get("schriftfarbe_titel_links", "#000000"), bei_aenderung=lambda: _anwendung())

            abschnitt("Achsen, Hintergrund und Legende")
            grid = ctk.BooleanVar(value=zustand["gitter_anzeigen_links"])
            grid_checkbox = ctk.CTkCheckBox(inhalt, text="Gitter anzeigen", variable=grid)
            grid_checkbox.pack(anchor="w", pady=3)
            minor_grid = ctk.BooleanVar(value=zustand["minor_grid_anzeigen_links"])
            minor_grid_checkbox = ctk.CTkCheckBox(inhalt, text="Minor Grid anzeigen", variable=minor_grid)
            minor_grid_checkbox.pack(anchor="w", pady=3)
            obere_achse = ctk.BooleanVar(value=zustand["obere_achse_ausblenden_links"])
            obere_achse_checkbox = ctk.CTkCheckBox(inhalt, text="Obere Achse ausblenden", variable=obere_achse)
            obere_achse_checkbox.pack(anchor="w", pady=3)
            rechte_achse = ctk.BooleanVar(value=zustand["rechte_achse_ausblenden_links"])
            rechte_achse_checkbox = ctk.CTkCheckBox(inhalt, text="Rechte Achse ausblenden", variable=rechte_achse)
            rechte_achse_checkbox.pack(anchor="w", pady=3)
            diagramm_hg = _hex_feld(inhalt, "Diagramm-Hintergrund:", zustand["hintergrund_diagramm_links"], bei_aenderung=lambda: _anwendung())
            figure_hg = _hex_feld(inhalt, "Gesamter Hintergrund:", zustand["hintergrund_figure"], bei_aenderung=lambda: _anwendung())
            legende = ctk.BooleanVar(value=zustand["legende_anzeigen_links"])
            legende_checkbox = ctk.CTkCheckBox(inhalt, text="Legende anzeigen", variable=legende)
            legende_checkbox.pack(anchor="w", pady=3)

            abschnitt("Download")
            dpi = ctk.CTkOptionMenu(inhalt, values=["150", "300", "600", "1200"], fg_color=MUL_TURKIS)
            dpi.set(str(zustand.get("export_dpi", 300)))
            dpi.pack(fill="x", pady=(2, 5))
            dateiformat = ctk.CTkOptionMenu(inhalt, values=["png", "pdf", "svg"], fg_color=MUL_TURKIS)
            dateiformat.set(zustand.get("export_format", "png"))
            dateiformat.pack(fill="x", pady=(2, 8))
            transparent = ctk.BooleanVar(value=bool(zustand.get("transparenter_hintergrund", False)))
            transparent_checkbox = ctk.CTkCheckBox(inhalt, text="Transparenter Hintergrund beim Download", variable=transparent)
            transparent_checkbox.pack(anchor="w", pady=3)

            def _seiten():
                auswahl = seiten_auswahl[seite.get()]
                return ["links", "rechts"] if auswahl == "beide" else [auswahl]

            def _anwendung():
                for s in _seiten():
                    suffix = s
                    neue_linienfarbe = farbe.get().strip()
                    if re.fullmatch(r"#[0-9A-Fa-f]{6}", neue_linienfarbe):
                        zustand["linienfarbe" if s == "links" else "linienfarbe2"] = neue_linienfarbe
                    zustand["linienbreite" if s == "links" else "linienbreite2"] = als_zahl_oder_none(linienbreite.get()) or 1.5
                    zustand[f"linienstil_{suffix}"] = PLOT_LINIENSTIL_LABEL_ZU_WERT.get(linienstil.get(), "-")
                    zustand[f"schriftgroesse_titel_{suffix}"] = als_zahl_oder_none(titelgroesse.get()) or 13
                    zustand[f"schriftgroesse_achsen_{suffix}"] = als_zahl_oder_none(achsengroesse.get()) or 11
                    zustand[f"schriftgroesse_ticks_{suffix}"] = als_zahl_oder_none(tickgroesse.get()) or 10
                    neue_titelfarbe = titelfarbe.get().strip()
                    if re.fullmatch(r"#[0-9A-Fa-f]{6}", neue_titelfarbe):
                        zustand[f"schriftfarbe_titel_{suffix}"] = neue_titelfarbe
                    zustand[f"gitter_anzeigen_{suffix}"] = bool(grid.get())
                    zustand[f"minor_grid_anzeigen_{suffix}"] = bool(minor_grid.get())
                    zustand[f"obere_achse_ausblenden_{suffix}"] = bool(obere_achse.get())
                    zustand[f"rechte_achse_ausblenden_{suffix}"] = bool(rechte_achse.get())
                    neuer_diagramm_hg = diagramm_hg.get().strip()
                    if re.fullmatch(r"#[0-9A-Fa-f]{6}", neuer_diagramm_hg):
                        zustand[f"hintergrund_diagramm_{suffix}"] = neuer_diagramm_hg
                    zustand[f"legende_anzeigen_{suffix}"] = bool(legende.get())
                neuer_figure_hg = figure_hg.get().strip()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}", neuer_figure_hg):
                    zustand["hintergrund_figure"] = neuer_figure_hg
                zustand["export_dpi"] = int(float(dpi.get()))
                zustand["export_format"] = dateiformat.get()
                zustand["transparenter_hintergrund"] = bool(transparent.get())
                zeichne()
                canvas.draw()
                _speichern()
                self._status("Darstellung automatisch gespeichert (nur dieser Versuch).", "#00ff88")

            auto_update_auftrag = {"id": None}

            def _sofort_anwenden(*_args):
                """Erst nach Abschluss des Klick-/Dropdown-Events zeichnen.
                Das stellt sicher, dass etwa ein gerade angeklicktes Grid auch
                wirklich mit seinem neuen Wert in der Vorschau landet."""
                if auto_update_auftrag["id"] is not None:
                    try:
                        dialog.after_cancel(auto_update_auftrag["id"])
                    except Exception:
                        pass
                auto_update_auftrag["id"] = dialog.after(30, _anwendung)

            def _enter_anwenden(_event=None):
                # Enter bestätigt ohne Verzögerung und verhindert, dass das
                # Eingabefeld die Aktion anschließend wieder überschreibt.
                _anwendung()
                return "break"

            def _setze_feld(eingabe, wert):
                eingabe.delete(0, "end")
                eingabe.insert(0, str(wert))

            def _lade_seitenwerte(_=None):
                """Zeigt beim Wechsel die bereits gespeicherten individuellen Werte."""
                s = "rechts" if seiten_auswahl[seite.get()] == "rechts" else "links"
                _setze_feld(farbe, zustand["linienfarbe2" if s == "rechts" else "linienfarbe"])
                _setze_feld(linienbreite, zustand["linienbreite2" if s == "rechts" else "linienbreite"])
                linienstil.set(PLOT_LINIENSTIL_WERT_ZU_LABEL.get(zustand[f"linienstil_{s}"], next(iter(PLOT_LINIENSTIL_LABEL_ZU_WERT))))
                _setze_feld(titelgroesse, zustand[f"schriftgroesse_titel_{s}"])
                _setze_feld(achsengroesse, zustand[f"schriftgroesse_achsen_{s}"])
                _setze_feld(tickgroesse, zustand[f"schriftgroesse_ticks_{s}"])
                _setze_feld(titelfarbe, zustand.get(f"schriftfarbe_titel_{s}", "#000000"))
                grid.set(bool(zustand[f"gitter_anzeigen_{s}"]))
                minor_grid.set(bool(zustand[f"minor_grid_anzeigen_{s}"]))
                obere_achse.set(bool(zustand[f"obere_achse_ausblenden_{s}"]))
                rechte_achse.set(bool(zustand[f"rechte_achse_ausblenden_{s}"]))
                _setze_feld(diagramm_hg, zustand[f"hintergrund_diagramm_{s}"])
                legende.set(bool(zustand[f"legende_anzeigen_{s}"]))
                stil.set(zustand.get(f"diagramm_stil_{s}", "Classic"))

            def _stil_anwenden(_=None):
                preset = PLOT_STIL_PRESETS.get(stil.get(), {})
                for s in _seiten():
                    zustand[f"diagramm_stil_{s}"] = stil.get()
                    for schluessel, wert in preset.items():
                        if schluessel == "hintergrund_figure":
                            zustand[schluessel] = wert
                        elif schluessel in ("gitter_anzeigen", "minor_grid_anzeigen", "obere_achse_ausblenden", "rechte_achse_ausblenden", "tick_richtung", "hintergrund_diagramm"):
                            zustand[f"{schluessel}_{s}"] = wert
                _lade_seitenwerte()
                zeichne()
                canvas.draw()
                _speichern()

            # Auswahlfelder gelten sofort: Vorschau und spätere Downloads
            # verwenden ohne weiteren Klick denselben gespeicherten Zustand.
            seite.configure(command=_lade_seitenwerte)
            stil.configure(command=_stil_anwenden)
            linienstil.configure(command=lambda _=None: _anwendung())
            dpi.configure(command=lambda _=None: _anwendung())
            dateiformat.configure(command=lambda _=None: _anwendung())
            for checkbox in (grid_checkbox, minor_grid_checkbox, obere_achse_checkbox,
                             rechte_achse_checkbox, legende_checkbox, transparent_checkbox):
                checkbox.configure(command=_anwendung)

            for eingabe in (farbe, linienbreite, titelgroesse, achsengroesse,
                            tickgroesse, titelfarbe, diagramm_hg, figure_hg):
                eingabe.bind("<FocusOut>", _sofort_anwenden)
                eingabe.bind("<Return>", _enter_anwenden)
                eingabe.bind("<KeyRelease>", _sofort_anwenden)

            def _fuer_alle_uebernehmen():
                if not messagebox.askyesno(
                    "Für alle übernehmen",
                    "Die aktuelle Darstellung wird für ALLE "
                    f"{len(verarbeitete_versuche)} Versuche dieser Methode "
                    "übernommen und überschreibt dabei auch bereits "
                    "individuell abweichend eingestellte Werte einzelner "
                    "Versuche.\n\nFortfahren?",
                    parent=dialog,
                ):
                    return
                _anwendung()
                _speichern_fuer_alle()
                self._status("Darstellung für alle Versuche übernommen.", "#00ff88")

            ctk.CTkButton(inhalt, text="Übernehmen & Vorschau aktualisieren", fg_color=MUL_TURKIS, command=_anwendung).pack(fill="x", pady=(12, 4))
            ctk.CTkButton(
                inhalt, text="Für alle Versuche übernehmen", fg_color="transparent",
                border_width=1, border_color=MUL_TURKIS, command=_fuer_alle_uebernehmen,
            ).pack(fill="x", pady=(0, 4))
            dialog.bind("<Return>", _enter_anwenden)

        def oeffne_settings():
            """Nur Titel sowie x-/y-Werte der beiden Diagramme bearbeiten."""
            dialog = ctk.CTkToplevel(self)
            dialog.title("Plot Settings")
            dialog.geometry("400x480")
            dialog.minsize(360, 420)
            dialog.resizable(True, True)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()
            tabs_kurz = ctk.CTkTabview(dialog, fg_color="transparent")
            tabs_kurz.pack(fill="both", expand=True, padx=5, pady=(10, 0))

            def _diagramm_felder(parent_tab, titel_key, x_key, y_key):
                inhalt_kurz = ctk.CTkFrame(parent_tab, fg_color="transparent")
                inhalt_kurz.pack(fill="both", expand=True, padx=10, pady=10)
                ctk.CTkLabel(inhalt_kurz, text="Titel:", anchor="w").pack(fill="x")
                titel = ctk.CTkEntry(inhalt_kurz)
                titel.insert(0, zustand[titel_key])
                titel.pack(fill="x", pady=(2, 12))
                ctk.CTkLabel(inhalt_kurz, text="x-value:", anchor="w").pack(fill="x")
                x_value = ctk.CTkOptionMenu(inhalt_kurz, values=TGA_ERGEBNIS_SPALTEN_LABELS, fg_color=MUL_TURKIS)
                x_value.set(TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand[x_key], TGA_ERGEBNIS_SPALTEN_LABELS[0]))
                x_value.pack(fill="x", pady=(2, 12))
                ctk.CTkLabel(inhalt_kurz, text="y-value:", anchor="w").pack(fill="x")
                y_value = ctk.CTkOptionMenu(inhalt_kurz, values=TGA_ERGEBNIS_SPALTEN_LABELS, fg_color=MUL_TURKIS)
                y_value.set(TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand[y_key], TGA_ERGEBNIS_SPALTEN_LABELS[0]))
                y_value.pack(fill="x", pady=(2, 12))
                return titel, x_value, y_value

            titel1, x1, y1 = _diagramm_felder(tabs_kurz.add("Diagramm 1"), "title_masse", "diagramm1_x_spalte", "diagramm1_y_spalte")
            titel2, x2, y2 = _diagramm_felder(tabs_kurz.add("Diagramm 2"), "title_kinetik", "diagramm2_x_spalte", "diagramm2_y_spalte")

            def _uebernehmen_kurz():
                zustand["title_masse"] = titel1.get().strip() or zustand["title_masse"]
                zustand["title_kinetik"] = titel2.get().strip() or zustand["title_kinetik"]
                zustand["diagramm1_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[x1.get()]
                zustand["diagramm1_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[y1.get()]
                zustand["diagramm2_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[x2.get()]
                zustand["diagramm2_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE[y2.get()]
                zeichne()
                _speichern()
                self._status("Plot-Einstellungen automatisch gespeichert (nur dieser Versuch).", "#00ff88")

            def _live_plot_settings(_=None):
                _uebernehmen_kurz()

            def _fuer_alle_uebernehmen_kurz():
                if not messagebox.askyesno(
                    "Für alle übernehmen",
                    "Titel und x-/y-Werte werden für ALLE "
                    f"{len(verarbeitete_versuche)} Versuche dieser Methode "
                    "übernommen und überschreiben dabei auch bereits "
                    "individuell abweichend eingestellte Werte einzelner "
                    "Versuche.\n\nFortfahren?",
                    parent=dialog,
                ):
                    return
                _uebernehmen_kurz()
                _speichern_fuer_alle()
                self._status("Plot-Einstellungen für alle Versuche übernommen.", "#00ff88")

            for auswahl in (x1, y1, x2, y2):
                auswahl.configure(command=_live_plot_settings)
            for titel_feld in (titel1, titel2):
                titel_feld.bind("<KeyRelease>", _live_plot_settings)
                titel_feld.bind("<FocusOut>", _live_plot_settings)

            ctk.CTkButton(dialog, text="Übernehmen", fg_color=MUL_TURKIS, command=_uebernehmen_kurz).pack(pady=(10, 4))
            ctk.CTkButton(
                dialog, text="Für alle Versuche übernehmen", fg_color="transparent",
                border_width=1, border_color=MUL_TURKIS, command=_fuer_alle_uebernehmen_kurz,
            ).pack(pady=(0, 10))
            dialog.bind("<Return>", lambda _event: _uebernehmen_kurz())
            return

            dialog = ctk.CTkToplevel(self)
            dialog.title("Plot Settings")
            dialog.geometry("480x560")
            dialog.minsize(420, 380)
            dialog.resizable(True, True)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()

            tabs = ctk.CTkTabview(dialog, fg_color="transparent")
            tabs.pack(fill="both", expand=True, padx=5, pady=(10, 0))
            tab_d1 = tabs.add("Diagramm 1")
            tab_d2 = tabs.add("Diagramm 2")
            tab_darstellung = tabs.add("Darstellung")

            # ==============================================================
            # TAB: Diagramm 1 (Relative Masse / links)
            # ==============================================================
            inhalt_d1 = ctk.CTkScrollableFrame(tab_d1, fg_color="transparent")
            inhalt_d1.pack(fill="both", expand=True)

            header_links = ctk.CTkLabel(
                inhalt_d1, text=f"Diagramm 1 ({zustand['title_masse']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_links.pack(fill="x", padx=10, pady=(5, 5))

            ctk.CTkLabel(inhalt_d1, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_masse = ctk.CTkEntry(inhalt_d1)
            eingabe_titel_masse.insert(0, zustand["title_masse"])
            eingabe_titel_masse.pack(fill="x", padx=10, pady=(2, 10))
            # Live: Titel wird SOFORT im echten Diagramm (nicht nur im
            # Dialog-Header) nachgezogen, waehrend getippt wird.
            live_kopplung(
                eingabe_titel_masse, header_links, "Diagramm 1 (", ")",
                live_chart=lambda text: ax_masse.set_title(text),
            )

            ctk.CTkLabel(inhalt_d1, text="x-value:", anchor="w").pack(fill="x", padx=10)
            zeile_d1_x = ctk.CTkFrame(inhalt_d1, fg_color="transparent")
            zeile_d1_x.pack(fill="x", padx=10, pady=(2, 2))
            diagramm1_x_dropdown = ctk.CTkOptionMenu(zeile_d1_x, values=TGA_ERGEBNIS_SPALTEN_LABELS,
                                                     fg_color=MUL_TURKIS, width=160)
            diagramm1_x_dropdown.set(
                TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand["diagramm1_x_spalte"], TGA_ERGEBNIS_SPALTEN_LABELS[0])
            )
            diagramm1_x_dropdown.pack(side="left", padx=(0, 5))
            diagramm1_x_edit = ctk.CTkEntry(zeile_d1_x, placeholder_text="oder manuell eingeben")
            diagramm1_x_edit.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(inhalt_d1, text="  Optionen: " + ", ".join(TGA_ERGEBNIS_SPALTEN_LABELS),
                         anchor="w", font=("Arial", 9), text_color="gray").pack(fill="x", padx=10, pady=(0, 10))

            ctk.CTkLabel(inhalt_d1, text="y-value:", anchor="w").pack(fill="x", padx=10)
            zeile_d1_y = ctk.CTkFrame(inhalt_d1, fg_color="transparent")
            zeile_d1_y.pack(fill="x", padx=10, pady=(2, 2))
            diagramm1_y_dropdown = ctk.CTkOptionMenu(zeile_d1_y, values=TGA_ERGEBNIS_SPALTEN_LABELS,
                                                     fg_color=MUL_TURKIS, width=160)
            diagramm1_y_dropdown.set(
                TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand["diagramm1_y_spalte"], TGA_ERGEBNIS_SPALTEN_LABELS[0])
            )
            diagramm1_y_dropdown.pack(side="left", padx=(0, 5))
            diagramm1_y_edit = ctk.CTkEntry(zeile_d1_y, placeholder_text="oder manuell eingeben")
            diagramm1_y_edit.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(inhalt_d1, text="  Optionen: " + ", ".join(TGA_ERGEBNIS_SPALTEN_LABELS),
                         anchor="w", font=("Arial", 9), text_color="gray").pack(fill="x", padx=10, pady=(0, 10))

            ctk.CTkLabel(inhalt_d1, text="X-Achsenbeschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_x_label_masse = ctk.CTkEntry(inhalt_d1)
            eingabe_x_label_masse.insert(0, zustand["label_x_masse"])
            eingabe_x_label_masse.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_x_label_masse, live_chart=lambda text: ax_masse.set_xlabel(text))

            ctk.CTkLabel(inhalt_d1, text="Y-Achsenbeschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label_masse = ctk.CTkEntry(inhalt_d1)
            eingabe_y_label_masse.insert(0, zustand["label_y_masse"])
            eingabe_y_label_masse.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_y_label_masse, live_chart=lambda text: ax_masse.set_ylabel(text))

            # ==============================================================
            # TAB: Diagramm 2 (Reaktionskinetik / rechts)
            # ==============================================================
            inhalt_d2 = ctk.CTkScrollableFrame(tab_d2, fg_color="transparent")
            inhalt_d2.pack(fill="both", expand=True)

            header_rechts = ctk.CTkLabel(
                inhalt_d2, text=f"Diagramm 2 ({zustand['title_kinetik']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_rechts.pack(fill="x", padx=10, pady=(5, 5))

            ctk.CTkLabel(inhalt_d2, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_kinetik = ctk.CTkEntry(inhalt_d2)
            eingabe_titel_kinetik.insert(0, zustand["title_kinetik"])
            eingabe_titel_kinetik.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(
                eingabe_titel_kinetik, header_rechts, "Diagramm 2 (", ")",
                live_chart=lambda text: ax_kinetik.set_title(text),
            )

            ctk.CTkLabel(inhalt_d2, text="x-value:", anchor="w").pack(fill="x", padx=10)
            zeile_d2_x = ctk.CTkFrame(inhalt_d2, fg_color="transparent")
            zeile_d2_x.pack(fill="x", padx=10, pady=(2, 2))
            diagramm2_x_dropdown = ctk.CTkOptionMenu(zeile_d2_x, values=TGA_ERGEBNIS_SPALTEN_LABELS,
                                                     fg_color=MUL_TURKIS, width=160)
            diagramm2_x_dropdown.set(
                TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand["diagramm2_x_spalte"], TGA_ERGEBNIS_SPALTEN_LABELS[0])
            )
            diagramm2_x_dropdown.pack(side="left", padx=(0, 5))
            diagramm2_x_edit = ctk.CTkEntry(zeile_d2_x, placeholder_text="oder manuell eingeben")
            diagramm2_x_edit.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(inhalt_d2, text="  Optionen: " + ", ".join(TGA_ERGEBNIS_SPALTEN_LABELS),
                         anchor="w", font=("Arial", 9), text_color="gray").pack(fill="x", padx=10, pady=(0, 10))

            ctk.CTkLabel(inhalt_d2, text="y-value:", anchor="w").pack(fill="x", padx=10)
            zeile_d2_y = ctk.CTkFrame(inhalt_d2, fg_color="transparent")
            zeile_d2_y.pack(fill="x", padx=10, pady=(2, 2))
            diagramm2_y_dropdown = ctk.CTkOptionMenu(zeile_d2_y, values=TGA_ERGEBNIS_SPALTEN_LABELS,
                                                     fg_color=MUL_TURKIS, width=160)
            diagramm2_y_dropdown.set(
                TGA_ERGEBNIS_SPALTE_ZU_LABEL.get(zustand["diagramm2_y_spalte"], TGA_ERGEBNIS_SPALTEN_LABELS[0])
            )
            diagramm2_y_dropdown.pack(side="left", padx=(0, 5))
            diagramm2_y_edit = ctk.CTkEntry(zeile_d2_y, placeholder_text="oder manuell eingeben")
            diagramm2_y_edit.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(inhalt_d2, text="  Optionen: " + ", ".join(TGA_ERGEBNIS_SPALTEN_LABELS),
                         anchor="w", font=("Arial", 9), text_color="gray").pack(fill="x", padx=10, pady=(0, 10))

            ctk.CTkLabel(inhalt_d2, text="X-Achsenbeschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_x_label_kinetik = ctk.CTkEntry(inhalt_d2)
            eingabe_x_label_kinetik.insert(0, zustand["label_x_kinetik"])
            eingabe_x_label_kinetik.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_x_label_kinetik, live_chart=lambda text: ax_kinetik.set_xlabel(text))

            ctk.CTkLabel(inhalt_d2, text="Y-Achsenbeschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label_kinetik = ctk.CTkEntry(inhalt_d2)
            eingabe_y_label_kinetik.insert(0, zustand["label_y_kinetik"])
            eingabe_y_label_kinetik.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_y_label_kinetik, live_chart=lambda text: ax_kinetik.set_ylabel(text))

            # ==============================================================
            # TAB: Darstellung
            # Jede Gruppe hat oben eine "Anwenden auf: Beide/Links/Rechts"-
            # Auswahl. Wechselt man sie, werden die Felder darunter mit dem
            # aktuell gespeicherten Wert der gewaehlten Seite neu befuellt.
            # ==============================================================
            inhalt = ctk.CTkScrollableFrame(tab_darstellung, fg_color="transparent")
            inhalt.pack(fill="both", expand=True)

            # Die Anordnung gehoert zu den Plot Settings. Die Auswahl wird
            # direkt angewandt und gespeichert, damit die Änderung sofort in
            # der Vorschau sichtbar ist.
            _abschnitt(inhalt, "Anordnung der Diagramme")
            ctk.CTkLabel(inhalt, text="Darstellung:", anchor="w").pack(fill="x", padx=10)
            layout_labels = {
                "Horizontal (nebeneinander)": "horizontal",
                "Vertikal (übereinander)": "vertical",
            }
            layout_werte = {wert: label for label, wert in layout_labels.items()}

            def _layout_gewaehlt(_=None):
                setze_layout(layout_labels.get(layout_dropdown.get(), "horizontal"))

            layout_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(layout_labels), fg_color=MUL_TURKIS,
                command=_layout_gewaehlt,
            )
            layout_dropdown.set(
                layout_werte.get(
                    zustand.get("diagramm_layout", "horizontal"),
                    "Horizontal (nebeneinander)",
                )
            )
            layout_dropdown.pack(fill="x", padx=10, pady=(2, 10))

            # --- Diagrammstil (Preset: Grid/Achsen/Tick/Hintergrund je Achse) ---
            _abschnitt(inhalt, "Diagrammstil")
            stil_seite_dd = _seiten_dropdown(inhalt)
            stil_dropdown = ctk.CTkOptionMenu(inhalt, values=PLOT_STIL_NAMEN, fg_color=MUL_TURKIS)
            stil_dropdown.pack(fill="x", padx=10, pady=(0, 10))

            def _stil_laden(_=None):
                seite = stil_seite_dd.get()
                quelle = zustand["diagramm_stil_rechts"] if seite == "Rechts" else zustand["diagramm_stil_links"]
                stil_dropdown.set(quelle)

            stil_seite_dd.configure(command=_stil_laden)
            _stil_laden()

            # --- Farbe ---
            _abschnitt(inhalt, "Farbe")
            farbe_seite_dd = _seiten_dropdown(inhalt)
            farbe_dropdown = ctk.CTkOptionMenu(inhalt, values=PLOT_FARBOPTIONEN_LABELS, fg_color=MUL_TURKIS)
            farbe_dropdown.pack(fill="x", padx=10, pady=(2, 6))
            # kleiner Umweg (live_ref), weil _hex_feld() beim Erstellen von
            # eingabe_farbe_custom noch keinen Verweis auf _farbe_live_anwenden
            # haben kann (die Funktion braucht ihrerseits eingabe_farbe_custom).
            live_ref = {"anwenden": lambda: None}
            eingabe_farbe_custom = _hex_feld(
                inhalt, "Custom-Hex:", zustand["linienfarbe"],
                bei_aenderung=lambda: live_ref["anwenden"](),
            )

            def _farbe_live_anwenden(_=None):
                """Wendet die aktuell im Dialog gewaehlte Farbe SOFORT auf
                das echte Diagramm an (Linienfarbe), ohne dass zustand oder
                die gespeicherte Datei veraendert werden - das passiert erst
                bei "Übernehmen"/Enter."""
                hex_code = plot_farb_label_zu_hex(farbe_dropdown.get(), eingabe_farbe_custom.get())
                seite = farbe_seite_dd.get()
                ziel_achsen = []
                if seite in ("Links", "Beide"):
                    ziel_achsen.append(ax_masse)
                if seite in ("Rechts", "Beide"):
                    ziel_achsen.append(ax_kinetik)
                try:
                    for ax in ziel_achsen:
                        for linie in ax.lines:
                            linie.set_color(hex_code)
                    canvas.draw_idle()
                except Exception:
                    pass

            live_ref["anwenden"] = _farbe_live_anwenden
            farbe_dropdown.configure(command=lambda _=None: _farbe_live_anwenden())

            # Wenn Nutzer im Custom-Hex Feld tippt → Dropdown automatisch auf
            # "Custom..." setzen UND Farbe sofort im Diagramm live anzeigen.
            def _farbe_custom_getippt(_event=None):
                farbe_dropdown.set("Custom...")
                _farbe_live_anwenden()

            eingabe_farbe_custom.bind("<KeyRelease>", _farbe_custom_getippt)

            def _farbe_laden(_=None):
                seite = farbe_seite_dd.get()
                quelle = zustand["linienfarbe2"] if seite == "Rechts" else zustand["linienfarbe"]
                farbe_dropdown.set(plot_farb_hex_zu_label(quelle))
                eingabe_farbe_custom.delete(0, "end")
                eingabe_farbe_custom.insert(0, str(quelle))

            farbe_seite_dd.configure(command=_farbe_laden)
            _farbe_laden()

            # --- Schrift ---
            _abschnitt(inhalt, "Schrift")
            schrift_seite_dd = _seiten_dropdown(inhalt)
            eingabe_schrift_titel = _feld(inhalt, "Titelgröße:", zustand["schriftgroesse_titel_links"])
            # kleiner Umweg (live_ref_titelfarbe) aus demselben Grund wie bei
            # der Linienfarbe oben: _hex_feld() braucht bei_aenderung schon
            # beim Erstellen, die Live-Funktion selbst braucht aber das
            # fertige Eingabefeld.
            live_ref_titelfarbe = {"anwenden": lambda: None}
            eingabe_schriftfarbe_titel = _hex_feld(
                inhalt, "Titelfarbe:", zustand.get("schriftfarbe_titel_links", "#000000"),
                bei_aenderung=lambda: live_ref_titelfarbe["anwenden"](),
            )

            def _titelfarbe_live_anwenden(_event=None):
                """Wendet die Titelfarbe SOFORT auf den echten Diagrammtitel
                an (analog zur Linienfarbe), ohne dass zustand oder die
                gespeicherte Datei veraendert werden - das passiert erst bei
                "Übernehmen"/Enter."""
                hex_code = eingabe_schriftfarbe_titel.get().strip() or "#000000"
                seite = schrift_seite_dd.get()
                ziel_achsen = []
                if seite in ("Links", "Beide"):
                    ziel_achsen.append(ax_masse)
                if seite in ("Rechts", "Beide"):
                    ziel_achsen.append(ax_kinetik)
                try:
                    for ax in ziel_achsen:
                        ax.title.set_color(hex_code)
                    canvas.draw_idle()
                except Exception:
                    pass

            live_ref_titelfarbe["anwenden"] = _titelfarbe_live_anwenden
            eingabe_schriftfarbe_titel.bind("<KeyRelease>", _titelfarbe_live_anwenden)

            eingabe_schrift_achsen = _feld(inhalt, "Achsentitel:", zustand["schriftgroesse_achsen_links"])
            eingabe_schrift_ticks = _feld(inhalt, "Ticklabels:", zustand["schriftgroesse_ticks_links"])

            def _schrift_laden(_=None):
                suffix = "rechts" if schrift_seite_dd.get() == "Rechts" else "links"
                for eingabe, schluessel in (
                    (eingabe_schrift_titel, f"schriftgroesse_titel_{suffix}"),
                    (eingabe_schrift_achsen, f"schriftgroesse_achsen_{suffix}"),
                    (eingabe_schrift_ticks, f"schriftgroesse_ticks_{suffix}"),
                ):
                    eingabe.delete(0, "end")
                    eingabe.insert(0, str(zustand[schluessel]))
                eingabe_schriftfarbe_titel.delete(0, "end")
                eingabe_schriftfarbe_titel.insert(0, zustand.get(f"schriftfarbe_titel_{suffix}", "#000000"))

            schrift_seite_dd.configure(command=_schrift_laden)
            _schrift_laden()

            # --- Linien ---
            _abschnitt(inhalt, "Linien")
            linien_seite_dd = _seiten_dropdown(inhalt)
            eingabe_linienbreite = _feld(inhalt, "Linienbreite:", zustand["linienbreite"])
            ctk.CTkLabel(inhalt, text="Linienstil:", anchor="w").pack(fill="x", padx=10)
            linienstil_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(PLOT_LINIENSTIL_LABEL_ZU_WERT.keys()), fg_color=MUL_TURKIS,
            )
            linienstil_dropdown.pack(fill="x", padx=10, pady=(2, 10))

            def _linien_laden(_=None):
                if linien_seite_dd.get() == "Rechts":
                    eingabe_linienbreite.delete(0, "end")
                    eingabe_linienbreite.insert(0, str(zustand["linienbreite2"]))
                    linienstil_dropdown.set(PLOT_LINIENSTIL_WERT_ZU_LABEL.get(zustand["linienstil_rechts"], "────"))
                else:
                    eingabe_linienbreite.delete(0, "end")
                    eingabe_linienbreite.insert(0, str(zustand["linienbreite"]))
                    linienstil_dropdown.set(PLOT_LINIENSTIL_WERT_ZU_LABEL.get(zustand["linienstil_links"], "────"))

            linien_seite_dd.configure(command=_linien_laden)
            _linien_laden()

            # --- Achsen (Grid + Rahmen) ---
            _abschnitt(inhalt, "Achsen")
            achsen_seite_dd = _seiten_dropdown(inhalt)
            gitter_anzeigen_var = ctk.BooleanVar(value=zustand["gitter_anzeigen_links"])
            ctk.CTkCheckBox(inhalt, text="Grid", variable=gitter_anzeigen_var).pack(anchor="w", padx=10, pady=(0, 6))
            minor_grid_var = ctk.BooleanVar(value=zustand["minor_grid_anzeigen_links"])
            ctk.CTkCheckBox(inhalt, text="Minor Grid", variable=minor_grid_var).pack(anchor="w", padx=10, pady=(0, 6))
            obere_achse_var = ctk.BooleanVar(value=zustand["obere_achse_ausblenden_links"])
            ctk.CTkCheckBox(inhalt, text="Obere Achse ausblenden", variable=obere_achse_var).pack(
                anchor="w", padx=10, pady=(0, 6)
            )
            rechte_achse_var = ctk.BooleanVar(value=zustand["rechte_achse_ausblenden_links"])
            ctk.CTkCheckBox(inhalt, text="Rechte Achse ausblenden", variable=rechte_achse_var).pack(
                anchor="w", padx=10, pady=(0, 10)
            )

            def _achsen_laden(_=None):
                suffix = "rechts" if achsen_seite_dd.get() == "Rechts" else "links"
                gitter_anzeigen_var.set(zustand[f"gitter_anzeigen_{suffix}"])
                minor_grid_var.set(zustand[f"minor_grid_anzeigen_{suffix}"])
                obere_achse_var.set(zustand[f"obere_achse_ausblenden_{suffix}"])
                rechte_achse_var.set(zustand[f"rechte_achse_ausblenden_{suffix}"])

            achsen_seite_dd.configure(command=_achsen_laden)
            _achsen_laden()

            # --- Achsenticks ---
            _abschnitt(inhalt, "Achsenticks")
            tick_seite_dd = _seiten_dropdown(inhalt)
            ctk.CTkLabel(
                inhalt, text="Innen/Außen/Beides: Richtung der kleinen Striche an der Achse.",
                anchor="w", font=("Arial", 10), text_color="#888888", wraplength=460,
            ).pack(fill="x", padx=10, pady=(0, 4))
            tick_richtung_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(PLOT_TICK_RICHTUNG_LABEL_ZU_WERT.keys()), fg_color=MUL_TURKIS,
            )
            tick_richtung_dropdown.pack(fill="x", padx=10, pady=(2, 10))
            eingabe_tick_laenge = _feld(inhalt, "Tick-Länge:", zustand["tick_laenge_links"])

            def _tick_laden(_=None):
                suffix = "rechts" if tick_seite_dd.get() == "Rechts" else "links"
                tick_richtung_dropdown.set(
                    PLOT_TICK_RICHTUNG_WERT_ZU_LABEL.get(zustand[f"tick_richtung_{suffix}"], "Außen")
                )
                eingabe_tick_laenge.delete(0, "end")
                eingabe_tick_laenge.insert(0, str(zustand[f"tick_laenge_{suffix}"]))

            tick_seite_dd.configure(command=_tick_laden)
            _tick_laden()

            # --- Hintergrund ---
            _abschnitt(inhalt, "Hintergrund")
            hg_seite_dd = _seiten_dropdown(inhalt, "Diagramm-Hintergrund für:")
            eingabe_hg_diagramm = _hex_feld(inhalt, "Diagramm-Fläche:", zustand["hintergrund_diagramm_links"])

            def _hg_laden(_=None):
                suffix = "rechts" if hg_seite_dd.get() == "Rechts" else "links"
                eingabe_hg_diagramm.delete(0, "end")
                eingabe_hg_diagramm.insert(0, str(zustand[f"hintergrund_diagramm_{suffix}"]))

            hg_seite_dd.configure(command=_hg_laden)
            _hg_laden()
            eingabe_hg_figure = _hex_feld(inhalt, "Gesamter Hintergrund:", zustand["hintergrund_figure"])

            def _stil_gewechselt(_=None):
                """Synct die Achsen-Checkboxen/-Dropdowns sofort mit dem
                gewaehlten Diagrammstil-Preset (z.B. 'Minimal' -> Grid aus),
                DAMIT beim spaeteren 'Übernehmen' die Achsen-Sektion (die
                gitter_anzeigen_var/minor_grid_var/... unconditionally liest)
                nicht die noch alte Checkbox-Stellung wieder zurückschreibt
                und den Preset-Effekt so aufhebt."""
                preset = PLOT_STIL_PRESETS.get(stil_dropdown.get(), {})
                if "gitter_anzeigen" in preset:
                    gitter_anzeigen_var.set(preset["gitter_anzeigen"])
                if "minor_grid_anzeigen" in preset:
                    minor_grid_var.set(preset["minor_grid_anzeigen"])
                if "obere_achse_ausblenden" in preset:
                    obere_achse_var.set(preset["obere_achse_ausblenden"])
                if "rechte_achse_ausblenden" in preset:
                    rechte_achse_var.set(preset["rechte_achse_ausblenden"])
                if "tick_richtung" in preset:
                    tick_richtung_dropdown.set(
                        PLOT_TICK_RICHTUNG_WERT_ZU_LABEL.get(
                            preset["tick_richtung"], tick_richtung_dropdown.get()
                        )
                    )
                if "hintergrund_diagramm" in preset:
                    eingabe_hg_diagramm.delete(0, "end")
                    eingabe_hg_diagramm.insert(0, str(preset["hintergrund_diagramm"]))

            stil_dropdown.configure(command=_stil_gewechselt)

            # --- Legende ---
            _abschnitt(inhalt, "Legende")
            legende_seite_dd = _seiten_dropdown(inhalt, "Anzeigen für:")
            legende_var = ctk.BooleanVar(value=zustand["legende_anzeigen_links"])
            ctk.CTkCheckBox(inhalt, text="Legende anzeigen", variable=legende_var).pack(
                anchor="w", padx=10, pady=(0, 6)
            )

            def _legende_laden(_=None):
                suffix = "rechts" if legende_seite_dd.get() == "Rechts" else "links"
                legende_var.set(zustand[f"legende_anzeigen_{suffix}"])

            legende_seite_dd.configure(command=_legende_laden)
            _legende_laden()

            ctk.CTkLabel(inhalt, text="Position (beide Diagramme):", anchor="w").pack(fill="x", padx=10, pady=(4, 0))
            legende_position_dropdown = ctk.CTkOptionMenu(
                inhalt, values=list(PLOT_LEGENDE_LABEL_ZU_LOC.keys()), fg_color=MUL_TURKIS,
            )
            legende_position_dropdown.set(zustand.get("legende_position", PLOT_LEGENDE_UNTER_ACHSE))
            legende_position_dropdown.pack(fill="x", padx=10, pady=(2, 10))

            # --- Export ---
            _abschnitt(inhalt, "Export")
            ctk.CTkLabel(inhalt, text="Export-DPI:", anchor="w").pack(fill="x", padx=10)
            export_dpi_dropdown = ctk.CTkOptionMenu(
                inhalt, values=["150", "300", "600", "1200"], fg_color=MUL_TURKIS,
            )
            export_dpi_dropdown.set(str(int(zustand.get("export_dpi", 300))))
            export_dpi_dropdown.pack(fill="x", padx=10, pady=(2, 10))
            ctk.CTkLabel(inhalt, text="Dateiformat:", anchor="w").pack(fill="x", padx=10)
            export_format_dropdown = ctk.CTkOptionMenu(
                inhalt, values=["png", "pdf", "svg"], fg_color=MUL_TURKIS,
            )
            export_format_dropdown.set(zustand.get("export_format", "png"))
            export_format_dropdown.pack(fill="x", padx=10, pady=(2, 10))

            # --- Zusätzliche Optionen ---
            _abschnitt(inhalt, "Zusätzliche Optionen")
            transparent_var = ctk.BooleanVar(value=bool(zustand.get("transparenter_hintergrund", False)))
            ctk.CTkCheckBox(inhalt, text="Transparenter Hintergrund (beim Download)", variable=transparent_var).pack(
                anchor="w", padx=10, pady=(0, 6)
            )
            tight_layout_var = ctk.BooleanVar(value=bool(zustand.get("tight_layout", True)))
            ctk.CTkCheckBox(inhalt, text="Tight Layout", variable=tight_layout_var).pack(
                anchor="w", padx=10, pady=(0, 10)
            )

            def _uebernehmen_intern():
                # --- Diagramm 1/2: Titel, Achsenbeschriftungen ---
                zustand["title_masse"] = eingabe_titel_masse.get().strip() or zustand["title_masse"]
                zustand["label_x_masse"] = eingabe_x_label_masse.get().strip() or zustand["label_x_masse"]
                zustand["label_y_masse"] = eingabe_y_label_masse.get().strip() or zustand["label_y_masse"]

                zustand["title_kinetik"] = eingabe_titel_kinetik.get().strip() or zustand["title_kinetik"]
                zustand["label_x_kinetik"] = eingabe_x_label_kinetik.get().strip() or zustand["label_x_kinetik"]
                zustand["label_y_kinetik"] = eingabe_y_label_kinetik.get().strip() or zustand["label_y_kinetik"]

                # --- Diagramm 1/2: gewaehlte Datenspalten fuer x-value/y-value ---
                zustand["diagramm1_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE.get(
                    diagramm1_x_dropdown.get(), zustand["diagramm1_x_spalte"]
                )
                zustand["diagramm1_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE.get(
                    diagramm1_y_dropdown.get(), zustand["diagramm1_y_spalte"]
                )
                zustand["diagramm2_x_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE.get(
                    diagramm2_x_dropdown.get(), zustand["diagramm2_x_spalte"]
                )
                zustand["diagramm2_y_spalte"] = TGA_ERGEBNIS_LABEL_ZU_SPALTE.get(
                    diagramm2_y_dropdown.get(), zustand["diagramm2_y_spalte"]
                )

                def ziel_seiten(auswahl):
                    if auswahl == "Links":
                        return ["links"]
                    if auswahl == "Rechts":
                        return ["rechts"]
                    return ["links", "rechts"]

                # --- Diagrammstil-Preset anwenden (pro Seite) ---
                neuer_stil = stil_dropdown.get()
                preset = PLOT_STIL_PRESETS.get(neuer_stil, {})
                for seite in ziel_seiten(stil_seite_dd.get()):
                    zustand[f"diagramm_stil_{seite}"] = neuer_stil
                    if "gitter_anzeigen" in preset:
                        zustand[f"gitter_anzeigen_{seite}"] = preset["gitter_anzeigen"]
                    if "minor_grid_anzeigen" in preset:
                        zustand[f"minor_grid_anzeigen_{seite}"] = preset["minor_grid_anzeigen"]
                    if "obere_achse_ausblenden" in preset:
                        zustand[f"obere_achse_ausblenden_{seite}"] = preset["obere_achse_ausblenden"]
                    if "rechte_achse_ausblenden" in preset:
                        zustand[f"rechte_achse_ausblenden_{seite}"] = preset["rechte_achse_ausblenden"]
                    if "tick_richtung" in preset:
                        zustand[f"tick_richtung_{seite}"] = preset["tick_richtung"]
                    if "hintergrund_diagramm" in preset:
                        zustand[f"hintergrund_diagramm_{seite}"] = preset["hintergrund_diagramm"]

                # --- Farbe ---
                neue_farbe = plot_farb_label_zu_hex(farbe_dropdown.get(), eingabe_farbe_custom.get())
                for seite in ziel_seiten(farbe_seite_dd.get()):
                    zustand["linienfarbe" if seite == "links" else "linienfarbe2"] = neue_farbe

                # --- Schrift ---
                for seite in ziel_seiten(schrift_seite_dd.get()):
                    for eingabe, schluessel in (
                        (eingabe_schrift_titel, f"schriftgroesse_titel_{seite}"),
                        (eingabe_schrift_achsen, f"schriftgroesse_achsen_{seite}"),
                        (eingabe_schrift_ticks, f"schriftgroesse_ticks_{seite}"),
                    ):
                        wert = als_zahl_oder_none(eingabe.get())
                        if wert:
                            zustand[schluessel] = wert
                    zustand[f"schriftfarbe_titel_{seite}"] = (
                        eingabe_schriftfarbe_titel.get().strip()
                        or zustand.get(f"schriftfarbe_titel_{seite}", "#000000")
                    )

                # --- Linien ---
                for seite in ziel_seiten(linien_seite_dd.get()):
                    linienbreite = als_zahl_oder_none(eingabe_linienbreite.get())
                    if linienbreite:
                        zustand["linienbreite" if seite == "links" else "linienbreite2"] = linienbreite
                    zustand[f"linienstil_{seite}"] = PLOT_LINIENSTIL_LABEL_ZU_WERT.get(
                        linienstil_dropdown.get(), "-"
                    )

                # --- Achsen ---
                for seite in ziel_seiten(achsen_seite_dd.get()):
                    zustand[f"gitter_anzeigen_{seite}"] = bool(gitter_anzeigen_var.get())
                    zustand[f"minor_grid_anzeigen_{seite}"] = bool(minor_grid_var.get())
                    zustand[f"obere_achse_ausblenden_{seite}"] = bool(obere_achse_var.get())
                    zustand[f"rechte_achse_ausblenden_{seite}"] = bool(rechte_achse_var.get())

                # --- Achsenticks ---
                for seite in ziel_seiten(tick_seite_dd.get()):
                    zustand[f"tick_richtung_{seite}"] = PLOT_TICK_RICHTUNG_LABEL_ZU_WERT.get(
                        tick_richtung_dropdown.get(), "out"
                    )
                    tick_laenge = als_zahl_oder_none(eingabe_tick_laenge.get())
                    if tick_laenge:
                        zustand[f"tick_laenge_{seite}"] = tick_laenge

                # --- Hintergrund ---
                for seite in ziel_seiten(hg_seite_dd.get()):
                    zustand[f"hintergrund_diagramm_{seite}"] = (
                        eingabe_hg_diagramm.get().strip() or zustand[f"hintergrund_diagramm_{seite}"]
                    )
                zustand["hintergrund_figure"] = eingabe_hg_figure.get().strip() or zustand["hintergrund_figure"]

                # --- Legende ---
                for seite in ziel_seiten(legende_seite_dd.get()):
                    zustand[f"legende_anzeigen_{seite}"] = bool(legende_var.get())
                zustand["legende_position"] = legende_position_dropdown.get()

                # --- Export ---
                zustand["export_dpi"] = als_zahl_oder_none(export_dpi_dropdown.get()) or zustand["export_dpi"]
                zustand["export_format"] = export_format_dropdown.get()

                # --- Zusätzliche Optionen ---
                zustand["transparenter_hintergrund"] = bool(transparent_var.get())
                zustand["tight_layout"] = bool(tight_layout_var.get())

                zeichne()
                self.speichere_diagramm_einstellungen(projekt, methode, zustand)
                # Fenster bleibt bewusst offen - der Nutzer schließt es selbst
                # (z.B. über das X), damit direkt weitere Anpassungen möglich
                # sind, ohne den Dialog jedes Mal neu öffnen zu müssen.

            def uebernehmen():
                """Aussenhuelle um _uebernehmen_intern(): faengt Fehler ab
                (die sonst in einer gepackten .exe/pythonw-App LAUTLOS
                verschluckt werden - man klickt 'Übernehmen' und scheinbar
                passiert nichts) und zeigt sie als Fehlermeldung an. Bei
                Erfolg blinkt der Button kurz auf, als sichtbare Bestätigung,
                dass die Änderungen uebernommen und gespeichert wurden.
                """
                try:
                    _uebernehmen_intern()
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    messagebox.showerror(
                        "Fehler beim Übernehmen",
                        f"Die Einstellungen konnten nicht übernommen werden:\n\n{exc}",
                        parent=dialog,
                    )
                    return
                try:
                    uebernehmen_button.configure(text="✓ Übernommen", fg_color="#2ca02c")
                    dialog.after(
                        800,
                        lambda: uebernehmen_button.configure(text="Übernehmen", fg_color=MUL_TURKIS),
                    )
                except Exception:
                    pass

            button_zeile = ctk.CTkFrame(dialog, fg_color="transparent")
            button_zeile.pack(side="bottom", fill="x", pady=10)
            uebernehmen_button = ctk.CTkButton(
                button_zeile, text="Übernehmen", fg_color=MUL_TURKIS, command=uebernehmen,
            )
            uebernehmen_button.pack()

            # Enter (in einem beliebigen Feld des Dialogs) übernimmt und
            # speichert direkt, ohne dass extra auf "Übernehmen" geklickt
            # werden muss.
            dialog.bind("<Return>", lambda _event: uebernehmen())
            dialog.bind("<KP_Enter>", lambda _event: uebernehmen())


        dropdown = ctk.CTkOptionMenu(
            seitenleiste, values=anzeige_werte, command=zeige_versuch, fg_color=MUL_TURKIS,
        )
        dropdown.pack(fill="x", pady=(0, 15))

        ctk.CTkButton(
            seitenleiste, text="Plot Settings", fg_color=MUL_DUNKEL, command=oeffne_settings,
        ).pack(fill="x")

        ctk.CTkButton(
            seitenleiste, text="Darstellung", fg_color=MUL_DUNKEL, command=oeffne_darstellung,
        ).pack(fill="x", pady=(8, 0))

        zeige_versuch(anzeige_werte[0])

    def _tga_auf_eafd_basis(self, dm_pct, dmdt_pctmin, cao_pct):
        """
        Rechnet Relative Masse/Reaktionskinetik von "% der Mischung" (Rohwert
        aus der Waage) auf "% des EAFD" um - CaO-Anteil wird rausgerechnet
        (siehe helper/TGA.py:_add_eafd_basis_columns aus dem Labor-Repo):
            eafd_frac = 1 - cao_frac
            dm_pct_eafd  = ((dm_pct/100 - cao_frac) / eafd_frac) * 100
            dmdt_pctmin_eafd = dmdt_pctmin / eafd_frac
        Gibt (dm_pct, dmdt_pctmin) unverändert zurück (= Mischungs-Basis),
        falls kein gültiger CaO-Wert vorliegt.
        """
        if cao_pct is None or cao_pct < 0 or cao_pct >= 100:
            return dm_pct, dmdt_pctmin, False
        cao_frac = cao_pct / 100.0
        eafd_frac = 1.0 - cao_frac
        dm_eafd = ((dm_pct / 100.0 - cao_frac) / eafd_frac) * 100.0
        dmdt_eafd = dmdt_pctmin / eafd_frac
        return dm_eafd, dmdt_eafd, True

    def _baue_tga_kinetik_filter(self, zustand):
        """
        Baut aus den im Settings-Dialog (rechtes Diagramm, "Filter") hinterlegten
        Werten das passende Filter-Objekt aus helper/Filter.py.

        Gibt None zurück bei "Kein Filter", fehlendem Import (siehe
        _FILTER_IMPORT_FEHLER) oder ungültigen Parametern - die
        Reaktionskinetik wird dann unverändert (ungefiltert) gezeichnet.

        Duennes Wrapper um _baue_filter_objekt() (siehe dort) - hier bleiben
        die Parameter-Schluessel unpraefigiert (wie im Ergebnisse-Tab
        gespeichert), waehrend die Rohdaten-Vorschau (Filter 1 / Filter 2)
        praefigierte Schluessel verwendet (siehe
        _rohdaten_filter_parameter_ansicht).
        """
        typ = zustand.get("filter_typ", "Kein Filter")
        return self._baue_filter_objekt(typ, zustand)

    # Spalten, die inhaltlich "Masse" bzw. "Reaktionskinetik" sind - nur fuer
    # DIESE wird automatisch auf "% of EAFD" umgerechnet (siehe
    # _tga_auf_eafd_basis) und der Kinetik-Glaettungsfilter angewendet.
    _TGA_MASSE_SPALTEN = ("dm_original_pct", "dm_filtered_pct")
    _TGA_KINETIK_SPALTEN = ("dmdt_original_pctmin", "dmdt_filtered_pctmin")

    def _tga_style_achse(self, ax, seite, zustand):
        """
        Wendet Grid/Rahmen/Tick-Richtung/Tick-Laenge/Diagramm-Hintergrund
        fuer EINE Achse an - "seite" ist "links" (Diagramm 1/Masse) oder
        "rechts" (Diagramm 2/Kinetik), passend zu den Feldnamen aus
        zustand_standard (z.B. "gitter_anzeigen_links").
        """
        gitter_an = bool(zustand.get(f"gitter_anzeigen_{seite}", True))
        if gitter_an:
            # WICHTIG: die Style-Kwargs (alpha/color/linewidth) duerfen NUR
            # zusammen mit True uebergeben werden. matplotlib ignoriert bei
            # ax.grid(False, ..., color=..., alpha=..., linewidth=...) das
            # False und schaltet das Grid trotzdem wieder EIN (mit Warnung
            # "The grid will be enabled") - deshalb blieb das Grid bisher
            # auch beim "Minimal"-Preset oder deaktivierter Checkbox sichtbar.
            ax.grid(True, which="major", alpha=0.5, color="#888888", linewidth=0.7)
        else:
            ax.grid(False, which="major")
        if bool(zustand.get(f"minor_grid_anzeigen_{seite}", False)):
            ax.minorticks_on()
            ax.grid(True, which="minor", alpha=0.15)
        else:
            ax.grid(False, which="minor")

        ax.spines["top"].set_visible(not bool(zustand.get(f"obere_achse_ausblenden_{seite}", True)))
        ax.spines["right"].set_visible(not bool(zustand.get(f"rechte_achse_ausblenden_{seite}", True)))

        ax.tick_params(
            direction=zustand.get(f"tick_richtung_{seite}", "out"),
            length=float(zustand.get(f"tick_laenge_{seite}", 5) or 5),
            labelsize=zustand.get(f"schriftgroesse_ticks_{seite}", 10),
        )

        hintergrund = zustand.get(f"hintergrund_diagramm_{seite}", "#ffffff") or "#ffffff"
        ax.set_facecolor(hintergrund)

    def _tga_zeige_legende(self, ax, seite, zustand):
        if not bool(zustand.get(f"legende_anzeigen_{seite}", False)):
            return
        handles, labels = ax.get_legend_handles_labels()
        if not handles:
            return
        position = zustand.get("legende_position", PLOT_LEGENDE_UNTER_ACHSE)
        if position == PLOT_LEGENDE_UNTER_ACHSE:
            ax.legend(
                handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.18),
                ncol=max(1, len(handles)), frameon=False, fontsize=8,
            )
        else:
            loc = PLOT_LEGENDE_LABEL_ZU_LOC.get(position, "upper right")
            ax.legend(handles, labels, loc=loc, frameon=False, fontsize=8)

    def _tga_lies_startgewicht_mg(self, voller_pfad):
        """
        Liest die Ausgangsmasse ("Startgewicht") aus der '# Weight: ... mg'-
        Kopfzeile der rohen TGA-.txt-Datei, z.B.:
            # Weight: 375.5 mg
        Diese Ausgangsmasse steht NICHT im Ergebnis-Parquet, wird aber
        gebraucht, um aus einer relativen Massenangabe [%] eine absolute
        Masse [mg] zu berechnen (siehe _tga_ergaenze_absolute_massenspalten).
        Gibt None zurueck, falls keine solche Kopfzeile gefunden wird.
        """
        if not voller_pfad or not os.path.isfile(voller_pfad):
            return None
        gewicht_pattern = re.compile(r"Weight:\s*([0-9]+[.,]?[0-9]*)\s*mg", re.IGNORECASE)
        try:
            with open(voller_pfad, encoding="ISO-8859-1") as f:
                for zeile in f:
                    if zeile.startswith("#"):
                        treffer = gewicht_pattern.search(zeile)
                        if treffer:
                            try:
                                return float(treffer.group(1).replace(",", "."))
                            except ValueError:
                                return None
                    elif not zeile.startswith("#"):
                        # Kopfzeilen sind vorbei (Datenteil/Spaltenkopf beginnt) -
                        # nicht die ganze (potenziell sehr grosse) Datei einlesen.
                        break
        except OSError:
            return None
        return None

    # Paare (fehlende absolute Spalte -> vorhandene relative %-Spalte), aus
    # denen sich die absolute Spalte mit der Ausgangsmasse nachrechnen laesst.
    _TGA_ABSOLUTE_MASSE_PAARE = (
        ("m_filtered_mg", "dm_filtered_pct"),
        ("m_original_mg", "dm_original_pct"),
    )
    _TGA_ABSOLUTE_KINETIK_PAARE = (
        ("dmdt_filtered_mgmin", "dmdt_filtered_pctmin"),
        ("dmdt_original_mgmin", "dmdt_original_pctmin"),
    )

    def _tga_ergaenze_absolute_massenspalten(self, df, voller_pfad):
        """
        Ergaenzt fehlende absolute Massen-/Kinetik-Spalten (z.B.
        m_filtered_mg, dmdt_filtered_mgmin) im DataFrame, FALLS die
        zugehoerige relative %-Spalte vorhanden ist, die absolute Spalte
        selbst aber fehlt (z.B. weil processed_data nur die relativen
        Werte enthaelt). Dafuer wird die Ausgangsmasse aus dem
        Rohdaten-Header gelesen (siehe _tga_lies_startgewicht_mg):
            m_mg        = startgewicht_mg * (dm_pct / 100)
            dmdt_mgmin  = startgewicht_mg * (dmdt_pctmin / 100)
        Bereits vorhandene Spalten werden NICHT ueberschrieben. Aendert df
        in-place und gibt es zurueck (unveraendert, falls keine Ausgangs-
        masse gefunden wird oder nichts fehlt).
        """
        fehlt_masse = any(
            ziel not in df.columns and quelle in df.columns
            for ziel, quelle in self._TGA_ABSOLUTE_MASSE_PAARE
        )
        fehlt_kinetik = any(
            ziel not in df.columns and quelle in df.columns
            for ziel, quelle in self._TGA_ABSOLUTE_KINETIK_PAARE
        )
        if not (fehlt_masse or fehlt_kinetik):
            return df

        startgewicht_mg = self._tga_lies_startgewicht_mg(voller_pfad)
        if not startgewicht_mg:
            return df

        for ziel_spalte, pct_spalte in self._TGA_ABSOLUTE_MASSE_PAARE:
            if ziel_spalte not in df.columns and pct_spalte in df.columns:
                df[ziel_spalte] = startgewicht_mg * (df[pct_spalte] / 100.0)
        for ziel_spalte, pctmin_spalte in self._TGA_ABSOLUTE_KINETIK_PAARE:
            if ziel_spalte not in df.columns and pctmin_spalte in df.columns:
                df[ziel_spalte] = startgewicht_mg * (df[pctmin_spalte] / 100.0)
        return df

    def zeichne_ergebnis_plot_tga(self, eintrag_info, fig, ax_masse, ax_kinetik, canvas, zustand):
        """
        Zeichnet Diagramm 1 (links) und Diagramm 2 (rechts) fuer einen
        TGA-Versuch neu in die bestehenden Achsen. Welche Spalten aus
        <versuch>_results.parquet auf x/y gezeichnet werden, waehlt der
        Nutzer pro Diagramm im Plot-Settings-Dialog (siehe
        TGA_ERGEBNIS_SPALTEN / diagramm1_x_spalte usw.). Rechnet
        automatisch auf "% of EAFD" um (nur fuer Masse-/Kinetik-Spalten),
        falls die CaO-Zugabe fuer den Versuch im Sheet hinterlegt ist
        (siehe _tga_auf_eafd_basis).
        """
        import numpy as np
        import pandas as pd


        staub, eintrag, voller_pfad = eintrag_info
        df = self.lade_ergebnis_dataframe(eintrag, voller_pfad)

        ax_masse.clear()
        ax_kinetik.clear()

        if df is None or df.empty:
            ax_masse.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            ax_kinetik.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            canvas.draw_idle()
            return

        # Fehlende absolute Massen-/Kinetik-Spalten (z.B. m_filtered_mg)
        # aus der relativen %-Spalte + Startgewicht der Rohdaten nachrechnen,
        # falls processed_data nur die relativen Werte enthaelt.
        df = self._tga_ergaenze_absolute_massenspalten(df, voller_pfad)

        sheet_name = self._tga_versuchsname_fuer_sheet(eintrag)
        tga_parameter = self.hole_tga_parameter_fuer_versuch(sheet_name)
        cao_pct = tga_parameter.get("cao_pct") if tga_parameter else None

        # WICHTIG: NICHT nach der x-Spalte sortieren! Bei einem isothermen
        # Haltebereich (Temperatur bleibt ueber viele Zeitschritte fast
        # konstant, waehrend die Masse weiter faellt) wuerde eine Sortierung
        # nach Temperatur die echte zeitliche Reihenfolge durcheinander-
        # wuerfeln - matplotlib verbindet die Punkte dann kreuz und quer, was
        # wie eine ausgefuellte Flaeche aussieht (ist aber ein dichtes
        # Liniengewirr). Die Parquet-Datei ist bereits chronologisch
        # sortiert (siehe TGA_calculation.py: sort_values("time_min")),
        # daher hier nur nach Zeit sortieren (falls vorhanden).
        basis = df.sort_values("time_min") if "time_min" in df.columns else df

        def werte_fuer(ax, x_spalte, y_spalte, y_label, farbe, linienbreite, linienstil, seite, ist_kinetik_seite):
            if x_spalte not in basis.columns or y_spalte not in basis.columns:
                ax.text(
                    0.5, 0.5, f"Spalte fehlt: {x_spalte if x_spalte not in basis.columns else y_spalte}",
                    ha="center", va="center", transform=ax.transAxes,
                )
                return y_label

            gueltig = basis.dropna(subset=[x_spalte, y_spalte])
            x_werte = gueltig[x_spalte].to_numpy()
            y_werte = gueltig[y_spalte].to_numpy(dtype=float)
            ist_eafd = False

            if y_spalte in self._TGA_MASSE_SPALTEN:
                y_werte, _dummy, ist_eafd = self._tga_auf_eafd_basis(y_werte, np.zeros_like(y_werte), cao_pct)
            elif y_spalte in self._TGA_KINETIK_SPALTEN:
                _dummy, y_werte, ist_eafd = self._tga_auf_eafd_basis(np.full_like(y_werte, 100.0), y_werte, cao_pct)
                # --- Reaktionskinetik glätten (Settings -> Filter) ---
                kinetik_filter = self._baue_tga_kinetik_filter(zustand)
                if kinetik_filter is not None:
                    try:
                        y_serie = pd.Series(y_werte).reset_index(drop=True)
                        x_index = pd.Series(np.arange(len(y_serie), dtype=float))
                        y_werte = kinetik_filter(x_index, y_serie).to_numpy()
                    except Exception as e:
                        print(f"[TGA-Kinetik-Filter] Filter fehlgeschlagen, zeige ungefiltert: {e}")

            ax.plot(
                x_werte, y_werte, color=farbe, linewidth=linienbreite, linestyle=linienstil,
                label=y_label.replace("\n", " "),
            )
            if ist_kinetik_seite:
                ax.axhline(0, linestyle="--", linewidth=0.5, color="grey")

            if not ist_eafd:
                # Ohne CaO-Wert bleibt es bei "% der Mischung" - Beschriftung
                # entsprechend anpassen, außer der Nutzer hat sie manuell
                # überschrieben (dann bleibt seine Wahl unangetastet).
                if y_label == "Relative Mass\n[% of EAFD]":
                    y_label = "Relative Mass\n[% of mixture]"
                elif y_label == "Reaction Kinetics\n[%/min of EAFD]":
                    y_label = "Reaction Kinetics\n[%/min of mixture]"
            return y_label

        y_label_masse = werte_fuer(
            ax_masse, zustand["diagramm1_x_spalte"], zustand["diagramm1_y_spalte"],
            tga_achsen_label_fuer_spalte(zustand["diagramm1_y_spalte"]),
            zustand["linienfarbe"], zustand["linienbreite"], zustand["linienstil_links"],
            "links", ist_kinetik_seite=False,
        )
        y_label_kinetik = werte_fuer(
            ax_kinetik, zustand["diagramm2_x_spalte"], zustand["diagramm2_y_spalte"],
            tga_achsen_label_fuer_spalte(zustand["diagramm2_y_spalte"]),
            zustand["linienfarbe2"], zustand["linienbreite2"], zustand["linienstil_rechts"],
            "rechts", ist_kinetik_seite=True,
        )

        ax_masse.set_xlabel(
            tga_achsen_label_fuer_spalte(zustand["diagramm1_x_spalte"]),
            fontsize=zustand.get("schriftgroesse_achsen_links", 11),
        )
        ax_masse.set_ylabel(y_label_masse, fontsize=zustand.get("schriftgroesse_achsen_links", 11))
        ax_masse.set_title(
            zustand["title_masse"], fontsize=zustand.get("schriftgroesse_titel_links", 13),
            color=zustand.get("schriftfarbe_titel_links", "#000000") or "#000000",
        )
        self._tga_style_achse(ax_masse, "links", zustand)
        self._tga_zeige_legende(ax_masse, "links", zustand)

        ax_kinetik.set_xlabel(
            tga_achsen_label_fuer_spalte(zustand["diagramm2_x_spalte"]),
            fontsize=zustand.get("schriftgroesse_achsen_rechts", 11),
        )
        ax_kinetik.set_ylabel(y_label_kinetik, fontsize=zustand.get("schriftgroesse_achsen_rechts", 11))
        ax_kinetik.set_title(
            zustand["title_kinetik"], fontsize=zustand.get("schriftgroesse_titel_rechts", 13),
            color=zustand.get("schriftfarbe_titel_rechts", "#000000") or "#000000",
        )
        self._tga_style_achse(ax_kinetik, "rechts", zustand)
        self._tga_zeige_legende(ax_kinetik, "rechts", zustand)

        # --- Figure-weiter Hintergrund (ein gemeinsames Canvas fuer beide
        # Achsen) - "Transparenter Hintergrund" wirkt sich vor allem beim
        # PNG/SVG-Download aus (siehe speichere_diagrammausschnitt). ---
        if bool(zustand.get("transparenter_hintergrund", False)):
            fig.patch.set_alpha(0.0)
        else:
            fig.patch.set_alpha(1.0)
            fig.patch.set_facecolor(zustand.get("hintergrund_figure", "#ffffff") or "#ffffff")

        if bool(zustand.get("tight_layout", True)):
            # Vertikale Darstellung braucht auf Laptop-Bildschirmen etwas
            # mehr Luft zwischen Titel/X-Achse und dem nächsten Diagramm.
            if zustand.get("diagramm_layout") == "vertical":
                fig.tight_layout(h_pad=4.5, pad=1.5)
            else:
                fig.tight_layout()
        canvas.draw_idle()

    # ------------------------------------------------------------------
    # HILFSFUNKTIONEN (Ordnerstruktur)
    # ------------------------------------------------------------------
    def erstelle_versuchs_struktur(self, projekt, staub, methode):
        base_dir = os.path.join(self.get_projekt_root(projekt), staub, methode)
        # data/raw_data + data/processed_data: Rohdaten und verarbeitete Daten
        # liegen gemeinsam im Überordner "data". outputs/diagramm: Ziel für
        # heruntergeladene Diagramm-Bilder (siehe Download-Buttons im
        # Ergebnisse-Tab).
        for sub in ["data/raw_data", "data/processed_data", "outputs/diagramm"]:
            os.makedirs(os.path.join(base_dir, sub), exist_ok=True)

    def diagramm_ordner_fuer(self, raw_data_ordner):
        """
        Zielordner für heruntergeladene Diagramm-Bilder, gespiegelt vom
        tatsächlichen raw_data-Ordner des Versuchs (analog zu
        processed_data_ordner_fuer) - funktioniert für BEIDE unterstützten
        Strukturen (siehe finde_raw_data_ordner):
          .../<Methode>/data/raw_data  -> .../<Methode>/outputs/diagramm
          .../<Methode>/raw_data       -> .../<Methode>/outputs/diagramm
        Wird bei Bedarf angelegt (inkl. "outputs", falls noch nicht vorhanden).
        """
        data_raw_suffix = os.path.join("data", "raw_data")
        if raw_data_ordner.endswith(os.sep + data_raw_suffix):
            methode_ordner = raw_data_ordner[: -(len(data_raw_suffix) + 1)]
        elif raw_data_ordner.endswith(os.sep + "raw_data"):
            methode_ordner = raw_data_ordner[: -(len("raw_data") + 1)]
        else:
            methode_ordner = os.path.dirname(raw_data_ordner)

        ziel_ordner = os.path.join(methode_ordner, "outputs", "diagramm")
        os.makedirs(ziel_ordner, exist_ok=True)
        return ziel_ordner

if __name__ == "__main__":
    app = LaborApp()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C ist ein normaler Benutzerabbruch. Das Fenster sauber
        # schliessen, damit CustomTkinter keine ausstehenden after()-Callbacks
        # (update/check_dpi_scaling) gegen bereits zerstoerte Widgets ausfuehrt.
        app.beim_schliessen()