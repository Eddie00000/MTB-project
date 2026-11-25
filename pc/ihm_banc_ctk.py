#!/usr/bin/env python3
"""IHM du banc MTB, version CustomTkinter (thème sombre).

La logique est la même que dans ihm_banc.py :
  - un thread lit le port série et met les lignes dans une file d'attente
  - la fenêtre demande une mesure ('m') toutes les 200 ms (signal de vie pour la carte)
  - les mesures sont tracées en direct et enregistrées dans un fichier CSV
Seul l'habillage change (CustomTkinter à la place de Tkinter).

Deux onglets :
  - Mesures : valeurs instantanées et courbes
  - PWM     : choix de la fréquence (liste de 8 valeurs), du rapport cyclique,
              et tracé du signal PWM théorique (reconstruit, ce n'est pas une mesure)
Les commandes envoyées à la carte sont celles du firmware : f<numéro>, d<pourcentage>, p
(chacune terminée par Entrée), le PWM ne sort qu'en RUN.

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

# PWM : la liste doit être la même que FREQUENCES_HZ dans le firmware (numéro 1 à 8)
FREQUENCES = ["1 kHz", "2 kHz", "5 kHz", "10 kHz", "20 kHz", "50 kHz", "100 kHz", "200 kHz"]
FREQUENCE_DEFAUT = 5            # numéro de la fréquence au démarrage du firmware (20 kHz)
RAPPORT_MAX_PCT = 90            # même valeur que RAPPORT_MAX_PCT dans le firmware

NB_PERIODES_PWM = 3          # nombre de périodes tracées dans l'onglet PWM

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
        self.pwm_autorise = False          # vrai quand la carte est en RUN sans défaut
        self.pwm_freq_hz = 20000           # dernière fréquence annoncée par la carte

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
        zone.grid_rowconfigure(1, weight=1)          # les onglets prennent la place libre

        # Ligne 0 : l'état de la carte (toujours visible)
        self.lbl_etat = ctk.CTkLabel(zone, text="Non connecté", font=ctk.CTkFont(size=16, weight="bold"))
        self.lbl_etat.grid(row=0, column=0, pady=(0, 8))

        # Ligne 1 : les onglets
        self.onglets = ctk.CTkTabview(zone)
        self.onglets.grid(row=1, column=0, sticky="nsew")
        self.construire_onglet_mesures(self.onglets.add("Mesures"))
        self.construire_onglet_pwm(self.onglets.add("PWM"))

        # Ligne 2 : le journal des messages (toujours visible)
        self.console = ctk.CTkTextbox(zone, height=110, font=ctk.CTkFont(family="Consolas", size=12))
        self.console.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.console.configure(state="disabled")

    def construire_onglet_mesures(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)        # les courbes prennent la place libre

        # Les 4 valeurs instantanées
        cartes = ctk.CTkFrame(parent, fg_color="transparent")
        cartes.grid(row=0, column=0, sticky="ew")
        self.val_i = self.creer_carte(cartes, 0, "Courant", "A")
        self.val_vbus = self.creer_carte(cartes, 1, "VBUS", "V")
        self.val_vdrv = self.creer_carte(cartes, 2, "VDRV", "V")
        self.val_temp = self.creer_carte(cartes, 3, "Température", "°C")

        # Les courbes
        self.construire_courbes(parent)

    def construire_onglet_pwm(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(6, weight=1)        # le graphique prend la place libre

        ctk.CTkLabel(parent, text="Commande du PWM (Timer1)",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, pady=(8, 0))
        ctk.CTkLabel(parent, text="Le PWM ne sort qu'en RUN. Changer la fréquence remet le rapport à 0 %.",
                     text_color="gray").grid(row=1, column=0, pady=(0, 6))

        # --- Fréquence : on choisit dans la liste, c'est envoyé tout de suite
        bloc_f = ctk.CTkFrame(parent)
        bloc_f.grid(row=2, column=0, padx=30, pady=4, sticky="ew")
        ctk.CTkLabel(bloc_f, text="Fréquence", width=140, anchor="w").pack(side="left", padx=15, pady=8)
        self.libelles_freq = [f"{i + 1} : {f}" for i, f in enumerate(FREQUENCES)]
        self.menu_freq = ctk.CTkOptionMenu(bloc_f, values=self.libelles_freq, width=160,
                                           command=self.changer_frequence, state="disabled")
        self.menu_freq.set(self.libelles_freq[FREQUENCE_DEFAUT - 1])
        self.menu_freq.pack(side="left", padx=10)

        # --- Rapport cyclique : on règle le curseur, puis on clique sur Appliquer
        bloc_d = ctk.CTkFrame(parent)
        bloc_d.grid(row=3, column=0, padx=30, pady=4, sticky="ew")
        bloc_d.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bloc_d, text="Rapport cyclique", width=140, anchor="w").grid(row=0, column=0, padx=15, pady=8)
        self.slider = ctk.CTkSlider(bloc_d, from_=0, to=RAPPORT_MAX_PCT, number_of_steps=RAPPORT_MAX_PCT,
                                    command=self.slider_bouge, state="disabled")
        self.slider.set(0)
        self.slider.grid(row=0, column=1, padx=10, sticky="ew")
        self.lbl_rapport = ctk.CTkLabel(bloc_d, text="0 %", width=60, font=ctk.CTkFont(size=16, weight="bold"))
        self.lbl_rapport.grid(row=0, column=2, padx=15)

        # --- Boutons
        boutons = ctk.CTkFrame(parent, fg_color="transparent")
        boutons.grid(row=4, column=0, pady=6)
        self.btn_appliquer = ctk.CTkButton(boutons, text="Appliquer le rapport", width=170,
                                           state="disabled", command=self.appliquer_rapport)
        self.btn_appliquer.pack(side="left", padx=8)
        self.btn_zero = ctk.CTkButton(boutons, text="PWM à 0 %", width=120, fg_color=ROUGE,
                                      state="disabled", command=self.rapport_zero)
        self.btn_zero.pack(side="left", padx=8)

        # --- Dernier message de la carte au sujet du PWM
        self.lbl_retour_pwm = ctk.CTkLabel(parent, text="Retour de la carte : ---", text_color="gray")
        self.lbl_retour_pwm.grid(row=5, column=0, pady=(4, 0))

        # --- Graphique du signal PWM théorique
        fig = Figure(figsize=(8, 2.2), dpi=100, facecolor=FOND_COURBES)
        self.ax_pwm = fig.subplots()
        (self.ligne_pwm,) = self.ax_pwm.plot([], [], color="#f2c94c", linewidth=2)
        self.ax_pwm.set_facecolor(FOND_COURBES)
        self.ax_pwm.tick_params(colors=TEXTE_COURBES)
        self.ax_pwm.xaxis.label.set_color(TEXTE_COURBES)
        self.ax_pwm.yaxis.label.set_color(TEXTE_COURBES)
        self.ax_pwm.title.set_color(TEXTE_COURBES)
        self.ax_pwm.grid(True, linestyle="--", alpha=0.3)
        for bord in self.ax_pwm.spines.values():
            bord.set_color("#555555")
        fig.subplots_adjust(left=0.08, right=0.98, top=0.82, bottom=0.25)
        self.canvas_pwm = FigureCanvasTkAgg(fig, master=parent)
        self.canvas_pwm.get_tk_widget().grid(row=6, column=0, sticky="nsew", padx=10, pady=(4, 0))
        ctk.CTkLabel(parent, text="Signal théorique reconstruit à partir des réglages (ce n'est pas une mesure). "
                                  "Le vrai signal se mesure à l'oscilloscope sur PB1.",
                     text_color="gray", font=ctk.CTkFont(size=11)).grid(row=7, column=0, pady=(0, 2))
        self.tracer_pwm(self.pwm_freq_hz, 0)

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
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")

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
        self.menu_freq.configure(state="normal")
        self.lbl_etat.configure(text="Connecté, démarrage...", text_color="white")
        self.log(f"Connecté sur {ser.port} - enregistrement dans {nom}")
        self.root.after(1500, self._activer_interrogation)   # laisse la carte démarrer

    def _activer_interrogation(self):
        if self.running:
            self.pret = True
            self.envoyer("p")          # demande l'état du PWM pour mettre le menu à jour

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
        self.menu_freq.configure(state="disabled")
        self.autoriser_pwm(False)
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
                if contenu.startswith("PWM :"):
                    self.lire_retour_pwm(contenu)

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

        # Le réglage du rapport cyclique n'est possible qu'en RUN sans défaut
        actif = bool(m["en"]) and not m["defaut"]
        if actif != self.pwm_autorise:
            self.autoriser_pwm(actif)

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

    # ------------------------------------------------------------
    # Onglet PWM
    # ------------------------------------------------------------
    def changer_frequence(self, choix):
        """Appelée quand on choisit une fréquence dans le menu : '5 : 20 kHz' -> envoie 'f5'."""
        numero = choix.split(" ")[0]
        self.envoyer(f"f{numero}\n")
        self.slider.set(0)                  # le firmware remet le rapport à 0 %
        self.slider_bouge(0)

    def slider_bouge(self, valeur):
        self.lbl_rapport.configure(text=f"{int(valeur)} %")

    def appliquer_rapport(self):
        pourcent = int(self.slider.get())
        self.envoyer(f"d{pourcent}\n")

    def rapport_zero(self):
        self.slider.set(0)
        self.slider_bouge(0)
        self.envoyer("d0\n")

    def autoriser_pwm(self, actif):
        """Active ou désactive les commandes du rapport cyclique (seulement en RUN)."""
        self.pwm_autorise = actif
        etat = "normal" if actif else "disabled"
        self.slider.configure(state=etat)
        self.btn_appliquer.configure(state=etat)
        self.btn_zero.configure(state=etat)
        if not actif:
            self.slider.set(0)              # la carte remet le rapport à 0 % quand EN est coupé
            self.slider_bouge(0)
            self.tracer_pwm(self.pwm_freq_hz, 0)

    def lire_retour_pwm(self, ligne):
        """Ligne de la carte : 'PWM : f3 = 5000 Hz | rapport ...' -> affichée et menu mis à jour."""
        self.lbl_retour_pwm.configure(text="Retour de la carte : " + ligne)
        try:
            numero = int(ligne.split("PWM : f")[1].split(" ")[0])
            frequence = float(ligne.split("= ")[1].split(" Hz")[0])
            if "rapport reel" in ligne:
                rapport = float(ligne.split("rapport reel ")[1].split(" %")[0])
            else:
                rapport = 0.0                    # "rapport 0 % (sortie coupee)"
        except (IndexError, ValueError):
            return
        if 1 <= numero <= len(FREQUENCES):
            self.menu_freq.set(self.libelles_freq[numero - 1])
        self.pwm_freq_hz = frequence
        self.tracer_pwm(frequence, rapport)

    def tracer_pwm(self, frequence_hz, rapport_pct):
        """Trace NB_PERIODES_PWM périodes du signal carré 0 - 5 V (au début de chaque période : niveau haut)."""
        periode = 1.0 / frequence_hz
        # Unité de l'axe du temps : ms si la période est >= 1 ms, sinon µs
        if periode >= 1e-3:
            unite, facteur = "ms", 1e3
        else:
            unite, facteur = "µs", 1e6
        periode_u = periode * facteur

        x = []
        y = []
        for k in range(NB_PERIODES_PWM):
            debut = k * periode_u
            if rapport_pct <= 0:
                x += [debut, debut + periode_u]                  # sortie coupée : ligne à 0 V
                y += [0, 0]
            else:
                fin_haut = debut + periode_u * rapport_pct / 100
                x += [debut, debut, fin_haut, fin_haut, debut + periode_u]
                y += [0, 5, 5, 0, 0]
        self.ligne_pwm.set_data(x, y)
        self.ax_pwm.set_xlim(0, NB_PERIODES_PWM * periode_u)
        self.ax_pwm.set_ylim(-0.6, 6)
        self.ax_pwm.set_xlabel(f"Temps ({unite})")
        self.ax_pwm.set_ylabel("PB1 (V)")
        if frequence_hz >= 1000:
            texte_f = f"{frequence_hz / 1000:g} kHz"
        else:
            texte_f = f"{frequence_hz:g} Hz"
        self.ax_pwm.set_title(f"{texte_f} - rapport cyclique {rapport_pct:.1f} %", fontsize=10)
        self.canvas_pwm.draw_idle()

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