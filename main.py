import customtkinter as ctk
import os
import shutil
from tkinter import messagebox

PROJEKT_QUELLE = r"C:\Users\marty\Desktop\wetransfer_h2lab_pub_25_9-lime-addition-in-eafd-recycling_2026-07-08_0739"
MUL_TURKIS = "#008c96"
MUL_DUNKEL = "#0a2a2d"
METHODEN = ["EMI", "TGA", "SEM"]
ATTRIBUTE = ["Gas", "Tmax", "m before press", "Dichte", "Mass Loss %"]

class LaborApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MUL - H2Lab Versuch-System")
        self.geometry("600x800")
        
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(pady=20, padx=20, fill="both", expand=True)

        ctk.CTkLabel(self.main_frame, text="Projekt auswählen:", font=("Arial", 14, "bold")).pack()
        self.projekt_menu = ctk.CTkOptionMenu(self.main_frame, values=self.get_projekte(), 
                                              command=self.on_projekt_wechsel, fg_color=MUL_TURKIS)
        self.projekt_menu.pack(pady=(0, 20))

        self.uebersicht_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.uebersicht_frame.pack(fill="both", expand=True)

    def get_projekte(self):
        if not os.path.exists(PROJEKT_QUELLE): return ["--- Pfad nicht gefunden ---"]
        return [d for d in os.listdir(PROJEKT_QUELLE) if os.path.isdir(os.path.join(PROJEKT_QUELLE, d))]

    def on_projekt_wechsel(self, projekt):
        self.baue_methoden_uebersicht(projekt)

    def baue_methoden_uebersicht(self, projekt):
        for widget in self.uebersicht_frame.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.uebersicht_frame, text=f"Projekt: {projekt}", font=("Arial", 16, "bold")).pack(pady=10)
        
        projekt_pfad = os.path.join(PROJEKT_QUELLE, projekt)
        karten_frame = ctk.CTkFrame(self.uebersicht_frame, fg_color="transparent")
        karten_frame.pack()

        for i, methode in enumerate(METHODEN):
            versuche = self.liste_versuche(projekt_pfad, methode)
            ctk.CTkButton(karten_frame, text=f"{methode}\n({len(versuche)} Versuche)", width=150, height=80, 
                          fg_color=MUL_DUNKEL, command=lambda m=methode, p=projekt_pfad: self.oeffne_detail(p, m, projekt)).grid(row=0, column=i, padx=5)

    def liste_versuche(self, projekt_pfad, methode):
        ergebnisse = []
        for dirpath, dirnames, filenames in os.walk(projekt_pfad):
            if "raw_data" in dirpath.split(os.sep) and methode.lower() in dirpath.lower():
                for versuch_ordner in dirnames:
                    ergebnisse.append((versuch_ordner, os.path.join(dirpath, versuch_ordner)))
                break 
        return ergebnisse

    def oeffne_detail(self, projekt_pfad, methode, projekt_name):
        top = ctk.CTkToplevel(self)
        top.title(f"Analyse: {methode}")
        top.geometry("500x600")
        
        scroll = ctk.CTkScrollableFrame(top, width=400, height=450)
        scroll.pack(padx=10, pady=10, fill="both", expand=True)

        for ordner_name, voller_pfad in self.liste_versuche(projekt_pfad, methode):
            zeile = ctk.CTkFrame(scroll)
            zeile.pack(fill="x", pady=2)
            ctk.CTkLabel(zeile, text=ordner_name).pack(side="left", padx=5)
            ctk.CTkButton(zeile, text="Berechnen", width=80, 
                          command=lambda p=voller_pfad, d=ordner_name: self.berechne_dialog(p, d, projekt_pfad, methode, projekt_name)).pack(side="right")

    def berechne_dialog(self, pfad, ordner_name, projekt_pfad, methode, projekt_name):
        top = ctk.CTkToplevel(self)
        top.title("Ergebnisse eintragen")
        top.attributes("-topmost", True)
        
        ctk.CTkLabel(top, text=f"Werte für Versuch: {ordner_name}").pack(pady=10)
        eingaben = {attr: ctk.CTkEntry(top, placeholder_text=attr) for attr in ATTRIBUTE}
        for e in eingaben.values(): e.pack(pady=2)

        def speichern():
            # Verschiebt den kompletten Versuchs-Ordner
            base_dir = os.path.dirname(os.path.dirname(pfad))
            proc_pfad = os.path.join(base_dir, "processed_data", methode)
            os.makedirs(proc_pfad, exist_ok=True)
            
            shutil.move(pfad, os.path.join(proc_pfad, ordner_name))
            messagebox.showinfo("Erfolg", "Versuch verschoben!")
            top.destroy()
            self.baue_methoden_uebersicht(projekt_name)

        ctk.CTkButton(top, text="Speichern & Verschieben", command=speichern).pack(pady=20)
        ctk.CTkButton(top, text="X", command=top.destroy, fg_color="red").pack()

if __name__ == "__main__":
    app = LaborApp()
    app.mainloop()