#pragma once
#include <Arduino.h>

// ---------------------------------------------------------------
// Brochage (noms Arduino) - carte numérique MTB, ATmega328P  (const expr pour initialisation des la compilation)
// ---------------------------------------------------------------
constexpr uint8_t PIN_PWM         = 9;   // PB1 / OC1A  -> U1 (NAND) -> TC4420
constexpr uint8_t PIN_EN          = 4;   // PD4 -> autorisation du pilote
constexpr uint8_t PIN_FAULT       = 2;   // PD2 / INT0  : sortie Q du verrou 74HC74 (1 = défaut)
constexpr uint8_t PIN_STOP_N      = 3;   // PD3 / INT1  : bouton STOP (0 = appuyé)
constexpr uint8_t PIN_FAULT_RST   = 5;   // PD5 -> horloge du verrou (front montant = remise à zéro)
constexpr uint8_t PIN_LED         = 6;   // PD6 -> D14 (actif à l'état haut)
constexpr uint8_t PIN_USB_PRESENT = 7;   // PD7

constexpr uint8_t PIN_I_MEAS      = A0;  // PC0
constexpr uint8_t PIN_VBUS_MEAS   = A1;  // PC1
constexpr uint8_t PIN_VDRV_MEAS   = A2;  // PC2
constexpr uint8_t PIN_TEMP_MEAS   = A3;  // PC3

// Accès direct au port pour l'ISR-routine d'interruption (garder cohérent avec PIN_EN) (voir page 111 du datasheet AVR ATmega328P)
#define EN_PORT PORTD
#define EN_BIT  PORTD4

// ---------------------------------------------------------------
// Échelles de mesure (calculées sur les valeurs du PCB, A CALIBRER) - pour conversion ADC -> unité physique
// ---------------------------------------------------------------
constexpr float ADC_VREF       = 5.0f;                 // AVCC (varie : 5,0 V régulé / 5,2 V via USB)
constexpr float SHUNT_OHM      = 0.010f;               // R20
constexpr float I_AMP_GAIN     = 49.9f;                // 49,9 k / 1 k
constexpr float VBUS_DIV       = (47.0f + 8.2f) / 8.2f;
constexpr float VDRV_DIV       = (15.0f + 10.0f) / 10.0f;
constexpr float TEMP_GAIN      = 4.0f;                 // 1 + 30 k / 10 k
constexpr float TEMP_V_PER_C   = 0.010f;               // A CONFIRMER selon le capteur de J11

// ---------------------------------------------------------------
// Divers
// ---------------------------------------------------------------
constexpr uint32_t SERIAL_BAUD = 115200;
