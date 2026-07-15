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
from tkinter import messagebox
from datetime import datetime

# --- KONFIGURATION ---
BASIS_PFAD = r"C:\Users\aaron\Nextcloud\Documents\work\H2Lab_Evaluation_System"
MUL_TURKIS = "#008c96"
MUL_DUNKEL = "#0a2a2d"

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
    def __init__(self):
        super().__init__()
        self.title("MUL - H2Lab Staub-System")
        self.geometry("600x800")
        ctk.set_appearance_mode("System")

        # Bekanntes CustomTkinter-Problem: die interne DPI-Scaling-Prüfschleife
        # (check_dpi_scaling / update, per self.after() geplant) läuft weiter,
        # auch nachdem das Fenster mit dem X geschlossen wurde -> danach
        # "invalid command name ... (after script)" im Terminal. Fix laut
        # CustomTkinter-Doku: beim Schließen ZUERST quit(), DANN destroy().
        self.protocol("WM_DELETE_WINDOW", self.beim_schliessen)

        self.aktuelle_sheet_daten = None  # Zeilen aus dem Datenblatt des aktuell gewählten Projekts
        self.ergebnisse_cache = {}  # {Methode: (header, gefilterte_zeilen)} - vorberechnet pro Projektwechsel

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
        top.title(f"{projekt} – {methode}")

        # Fenster an die Bildschirmgröße anpassen (statt fixer 680x620), damit die
        # Tabellen (Metadaten/Rohdaten/Ergebnisse) genug Platz haben und Werte
        # nicht abgeschnitten werden. Zusätzlich frei größenverstellbar.
        bildschirm_breite = top.winfo_screenwidth()
        bildschirm_hoehe = top.winfo_screenheight()
        breite = min(int(bildschirm_breite * 0.85), 1400)
        hoehe = min(int(bildschirm_hoehe * 0.85), 950)
        x = (bildschirm_breite - breite) // 2
        y = (bildschirm_hoehe - hoehe) // 2
        top.geometry(f"{breite}x{hoehe}+{x}+{y}")
        top.minsize(900, 600)
        top.resizable(True, True)

        ctk.CTkLabel(top, text=f"{methode} – {projekt}", font=("Arial", 16, "bold")).pack(pady=(15, 10))

        tabs = ctk.CTkTabview(top)
        tabs.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        tab_metadaten = tabs.add("Metadaten")
        tab_rohdaten = tabs.add("Rohdaten")
        tab_ergebnisse = tabs.add("Ergebnisse")

        self.baue_metadaten_tab(tab_metadaten, projekt, methode)
        self.baue_rohdaten_tab(tab_rohdaten, projekt, methode)
        self.baue_ergebnisse_tab(tab_ergebnisse, projekt, methode)

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
            text="Berechnen",
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
        speicherbar = {k: v for k, v in zustand.items() if k != "versuch_name"}
        try:
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(speicherbar, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[Diagramm-Einstellungen speichern Fehler] {pfad}: {e}")

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

        # Figure/Canvas EINMAL erzeugen (siehe Docstring oben)
        fig, (ax_hoehe, ax_form) = plt.subplots(1, 2, figsize=(10, 4.6))
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        cmap = plt.get_cmap("jet")
        vmin_start, vmax_start = farbskala_grenzen()
        sm = cm.ScalarMappable(cmap=cmap, norm=mcolors.Normalize(vmin=vmin_start, vmax=vmax_start))
        sm.set_array([])
        farbskala = fig.colorbar(sm, ax=ax_form, shrink=0.85)

        def zeichne():
            versuch_name = zustand["versuch_name"]
            if not versuch_name:
                return
            eintrag_info = next(
                (s, e, p) for s, e, p in verarbeitete_versuche
                if os.path.splitext(e)[0] == versuch_name
            )
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
            zustand["versuch_name"] = anzeige_zu_name.get(anzeige, anzeige)
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

        def oeffne_settings():
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

                zeichne()
                self.speichere_diagramm_einstellungen(projekt, methode, zustand)
                dialog.destroy()

            # WICHTIG: Button-Leiste ZUERST mit side="bottom" packen, damit ihr
            # Platz fest reserviert ist - sonst kann zu viel Inhalt darüber
            # (wie beim "Berechnen"-Button vorher) den Button aus dem
            # sichtbaren Bereich drücken und er wirkt "kaputt"/unklickbar.
            button_zeile = ctk.CTkFrame(dialog, fg_color="transparent")
            button_zeile.pack(side="bottom", fill="x", pady=10)
            ctk.CTkButton(button_zeile, text="Übernehmen", fg_color=MUL_TURKIS, command=uebernehmen).pack()

            inhalt = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
            inhalt.pack(fill="both", expand=True, padx=5, pady=(10, 0))

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
                    ax_hoehe.plot(gueltig["Temperature"], hoehe_rel_pct, color=MUL_TURKIS, linewidth=2)
        ax_hoehe.set_xlabel(zustand["label_x"])
        ax_hoehe.set_ylabel(zustand["label_y"])
        ax_hoehe.set_title(zustand["title_hoehe"])
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
        farbskala.set_label(zustand["label_x"])

        ax_form.set_xlabel(zustand["label_x_form"])
        ax_form.set_ylabel(zustand["label_y_form"])
        ax_form.set_title(zustand["title_form"])
        ax_form.grid(True, alpha=0.2)

        fig.tight_layout()
        canvas.draw_idle()

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
            "title_masse": "Relative Mass",
            "label_x": "Temperature [°C]",
            "label_y_masse": "Relative Mass\n[% of EAFD]",
            "x_min": None,
            "x_max": None,
            "y_min_masse": None,
            "y_max_masse": None,
            "title_kinetik": "Reaction Kinetics",
            "label_y_kinetik": "Reaction Kinetics\n[%/min of EAFD]",
            "y_min_kinetik": None,
            "y_max_kinetik": None,
            # --- Filter fuer die Reaktionskinetik (rechtes Diagramm) ---
            "filter_typ": "Butterworth",
            "filter_butter_cutoff": 0.05,
            "filter_butter_order": 2,
            "filter_savgol_window": 15,
            "filter_savgol_polyorder": 2,
            "filter_ema_alpha": 0.3,
            "filter_median_kernel": 9,
            "filter_gauss_sigma": 1.0,
            "filter_rollavg_fenster": 10,
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

        fig, (ax_masse, ax_kinetik) = plt.subplots(1, 2, figsize=(10, 4.6))
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        def zeichne():
            versuch_name = zustand["versuch_name"]
            if not versuch_name:
                return
            eintrag_info = next(
                (s, e, p) for s, e, p in verarbeitete_versuche
                if os.path.splitext(e)[0] == versuch_name
            )
            zustand["_aktueller_raw_data_ordner"] = os.path.dirname(eintrag_info[2])
            self.zeichne_ergebnis_plot_tga(eintrag_info, fig, ax_masse, ax_kinetik, canvas, zustand)

        def speichere_diagrammausschnitt(ax, dateiname_teil):
            versuch_name = zustand["versuch_name"]
            raw_data_ordner = zustand.get("_aktueller_raw_data_ordner")
            if not versuch_name or not raw_data_ordner:
                return
            try:
                ziel_ordner = self.diagramm_ordner_fuer(raw_data_ordner)
                zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
                dateiname = f"{self._sanitiere_versuchsnamen(versuch_name)}_{dateiname_teil}_{zeitstempel}.png"
                ziel_pfad = os.path.join(ziel_ordner, dateiname)
                canvas.draw()
                bbox = ax.get_tightbbox(canvas.get_renderer()).transformed(fig.dpi_scale_trans.inverted())
                fig.savefig(ziel_pfad, dpi=200, bbox_inches=bbox)
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
            zustand["versuch_name"] = anzeige_zu_name.get(anzeige, anzeige)
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
            def aktualisieren(_event=None):
                wert = entry.get().strip() or "..."
                label_widget.configure(text=f"{praefix}{wert}{suffix}")
            entry.bind("<KeyRelease>", aktualisieren)

        def oeffne_settings():
            dialog = ctk.CTkToplevel(self)
            dialog.title("Diagramm-Einstellungen")
            dialog.geometry("480x680")
            dialog.minsize(420, 420)
            dialog.resizable(True, True)
            dialog.attributes("-topmost", True)
            dialog.lift()
            dialog.focus_force()

            def uebernehmen():
                zustand["title_masse"] = eingabe_titel_masse.get().strip() or zustand["title_masse"]
                zustand["label_y_masse"] = eingabe_y_label_masse.get().strip() or zustand["label_y_masse"]
                zustand["label_x"] = eingabe_x_label.get().strip() or zustand["label_x"]
                zustand["x_min"] = als_zahl_oder_none(eingabe_x_min.get())
                zustand["x_max"] = als_zahl_oder_none(eingabe_x_max.get())
                zustand["y_min_masse"] = als_zahl_oder_none(eingabe_y_min_masse.get())
                zustand["y_max_masse"] = als_zahl_oder_none(eingabe_y_max_masse.get())

                zustand["title_kinetik"] = eingabe_titel_kinetik.get().strip() or zustand["title_kinetik"]
                zustand["label_y_kinetik"] = eingabe_y_label_kinetik.get().strip() or zustand["label_y_kinetik"]
                zustand["y_min_kinetik"] = als_zahl_oder_none(eingabe_y_min_kinetik.get())
                zustand["y_max_kinetik"] = als_zahl_oder_none(eingabe_y_max_kinetik.get())

                zustand["filter_typ"] = filter_dropdown.get()
                # Alle Parameterfelder (auch von gerade nicht sichtbaren
                # Filtertypen) uebernehmen, damit beim spaeteren Zurueckwechseln
                # die zuletzt eingestellten Werte erhalten bleiben.
                for felder in filter_eingabe_widgets.values():
                    for schluessel, entry in felder.items():
                        wert = als_zahl_oder_none(entry.get())
                        if wert is not None:
                            zustand[schluessel] = wert

                zeichne()
                self.speichere_diagramm_einstellungen(projekt, methode, zustand)
                dialog.destroy()

            button_zeile = ctk.CTkFrame(dialog, fg_color="transparent")
            button_zeile.pack(side="bottom", fill="x", pady=10)
            ctk.CTkButton(button_zeile, text="Übernehmen", fg_color=MUL_TURKIS, command=uebernehmen).pack()

            inhalt = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
            inhalt.pack(fill="both", expand=True, padx=5, pady=(10, 0))

            # --- Linkes Diagramm (Relative Masse) ---
            header_links = ctk.CTkLabel(
                inhalt, text=f"Linkes Diagramm ({zustand['title_masse']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_links.pack(fill="x", padx=10, pady=(5, 5))

            ctk.CTkLabel(inhalt, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_masse = ctk.CTkEntry(inhalt)
            eingabe_titel_masse.insert(0, zustand["title_masse"])
            eingabe_titel_masse.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_titel_masse, header_links, "Linkes Diagramm (", ")")

            ctk.CTkLabel(inhalt, text="Y-Achsen-Beschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label_masse = ctk.CTkEntry(inhalt)
            eingabe_y_label_masse.insert(0, zustand["label_y_masse"])
            eingabe_y_label_masse.pack(fill="x", padx=10, pady=(2, 10))

            ctk.CTkLabel(
                inhalt, text="X-Achsen-Beschriftung (gilt für beide Diagramme):", anchor="w",
            ).pack(fill="x", padx=10)
            eingabe_x_label = ctk.CTkEntry(inhalt)
            eingabe_x_label.insert(0, zustand["label_x"])
            eingabe_x_label.pack(fill="x", padx=10, pady=(2, 10))

            bereich_zeile_x = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_x.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(bereich_zeile_x, text="X-Achse von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_x_min = ctk.CTkEntry(bereich_zeile_x, placeholder_text="auto")
            eingabe_x_min.insert(0, "" if zustand["x_min"] is None else str(zustand["x_min"]))
            eingabe_x_min.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_x_max = ctk.CTkEntry(bereich_zeile_x, placeholder_text="auto")
            eingabe_x_max.insert(0, "" if zustand["x_max"] is None else str(zustand["x_max"]))
            eingabe_x_max.pack(side="left", padx=(5, 0), fill="x", expand=True)

            bereich_zeile_y_masse = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_y_masse.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(bereich_zeile_y_masse, text="Y-Achse von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_y_min_masse = ctk.CTkEntry(bereich_zeile_y_masse, placeholder_text="auto")
            eingabe_y_min_masse.insert(0, "" if zustand["y_min_masse"] is None else str(zustand["y_min_masse"]))
            eingabe_y_min_masse.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_y_max_masse = ctk.CTkEntry(bereich_zeile_y_masse, placeholder_text="auto")
            eingabe_y_max_masse.insert(0, "" if zustand["y_max_masse"] is None else str(zustand["y_max_masse"]))
            eingabe_y_max_masse.pack(side="left", padx=(5, 0), fill="x", expand=True)

            # --- Trennlinie ---
            ctk.CTkFrame(inhalt, height=2, fg_color=("gray75", "gray30")).pack(fill="x", padx=10, pady=(5, 15))

            # --- Rechtes Diagramm (Reaktionskinetik) ---
            header_rechts = ctk.CTkLabel(
                inhalt, text=f"Rechtes Diagramm ({zustand['title_kinetik']})",
                font=("Arial", 13, "bold"), anchor="w",
            )
            header_rechts.pack(fill="x", padx=10, pady=(0, 5))

            ctk.CTkLabel(inhalt, text="Titel:", anchor="w").pack(fill="x", padx=10)
            eingabe_titel_kinetik = ctk.CTkEntry(inhalt)
            eingabe_titel_kinetik.insert(0, zustand["title_kinetik"])
            eingabe_titel_kinetik.pack(fill="x", padx=10, pady=(2, 10))
            live_kopplung(eingabe_titel_kinetik, header_rechts, "Rechtes Diagramm (", ")")

            ctk.CTkLabel(inhalt, text="Y-Achsen-Beschriftung:", anchor="w").pack(fill="x", padx=10)
            eingabe_y_label_kinetik = ctk.CTkEntry(inhalt)
            eingabe_y_label_kinetik.insert(0, zustand["label_y_kinetik"])
            eingabe_y_label_kinetik.pack(fill="x", padx=10, pady=(2, 10))

            bereich_zeile_y_kinetik = ctk.CTkFrame(inhalt, fg_color="transparent")
            bereich_zeile_y_kinetik.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(bereich_zeile_y_kinetik, text="Y-Achse von/bis:", width=110, anchor="w").pack(side="left")
            eingabe_y_min_kinetik = ctk.CTkEntry(bereich_zeile_y_kinetik, placeholder_text="auto")
            eingabe_y_min_kinetik.insert(0, "" if zustand["y_min_kinetik"] is None else str(zustand["y_min_kinetik"]))
            eingabe_y_min_kinetik.pack(side="left", padx=(5, 5), fill="x", expand=True)
            eingabe_y_max_kinetik = ctk.CTkEntry(bereich_zeile_y_kinetik, placeholder_text="auto")
            eingabe_y_max_kinetik.insert(0, "" if zustand["y_max_kinetik"] is None else str(zustand["y_max_kinetik"]))
            eingabe_y_max_kinetik.pack(side="left", padx=(5, 0), fill="x", expand=True)

            # --- Filter fuer die Reaktionskinetik (rechtes Diagramm) ---
            ctk.CTkLabel(inhalt, text="Filter (Glättung):", anchor="w").pack(fill="x", padx=10, pady=(5, 0))

            if _FILTER_IMPORT_FEHLER:
                ctk.CTkLabel(
                    inhalt,
                    text=(
                        "Filter aus helper/Filter.py konnten nicht geladen werden:\n"
                        f"{_FILTER_IMPORT_FEHLER}"
                    ),
                    text_color="#ff8080", anchor="w", justify="left", wraplength=420,
                ).pack(fill="x", padx=10, pady=(2, 10))

            filter_parameter_frame = ctk.CTkFrame(inhalt, fg_color="transparent")
            # gefuellt von _zeige_filter_parameter() weiter unten
            filter_eingabe_widgets = {}  # {filter_typ: {zustand_schluessel: entry_widget}}

            def _zeige_filter_parameter(*_):
                for kind in filter_parameter_frame.winfo_children():
                    kind.destroy()
                filter_parameter_frame.pack(fill="x", padx=0, pady=(0, 10))

                aktueller_typ = filter_dropdown.get()
                felder_definition = TGA_FILTER_PARAMETER.get(aktueller_typ, [])
                widgets_fuer_typ = {}
                for schluessel, label_text, _default in felder_definition:
                    ctk.CTkLabel(filter_parameter_frame, text=f"{label_text}:", anchor="w").pack(
                        fill="x", padx=10
                    )
                    eingabe = ctk.CTkEntry(filter_parameter_frame)
                    eingabe.insert(0, str(zustand.get(schluessel, "")))
                    eingabe.pack(fill="x", padx=10, pady=(2, 8))
                    widgets_fuer_typ[schluessel] = eingabe
                filter_eingabe_widgets[aktueller_typ] = widgets_fuer_typ

            filter_dropdown_werte = TGA_FILTER_OPTIONEN if not _FILTER_IMPORT_FEHLER else ["Kein Filter"]
            filter_dropdown = ctk.CTkOptionMenu(
                inhalt, values=filter_dropdown_werte, fg_color=MUL_TURKIS, command=_zeige_filter_parameter,
            )
            filter_dropdown.set(
                zustand.get("filter_typ", "Kein Filter")
                if zustand.get("filter_typ", "Kein Filter") in filter_dropdown_werte
                else "Kein Filter"
            )
            filter_dropdown.pack(fill="x", padx=10, pady=(2, 8))

            _zeige_filter_parameter()

        dropdown = ctk.CTkOptionMenu(
            seitenleiste, values=anzeige_werte, command=zeige_versuch, fg_color=MUL_TURKIS,
        )
        dropdown.pack(fill="x", pady=(0, 15))

        ctk.CTkButton(
            seitenleiste, text="Settings", fg_color=MUL_DUNKEL, command=oeffne_settings,
        ).pack(fill="x")

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
        """
        typ = zustand.get("filter_typ", "Kein Filter")
        if typ == "Kein Filter" or ButterworthFilter is None:
            return None
        try:
            if typ == "Butterworth":
                return ButterworthFilter(
                    cutoff=float(zustand.get("filter_butter_cutoff", 0.05)),
                    order=int(zustand.get("filter_butter_order", 2)),
                    time_unit="sec",
                )
            if typ == "Savitzky-Golay":
                fenster = int(zustand.get("filter_savgol_window", 15))
                if fenster % 2 == 0:
                    fenster += 1
                return SavitzkyGolayFilter(
                    window_length=fenster,
                    polyorder=int(zustand.get("filter_savgol_polyorder", 2)),
                )
            if typ == "Exponentielles gleitendes Mittel":
                return ExponentialMovingAverage(alpha=float(zustand.get("filter_ema_alpha", 0.3)))
            if typ == "Median":
                kernel = int(zustand.get("filter_median_kernel", 9))
                if kernel % 2 == 0:
                    kernel += 1
                return MedianFilter(kernel_size=kernel)
            if typ == "Gaussian":
                return GaussianFilter(sigma=float(zustand.get("filter_gauss_sigma", 1.0)))
            if typ == "Gleitender Mittelwert":
                return RollingAverage(sampling_rate=int(zustand.get("filter_rollavg_fenster", 10)))
        except Exception as e:
            print(f"[TGA-Kinetik-Filter] Ungültige Einstellungen ({typ}): {e}")
            return None
        return None

    def zeichne_ergebnis_plot_tga(self, eintrag_info, fig, ax_masse, ax_kinetik, canvas, zustand):
        """
        Zeichnet Relative Masse (links) und Reaktionskinetik (rechts) über
        der Temperatur für einen TGA-Versuch neu in die bestehenden Achsen.
        Rechnet automatisch auf "% of EAFD" um, falls die CaO-Zugabe für den
        Versuch im Sheet hinterlegt ist (siehe _tga_auf_eafd_basis).
        """
        staub, eintrag, voller_pfad = eintrag_info
        df = self.lade_ergebnis_dataframe(eintrag, voller_pfad)

        ax_masse.clear()
        ax_kinetik.clear()

        if df is None or df.empty:
            ax_masse.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            ax_kinetik.text(0.5, 0.5, "Konnte processed_data nicht laden.", ha="center", va="center")
            canvas.draw_idle()
            return

        sheet_name = self._tga_versuchsname_fuer_sheet(eintrag)
        tga_parameter = self.hole_tga_parameter_fuer_versuch(sheet_name)
        cao_pct = tga_parameter.get("cao_pct") if tga_parameter else None

        y_label_masse = zustand["label_y_masse"]
        y_label_kinetik = zustand["label_y_kinetik"]

        if (
            "temperature_C" in df.columns
            and "dm_filtered_pct" in df.columns
            and "dmdt_filtered_pctmin" in df.columns
        ):
            import numpy as np
            import pandas as pd

            # WICHTIG: NICHT nach temperature_C sortieren! Bei einem
            # isothermen Haltebereich (Temperatur bleibt ueber viele
            # Zeitschritte fast konstant, waehrend die Masse weiter faellt)
            # wuerde eine Sortierung nach Temperatur die echte zeitliche
            # Reihenfolge durcheinanderwuerfeln - matplotlib verbindet die
            # Punkte dann kreuz und quer hin und her, was wie eine
            # ausgefuellte Flaeche aussieht (ist aber ein dichtes
            # Liniengewirr). Die Parquet-Datei ist bereits chronologisch
            # sortiert (siehe TGA_calculation.py: sort_values("time_min")),
            # daher hier nur nach Zeit sortieren (falls vorhanden) bzw. die
            # bestehende Reihenfolge beibehalten.
            gueltig = df.dropna(subset=["dm_filtered_pct", "dmdt_filtered_pctmin"])
            if "time_min" in gueltig.columns:
                gueltig = gueltig.sort_values("time_min")
            dm_werte, dmdt_werte, ist_eafd = self._tga_auf_eafd_basis(
                gueltig["dm_filtered_pct"], gueltig["dmdt_filtered_pctmin"], cao_pct
            )

            # --- Reaktionskinetik glätten (Settings -> Filter) ---
            dmdt_anzeige = pd.Series(np.asarray(dmdt_werte)).reset_index(drop=True)
            kinetik_filter = self._baue_tga_kinetik_filter(zustand)
            if kinetik_filter is not None:
                try:
                    x_index = pd.Series(np.arange(len(dmdt_anzeige), dtype=float))
                    dmdt_anzeige = kinetik_filter(x_index, dmdt_anzeige)
                except Exception as e:
                    print(f"[TGA-Kinetik-Filter] Filter fehlgeschlagen, zeige ungefiltert: {e}")
                    dmdt_anzeige = pd.Series(np.asarray(dmdt_werte)).reset_index(drop=True)

            # Nur die Linie zeichnen - keine Flächen-Überlagerung unter der Kurve.
            ax_masse.plot(gueltig["temperature_C"], dm_werte, color=MUL_TURKIS, linewidth=1.5)
            ax_kinetik.plot(gueltig["temperature_C"].to_numpy(), dmdt_anzeige.to_numpy(), color=MUL_TURKIS, linewidth=1.5)
            ax_kinetik.axhline(0, linestyle="--", linewidth=0.5, color="grey")
            if not ist_eafd:
                # Ohne CaO-Wert bleibt es bei "% of mixture" - Beschriftung
                # entsprechend anpassen, außer der Nutzer hat sie manuell
                # überschrieben (dann bleibt seine Wahl unangetastet).
                if y_label_masse == "Relative Mass\n[% of EAFD]":
                    y_label_masse = "Relative Mass\n[% of mixture]"
                if y_label_kinetik == "Reaction Kinetics\n[%/min of EAFD]":
                    y_label_kinetik = "Reaction Kinetics\n[%/min of mixture]"

        ax_masse.set_xlabel(zustand["label_x"])
        ax_masse.set_ylabel(y_label_masse)
        ax_masse.set_title(zustand["title_masse"])
        ax_masse.set_xlim(left=zustand["x_min"], right=zustand["x_max"])
        ax_masse.set_ylim(bottom=zustand["y_min_masse"], top=zustand["y_max_masse"])
        ax_masse.grid(True, alpha=0.3)

        ax_kinetik.set_xlabel(zustand["label_x"])
        ax_kinetik.set_ylabel(y_label_kinetik)
        ax_kinetik.set_title(zustand["title_kinetik"])
        ax_kinetik.set_xlim(left=zustand["x_min"], right=zustand["x_max"])
        ax_kinetik.set_ylim(bottom=zustand["y_min_kinetik"], top=zustand["y_max_kinetik"])
        ax_kinetik.grid(True, alpha=0.3)

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
    app.mainloop()