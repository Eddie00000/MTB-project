# Guide d'utilisation : IHM Python du banc MTB

Ce guide explique comment installer, lancer et ranger dans le projet le script `ihm_banc.py`, qui pilote la carte et affiche les mesures sur le PC.

## 1. Rôle de chaque partie

```
   PC (Python)                         Carte MTB (ATmega328P)
┌──────────────────┐   câble USB    ┌────────────────────────┐
│  ihm_banc.py     │ ─────────────► │  main.cpp (firmware)   │
│  fenêtre + CSV   │  r, s, c, ?, m │  EN, FAULT, STOP,      │
│                  │ ◄───────────── │  mesures ADC           │
└──────────────────┘  ligne "M,..." └────────────────────────┘
```

- Le **firmware** (PlatformIO) fait le travail de sécurité et de mesure. Il tourne sans le PC.
- Le **script Python** envoie des commandes d'une lettre et affiche ce que la carte répond.
- Le PC et la carte ne se partagent que le **câble USB** : un seul programme à la fois peut utiliser le port (soit le moniteur PlatformIO, soit le script).

## 2. Ranger le script dans le projet

Ajoute un dossier `pc/` à côté de `firmware/` :

```
MTB-project/
├── firmware/        projet PlatformIO (src/main.cpp, platformio.ini...)
├── pc/              script Python
│   ├── ihm_banc.py
│   ├── requirements.txt
│   └── lancer_ihm.bat
├── hardware/        projet KiCad
├── docs/            notes et ce guide
└── .gitignore
```

**`pc/requirements.txt`** (la liste des bibliothèques nécessaires) :
```
pyserial
matplotlib
```

**`pc/lancer_ihm.bat`** (double-clic pour lancer, sans ouvrir de terminal à la main) :
```
@echo off
python ihm_banc.py
pause
```

**Ajouter dans `.gitignore`** (pour ne pas envoyer tes mesures sur GitHub) :
```
essai_mtb_*.csv
pc/.venv/
```

**Envoyer sur GitHub** :
```
git add .
git commit -m "Ajout de l'IHM Python"
git push
```

## 3. Logiciels nécessaires

| Logiciel | Rôle | Remarque |
|---|---|---|
| **Python 3.12** (python.org) | exécute le script | cocher « Add python.exe to PATH » à l'installation. Tkinter (la fenêtre) est inclus |
| **VS Code** (déjà installé) | modifier le script et le lancer | ajouter l'extension **Python** de Microsoft |
| **PlatformIO** (déjà installé) | modifier et téléverser le firmware | reste séparé du script Python |
| **Excel** | analyser les CSV | s'ouvre en double-clic (séparateur `;`, virgule décimale) |

Installation des bibliothèques, depuis un terminal dans le dossier `pc/` :
```
pip install -r requirements.txt
```

*(Facultatif)* Pour isoler les bibliothèques du projet : `python -m venv .venv`, puis `.venv\Scripts\activate`, puis `pip install -r requirements.txt`. Dans VS Code, choisis ensuite cet environnement (`Ctrl+Maj+P`, « Python: Select Interpreter »).

## 4. Utilisation au quotidien

1. Modifier le firmware dans VS Code (dossier `firmware/`), puis **téléverser**.
2. **Fermer le moniteur série** de PlatformIO (`Ctrl+C` dans son terminal).
3. Lancer `lancer_ihm.bat`, ou dans VS Code ouvrir `ihm_banc.py` et cliquer sur le triangle « Run Python File ».
4. Choisir le port de la carte (celui du CP210x) dans la liste, puis **Connecter**.
5. Faire l'essai, puis **Déconnecter** ou fermer la fenêtre : le script envoie `s` pour couper EN.
6. Récupérer le fichier `essai_mtb_AAAAMMJJ_HHMMSS.csv` créé dans le dossier d'où tu as lancé le script.

## 5. L'interface, élément par élément

| Élément | Ce qu'il fait |
|---|---|
| Liste de ports | choisir le port COM de la carte |
| **Connecter / Déconnecter** | ouvre ou ferme la liaison et le fichier CSV |
| **RUN** | envoie `r` : active EN (refusé en cas de défaut) |
| **STOP** | envoie `s` : coupe EN |
| **CLEAR** | envoie `c` : efface le défaut si la cause a disparu |
| **ETAT** | envoie `?` : la carte répond dans la console |
| Ligne de valeurs | courant (A), VBUS (V), VDRV (V), température (°C) |
| Ligne d'état | vert « RUN », rouge « DEFAUT », noir « Repos » |
| 3 courbes | courant, tensions, température, sur les 60 dernières secondes |
| Console verte | messages de la carte (RUN, STOP, DEFAUT, TIMEOUT PC...) |

Le script demande une mesure à la carte toutes les **200 ms** (commande `m`). Cette demande sert aussi de **signal de vie** : si la carte ne reçoit plus de `m` pendant 1 seconde en RUN, elle coupe EN toute seule.

## 6. Ce que tu peux modifier toi-même

Au début de `ihm_banc.py` :

| Constante | Rôle | Attention |
|---|---|---|
| `BAUD = 115200` | vitesse de la liaison | doit être la même que `SERIAL_BAUD` dans `config.h` |
| `PERIODE_MS = 200` | délai entre deux mesures | **doit rester inférieur à 1000 ms** (`TIMEOUT_PC_MS` du firmware), sinon la carte coupe EN |
| `FENETRE_S = 60` | durée affichée sur les courbes | sans risque |

## 7. Le fichier CSV

Colonnes : `t_pc_s;t_carte_s;i_a;vbus_v;vdrv_v;temp_c;en;defaut`.

- `t_pc_s` : temps depuis la connexion (en secondes), mesuré par le PC.
- `t_carte_s` : temps depuis le démarrage de la carte.
- `en` et `defaut` : 1 ou 0.

Dans Excel : double-clic sur le fichier, puis *Insertion > Graphique en nuage de points* pour tracer I = f(t), par exemple.

## 8. Quelle option pour l'interface ?

| Option | Avantages | Inconvénients |
|---|---|---|
| **Tkinter** (actuel) | inclus avec Python, aucun logiciel en plus | aspect simple, interface écrite à la main |
| PyQt + Qt Designer | dessin de l'interface à la souris, aspect pro | installation plus lourde, plus d'apprentissage |
| Page web (Streamlit) | rendu moderne | il faut apprendre un nouvel outil |
| Excel ou terminal seulement | rien à développer | pas de pilotage ni de courbes en direct |

**Conseil** : garde Tkinter pour le projet. Si tu veux améliorer l'interface plus tard, on le fera en modifiant le code existant.

## 9. Problèmes fréquents

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Accès refusé » à la connexion | le moniteur PlatformIO (ou un autre programme) occupe le port | le fermer |
| Aucun port dans la liste | câble USB débranché ou pilote CP210x manquant | vérifier le Gestionnaire de périphériques |
| Connecté mais aucune mesure | firmware sans la commande `m` | téléverser la version avec `m` (`main_v03.cpp`) |
| `python` introuvable | Python pas dans le PATH | réinstaller en cochant « Add python.exe to PATH », ouvrir un nouveau terminal |
| La LED s'éteint au bout d'1 s en RUN | la carte ne reçoit plus de `m` | vérifier que le script tourne et que `PERIODE_MS` < 1000 |
| Valeurs étranges à vide | entrées flottantes, offset non calibré | normal sur la carte numérique seule |
