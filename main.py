import customtkinter as ctk
import os
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

# Pfad zu EMI_calculation.py (die echte Berechnung von raw_data zu processed_data,
# via HSMTools). Wird als eigener Subprozess gestartet, NICHT im main.py-Prozess
# importiert - so bleibt die GUI responsiv und fehlende Pakete/Abstürze im
# Berechnungs-Skript reißen die App nicht mit runter.
# Wenn None, wird automatisch gesucht:
#   1) neben dieser main.py
#   2) unter <main.py-Ordner>/EMI/data_preparation/EMI_calculation.py
# Falls das Skript wo ganz anders liegt, hier den vollen Pfad eintragen, z.B.:
# EMI_CALCULATION_SCRIPT_PFAD = r"C:\Users\aaron\...\EMI\data_preparation\EMI_calculation.py"
EMI_CALCULATION_SCRIPT_PFAD = None

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
        Bei EMI: sucht die von EMI_calculation.py erzeugte
        "<sanitierter_name>_results.parquet"-Datei in processed_data.
        Bei anderen Methoden (noch kein Berechnungs-Skript vorhanden):
        Platzhalter-Check, ob irgendwas mit demselben Namen existiert.
        """
        processed_ordner = self.processed_data_ordner_fuer(raw_data_ordner)
        if methode == "EMI":
            versuch_name = os.path.splitext(eintrag)[0]
            ziel = os.path.join(processed_ordner, f"{self._sanitiere_versuchsnamen(versuch_name)}_results.parquet")
        else:
            ziel = os.path.join(processed_ordner, eintrag)
        return os.path.exists(ziel)

    # ------------------------------------------------------------------
    # EMI-Berechnung (echtes Skript: EMI_calculation.py / HSMTools)
    # Läuft bewusst NICHT im main.py-Prozess: wird als eigener Python-
    # Subprozess gestartet (wie in der .md als CLI-Aufruf beschrieben), in
    # einem Hintergrund-Thread, damit die GUI währenddessen nicht einfriert
    # und Abstürze/fehlende Pakete im Berechnungs-Skript die App nicht
    # mitreißen.
    # ------------------------------------------------------------------
    def finde_emi_calculation_skript(self):
        """
        Sucht EMI_calculation.py in dieser Reihenfolge:
          1) EMI_CALCULATION_SCRIPT_PFAD (falls gesetzt)
          2) neben main.py
          3) <main.py-Ordner>/EMI/data_preparation/EMI_calculation.py
        Gibt den Pfad zurück oder wirft FileNotFoundError.
        """
        skript_ordner = os.path.dirname(os.path.abspath(__file__))
        kandidaten = []
        if EMI_CALCULATION_SCRIPT_PFAD:
            kandidaten.append(EMI_CALCULATION_SCRIPT_PFAD)
        kandidaten.append(os.path.join(skript_ordner, "EMI_calculation.py"))
        kandidaten.append(os.path.join(skript_ordner, "EMI", "data_preparation", "EMI_calculation.py"))

        gefundener_pfad = next((p for p in kandidaten if p and os.path.isfile(p)), None)
        if not gefundener_pfad:
            raise FileNotFoundError(
                "EMI_calculation.py nicht gefunden. Gesucht an:\n"
                + "\n".join(kandidaten)
                + "\n\nEntweder das Skript dort ablegen oder EMI_CALCULATION_SCRIPT_PFAD "
                "am Kopf von main.py auf den vollen Pfad setzen."
            )
        return gefundener_pfad

    def fuehre_emi_berechnung_aus(self, unverarbeitete_versuche, log_zeile_callback):
        """
        Startet EMI_calculation.py als eigenen Python-Subprozess (CLI, wie in
        der .md dokumentiert) - NICHT im main.py-Prozess. Läuft in DIESEM
        Aufruf synchron (blockierend), wird daher von starte_berechnung()
        immer in einem Hintergrund-Thread aufgerufen, nie direkt im GUI-Thread.
        Ruft NUR für die übergebenen, noch unverarbeiteten Versuche auf
        (--samples, kein --force) - bestehende processed_data-Dateien
        anderer Versuche bleiben unangetastet. Gruppiert nach raw_data-
        Ordner, da das Skript einen ganzen Ordner voller Sample-Unterordner
        pro Aufruf erwartet.

        log_zeile_callback(text) wird für JEDE Ausgabezeile des Subprozesses
        sofort aufgerufen (live), damit man im UI sieht, dass/was gerade
        passiert - statt stumm auf das Ende zu warten.

        Gibt None bei Erfolg zurück, sonst einen Fehlertext.
        """
        try:
            skript_pfad = self.finde_emi_calculation_skript()
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
                return f"Konnte EMI_calculation.py nicht starten ('{raw_data_ordner}'):\n{e}"

            gesammelte_ausgabe = []
            for zeile in prozess.stdout:
                zeile = zeile.rstrip("\n")
                gesammelte_ausgabe.append(zeile)
                log_zeile_callback(zeile)

            returncode = prozess.wait(timeout=3600)
            if returncode != 0:
                ausgabe = "\n".join(gesammelte_ausgabe[-40:]) or "(keine Ausgabe)"
                return f"EMI_calculation.py meldete einen Fehler (Exit-Code {returncode}) in '{raw_data_ordner}':\n\n{ausgabe}"

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
        'Berechnen'-Button. Bei EMI: startet die echte Berechnung
        (EMI_calculation.py/HSMTools) als eigenen Subprozess in einem
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

        if methode != "EMI":
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
            self._status("Alle EMI-Versuche bereits in processed_data vorhanden.", "#00ff88")
            for widget in rohdaten_frame.winfo_children():
                widget.destroy()
            self.baue_rohdaten_tab(rohdaten_frame, projekt, methode)
            return

        self._status(f"Berechne {len(unverarbeitete)} EMI-Versuch(e) im Hintergrund ...", "#ffff00")
        log_fenster, log_textbox = self.oeffne_berechnungs_log_fenster(
            f"EMI-Berechnung läuft: {projekt} ({len(unverarbeitete)} Versuch(e))"
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

            fehler = self.fuehre_emi_berechnung_aus(unverarbeitete, live_zeile)
            self.after(0, lambda: fertig_im_gui_thread(fehler))

        def fertig_im_gui_thread(fehler):
            if fehler:
                log_anhaengen(f"\n--- FEHLER ---\n{fehler}")
                messagebox.showerror("EMI-Berechnung fehlgeschlagen", fehler)
                self._status("EMI-Berechnung fehlgeschlagen (siehe Fehlermeldung/Log).", "#ff5555")
            else:
                jetzt_verarbeitet = anzahl_gesamt - sum(
                    1 for _s, e, p in versuche
                    if not self.ist_versuch_verarbeitet(os.path.dirname(p), e, methode)
                )
                log_anhaengen(f"\n--- FERTIG ({jetzt_verarbeitet}/{anzahl_gesamt} verarbeitet) ---")
                self._status(
                    f"EMI-Berechnung abgeschlossen ({jetzt_verarbeitet}/{anzahl_gesamt} verarbeitet).",
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
                    werte = [versuch_name, "Noch keine passende Zeile im Sheet gefunden.", "", "", "", ""]
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
    # TAB: ERGEBNISSE (Höhenverlauf + Form pro Versuch, aus processed_data)
    # ------------------------------------------------------------------
    def baue_ergebnisse_tab(self, parent, projekt, methode):
        """
        Zeigt pro (bereits verarbeitetem) Versuch den Höhenverlauf
        (sample_height_px über Temperature) und die Form/Kontur
        (contour_x/contour_y) aus der zugehörigen <versuch>_results.parquet-
        Datei. Jeder Versuch hat seine eigene Form, daher Auswahl per Dropdown.
        """
        if methode != "EMI":
            ctk.CTkLabel(
                parent, text="Ergebnisdarstellung ist aktuell nur für EMI verfügbar."
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

        auswahl_zeile = ctk.CTkFrame(parent, fg_color="transparent")
        auswahl_zeile.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(auswahl_zeile, text="Versuch:", font=("Arial", 12, "bold")).pack(
            side="left", padx=(0, 10)
        )

        plot_frame = ctk.CTkFrame(parent, fg_color="transparent")
        plot_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        versuch_namen = [os.path.splitext(eintrag)[0] for _s, eintrag, _p in verarbeitete_versuche]

        def zeige_versuch(versuch_name):
            for widget in plot_frame.winfo_children():
                widget.destroy()
            eintrag_info = next(
                (s, e, p) for s, e, p in verarbeitete_versuche
                if os.path.splitext(e)[0] == versuch_name
            )
            self.rendere_ergebnis_plot(plot_frame, eintrag_info)

        dropdown = ctk.CTkOptionMenu(
            auswahl_zeile, values=versuch_namen, command=zeige_versuch, fg_color=MUL_TURKIS,
        )
        dropdown.pack(side="left")

        zeige_versuch(versuch_namen[0])

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

    def rendere_ergebnis_plot(self, parent, eintrag_info):
        """Zeichnet Höhenverlauf (links) und letzte gültige Form/Kontur (rechts) für einen Versuch."""
        staub, eintrag, voller_pfad = eintrag_info
        df = self.lade_ergebnis_dataframe(eintrag, voller_pfad)

        if df is None or df.empty:
            ctk.CTkLabel(
                parent, text="Konnte processed_data nicht laden (siehe Konsole für Details)."
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

        fig, (ax_hoehe, ax_form) = plt.subplots(1, 2, figsize=(9, 4.2))

        # --- Höhenverlauf ---
        if "Temperature" in df.columns and "sample_height_px" in df.columns:
            gueltig = df.dropna(subset=["sample_height_px"]).sort_values("Temperature")
            if not gueltig.empty:
                referenz_hoehe = gueltig["sample_height_px"].iloc[0]
                if referenz_hoehe:
                    hoehe_rel_pct = gueltig["sample_height_px"] / referenz_hoehe * 100
                    ax_hoehe.plot(gueltig["Temperature"], hoehe_rel_pct, color=MUL_TURKIS, linewidth=2)
        ax_hoehe.set_xlabel("Temperature [°C]")
        ax_hoehe.set_ylabel("Sample Height [% of initial]")
        ax_hoehe.set_title("Höhenverlauf")
        ax_hoehe.grid(True, alpha=0.3)

        # --- Form / Kontur (letzte gültige, meist bei höchster Temperatur) ---
        letzte_kontur = None
        if "contour_x" in df.columns and "contour_y" in df.columns:
            for _, zeile in df.iloc[::-1].iterrows():
                cx, cy = zeile.get("contour_x"), zeile.get("contour_y")
                if cx is not None and cy is not None and len(cx) > 0:
                    letzte_kontur = (cx, cy)
                    break
        if letzte_kontur:
            cx, cy = letzte_kontur
            ax_form.plot(cx, cy, color=MUL_DUNKEL, linewidth=2)
            ax_form.invert_yaxis()
            ax_form.set_aspect("equal", adjustable="box")
        ax_form.set_xlabel("x [px]")
        ax_form.set_ylabel("y [px]")
        ax_form.set_title("Form (letzte gültige Kontur)")
        ax_form.grid(True, alpha=0.3)

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # HILFSFUNKTIONEN (Ordnerstruktur)
    # ------------------------------------------------------------------
    def erstelle_versuchs_struktur(self, projekt, staub, methode):
        base_dir = os.path.join(self.get_projekt_root(projekt), staub, methode)
        for sub in ["data/raw_data", "data/processed_data", "diagram"]:
            os.makedirs(os.path.join(base_dir, sub), exist_ok=True)


if __name__ == "__main__":
    app = LaborApp()
    app.mainloop()