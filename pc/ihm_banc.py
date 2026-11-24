#!/usr/bin/env python3
"""IHM du banc MTB : pilotage, courbes en direct, enregistrement CSV.

Dépendances :  pip install pyserial matplotlib
Lancer      :  python ihm_banc.py
(fermer d'abord le moniteur série de PlatformIO : un seul programme peut utiliser le port)

La carte doit avoir le firmware avec la commande 'm' (main_v03.cpp).
"""
import csv
import queue
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import messagebox

import serial
import serial.tools.list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

BAUD = 115200
PERIODE_MS = 200        # une mesure demandée toutes les 200 ms (sert aussi de signal de vie)
FENETRE_S = 60          # durée affichée sur les courbes
NB_POINTS = int(FENETRE_S * 1000 / PERIODE_MS)


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

        self.ser = None
        self.running = False
        self.pret = False                  # vrai quand on peut interroger la carte
        self.file = queue.Queue()          # lignes reçues, du thread de lecture vers la fenêtre
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

        # --- barre du haut : port, connexion, commandes ---
        haut = tk.Frame(root, pady=6)
        haut.pack(fill=tk.X, padx=10)

        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_var = tk.StringVar(value=ports[0] if ports else "COM3")
        tk.OptionMenu(haut, self.port_var, *(ports if ports else ["COM3"])).pack(side=tk.LEFT)
        self.btn_conn = tk.Button(haut, text="Connecter", command=self.basculer_connexion)
        self.btn_conn.pack(side=tk.LEFT, padx=6)

        self.boutons = []
        for texte, lettre, couleur in [("RUN", "r", "#4CAF50"), ("STOP", "s", "#f44336"),
                                       ("CLEAR", "c", "#FF9800"), ("ETAT", "?", "#607D8B")]:
            b = tk.Button(haut, text=texte, bg=couleur, fg="white", width=8, state=tk.DISABLED,
                          command=lambda x=lettre: self.envoyer(x))
            b.pack(side=tk.LEFT, padx=3)
            self.boutons.append(b)

        # --- valeurs instantanées ---
        self.lbl_val = tk.Label(root, text="I: ---   VBUS: ---   VDRV: ---   TEMP: ---",
                                font=("Consolas", 12, "bold"))
        self.lbl_val.pack()
        self.lbl_etat = tk.Label(root, text="Non connecté", font=("Consolas", 11))
        self.lbl_etat.pack()

        # --- courbes : 3 axes avec le temps en secondes ---
        fig = Figure(figsize=(8, 6), dpi=100)
        self.axes = fig.subplots(3, 1, sharex=True)
        ax_i, ax_v, ax_t = self.axes
        (self.c_i,) = ax_i.plot([], [], color="tab:red")
        (self.c_vbus,) = ax_v.plot([], [], color="tab:blue", label="VBUS")
        (self.c_vdrv,) = ax_v.plot([], [], color="tab:purple", label="VDRV")
        (self.c_t,) = ax_t.plot([], [], color="tab:green")
        ax_i.set_ylabel("Courant (A)")
        ax_v.set_ylabel("Tension (V)")
        ax_v.legend(loc="upper left")
        ax_t.set_ylabel("Temp. (°C)")
        ax_t.set_xlabel("Temps (s)")
        for ax in self.axes:
            ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(fig, master=root)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10)

        # --- journal des messages de la carte ---
        self.console = tk.Text(root, height=5, bg="#1e1e1e", fg="#00ff00", state=tk.DISABLED)
        self.console.pack(fill=tk.X, padx=10, pady=6)

        root.protocol("WM_DELETE_WINDOW", self.fermer)
        self._tick()

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
        self.btn_conn.config(text="Déconnecter")
        for b in self.boutons:
            b.config(state=tk.NORMAL)
        self.lbl_etat.config(text="Connecté, démarrage...", fg="black")
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
        self.btn_conn.config(text="Connecter")
        for b in self.boutons:
            b.config(state=tk.DISABLED)
        self.lbl_etat.config(text="Non connecté", fg="black")

    def _perte(self, message):
        self.log("Liaison perdue : " + message)
        self.deconnecter()

    # ------------------------------------------------------------
    # Lecture : thread -> file -> fenêtre (Tkinter n'est touché que par la fenêtre)
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

        self.lbl_val.config(text=f"I: {m['i_a']:6.3f} A   VBUS: {m['vbus_v']:5.2f} V   "
                                 f"VDRV: {m['vdrv_v']:5.2f} V   TEMP: {m['temp_c']:5.1f} °C")
        if m["defaut"]:
            self.lbl_etat.config(text="DEFAUT - EN coupé", fg="red")
        elif m["en"]:
            self.lbl_etat.config(text="RUN - EN actif", fg="#2e7d32")
        else:
            self.lbl_etat.config(text="Repos - EN coupé", fg="black")

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
        self.console.config(state=tk.NORMAL)
        self.console.insert(tk.END, texte + "\n")
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)

    def fermer(self):
        self.deconnecter()
        self.root.destroy()


if __name__ == "__main__":
    fenetre = tk.Tk()
    IHM(fenetre)
    fenetre.mainloop()
