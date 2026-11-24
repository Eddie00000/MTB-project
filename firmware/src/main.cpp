// MTB - version simple (étapes 0 à 3 : mesures + coupure si le PC disparaît)
// Ce programme fait 5 choses :
//   1. démarrer en état sûr (rien n'est activé)
//   2. surveiller le bouton STOP et le verrou de défaut
//   3. recevoir des commandes par le port série
//   4. faire clignoter la LED selon l'état
//   5. mesurer courant, VBUS, VDRV et température
//
// Commandes (une lettre, sans Entrée obligatoire) :
//   r = RUN   : active EN (autorise le pilote de grille)
//   s = STOP  : coupe EN
//   c = CLEAR : efface un défaut (si la cause a disparu)
//   ? = affiche l'état
//   m = une ligne de mesures : I_A | VBUS_V | VDRV_V | TEMP_C | EN | defaut

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
// Fonctions
// ---------------------------------------------------------------

// Coupe l'autorisation du pilote
void coupeTout() {
  digitalWrite(PIN_EN, LOW);
  enabled = false;
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
  Serial.println(F("MTB firmware v0.2 - 2026-11-24"));

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

    switch (c) {
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

      case '\r':
      case '\n':
        break;                       // on ignore Entrée

      default:
        Serial.println(F("Commandes : r s c ? m"));
    }
  }

  // 3) LED : éteinte au repos, allumée en RUN, clignote vite en défaut
  if (fault)        digitalWrite(PIN_LED, (millis() / 100) % 2);
  else if (enabled) digitalWrite(PIN_LED, HIGH);
  else              digitalWrite(PIN_LED, LOW);
}