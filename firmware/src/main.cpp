// MTB - version simple (étapes 0 à 4 : mesures + coupure si le PC disparaît + PWM)
// Ce programme fait 6 choses :
//   1. démarrer en état sûr (rien n'est activé)
//   2. surveiller le bouton STOP et le verrou de défaut
//   3. recevoir des commandes par le port série
//   4. faire clignoter la LED selon l'état
//   5. mesurer courant, VBUS, VDRV et température
//   6. générer le PWM (Timer1) : fréquence choisie dans une liste, rapport cyclique réglable
//
// Commandes (une lettre, sans Entrée obligatoire) :
//   r = RUN   : active EN (autorise le pilote de grille)
//   s = STOP  : coupe EN
//   c = CLEAR : efface un défaut (si la cause a disparu)
//   ? = affiche l'état
//   m = une ligne de mesures : I_A | VBUS_V | VDRV_V | TEMP_C | EN | defaut
//   v = mesures lisibles (avec les noms), pour le moniteur série
//   f + numéro + Entrée = choisit la fréquence dans la liste (ex : f4 puis Entrée = 20 kHz)
//   d + pourcentage + Entrée = rapport cyclique, seulement en RUN (ex : d30 puis Entrée)
//   p = affiche la fréquence et le rapport cyclique du PWM

#include <Arduino.h>
#include "config.h"

// Deux variables suffisent pour décrire l'état du programme :
bool enabled = false;   // true  = EN est actif (RUN)
bool fault   = false;   // true  = un défaut a été détecté

// Surveillance du PC (signal de vie = la commande 'm')
bool pcActif = false;                          // true = un script PC interroge la carte
unsigned long dernierM = 0;                    // instant de la dernière commande 'm' (dernier message de l'utilisateur)
const unsigned long TIMEOUT_PC_MS = 120000;    // silence maximal toléré en RUN

// ---------------------------------------------------------------
// PWM (Timer1, mode 14 : Fast PWM, ICR1 = valeur maximale du compteur) PARTIE AIDER PAR IA 
// ---------------------------------------------------------------
// Fréquences disponibles (numéro 1 à 8). Toutes sont exactes : 16 000 000 / f est un nombre entier.
const uint8_t  NB_FREQUENCES = 8;
const uint32_t FREQUENCES_HZ[NB_FREQUENCES] = {1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000};

const uint8_t RAPPORT_MAX_PCT = 90;   // rapport cyclique maximal autorisé (premiers essais)

uint8_t indexFreq  = 4;               // 4 = 5e valeur de la liste = 20 kHz (au démarrage)
uint8_t rapportPct = 0;               // rapport cyclique demandé, en % (0 = sortie coupée)

// Saisie d'un nombre après 'f' ou 'd' (terminée par Entrée)
char     commandeEnCours = 0;         // 'f' ou 'd' quand on attend un nombre, sinon 0
uint16_t nombreRecu      = 0;         // nombre en cours de saisie

// ---------------------------------------------------------------
// Fonctions
// ---------------------------------------------------------------


//----------------------PWM AIDE PAR IA-------------------------
// Règle le Timer1 selon indexFreq et rapportPct.
// La sortie n'est active que si on est en RUN, sans défaut, avec un rapport > 0 %.
// Sinon, OC1A est déconnectée du timer et la broche est forcée à 0.
void appliquePwm() {
  // 1. On arrête le timer et on déconnecte la sortie OC1A (la broche redevient une sortie normale)
  TCCR1B = 0;
  TCCR1A = 0;
  digitalWrite(PIN_PWM, LOW);

  bool sortieActive = enabled && !fault && (rapportPct > 0);
  if (!sortieActive) {
    return;                                   // PWM coupé : la broche reste à 0
  }

  // 2. Calcul des valeurs des registres
  uint32_t frequence = FREQUENCES_HZ[indexFreq];
  uint16_t top = F_CPU / frequence - 1;       // valeur de ICR1 : f = F_CPU / (ICR1 + 1)
  uint32_t n = ((uint32_t)(top + 1) * rapportPct) / 100;   // nombre de pas à l'état haut
  if (n < 1) n = 1;                           // au moins 1 pas (sinon OCR1A serait négatif)
  uint16_t ocr = n - 1;                       // rapport réel = (OCR1A + 1) / (ICR1 + 1)

  // 3. Démarrage du Timer1 (voir main_pwm_test.cpp pour le détail de chaque ligne)
  TCNT1  = 0;
  ICR1   = top;
  OCR1A  = ocr;
  TCCR1A = (1 << COM1A1) | (1 << WGM11);
  TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS10);
}

//---------------------------------------------------------------

// Coupe l'autorisation du pilote
void coupeTout() {
  digitalWrite(PIN_EN, LOW);
  enabled = false;
  rapportPct = 0;                // le rapport cyclique retombe à 0 %
  appliquePwm();                 // et le PWM s'arrête
}

// Vrai si le verrou matériel est actif OU si le bouton STOP est appuyé
bool defautPresent() {
  bool verrou = (digitalRead(PIN_FAULT) == HIGH);   // sortie Q du 74HC74 : 1 = défaut
  bool stop   = (digitalRead(PIN_STOP_N) == LOW);   // bouton STOP : 0 = appuyé
  return verrou || stop;
}

// Impulsion sur l'horloge du 74HC74 pour remettre le verrou à zéro
void viderVerrou() {
  digitalWrite(PIN_FAULT_RST, HIGH);
  delayMicroseconds(20);
  digitalWrite(PIN_FAULT_RST, LOW);
  delayMicroseconds(20);
}

void afficheEtat() {
  Serial.print(F("EN="));      Serial.print(enabled);
  Serial.print(F(" defaut=")); Serial.print(fault);
  Serial.print(F(" FAULT="));  Serial.print(digitalRead(PIN_FAULT));
  Serial.print(F(" STOP_N=")); Serial.println(digitalRead(PIN_STOP_N));
}

// Affiche la fréquence et le rapport cyclique réellement obtenus
void affichePwm() {
  uint32_t frequence = FREQUENCES_HZ[indexFreq];

  Serial.print(F("PWM : f")); Serial.print(indexFreq + 1);
  Serial.print(F(" = ")); Serial.print(frequence); Serial.print(F(" Hz | "));

  if (rapportPct == 0) {
    Serial.println(F("rapport 0 % (sortie coupee)"));
    return;
  }

  uint16_t top = F_CPU / frequence - 1;
  uint32_t n = ((uint32_t)(top + 1) * rapportPct) / 100;

  if (n < 1) n = 1;

  Serial.print(F("rapport demande ")); Serial.print(rapportPct);
  Serial.print(F(" % | rapport reel "));
  Serial.print(100.0f * n / (top + 1.0f), 1);
  Serial.println(F(" %"));
}

// Appelée quand l'utilisateur a tapé Entrée après 'f' ou 'd' et un nombre
void appliqueNombre() {

  if (commandeEnCours == 'f') {
    if (nombreRecu < 1 || nombreRecu > NB_FREQUENCES) {
      Serial.println(F("Refuse : numero de 1 a 8"));
    } else {
      indexFreq  = nombreRecu - 1;
      rapportPct = 0;                         // par prudence, on repart à 0 %
      appliquePwm();
      Serial.println(F("Frequence changee, rapport remis a 0 %"));
      affichePwm();
    }
  }

  else if (commandeEnCours == 'd') {
    if (!enabled || fault) {
      Serial.println(F("Refuse : il faut etre en RUN (r) sans defaut"));
    } else if (nombreRecu > RAPPORT_MAX_PCT) {
      Serial.print(F("Refuse : maximum ")); Serial.print(RAPPORT_MAX_PCT); Serial.println(F(" %"));
    } else {
      rapportPct = nombreRecu;
      appliquePwm();
      affichePwm();
    }
  }
}

// Tension (en volts) présente sur une entrée analogique, moyenne de 8 mesures
float litVolts(uint8_t pin) {
  analogRead(pin);                       // mesure à jeter : laisse le multiplexeur se stabiliser
  uint16_t somme = 0;
  for (uint8_t i = 0; i < 8; i++){
    somme += analogRead(pin);            // equivalent de : somme = somme + analogRead(pin);
  }
  return (somme / 8.0f) * ADC_VREF / 1023.0f;
}

// Une ligne de mesures, utilisée par un script Python
void afficheMesures() {
  float courant = litVolts(PIN_I_MEAS)    / (I_AMP_GAIN * SHUNT_OHM);        // en A
  float vbus    = litVolts(PIN_VBUS_MEAS) * VBUS_DIV;                         // en V
  float vdrv    = litVolts(PIN_VDRV_MEAS) * VDRV_DIV;                         // en V
  float temp    = litVolts(PIN_TEMP_MEAS) / TEMP_GAIN / TEMP_V_PER_C;         // en °C

  Serial.print(F("M,"));
  Serial.print(millis());  Serial.print(',');
  Serial.print(courant, 3); Serial.print(',');
  Serial.print(vbus, 2);    Serial.print(',');
  Serial.print(vdrv, 2);    Serial.print(',');
  Serial.print(temp, 1);    Serial.print(',');
  Serial.print(enabled);    Serial.print(',');
  Serial.println(fault);
}

// Affichage lisible pour le moniteur série (la commande 'm' reste réservée au script Python)
void afficheMesuresLisibles() {
  float courant = litVolts(PIN_I_MEAS)    / (I_AMP_GAIN * SHUNT_OHM);   // en A
  float vbus    = litVolts(PIN_VBUS_MEAS) * VBUS_DIV;                    // en V
  float vdrv    = litVolts(PIN_VDRV_MEAS) * VDRV_DIV;                    // en V
  float temp    = litVolts(PIN_TEMP_MEAS) / TEMP_GAIN / TEMP_V_PER_C;    // en degres C

  Serial.print(F("MESURES : "));
  Serial.print(F("I="));      Serial.print(courant, 3); Serial.print(F(" A | "));
  Serial.print(F("VBUS="));   Serial.print(vbus, 2);    Serial.print(F(" V | "));
  Serial.print(F("VDRV="));   Serial.print(vdrv, 2);    Serial.print(F(" V | "));
  Serial.print(F("TEMP="));   Serial.print(temp, 1);    Serial.print(F(" C | "));
  Serial.print(F("EN="));     Serial.print(enabled);    Serial.print(F(" | "));
  Serial.print(F("defaut=")); Serial.println(fault);
}

// ---------------------------------------------------------------
// setup() : exécuté une seule fois au démarrage
// ---------------------------------------------------------------
void setup() {
  // Sorties d'abord à l'état sûr (tout à 0)
  pinMode(PIN_EN, OUTPUT);        digitalWrite(PIN_EN, LOW);
  pinMode(PIN_PWM, OUTPUT);       digitalWrite(PIN_PWM, LOW);
  pinMode(PIN_FAULT_RST, OUTPUT); digitalWrite(PIN_FAULT_RST, LOW);
  pinMode(PIN_LED, OUTPUT);       digitalWrite(PIN_LED, LOW);

  // Entrées
  pinMode(PIN_FAULT, INPUT);
  pinMode(PIN_STOP_N, INPUT);     // pull-up déjà présent sur la carte (R61)

  Serial.begin(SERIAL_BAUD);
  Serial.println(F("MTB firmware v0.3 - 2026-11-25"));

  // On vide le verrou, puis on regarde s'il reste un défaut
  viderVerrou();
  if (defautPresent()) {
    fault = true;
    Serial.println(F("Defaut au demarrage"));
  }
}

// ---------------------------------------------------------------
// loop() : répétée sans arrêt
// ---------------------------------------------------------------
void loop() {

  // 1) Sécurité : on regarde le défaut à chaque tour
  if (defautPresent() && !fault) {
    coupeTout();
    fault = true;
    Serial.println(F("DEFAUT ! EN coupe"));
  }

    // 1b) Sécurité PC : plus de 'm' depuis 1 s pendant un RUN -> on coupe EN
  if (enabled && pcActif && (millis() - dernierM > TIMEOUT_PC_MS)) {
    coupeTout();
    pcActif = false;
    Serial.println(F("TIMEOUT PC ! EN coupe"));
  }

  // 2) Commandes reçues par le port série
  if (Serial.available()) {
    char c = Serial.read();

    // 2a) Si on attend un nombre (après 'f' ou 'd')
    if (commandeEnCours != 0) {
      if (c >= '0' && c <= '9') {
        if (nombreRecu < 1000) {
          nombreRecu = nombreRecu * 10 + (c - '0');    // ex : '3' puis '0' -> 30
        }
        c = 0;                                         // caractère utilisé, rien d'autre à faire
      } else if (c == '\r' || c == '\n') {
        appliqueNombre();                              // Entrée : on applique le nombre
        commandeEnCours = 0;
        c = 0;
      } else {
        // Un autre caractère (par exemple 's') : on annule la saisie,
        // puis le caractère est traité comme une commande normale (STOP reste toujours possible)
        Serial.println(F("Saisie annulee"));
        commandeEnCours = 0;
      }
    }

    // 2b) Commandes d'une lettre
    switch (c) {
      case 0:
        break;                       // rien à faire (caractère déjà utilisé)

      case 'r':
      case 'R':
        if (fault) {
          Serial.println(F("Refuse : defaut actif (faire c)"));
        } else {
          digitalWrite(PIN_EN, HIGH);
          enabled = true;
          Serial.println(F("RUN"));
        }
        break;

      case 's':
      case 'S':
        coupeTout();
        Serial.println(F("STOP"));
        break;

      case 'c':
      case 'C':
        viderVerrou();
        if (defautPresent()) {
          Serial.println(F("Cause toujours active"));
        } else {
          fault = false;
          Serial.println(F("Defaut efface"));
        }
        break;

      case '?':
        afficheEtat();
        break;

      case 'm':
      case 'M':
        pcActif = true;
        dernierM = millis();
        afficheMesures();
        break;
        
      case 'v':
      case 'V':
        afficheMesuresLisibles();
        break;

      case 'f':
      case 'F':
        commandeEnCours = 'f';       // le numéro arrive avec les prochains caractères
        nombreRecu = 0;
        break;

      case 'd':
      case 'D':
        commandeEnCours = 'd';
        nombreRecu = 0;
        break;

      case 'p':
      case 'P':
        affichePwm();
        break;

      case '\r':
      case '\n':
        break;                       // on ignore Entrée

      default:
        Serial.println(F("Commandes : r s c ? m v f d p"));
    }
  }

  // 3) LED : éteinte au repos, allumée en RUN, clignote vite en défaut
  if (fault)        digitalWrite(PIN_LED, (millis() / 100) % 2);
  else if (enabled) digitalWrite(PIN_LED, HIGH);
  else              digitalWrite(PIN_LED, LOW);
}