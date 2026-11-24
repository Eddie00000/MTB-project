#!/usr/bin/env python3
"""IHM du banc MTB, version CustomTkinter (thème sombre).

La logique est la même que dans ihm_banc.py :
  - un thread lit le port série et met les lignes dans une file d'attente
  - la fenêtre demande une mesure ('m') toutes les 200 ms (signal de vie pour la carte)
  - les mesures sont tracées en direct et enregistrées dans un fichier CSV
Seul l'habillage change (CustomTkinter à la place de Tkinter).

Dépendances :  pip install -r requirements.txt
Lancer      :  python ihm_banc_ctk.py
(fermer d'abord le moniteur série de PlatformIO : un seul programme peut utiliser le port)
"""
import csv
import queue
import threading
import time
from collections import deque
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
import serial
import serial.tools.list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ----------------------------------------------------------------
# Réglages
# ----------------------------------------------------------------
BAUD = 115200
PERIODE_MS = 200        # une mesure demandée toutes les 200 ms (doit rester sous 1000 ms)
FENETRE_S = 60          # durée affichée sur les courbes
NB_POINTS = int(FENETRE_S * 1000 / PERIODE_MS)

# Couleurs (thème sombre)
FOND_COURBES = "#242424"
TEXTE_COURBES = "#dddddd"
VERT = "#2e9e4f"
ROUGE = "#d03a3a"
ORANGE = "#e08a1e"
GRIS = "#5a6b78"

ctk.set_appearance_mode("dark")          # "dark", "light" ou "system"
ctk.set_default_color_theme("blue")


def parse_mesure(ligne):
    """'M,ms,I,VBUS,VDRV,TEMP,EN,defaut' -> dictionnaire, ou None si c'est une autre ligne."""
    morceaux = ligne.strip().split(",")
    if len(morceaux) != 8 or morceaux[0] != "M":
        return None
    try:
        return {
            "t_carte_s": int(morceaux[1]) / 1000.0,
            "i_a": float(morceaux[2]),
            "vbus_v": float(morceaux[3]),
            "vdrv_v": float(morceaux[4]),
            "temp_c": float(morceaux[5]),
            "en": int(morceaux[6]),
            "defaut": int(morceaux[7]),
        }
    except ValueError:
        return None


def fr(x):
    """Nombre au format Excel belge : virgule décimale."""
    return str(x).replace(".", ",")


class IHM:
    def __init__(self, root):
        self.root = root
        root.title("MTB - Pilotage et acquisition")
        root.geometry("1100x750")

        # --- variables de fonctionnement (comme dans la version Tkinter) ---
        self.ser = None
        self.running = False
        self.pret = False                  # vrai quand on peut interroger la carte
        self.file = queue.Queue()          # lignes reçues : thread de lecture -> fenêtre
        self.thread = None
        self.csv_f = None
        self.csv_w = None
        self.t0 = time.time()
        self.nouveau = False

        self.temps = deque(maxlen=NB_POINTS)
        self.h_i = deque(maxlen=NB_POINTS)
        self.h_vbus = deque(maxlen=NB_POINTS)
        self.h_vdrv = deque(maxlen=NB_POINTS)
        self.h_temp = deque(maxlen=NB_POINTS)

        # --- disposition : 2 colonnes (panneau à gauche, contenu à droite) ---
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        self.construire_panneau_gauche()
        self.construire_zone_droite()

        root.protocol("WM_DELETE_WINDOW", self.fermer)
        self._tick()

    # ------------------------------------------------------------
    # Construction de la fenêtre
    # ------------------------------------------------------------
    def construire_panneau_gauche(self):
        panneau = ctk.CTkFrame(self.root, width=180, corner_radius=0)
        panneau.grid(row=0, column=0, sticky="ns")

        ctk.CTkLabel(panneau, text="MTB", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20, 2))
        ctk.CTkLabel(panneau, text="Mosfet Test Bench", text_color="gray").pack(pady=(0, 20))

        # Choix du port et bouton de connexion
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["COM3"]
        self.port_var = ctk.StringVar(value=ports[0])
        ctk.CTkOptionMenu(panneau, values=ports, variable=self.port_var, width=140).pack(pady=5, padx=20)
        self.btn_conn = ctk.CTkButton(panneau, text="Connecter", width=140, command=self.basculer_connexion)
        self.btn_conn.pack(pady=5, padx=20)

        # Boutons de commande (désactivés tant qu'on n'est pas connecté)
        self.boutons = []
        commandes = [("RUN", "r", VERT), ("STOP", "s", ROUGE), ("CLEAR", "c", ORANGE), ("ETAT", "?", GRIS)]
        for texte, lettre, couleur in commandes:
            b = ctk.CTkButton(panneau, text=texte, width=140, height=40, fg_color=couleur,
                              state="disabled", command=lambda x=lettre: self.envoyer(x))
            b.pack(pady=(14, 0), padx=20)
            self.boutons.append(b)

    def construire_zone_droite(self):
        zone = ctk.CTkFrame(self.root, fg_color="transparent")
        zone.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        zone.grid_columnconfigure(0, weight=1)
        zone.grid_rowconfigure(2, weight=1)          # les courbes prennent la place libre

        # Ligne 0 : les 4 valeurs instantanées
        cartes = ctk.CTkFrame(zone, fg_color="transparent")
        cartes.grid(row=0, column=0, sticky="ew")
        self.val_i = self.creer_carte(cartes, 0, "Courant", "A")
        self.val_vbus = self.creer_carte(cartes, 1, "VBUS", "V")
        self.val_vdrv = self.creer_carte(cartes, 2, "VDRV", "V")
        self.val_temp = self.creer_carte(cartes, 3, "Température", "°C")

        # Ligne 1 : l'état de la carte
        self.lbl_etat = ctk.CTkLabel(zone, text="Non connecté", font=ctk.CTkFont(size=16, weight="bold"))
        self.lbl_etat.grid(row=1, column=0, pady=8)

        # Ligne 2 : les courbes
        self.construire_courbes(zone)

        # Ligne 3 : le journal des messages
        self.console = ctk.CTkTextbox(zone, height=110, font=ctk.CTkFont(family="Consolas", size=12))
        self.console.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.console.configure(state="disabled")

    def creer_carte(self, parent, colonne, titre, unite):
        """Une petite carte avec un titre et une valeur. Renvoie le label de la valeur."""
        parent.grid_columnconfigure(colonne, weight=1)
        carte = ctk.CTkFrame(parent)
        carte.grid(row=0, column=colonne, padx=5, sticky="ew")
        ctk.CTkLabel(carte, text=f"{titre} ({unite})", text_color="gray").pack(pady=(8, 0))
        valeur = ctk.CTkLabel(carte, text="---", font=ctk.CTkFont(size=26, weight="bold"))
        valeur.pack(pady=(0, 8))
        return valeur

    def construire_courbes(self, parent):
        fig = Figure(figsize=(8, 5), dpi=100, facecolor=FOND_COURBES)
        self.axes = fig.subplots(3, 1, sharex=True)
        ax_i, ax_v, ax_t = self.axes
        (self.c_i,) = ax_i.plot([], [], color="#ff6b6b")
        (self.c_vbus,) = ax_v.plot([], [], color="#4da3ff", label="VBUS")
        (self.c_vdrv,) = ax_v.plot([], [], color="#c792ea", label="VDRV")
        (self.c_t,) = ax_t.plot([], [], color="#6bdc8b")
        ax_i.set_ylabel("Courant (A)")
        ax_v.set_ylabel("Tension (V)")
        ax_v.legend(loc="upper left", facecolor=FOND_COURBES, labelcolor=TEXTE_COURBES)
        ax_t.set_ylabel("Temp. (°C)")
        ax_t.set_xlabel("Temps (s)")
        for ax in self.axes:
            ax.set_facecolor(FOND_COURBES)
            ax.tick_params(colors=TEXTE_COURBES)
            ax.xaxis.label.set_color(TEXTE_COURBES)
            ax.yaxis.label.set_color(TEXTE_COURBES)
            ax.grid(True, linestyle="--", alpha=0.3)
            for bord in ax.spines.values():
                bord.set_color("#555555")
        fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(fig, master=parent)
        self.canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew")

    # ------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------
    def basculer_connexion(self):
        if self.running:
            self.deconnecter()
        else:
            self.connecter()

    def connecter(self):
        try:
            ser = serial.Serial()
            ser.port = self.port_var.get()
            ser.baudrate = BAUD
            ser.timeout = 0.1
            ser.dtr = False          # évite (souvent) de resetter la carte à l'ouverture
            ser.rts = False
            ser.open()
        except (serial.SerialException, OSError, ValueError) as e:
            messagebox.showerror("Connexion impossible", str(e))
            return
        self.ser = ser
        ser.reset_input_buffer()

        for d in (self.temps, self.h_i, self.h_vbus, self.h_vdrv, self.h_temp):
            d.clear()
        self.t0 = time.time()

        nom = datetime.now().strftime("essai_mtb_%Y%m%d_%H%M%S.csv")
        self.csv_f = open(nom, "w", newline="")
        self.csv_w = csv.writer(self.csv_f, delimiter=";")     # ";" : séparateur d'Excel en Belgique
        self.csv_w.writerow(["t_pc_s", "t_carte_s", "i_a", "vbus_v", "vdrv_v", "temp_c", "en", "defaut"])

        self.running = True
        self.thread = threading.Thread(target=self._lire, daemon=True)
        self.thread.start()
        self.btn_conn.configure(text="Déconnecter")
        for b in self.boutons:
            b.configure(state="normal")
        self.lbl_etat.configure(text="Connecté, démarrage...", text_color="white")
        self.log(f"Connecté sur {ser.port} - enregistrement dans {nom}")
        self.root.after(1500, self._activer_interrogation)   # laisse la carte démarrer

    def _activer_interrogation(self):
        if self.running:
            self.pret = True

    def deconnecter(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(b"s")        # coupe EN avant de partir
            except (serial.SerialException, OSError):
                pass
        self.running = False
        self.pret = False
        if self.thread:
            self.thread.join(timeout=0.5)
        if self.ser:
            try:
                self.ser.close()
            except (serial.SerialException, OSError):
                pass
        if self.csv_f:
            self.csv_f.close()
            self.csv_f = None
            self.csv_w = None
        self.btn_conn.configure(text="Connecter")
        for b in self.boutons:
            b.configure(state="disabled")
        self.lbl_etat.configure(text="Non connecté", text_color="white")

    def _perte(self, message):
        self.log("Liaison perdue : " + message)
        self.deconnecter()

    # ------------------------------------------------------------
    # Lecture : thread -> file -> fenêtre (la fenêtre est la seule à toucher à l'affichage)
    # ------------------------------------------------------------
    def _lire(self):
        while self.running:
            try:
                ligne = self.ser.readline().decode(errors="ignore").strip()
            except (serial.SerialException, OSError, TypeError, AttributeError) as e:
                if self.running:
                    self.file.put(("erreur", str(e)))
                return
            if ligne:
                self.file.put(("ligne", ligne))

    def envoyer(self, lettre):
        if self.running:
            try:
                self.ser.write(lettre.encode("ascii"))
            except (serial.SerialException, OSError) as e:
                self._perte(str(e))

    def _tick(self):
        """Appelée toutes les 200 ms : demande une mesure, lit ce qui est arrivé, redessine."""
        if self.running and self.pret:
            self.envoyer("m")
        self._vider_file()
        if self.nouveau:
            self._tracer()
            self.nouveau = False
        self.root.after(PERIODE_MS, self._tick)

    def _vider_file(self):
        while True:
            try:
                genre, contenu = self.file.get_nowait()
            except queue.Empty:
                return
            if genre == "erreur":
                self._perte(contenu)
                return
            m = parse_mesure(contenu)
            if m:
                self._nouvelle_mesure(m)
            else:
                self.log(contenu)

    def _nouvelle_mesure(self, m):
        t = time.time() - self.t0
        self.temps.append(t)
        self.h_i.append(m["i_a"])
        self.h_vbus.append(m["vbus_v"])
        self.h_vdrv.append(m["vdrv_v"])
        self.h_temp.append(m["temp_c"])
        self.nouveau = True

        # Valeurs affichées dans les cartes
        self.val_i.configure(text=f"{m['i_a']:.3f}")
        self.val_vbus.configure(text=f"{m['vbus_v']:.2f}")
        self.val_vdrv.configure(text=f"{m['vdrv_v']:.2f}")
        self.val_temp.configure(text=f"{m['temp_c']:.1f}")

        # État de la carte
        if m["defaut"]:
            self.lbl_etat.configure(text="DEFAUT - EN coupé", text_color="#ff5555")
        elif m["en"]:
            self.lbl_etat.configure(text="RUN - EN actif", text_color="#4cd964")
        else:
            self.lbl_etat.configure(text="Repos - EN coupé", text_color="white")

        # Enregistrement CSV
        if self.csv_w:
            self.csv_w.writerow([fr(f"{t:.2f}"), fr(m["t_carte_s"]), fr(m["i_a"]), fr(m["vbus_v"]),
                                 fr(m["vdrv_v"]), fr(m["temp_c"]), m["en"], m["defaut"]])
            self.csv_f.flush()

    def _tracer(self):
        self.c_i.set_data(self.temps, self.h_i)
        self.c_vbus.set_data(self.temps, self.h_vbus)
        self.c_vdrv.set_data(self.temps, self.h_vdrv)
        self.c_t.set_data(self.temps, self.h_temp)
        t_fin = self.temps[-1]
        self.axes[0].set_xlim(max(0, t_fin - FENETRE_S), max(FENETRE_S, t_fin))
        for ax in self.axes:
            ax.relim()
            ax.autoscale_view(scalex=False)
        self.canvas.draw_idle()

    def log(self, texte):
        self.console.configure(state="normal")
        self.console.insert("end", texte + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def fermer(self):
        self.deconnecter()
        self.root.destroy()


if __name__ == "__main__":
    fenetre = ctk.CTk()
    IHM(fenetre)
    fenetre.mainloop()
