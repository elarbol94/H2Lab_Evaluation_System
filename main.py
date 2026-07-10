import customtkinter as ctk
import os
import shutil
from tkinter import messagebox
from datetime import datetime

# --- KONFIGURATION ---
BASIS_PFAD = r"C:\Users\aaron\Nextcloud\Documents\work\H2Lab_Evaluation_System"
MUL_TURKIS = "#008c96" 

class LaborApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MUL - H2Lab Staub-System")
        self.geometry("500x750")
        ctk.set_appearance_mode("System")
        
        self.grid_columnconfigure(0, weight=1)
        
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(pady=20, padx=20, fill="both", expand=True)

        # UI Elemente
        ctk.CTkLabel(main_frame, text="Projekt auswählen:", font=("Arial", 14, "bold")).pack(pady=(0, 5))
        self.projekt_menu = ctk.CTkOptionMenu(main_frame, values=self.get_projekte(), command=self.update_staub_list, fg_color=MUL_TURKIS)
        self.projekt_menu.pack(pady=(0, 20))

        ctk.CTkLabel(main_frame, text="Neuen Staub erfassen:", font=("Arial", 14, "bold")).pack(pady=(0, 5))
        self.staub_entry = ctk.CTkEntry(main_frame, placeholder_text="Staub-ID")
        self.staub_entry.pack(pady=(0, 5))
        ctk.CTkButton(main_frame, text="Neuen Staub anlegen", command=self.create_staub, fg_color=MUL_TURKIS).pack(pady=(0, 20))

        ctk.CTkLabel(main_frame, text="Bestehende Stäube:", font=("Arial", 14, "bold")).pack(pady=(0, 5))
        self.staub_menu = ctk.CTkOptionMenu(main_frame, values=["--- keine Stäube ---"], fg_color=MUL_TURKIS)
        self.staub_menu.pack(pady=(0, 10))
        
        btn_row = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_row.pack(pady=(0, 20))
        ctk.CTkButton(btn_row, text="Akzeptieren", command=self.assign_staub, fg_color=MUL_TURKIS, width=120).pack(side="left", padx=5)
        ctk.CTkButton(btn_row, text="Löschen", command=self.delete_staub, fg_color="#a83232", width=120).pack(side="left", padx=5)

        ctk.CTkLabel(main_frame, text="Messmethode wählen:", font=("Arial", 14, "bold")).pack(pady=(0, 5))
        self.methode_menu = ctk.CTkOptionMenu(main_frame, values=["EMI", "REM", "SEM"], fg_color=MUL_TURKIS)
        self.methode_menu.pack(pady=(0, 25))

        ctk.CTkButton(main_frame, text="Messdaten erfassen", command=self.open_data_entry, height=45, width=220,
                      fg_color="#0a2a2d", hover_color=MUL_TURKIS, border_width=1, border_color=MUL_TURKIS).pack(pady=20)

        self.status_label = ctk.CTkLabel(self, text="System bereit.", text_color="white")
        self.status_label.pack(side="bottom", pady=10)

    # --- DATENERFASSUNG MIT AUTOMATIK ---
    def open_data_entry(self):
        projekt = self.projekt_menu.get()
        staub = self.staub_menu.get()
        methode = self.methode_menu.get()
        if not staub or "---" in staub: return
        
        top = ctk.CTkToplevel(self)
        top.title(f"Datenerfassung: {staub}")
        top.geometry("400x750")
        
        ctk.CTkLabel(top, text=f"Projekt: {projekt}", font=("Arial", 12, "bold")).pack(pady=(10, 0))
        ctk.CTkLabel(top, text=f"Staub: {staub}", font=("Arial", 12, "bold")).pack(pady=(0, 10))
        
        ctk.CTkLabel(top, text="Ziel-Ordner:", font=("Arial", 11, "bold")).pack()
        data_type_selector = ctk.CTkSegmentedButton(top, values=["raw_data", "processed_data"])
        data_type_selector.set("raw_data")
        data_type_selector.pack(pady=5)

        ctk.CTkLabel(top, text="Labor-Parameter:", font=("Arial", 11, "bold")).pack(pady=(15, 5))
        
        inputs = {
            "Temperaturverlauf": ctk.CTkEntry(top, placeholder_text="z.B. 20-500°C"),
            "Gas": ctk.CTkEntry(top, placeholder_text="z.B. Argon"),
            "Durchfluss": ctk.CTkEntry(top, placeholder_text="ml/min"),
            "Ca-Gehalt (%)": ctk.CTkEntry(top, placeholder_text="Prozentwert"),
            "Vorreduziert (J/N)": ctk.CTkEntry(top, placeholder_text="J oder N"),
            "Bildpfad": ctk.CTkEntry(top, placeholder_text="Pfad zu den Bildern")
        }

        for label, entry in inputs.items():
            ctk.CTkLabel(top, text=label).pack()
            entry.pack(pady=(0, 5))

        def save_data():
            ziel_dir = os.path.join(BASIS_PFAD, projekt, staub, methode, "data", data_type_selector.get())
            os.makedirs(ziel_dir, exist_ok=True)
            
            existing_files = [f for f in os.listdir(ziel_dir) if f.startswith(methode)]
            nr = len(existing_files) + 1
            filename = f"{methode}{nr}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
            
            with open(os.path.join(ziel_dir, filename), "w") as f:
                f.write(f"Versuchs-ID: {methode}{nr}\n")
                f.write(f"Projekt: {projekt}\n")
                f.write(f"Staub: {staub}\n")
                f.write(f"Datum: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
                f.write("-" * 30 + "\n")
                for label, entry in inputs.items():
                    f.write(f"{label}: {entry.get()}\n")
            
            messagebox.showinfo("Gespeichert", f"Datei {filename} wurde erfolgreich erstellt.")
            top.destroy()
            
        ctk.CTkButton(top, text="Speichern", command=save_data, fg_color=MUL_TURKIS).pack(pady=20)

    # --- HILFSFUNKTIONEN ---
    def erstelle_versuchs_struktur(self, projekt, staub, methode):
        base_dir = os.path.join(BASIS_PFAD, projekt, staub, methode)
        for sub in ["data/raw_data", "data/processed_data", "diagram"]:
            os.makedirs(os.path.join(base_dir, sub), exist_ok=True)

    def get_projekte(self):
        if not os.path.exists(BASIS_PFAD): return []
        return [d for d in os.listdir(BASIS_PFAD) if os.path.isdir(os.path.join(BASIS_PFAD, d)) and not d.startswith('.')]

    def update_staub_list(self, selection):
        projekt_pfad = os.path.join(BASIS_PFAD, selection)
        staeube = [d for d in os.listdir(projekt_pfad) if os.path.isdir(os.path.join(projekt_pfad, d)) and d not in ["EMI", "REM", "SEM"] and not d.startswith('.')]
        self.staub_menu.configure(values=staeube if staeube else ["--- keine Stäube ---"])
        self.staub_menu.set(staeube[0] if staeube else "--- keine Stäube ---")

    def create_staub(self):
        projekt = self.projekt_menu.get()
        name = self.staub_entry.get()
        if projekt and name and name not in ["EMI", "REM", "SEM"]:
            os.makedirs(os.path.join(BASIS_PFAD, projekt, name), exist_ok=True)
            for methode in ["EMI", "REM", "SEM"]:
                self.erstelle_versuchs_struktur(projekt, name, methode)
            self.update_staub_list(projekt)
            self.staub_menu.set(name)
            self.staub_entry.delete(0, 'end')

    def assign_staub(self):
        selected = self.staub_menu.get()
        if selected and "---" not in selected:
            self.status_label.configure(text=f"Staub '{selected}' aktiv", text_color="#ffff00")

    def delete_staub(self):
        projekt, staub = self.projekt_menu.get(), self.staub_menu.get()
        if not staub or "---" in staub: return
        if messagebox.askyesno("Löschen", f"Staub '{staub}' wirklich löschen?"):
            shutil.rmtree(os.path.join(BASIS_PFAD, projekt, staub))
            self.update_staub_list(projekt)

if __name__ == "__main__":
    app = LaborApp()
    app.mainloop()