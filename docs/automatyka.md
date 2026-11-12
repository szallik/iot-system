# Logika systemu i automatyzacje – wersja 2 (MVP)

Dokument opisuje logikę sterowania systemu IoT w zakresie minimalnej działającej wersji (MVP). Zastępuje dokument roboczy „Architektura Logiki i Systemu Automatyzacji IoT” (wersja 1). Zmiany względem wersji 1 zestawiono w rozdziale 8.

**Stan dokumentu:** cała opisana logika jest **planowana**. Gdzie podano „komunikat zarejestrowany”, chodzi o komunikat MQTT zapisany w notatkach z testów sprzed awarii karty SD; po odtworzeniu Raspberry Pi wymaga ponownego sprawdzenia.

---

## 1. Założenia

1. Cała logika decyzyjna działa w backendzie (FastAPI). Urządzenia ZigBee i moduły ESP32-C6 są wyłącznie źródłami zdarzeń i wykonawcami poleceń.
2. Reguła ma postać: **wyzwalacz → warunki → akcje**.
3. System ma jeden globalny **tryb pracy** (rozdz. 2). Tryb jest zapisany w bazie danych, więc przetrwa restart backendu.
4. W MVP każde urządzenie jest wykorzystane co najmniej w jednej funkcji systemu.
5. Poza MVP pozostają: sekwencje czasowe (miganie, stroboskop), sterowanie syreną, polecenia wysyłane do ciepłomierza i walidacja krzyżowa PIN z czujnikiem obecności.
6. Raspberry Pi, koordynator ZigBee i inne elementy infrastruktury **nie są zasilane z listwy NOUS A11Z**. Dzięki temu akcje wyłączające gniazda nie mogą wyłączyć samego systemu.

## 2. Tryby pracy systemu

| Tryb | Znaczenie | Akcje przy wejściu w tryb |
|---|---|---|
| `disarmed` | praca normalna, aktywne reguły komfortu | żarówka zielona, gniazdo L1 włączone, licznik błędnych PIN = 0 |
| `armed` | czuwanie, reguły komfortu wyłączone | żarówka wyłączona, gniazda L1–L3 wyłączone |
| `alarm` | wykryto naruszenie | żarówka czerwona (jasność 254), gniazda L1–L3 wyłączone, zdarzenie `alarm` o priorytecie krytycznym w bazie |

Akcje przy wejściu wykonuje się **tylko przy faktycznej zmianie trybu**. Ponowne ustawienie tego samego trybu nie powtarza akcji.

Przejścia między trybami:

| Z trybu | Do trybu | Zdarzenie |
|---|---|---|
| `disarmed` | `armed` | pilot `1_single` |
| `disarmed` | `alarm` | 3 kolejne błędne PIN-y |
| `armed` | `disarmed` | poprawny PIN |
| `armed` | `alarm` | obecność, wibracje lub 3 kolejne błędne PIN-y |
| `alarm` | `armed` | przycisk `single` (kwitowanie) |
| `alarm` | `disarmed` | poprawny PIN |

Stanem początkowym po pierwszym uruchomieniu jest `disarmed`.

## 3. Urządzenia i ich rola

Identyfikatory są zgodne z identyfikatorami urządzeń w backendzie (`DEVICE_ALIASES`, seed w `main.py`).

| ID w backendzie | Urządzenie | Rola w logice | Wykorzystywane pola | Stan wiedzy |
|---|---|---|---|---|
| `pilot` | Tuya TS0044 | uzbrojenie, sterowanie ręczne | `action`: `1_single`, `2_single`, `3_single` | komunikaty `1_single`…`4_single`, `1_double`, `4_double`, `1_hold` zarejestrowane |
| `access_1` | moduł PIN (ESP32-C6) | rozbrojenie, wykrywanie prób odgadnięcia | zdarzenia `access_granted`, `access_denied` (rozdz. 5.3) | firmware zaimplementowany, komunikacja niesprawdzona |
| `czujnik_obecnosci` | Sonoff SNZB-06P | światło w trybie normalnym, wtargnięcie w trybie czuwania | `occupancy` (bool) | zarejestrowano tylko komunikat konfiguracji (`occupancy_timeout: 60`), zdarzeń obecności nie sprawdzono |
| `czujnik_wibracji` | NOUS E14 | alarm w trybie czuwania | `alarm_status`: `normal` / `alarm` | komunikaty zarejestrowane |
| `przycisk` | Sonoff SNZB-01P | kwitowanie alarmu, gniazdo L1 | `action`: `single`, `double` | komunikaty `single`, `double`, `long` zarejestrowane |
| `czujnik_temperatury` | NOUS E5 | sterowanie „ogrzewaniem” (gniazdo L2) | `temperature` [°C] | komunikaty zarejestrowane |
| `listwa` | NOUS A11Z | wykonawca: L1 – stanowisko, L2 – ogrzewanie (atrapa, np. lampka), L3 – sterowanie ręczne | `state_l1`…`state_l3`, `power`, `energy` | zmiany `state_l1`…`state_l3` zarejestrowane; polecenie `/set` z backendu niesprawdzone |
| `zarowka_rgb` | NOUS P3Z | sygnalizacja trybu, oświetlenie | `state`, `brightness`, `color` | `{"state":"OFF"}` przez `/set` zarejestrowane; kolor niesprawdzony |
| `heat_meter_1` | ciepłomierz (ESP32-C6) | pomiary do raportów, bez reguł | `temperature_1`, `temperature_2`, `flow`, `power`, `energy` | zaplanowany |

## 4. Reguły MVP

Oznaczenia trybów w kolumnie „Warunki”: **D** – `disarmed`, **A** – `armed`, **L** – `alarm`.

| Nr | Wyzwalacz | Warunki | Akcje |
|---|---|---|---|
| R1 | `pilot.action == "1_single"` | D | tryb → `armed` |
| R2 | `pilot.action == "2_single"` | D | żarówka `TOGGLE` |
| R3 | `pilot.action == "3_single"` | D | gniazdo L3 `TOGGLE` |
| R4 | zdarzenie `access_granted` | A lub L | tryb → `disarmed` |
| R5 | zdarzenie `access_granted` | D | żarówka zielona, gniazdo L1 `ON` |
| R6 | zdarzenie `access_denied` | licznik błędnych PIN ≥ 3 | tryb → `alarm` |
| R7 | `czujnik_obecnosci.occupancy` zmienia się na `true` | D | żarówka `ON`, światło białe |
| R8 | `czujnik_obecnosci.occupancy` zmienia się na `false` | D | żarówka `OFF` |
| R9 | `czujnik_obecnosci.occupancy` zmienia się na `true` | A | tryb → `alarm` |
| R10 | `czujnik_wibracji.alarm_status` zmienia się na `"alarm"` | A | tryb → `alarm` |
| R11 | `przycisk.action == "single"` | L | tryb → `armed` (kwitowanie) |
| R12 | `przycisk.action == "double"` | D | gniazdo L1 `TOGGLE` |
| R13 | `czujnik_temperatury.temperature < 20` | D, `listwa.state_l2 == "OFF"` | gniazdo L2 `ON` |
| R14 | `czujnik_temperatury.temperature > 21` | `listwa.state_l2 == "ON"` | gniazdo L2 `OFF` |

Uwagi:

- R13 i R14 tworzą histerezę 1 °C, żeby gniazdo nie przełączało się przy każdym odczycie. Progi trzeba dobrać do warunków w laboratorium (w notatkach odczyty wynosiły 22,9–27,6 °C), żeby dało się to zademonstrować.
- E5 wysyła odczyt tylko przy zmianie wartości (w notatkach przez 60 s nie było żadnego komunikatu), więc R13 i R14 reagują z opóźnieniem.
- Licznik błędnych PIN (R6) jest zerowany przy wejściu w tryb `disarmed` i przy kwitowaniu alarmu. Zliczanie w oknie czasowym (np. 5 min) jest opcjonalne.
- W trybie `disarmed` wibracja jest tylko zapisywana jako zdarzenie (backend już to robi) i nie wywołuje alarmu.
- Gesty `double`/`hold` pilota oraz przycisk 4 zostają w rezerwie.
- Warunki wszystkich reguł dla danego zdarzenia sprawdza się względem trybu sprzed jego przetworzenia. Dzięki temu R4 i R5 nie wykonują się razem przy jednym poprawnym PIN-ie.

## 5. Format reguł

Format rozszerza obecny format z `automation_service.py`. Pola `trigger`, `warunek` i `akcja` pozostają obiektami JSON, więc schemat API (`AutomationCreateRequest`) nie wymaga zmiany typów.

### 5.1. Wyzwalacz

```json
{"device_id": "pilot", "field": "action", "op": "==", "value": "1_single"}
```

```json
{"device_id": "czujnik_obecnosci", "field": "occupancy", "op": "==", "value": true, "on_change": true}
```

```json
{"event": "access_denied"}
```

- `on_change: true` – reguła wykonuje się tylko wtedy, gdy wartość pola różni się od poprzedniej wartości w `last_state`. Jest to konieczne dla pól stanu (`occupancy`, `alarm_status`), bo Zigbee2MQTT przy `cache_state: true` publikuje w każdym komunikacie ostatnią znaną wartość.
- Dla pól `action` (przyciski, pilot) wyzwalacz działa na każdy komunikat zawierający to pole. Do sprawdzenia: czy Zigbee2MQTT nie powtarza `action` w kolejnych komunikatach (w notatkach komunikaty bez akcji nie zawierały pola `action`).
- `event` – zdarzenie generowane przez backend (rozdz. 5.3), a nie komunikat urządzenia.

### 5.2. Warunki i akcje

```json
{
  "nazwa": "R13 Ogrzewanie przy niskiej temperaturze",
  "trigger": {"device_id": "czujnik_temperatury", "field": "temperature", "op": "<", "value": 20},
  "warunek": {
    "mode": ["disarmed"],
    "all": [{"device_id": "listwa", "field": "state_l2", "op": "==", "value": "OFF"}]
  },
  "akcja": {
    "commands": [{"device_id": "listwa", "command": {"state_l2": "ON"}}]
  },
  "aktywna": true
}
```

```json
{
  "nazwa": "R10 Wibracje w trybie czuwania",
  "trigger": {"device_id": "czujnik_wibracji", "field": "alarm_status", "op": "==", "value": "alarm", "on_change": true},
  "warunek": {"mode": ["armed"]},
  "akcja": {"set_mode": "alarm"},
  "aktywna": true
}
```

- `warunek.mode` – lista trybów, w których reguła jest aktywna.
- `warunek.all` – lista warunków na `last_state` urządzeń (obecna funkcja `evaluate_condition`); wszystkie muszą być spełnione.
- `warunek.min_failed_pins` – warunek dla R6.
- `akcja.commands` – lista poleceń MQTT wysyłanych na `zigbee2mqtt/<nazwa>/set`.
- `akcja.set_mode` – zmiana trybu, która uruchamia akcje wejścia z rozdz. 2.
- Dotychczasowy format `{"device_id": ..., "command": {...}}` w polu `akcja` pozostaje obsługiwany.

Akcje wejścia w tryby (rozdz. 2) są zapisane w konfiguracji backendu, a nie jako reguły w bazie.

### 5.3. Zdarzenia z kontroli dostępu

Kontrakt z modułem PIN przyjmujemy zgodnie ze specyfikacją modułu (`specification_report.md`):

- żądanie na `zigbee2mqtt/access_1`: `{"device_id": "access_1", "request_id": 1, "credential": "2137"}`,
- odpowiedź na `zigbee2mqtt/access_1/set`: `{"request_id": 1, "granted": true}`.

Po weryfikacji backend:

1. wysyła odpowiedź z tym samym `request_id`,
2. zapisuje próbę w `access_attempts`,
3. generuje zdarzenie `access_granted` lub `access_denied` (przy `access_denied` zwiększa licznik błędnych PIN) i przekazuje je do silnika reguł,
4. **nie zapisuje** PIN-u w `last_state` ani w zdarzeniach.

## 6. Zakres zmian w kodzie

| Element | Zmiana | Odpowiedzialni |
|---|---|---|
| `models/entities.py` | tabela `system_state` (tryb, licznik błędnych PIN, czas zmiany) | osoba 3 |
| `services/automation_service.py` | tryby i akcje wejścia, `warunek.mode`/`all`, `akcja.commands`/`set_mode`, `on_change`, wyzwalacze `event` | osoba 7 (logika), osoba 3 (integracja) |
| `services/mqtt_service.py` | porównanie z poprzednim `last_state` przed aktualizacją; obsługa `credential`/`request_id`; usunięcie PIN z `last_state` | osoba 3 |
| `services/access_service.py` | zwracanie wyniku jako zdarzenia, licznik błędnych PIN | osoba 3 |
| `main.py` | nowe reguły R1–R14 w seedzie zamiast obecnych czterech | osoba 7 |
| API | `GET /api/system/mode`, `POST /api/system/mode` (ręczna zmiana trybu przez administratora) | osoba 3 |
| frontend | wyświetlanie trybu systemu; `formatAction` dla `commands` i `set_mode` (obecnie zakłada jedno polecenie) | osoba 4 |
| testy | test silnika reguł na sekwencjach zdarzeń bez sprzętu (np. przez `simulate_mqtt.py`) | osoba 7 |

## 7. Scenariusz demonstracyjny

1. Tryb `disarmed`: ktoś wchodzi → radar → żarówka się włącza (R7).
2. Pilot `2_single` / `3_single` → żarówka i gniazdo L3 sterowane ręcznie (R2, R3).
3. Chłodzenie czujnika E5 poniżej progu → włącza się lampka na L2 (R13), po ogrzaniu gaśnie (R14).
4. Pilot `1_single` → tryb `armed`: żarówka i gniazda wyłączone (R1).
5. Poruszenie czujnika E14 → tryb `alarm`: czerwone światło, gniazda wyłączone, zdarzenie w bazie (R10).
6. Przycisk `single` → kwitowanie, powrót do `armed` (R11).
7. Trzy błędne PIN-y → `alarm` (R6). Poprawny PIN → `disarmed`, zielone światło, L1 włączone (R4).
8. W aplikacji: historia zdarzeń i prób dostępu, wykresy temperatury, energii listwy i ciepłomierza.

## 8. Zmiany względem wersji 1

| Element wersji 1 | Decyzja | Uzasadnienie |
|---|---|---|
| Walidacja krzyżowa PIN z radarem | usunięta | wpisanie PIN-u i tak wymaga obecności przy klawiaturze; w laboratorium radar prawie stale wykrywa osoby; radar wykorzystano osobno (R7–R9) |
| Syrena NOUS E14 | poza MVP | w zarejestrowanych komunikatach Z2M udostępnia tylko `alarm_status`, `battery_state`, `sensitivity`, `silent_mode`; nie potwierdzono sterowania syreną |
| Stroboskop i trzykrotne mignięcie | zastąpione stałym kolorem | wymagają sekwencji czasowych; częste polecenia obciążają sieć ZigBee; obsługa efektów przez P3Z niesprawdzona |
| Odcięcie wszystkich gniazd | pozostawione | pod warunkiem, że infrastruktura nie jest zasilana z listwy (założenie 6) |
| Grzanie wspomagające | pozostawione z histerezą | na L2 atrapa (lampka) zamiast grzałki |
| Ramka wybudzająca ciepłomierz | poza MVP | urządzenie uśpione odbiera polecenia tylko przy odpytywaniu rodzica; firmware ciepłomierza nie istnieje |
| Znaczniki czasu z milisekundami | odrzucone | frontend porównuje znaczniki jako tekst w obecnym formacie bez milisekund |
| Ukrywanie `presented_value` w interfejsie | zastąpione | PIN nie jest w ogóle zapisywany w `last_state` |
| Przycisk SNZB-01P jako „panic button” | zmienione na kwitowanie alarmu | powrót do `armed`, nie do pełnego rozbrojenia; rozbrojenie wymaga PIN |

## 9. Do sprawdzenia na sprzęcie

| Test | Polecenie / obserwacja | Oczekiwany wynik |
|---|---|---|
| Kolor żarówki | `mosquitto_pub -t zigbee2mqtt/zarowka_rgb/set -m '{"state":"ON","color":{"r":255,"g":0,"b":0}}'` | czerwone światło, zmiana w komunikacie stanu |
| `TOGGLE` żarówki | `-m '{"state":"TOGGLE"}'` | przełączenie stanu |
| Gniazda listwy | `mosquitto_pub -t zigbee2mqtt/listwa/set -m '{"state_l2":"ON"}'` | L2 włączone, `state_l2: "ON"` w komunikacie |
| Zdarzenia obecności | `mosquitto_sub -t zigbee2mqtt/czujnik_obecnosci -v`, wejście i wyjście z zasięgu | `occupancy: true`, po ok. 60 s bez ruchu `false` |
| Wibracje | `mosquitto_sub -t zigbee2mqtt/czujnik_wibracji -v`, poruszenie czujnika | `alarm_status: "alarm"`, potem `"normal"` |
| Powtarzanie `action` | obserwacja `pilot` i `przycisk` | `action` tylko w komunikacie po naciśnięciu |
| Moduł PIN | wpisanie PIN, odpowiedź `mosquitto_pub` wg rozdz. 5.3 | dioda zielona / czerwona |
| Efekty żarówki (opcjonalnie) | `-m '{"effect":"blink"}'` | do ustalenia, czy urządzenie obsługuje |
