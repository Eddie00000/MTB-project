# MTB-project : Mosfet Test Bench

Banc didactique d'étude des MOSFET de puissance (HELHa, projet multidisciplinaire 3BE).

```
firmware/   projet PlatformIO (ATmega328P, framework Arduino)
hardware/   projet KiCad (schémas et PCB)
docs/       notes, mesures, rapports
```

## Firmware
1. Ouvrir le dossier `firmware/` dans VS Code (extension PlatformIO).
2. Brancher la carte en USB-C (voir les notes d'alimentation dans `docs/`).
3. `PlatformIO: Upload`, puis `PlatformIO: Serial Monitor` (115200 baud).

Commandes série (terminées par Entrée) : `ARM`, `RUN`, `STOP`, `CLEAR`, `STATUS`.

## Règles de sécurité
- La protection matérielle (comparateurs, verrou 74HC74, STOP) reste la sécurité principale.
- Le firmware démarre toujours avec EN = 0 et PWM = 0.
- Ne jamais brancher J2 avec le fil +5V_USB -> +5V encore en place.
# MTB-project
