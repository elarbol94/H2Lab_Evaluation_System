import sqlite3

def get_db_connection():
    conn = sqlite3.connect('labor_daten.db')
    conn.row_factory = sqlite3.Row  
    return conn

def init_db(): 
    conn = get_db_connection() 
    conn.executescript('''  
        CREATE TABLE IF NOT EXISTS Projekte (
            Projekt_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Projektname TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS StaubProben (
            Probe_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ProbenName TEXT NOT NULL UNIQUE
        );
        -- Das ist die Verknüpfungstabelle:
        CREATE TABLE IF NOT EXISTS Projekt_Probe_Verknuepfung (
            Projekt_ID INTEGER,
            Probe_ID INTEGER,
            FOREIGN KEY (Projekt_ID) REFERENCES Projekte(Projekt_ID),
            FOREIGN KEY (Probe_ID) REFERENCES StaubProben(Probe_ID)
        );
        CREATE TABLE IF NOT EXISTS Versuche (
            Versuch_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Probe_ID INTEGER,
            Methode TEXT,
            Versuchsnummer TEXT,
            FOREIGN KEY (Probe_ID) REFERENCES StaubProben(Probe_ID)
        );
        # Ersetze die einfache 'Versuche' Tabelle durch spezifische Tabellen:
        CREATE TABLE IF NOT EXISTS Versuche_EMI (
            Versuch_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Probe_ID INTEGER,
            Versuchsnummer TEXT,
            GasDurchfluss REAL,
            BildPfad TEXT,
            Datum TEXT,
            FOREIGN KEY (Probe_ID) REFERENCES StaubProben(Probe_ID)
        );
        CREATE TABLE IF NOT EXISTS Versuche_TGA (
            Versuch_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Probe_ID INTEGER,
            Versuchsnummer TEXT,
            Temperaturverlauf TEXT,
            GewichtsAenderung REAL,
            Datum TEXT,
            FOREIGN KEY (Probe_ID) REFERENCES StaubProben(Probe_ID)
        );
                       
    ''')
    conn.commit()
    conn.close()

    # 1. Projekte abrufen (damit dein Dropdown in main.py gefüllt wird)
def get_projekte():
    conn = get_db_connection()
    projekte = conn.execute("SELECT * FROM Projekte").fetchall()
    conn.close()
    return projekte

# 2. Eine neue Probe anlegen UND direkt einem Projekt zuweisen
def add_probe_to_projekt(projekt_id, proben_name):
    conn = get_db_connection()
    try:
        # Probe anlegen (wenn sie noch nicht existiert)
        conn.execute("INSERT OR IGNORE INTO StaubProben (ProbenName) VALUES (?)", (proben_name,))
        # ID der Probe holen
        probe = conn.execute("SELECT Probe_ID FROM StaubProben WHERE ProbenName = ?", (proben_name,)).fetchone()
        probe_id = probe['Probe_ID']
        # Verknüpfung erstellen
        conn.execute("INSERT INTO Projekt_Probe_Verknuepfung (Projekt_ID, Probe_ID) VALUES (?, ?)", (projekt_id, probe_id))
        conn.commit()
    except Exception as e:
        print(f"Fehler: {e}")
    finally:
        conn.close()

# 3. Den Versuch zur Probe speichern
def save_versuch(proben_name, methode, versuchsnummer):
    conn = get_db_connection()
    # Erst die ID der Probe anhand des Namens suchen
    probe = conn.execute("SELECT Probe_ID FROM StaubProben WHERE ProbenName = ?", (proben_name,)).fetchone()
    if probe:
        conn.execute("INSERT INTO Versuche (Probe_ID, Methode, Versuchsnummer) VALUES (?, ?, ?)", 
                     (probe['Probe_ID'], methode, versuchsnummer))
        conn.commit()
    conn.close()