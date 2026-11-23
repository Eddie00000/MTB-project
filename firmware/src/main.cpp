// MTB - version simple (étapes 0 à 2)
// Ce programme fait 4 choses :
//   1. démarrer en état sûr (rien n'est activé)
//   2. surveiller le bouton STOP et le verrou de défaut
//   3. recevoir des commandes par le port série
//   4. faire clignoter la LED selon l'état
//
// Commandes (une lettre, sans Entrée obligatoire) :
//   r = RUN   : active EN (autorise le pilote de grille)
//   s = STOP  : coupe EN
//   c = CLEAR : efface un défaut (si la cause a disparu)
//   ? = affiche l'état

#include <Arduino.h>
#include "config.h"

// Deux variables suffisent pour décrire l'état du programme :
bool enabled = false;   // true  = EN est actif (RUN)
bool fault   = false;   // true  = un défaut a été détecté

// ---------------------------------------------------------------
// onctions
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
  Serial.println(F("MTB firmware v0.1 - 2026-11-23"));

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

      case '\r':
      case '\n':
        break;                       // on ignore Entrée

      default:
        Serial.println(F("Commandes : r s c ?"));
    }
  }

  // 3) LED : éteinte au repos, allumée en RUN, clignote vite en défaut
  if (fault)        digitalWrite(PIN_LED, (millis() / 100) % 2);
  else if (enabled) digitalWrite(PIN_LED, HIGH);
  else              digitalWrite(PIN_LED, LOW);
}