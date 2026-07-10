import customtkinter as ctk
import os
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
    # GOOGLE SHEET (Projektliste)
    # ------------------------------------------------------------------
    def _status(self, text, farbe="white"):
        """Setzt die Statuszeile, falls sie bereits existiert (defensiv, s. __init__-Reihenfolge)."""
        if hasattr(self, "status_label"):
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
        top.geometry("680x620")

        ctk.CTkLabel(top, text=f"{methode} – {projekt}", font=("Arial", 16, "bold")).pack(pady=(15, 10))

        tabs = ctk.CTkTabview(top, width=650, height=540)
        tabs.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        tab_metadaten = tabs.add("Metadaten")
        tab_rohdaten = tabs.add("Rohdaten")

        self.baue_metadaten_tab(tab_metadaten, projekt, methode)
        self.baue_rohdaten_tab(tab_rohdaten, projekt, methode)

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
    def processed_data_pfad_fuer(self, raw_data_ordner, eintrag):
        """
        Spiegelt den Pfad eines raw_data-Eintrags (Datei oder Ordner) nach
        processed_data (…/data/raw_data/X -> …/data/processed_data/X).
        Erzeugt NICHTS - reine Pfad-Berechnung für den Existenz-Check.
        """
        processed_ordner = raw_data_ordner.replace(
            os.sep + "raw_data", os.sep + "processed_data"
        )
        if processed_ordner == raw_data_ordner:  # falls "raw_data" nicht exakt im Pfad vorkam
            processed_ordner = os.path.join(os.path.dirname(raw_data_ordner), "processed_data")
        return os.path.join(processed_ordner, eintrag)

    def ist_versuch_verarbeitet(self, raw_data_ordner, eintrag):
        """
        Prüft NUR, ob unter processed_data bereits etwas mit demselben Namen
        wie der raw_data-Eintrag existiert (Datei oder Ordner) - es wird
        nichts berechnet oder erzeugt. Die eigentliche Berechnung/Auswertung
        liefert später ein separates Script (kommt noch von Aaron).
        """
        ziel = self.processed_data_pfad_fuer(raw_data_ordner, eintrag)
        return os.path.exists(ziel)

    def starte_berechnung(self, projekt, methode, rohdaten_frame):
        """
        'Berechnen'-Button (Platzhalter-Stand): geht jeden raw_data-Eintrag
        der Methode durch und prüft nur, ob er bereits in processed_data
        liegt - erzeugt/berechnet aktuell nichts. Baut danach die
        Rohdaten-Liste neu auf, damit die Häkchen den aktuellen Stand zeigen.
        """
        versuche = self.liste_versuche(projekt, methode)
        anzahl_gesamt = len(versuche)
        anzahl_verarbeitet = 0

        for staub, eintrag, voller_pfad in versuche:
            raw_data_ordner = os.path.dirname(voller_pfad)
            if self.ist_versuch_verarbeitet(raw_data_ordner, eintrag):
                anzahl_verarbeitet += 1

        self._status(
            f"Geprüft: {anzahl_verarbeitet}/{anzahl_gesamt} Versuche bereits in processed_data.",
            "#00ff88",
        )

        # Rohdaten-Liste neu aufbauen, damit die Häkchen aktuell sind
        for widget in rohdaten_frame.winfo_children():
            widget.destroy()
        self.baue_rohdaten_tab(rohdaten_frame, projekt, methode)

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

        scroll = ctk.CTkScrollableFrame(parent, width=620, height=500)
        scroll.pack(padx=5, pady=5, fill="both", expand=True)

        for staub, eintrag, voller_pfad in versuche:
            versuch_name = os.path.splitext(eintrag)[0]

            karte = ctk.CTkFrame(scroll, fg_color=("gray85", "gray20"))
            karte.pack(fill="x", pady=5, padx=2)

            ctk.CTkLabel(karte, text=versuch_name, font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(6, 2))

            if methode == "EMI":
                emi_parameter = self.hole_emi_parameter_fuer_versuch(versuch_name)
                if emi_parameter:
                    ctk.CTkLabel(
                        karte,
                        text=(
                            f"Material: {emi_parameter['material'] or '-'}   "
                            f"Kommentar (Lime addition): {emi_parameter['kommentar'] or '-'}"
                        ),
                        anchor="w", font=("Arial", 10), wraplength=580,
                    ).pack(anchor="w", padx=10, pady=(0, 2))
                    ctk.CTkLabel(
                        karte,
                        text=(
                            f"Temperaturverlauf: {emi_parameter['temperaturverlauf'] or '-'}   "
                            f"Gas: {emi_parameter['gas'] or '-'}   "
                            f"Durchfluss: {emi_parameter['durchfluss'] or '-'}"
                        ),
                        anchor="w", font=("Arial", 10), wraplength=580,
                    ).pack(anchor="w", padx=10, pady=(0, 8))
                else:
                    ctk.CTkLabel(
                        karte,
                        text="Noch keine Zeile mit passender 'Messung'-ID im Sheet gefunden.",
                        anchor="w", font=("Arial", 10), text_color=("gray40", "gray70"),
                    ).pack(anchor="w", padx=10, pady=(0, 8))
            else:
                ctk.CTkLabel(
                    karte, text="Für diese Methode noch keine Sheet-Anbindung.",
                    anchor="w", font=("Arial", 10), text_color=("gray40", "gray70"),
                ).pack(anchor="w", padx=10, pady=(0, 8))

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

        scroll = ctk.CTkScrollableFrame(parent, width=610, height=440)
        scroll.pack(padx=5, pady=(5, 0), fill="both", expand=True)

        for staub, eintrag, voller_pfad in versuche:
            zeile = ctk.CTkFrame(scroll, fg_color="transparent")
            zeile.pack(fill="x", pady=3)
            symbol = "📁" if os.path.isdir(voller_pfad) else "📄"
            ctk.CTkLabel(zeile, text=f"{symbol} [{staub}] {eintrag}", anchor="w").pack(
                side="left", padx=5
            )

        button_zeile = ctk.CTkFrame(parent, fg_color="transparent")
        button_zeile.pack(fill="x", padx=5, pady=8)
        ctk.CTkButton(
            button_zeile,
            text="Berechnen",
            fg_color=MUL_TURKIS,
            command=lambda: self.starte_berechnung(projekt, methode, parent),
        ).pack(side="right")

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
