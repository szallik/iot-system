#ifndef ZIGBEE_MODE_ED
#error "Wybierz w menu Tools -> Zigbee Mode: Zigbee ED (end device)"
#endif

#include <Arduino.h>
#include <esp_cpu.h>
#include <Zigbee.h>

// --- Piny sprzętowe ---
constexpr uint8_t PIN_FLOW_SENSOR = 4;  // GPIO 4 - YF-S402
constexpr uint8_t PIN_ONEWIRE     = 7;  // GPIO 7 - DS18B20 (T1 i T2)
constexpr uint8_t PIN_BOOT_BTN    = 9;  // GPIO 9 - Reset ZigBee (> 3s)

// --- Parametry fizyczne i kalibracja ---
constexpr float FLOW_CALIBRATION_FACTOR = 4380.0f; // imp/litr (YF-S402)
constexpr float WATER_HEAT_CAPACITY_J_L = 4186.0f; // rho * cw [J / (l * K)]
constexpr uint32_t MEASURE_INTERVAL_MS  = 5000;    // Co 5 s raport do Z2M
constexpr uint32_t FACTORY_RESET_MS     = 3000;

// --- Endpointy ZigBee (zgodne z konwerterem heat_meter_1.js) ---
constexpr uint8_t EP_TEMP_1 = 1;
constexpr uint8_t EP_TEMP_2 = 2;
constexpr uint8_t EP_FLOW   = 3;
constexpr uint8_t EP_POWER  = 4;
constexpr uint8_t EP_ENERGY = 5;

ZigbeeAnalog zbTemp1(EP_TEMP_1);
ZigbeeAnalog zbTemp2(EP_TEMP_2);
ZigbeeAnalog zbFlow(EP_FLOW);
ZigbeeAnalog zbPower(EP_POWER);
ZigbeeAnalog zbEnergy(EP_ENERGY);

const char* ZB_MANUFACTURER = "HeatTech";
const char* ZB_MODEL        = "ESP32C6_HEAT_METER_V1";

// --- Zmienne pomiarowe ---
volatile unsigned long pulseCounter = 0;
double totalEnergyKwh = 0.0;
float lastValidT1 = 20.0f;
float lastValidT2 = 20.0f;

void IRAM_ATTR flowPulseIsr() {
    pulseCounter++;
}

// --- Precyzyjne 1-Wire dla RISC-V (160 MHz) ---
static inline void delayCycles(uint32_t cycles) {
    uint32_t start = esp_cpu_get_cycle_count();
    while ((esp_cpu_get_cycle_count() - start) < cycles);
}
static inline void delayUs(uint32_t us) { delayCycles(us * 160); }
static inline void driveLow(uint8_t pin) { digitalWrite(pin, LOW); }
static inline void releaseLine(uint8_t pin) { digitalWrite(pin, HIGH); }

static portMUX_TYPE dsMux = portMUX_INITIALIZER_UNLOCKED;

bool dsReset(uint8_t pin) {
    portENTER_CRITICAL(&dsMux);
    driveLow(pin);
    delayUs(480);
    releaseLine(pin);
    delayUs(70);
    int presence = digitalRead(pin);
    portEXIT_CRITICAL(&dsMux);
    delayUs(410);
    return (presence == LOW);
}

void dsWriteBit(uint8_t pin, uint8_t bit) {
    portENTER_CRITICAL(&dsMux);
    if (bit & 1) {
        driveLow(pin); delayCycles(960);
        releaseLine(pin); delayCycles(10240);
    } else {
        driveLow(pin); delayCycles(9600);
        releaseLine(pin); delayCycles(1600);
    }
    portEXIT_CRITICAL(&dsMux);
}

uint8_t dsReadBit(uint8_t pin) {
    portENTER_CRITICAL(&dsMux);
    driveLow(pin); delayCycles(480);
    releaseLine(pin); delayCycles(1600);
    uint8_t bit = digitalRead(pin) ? 1 : 0;
    portEXIT_CRITICAL(&dsMux);
    delayCycles(8480);
    return bit;
}

void dsWriteByte(uint8_t pin, uint8_t data) {
    for (uint8_t i = 0; i < 8; i++) {
        dsWriteBit(pin, data & 0x01);
        data >>= 1;
    }
}

uint8_t dsReadByte(uint8_t pin) {
    uint8_t res = 0;
    for (uint8_t i = 0; i < 8; i++) {
        if (dsReadBit(pin)) res |= (1 << i);
    }
    return res;
}

uint8_t sensorRom[2][8];
uint8_t devicesFound = 0;
int lastDiscrepancy = 0;
bool lastDeviceFlag = false;

bool dsSearch(uint8_t pin, uint8_t *newAddr) {
    int idBitNumber = 1, lastZero = 0, romByteNumber = 0;
    uint8_t romByteMask = 1;
    bool searchResult = false;

    if (!lastDeviceFlag) {
        if (!dsReset(pin)) { lastDiscrepancy = 0; return false; }
        dsWriteByte(pin, 0xF0);
        while (idBitNumber <= 64) {
            uint8_t idBit = dsReadBit(pin);
            uint8_t cmpIdBit = dsReadBit(pin);
            if (idBit == 1 && cmpIdBit == 1) break;
            uint8_t dir;
            if (idBit != cmpIdBit) dir = idBit;
            else {
                dir = (idBitNumber < lastDiscrepancy) ? ((newAddr[romByteNumber] & romByteMask) > 0) : (idBitNumber == lastDiscrepancy);
                if (dir == 0) lastZero = idBitNumber;
            }
            if (dir == 1) newAddr[romByteNumber] |= romByteMask;
            else newAddr[romByteNumber] &= ~romByteMask;
            dsWriteBit(pin, dir);
            idBitNumber++;
            romByteMask <<= 1;
            if (romByteMask == 0) { romByteNumber++; romByteMask = 1; }
        }
        if (idBitNumber > 64) {
            lastDiscrepancy = lastZero;
            if (lastDiscrepancy == 0) lastDeviceFlag = true;
            searchResult = true;
        }
    }
    if (!searchResult || !newAddr[0]) { lastDiscrepancy = 0; lastDeviceFlag = false; return false; }
    return true;
}

void dsStartAllConversion(uint8_t pin) {
    if (dsReset(pin)) {
        dsWriteByte(pin, 0xCC);
        dsWriteByte(pin, 0x44);
    }
}

float dsReadTempByRom(uint8_t pin, const uint8_t *rom) {
    if (!dsReset(pin)) return -999.0f;
    dsWriteByte(pin, 0x55);
    for (uint8_t i = 0; i < 8; i++) dsWriteByte(pin, rom[i]);
    dsWriteByte(pin, 0xBE);
    uint8_t sp[9];
    for (uint8_t i = 0; i < 9; i++) sp[i] = dsReadByte(pin);
    dsReset(pin);
    if ((sp[0] == 0xFF && sp[1] == 0xFF) || (sp[0] == 0x00 && sp[1] == 0x00 && sp[4] == 0x00)) return -999.0f;
    int16_t raw = (sp[1] << 8) | sp[0];
    float t = (float)raw / 16.0f;
    if (t < -55.0f || t > 125.0f) return -999.0f; // Odrzucenie błędnych odczytów
    return t;
}

// --- Reset fabryczny ZigBee przyciskiem BOOT ---
bool bootPressed = false;
uint32_t bootPressStart = 0;

void handleBootReset() {
    bool isLow = (digitalRead(PIN_BOOT_BTN) == LOW);
    uint32_t now = millis();
    if (isLow && !bootPressed) {
        bootPressed = true;
        bootPressStart = now;
    } else if (isLow && bootPressed && (now - bootPressStart >= FACTORY_RESET_MS)) {
        Serial.println("[BOOT] Reset fabryczny ZigBee...");
        Zigbee.factoryReset();
    } else if (!isLow) {
        bootPressed = false;
    }
}

void setupAnalogEp(ZigbeeAnalog &ep, const char* desc, float res) {
    ep.setManufacturerAndModel(ZB_MANUFACTURER, ZB_MODEL);
    ep.addAnalogInput();
    ep.setAnalogInputDescription(desc);
    ep.setAnalogInputResolution(res);
    Zigbee.addEndpoint(&ep);
}

uint32_t lastMeasureMs = 0;

void setup() {
    Serial.begin(115200);
    pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
    pinMode(PIN_FLOW_SENSOR, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_FLOW_SENSOR), flowPulseIsr, RISING);

    pinMode(PIN_ONEWIRE, OUTPUT_OPEN_DRAIN);
    releaseLine(PIN_ONEWIRE);

    uint8_t rom[8] = {0};
    while (dsSearch(PIN_ONEWIRE, rom) && devicesFound < 2) {
        memcpy(sensorRom[devicesFound], rom, 8);
        devicesFound++;
    }
    dsStartAllConversion(PIN_ONEWIRE);

    // Rejestracja 5 endpointów analogowych
    setupAnalogEp(zbTemp1,  "temperature_1", 0.01f);
    setupAnalogEp(zbTemp2,  "temperature_2", 0.01f);
    setupAnalogEp(zbFlow,   "flow",          0.001f);
    setupAnalogEp(zbPower,  "power",         0.1f);
    setupAnalogEp(zbEnergy, "energy",        0.0001f);

    if (!Zigbee.begin(ZIGBEE_END_DEVICE)) {
        Serial.println("[ZIGBEE] Blad inicjalizacji! Restart...");
        ESP.restart();
    }
    lastMeasureMs = millis();
}

void loop() {
    handleBootReset();
    uint32_t now = millis();

    if (now - lastMeasureMs >= MEASURE_INTERVAL_MS) {
        float dtSec = (now - lastMeasureMs) / 1000.0f;
        lastMeasureMs = now;

        noInterrupts();
        unsigned long pulses = pulseCounter;
        pulseCounter = 0;
        interrupts();

        float flowLPerMin = ((float)pulses / FLOW_CALIBRATION_FACTOR) / dtSec * 60.0f;
        float flowLPerSec = flowLPerMin / 60.0f;

        float t1 = (devicesFound > 0) ? dsReadTempByRom(PIN_ONEWIRE, sensorRom[0]) : -999.0f;
        float t2 = (devicesFound > 1) ? dsReadTempByRom(PIN_ONEWIRE, sensorRom[1]) : -999.0f;
        dsStartAllConversion(PIN_ONEWIRE);

        if (t1 > -100.0f) lastValidT1 = t1;
        if (t2 > -100.0f) lastValidT2 = t2;

        // Obliczenie mocy [W] i energii [kWh]
        float deltaT = lastValidT1 - lastValidT2;
        float powerW = (deltaT > 0.0f && flowLPerSec > 0.0f) ? (flowLPerSec * WATER_HEAT_CAPACITY_J_L * deltaT) : 0.0f;
        totalEnergyKwh += ((double)powerW * (double)dtSec) / 3600000.0;

        Serial.printf("T1: %.2f C | T2: %.2f C | Flow: %.3f l/min | P: %.1f W | E: %.5f kWh | ZB: %s\n",
                      lastValidT1, lastValidT2, flowLPerMin, powerW, (float)totalEnergyKwh,
                      Zigbee.connected() ? "ONLINE" : "JOINING");

        if (Zigbee.connected()) {
            zbTemp1.setAnalogInput(lastValidT1);
            zbTemp1.reportAnalogInput();

            zbTemp2.setAnalogInput(lastValidT2);
            zbTemp2.reportAnalogInput();

            zbFlow.setAnalogInput(flowLPerMin);
            zbFlow.reportAnalogInput();

            zbPower.setAnalogInput(powerW);
            zbPower.reportAnalogInput();

            zbEnergy.setAnalogInput((float)totalEnergyKwh);
            zbEnergy.reportAnalogInput();
        }
    }
}
