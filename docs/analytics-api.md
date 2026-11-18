# API analizy historii i symulacji

Moduł służy **wyłącznie do analizy historii i symulacji**. Nie steruje
urządzeniami, nie publikuje MQTT, nie aktywuje strategii i nie zmienia
automatyzacji. Maszyna stanów jest silnikiem symulacji. Rekomendacja oznacza
wynik porównania parametrów, a nie polecenie wdrożenia.

## Dostęp i źródło

Wszystkie endpointy wymagają `Authorization: Bearer <token>` z istniejącego
logowania backendu. Sprawdzany jest aktualny rekord użytkownika: konto musi być
aktywne, a rola nie może być `blocked`, `guest_no_access` ani `none`, zgodnie
z istniejącą regułą uprawnień. `admin` i `user` mają dostęp. Nie ma nowego
systemu kont ani nowego sposobu wydawania tokenów.

Ścieżka pochodzi wyłącznie z `settings.DATABASE_URL`
(`backend/app/core/config.py`, zmienne środowiska / `.env`). Względna ścieżka
jest rozwiązywana względem katalogu pracy backendu, tak jak dotychczas.
Wymagana jest istniejąca plikowa baza SQLite. Analityka oraz odczyt konta
użytkownika korzystają z `mode=ro`, bez inicjalizacji tabel, seedowania,
zmiany PRAGMA bazy ani zwykłej sesji zapisującej SQLAlchemy.
HTTP nie przyjmuje ścieżek do baz ani plików strategii; nieznane pola są błędem.
Ścieżka serwera nie jest ujawniana w odpowiedzi.

`source.kind` to ustawione po stronie serwera `synthetic`, `real` lub
`unknown` (domyślnie). SQLite samo w sobie nie dowodzi pochodzenia danych
ze sprzętu. Niezależnie od pochodzenia obecności energia jest **symulowana**.
Zachowano CLI CSV i SQLite opisane w [analytics-csv.md](analytics-csv.md)
i [analytics-sqlite.md](analytics-sqlite.md).

## Endpointy i jednostki

| Metoda i adres | Wejście | Wynik |
| --- | --- | --- |
| `GET /api/analytics/devices` | bez parametrów | urządzenia z occupancy, liczby odczytów, zakresy i jakość |
| `GET /api/analytics/quality` | `device_id`, `from`, `to` | wspólny raport jakości, bez TTL i bez symulacji |
| `GET /api/analytics/environment` | `device_id`, `from`, `to`, `metric`, `ttl_seconds`, opcjonalne progi | statystyki temperatury lub wilgotności ważone czasem |
| `GET /api/analytics/electricity` | `device_id`, `from`, `to`, opcjonalne `price_per_kwh` | przyrosty licznika energii całej listwy i maksimum zaobserwowanej mocy |
| `GET /api/analytics/heat` | `device_id`, `from`, `to`, `ttl_seconds` | historia ciepłomierza, średnie czasowe i różnica temperatur |
| `POST /api/analytics/compare` | okres, strategie, TTL, model i ograniczenia | pełne porównanie na identycznej historii |
| `POST /api/analytics/recommend` | wcześniejszy i późniejszy okres, kandydaci, osobne ograniczenia | dobór i ocena zamrożonego wariantu |

Daty są ciągami ISO 8601 z jawną strefą, normalizowanymi do UTC. Zakres to
`[from,to)`. Sekundy obowiązują dla TTL, opóźnień, progu krótkiego powrotu
i wszystkich czasów trwania (także `on_duration` i `off_duration`). Moce są
w W, energie w Wh, `difference_percent` w procentach; `known_share` i
`known_time_share` są udziałami od 0 do 1, a nie procentami.

Symulacja wymaga jawnego `ttl_seconds` i obu mocy. Nie ma automatycznego TTL
120 s dla danych historycznych. Same odstępy raportów nie rozróżniają
raportowania cyklicznego i zdarzeniowego. TTL musi odpowiadać czujnikowi.
Bez TTL raport jakości nie określa czasu UNKNOWN. Symulacja robi to jawnie.
Moce muszą być skończone i nieujemne (limit wejścia `1e12 W`), TTL/opóźnienie
od mikrosekundy do 365 dni. Liczniki ograniczeń są nieujemnymi liczbami
całkowitymi, `min_known_share` mieści się w `[0,1]`.

Uwzględniamy wszystkie rekordy najpóźniejszej chwili sprzed początku zakresu,
żeby nie ukryć konfliktów. Wykorzystanie wcześniejszego raportu zależy od TTL.
Kolejne raporty ABSENT nie resetują odliczania. UNKNOWN je anuluje i nie
powoduje nowego wyłączenia; jeśli symulowany odbiornik był OFF, pozostaje OFF.
Obecność/UNKNOWN dokładnie w terminie wyłączenia ma pierwszeństwo. Początkowy
stan to ON, osobno na początku każdego ocenianego okresu; koniec nie generuje
powrotu. Semantyka wspólnego silnika nie została zmieniona.

## Przykładowe żądania

PowerShell, do już działającego backendu; `$token` to token istniejącej sesji:

```powershell
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Headers $headers -Uri 'http://localhost:8000/api/analytics/devices'
Invoke-RestMethod -Headers $headers -Uri 'http://localhost:8000/api/analytics/quality?device_id=sensor&from=2026-01-01T08:00:00Z&to=2026-01-01T09:00:00Z'
```

Przy offsetach w query trzeba zakodować `+` jako `%2B`. JSON zachowuje `+`
bez dodatkowego kodowania. Przykład `compare` (nazwę urządzenia należy wybrać
z `devices`; liczby i okresy są przykładami, a nie zaleceniami):

```json
{
  "device_id": "sensor",
  "period": {"from": "2026-01-01T08:00:00Z", "to": "2026-01-01T09:00:00Z"},
  "ttl_seconds": 3600,
  "short_return_seconds": 120,
  "receiver": {"on_watts": 60, "off_watts": 2},
  "constraints": {"max_short_returns": 10, "max_shutdowns": 10, "min_known_share": 0.5, "max_uncertain_returns": 10},
  "strategies": [
    {"kind": "fixed", "delay_seconds": 60},
    {"kind": "schedule", "timezone": "Europe/Warsaw", "default_delay_seconds": 120,
     "windows": [{"weekdays": [0,1,2,3,4], "start": "08:00", "end": "16:00", "delay_seconds": 600}]},
    {"kind": "adaptive", "timezone": "Europe/Warsaw", "groups": [
      {"name": "all", "weekdays": [0,1,2,3,4,5,6], "hours": [["00:00","24:00"]], "delay_seconds": 300}
    ]}
  ]
}
```

Zapisując JSON jako `compare.json`, można wysłać:

```powershell
Invoke-RestMethod -Method Post -Headers $headers -ContentType 'application/json' -InFile .\compare.json -Uri 'http://localhost:8000/api/analytics/compare'
```

Dni tygodnia: poniedziałek=0, niedziela=6. Przedziały lokalne są lewostronnie
domknięte, bez nakładania; przez północ wymagają rozdzielenia. Adaptacyjne
grupy muszą bez luk i nakładania pokrywać cały tydzień. `adaptive` przyjmuje
**już ustalone** grupy i opóźnienia (np. pola `strategy` wcześniejszego
artefaktu uczenia plus `kind: "adaptive"`). API nie trenuje adaptacji ani nie
odczytuje ścieżek artefaktów. Przykład jednej grupy tylko ilustruje kontrakt.
Nie można ustalić z samych liczb, skąd klient pozyskał konfigurację; frontend
powinien zachować pochodzenie artefaktu i nie przygotowywać go na okresie oceny.

`recommend` używa tych samych pól bazowych (`device_id`, `ttl_seconds`,
`receiver`, `strategies`, `short_return_seconds`). Zamiast `period` i
`constraints` należy podać:

```json
{
  "selection_period": {"from": "2026-01-01T08:00:00Z", "to": "2026-01-01T09:00:00Z"},
  "evaluation_period": {"from": "2026-01-01T09:00:00Z", "to": "2026-01-01T10:00:00Z"},
  "selection_constraints": {"max_short_returns": 10, "max_shutdowns": 10, "min_known_share": 0.5, "max_uncertain_returns": 10},
  "evaluation_constraints": {"max_short_returns": 0, "max_shutdowns": 0, "min_known_share": 0.95, "max_uncertain_returns": 0}
}
```

To fragment pól do zastąpienia, nie kompletny request. Ocena musi zaczynać się
nie wcześniej niż koniec doboru. Oba okresy pochodzą z jednego odczytu bazy.
Limity zdarzeń odnoszą się do **całego danego okresu**, nie do dnia. Odpowiedź
zawiera `duration_seconds` dla każdego okresu. Cała wybrana konfiguracja jest
zamrożona. Nie ma ponownego rankingu ani dostrajania podczas oceny.

## Odpowiedzi i interpretacja

Pełne schematy Pydantic są w OpenAPI backendu. Poniższy wyciąg z odpowiedzi
`compare` opisuje syntetyczny przypadek testowy: ABSENT 08:00–08:30,
PRESENT 08:30–09:00, TTL 3600 s, opóźnienie 60 s, moce 60/2 W:

```json
{
  "assumptions": {"energy_simulated": true, "initial_power_state": "ON", "baseline": "same receiver always ON"},
  "comparison": {
    "source": {"format": "SQLite measurements", "kind": "synthetic", "read_only": true, "location": "server-configured"},
    "duration_seconds": 3600,
    "unknown_seconds": 0,
    "candidates": [{
      "strategy_name": "fixed_delay",
      "configuration": {"delay_seconds": 60},
      "known_energy_wh": 31.966666666666665,
      "unknown_energy_wh": 0,
      "shutdowns": 1,
      "short_returns": 0,
      "uncertain_returns": 0,
      "known_share": 1,
      "admissible": true,
      "rejection_reasons": []
    }]
  }
}
```

Wyciąg pomija m.in. jakość, pełne założenia i przebieg symulacji. Każdy kandydat
ma `identifier`, nazwę i pełną konfigurację. W `simulation` są przedziały
obecności, wcześniejszy raport i jego aktualność, pełne energie (`energy`)
oraz przejścia i OFF (`simulation`). Czasy w odpowiedzi mają strefę UTC (`Z`
lub `+00:00`, równoważne zapisy).

- `quality` zachowuje wspólny format CLI: liczniki wejścia/wyboru/odrzucenia,
  duplikaty, konflikty, błędne daty/wartości, odstępy i najdłuższe przerwy.
  Dla zakresu liczniki obejmują wybrany okres i wcześniejszą grupę raportów.
  Błędne daty z wybranego strumienia są zachowywane, bo nie można ustalić ich
  przynależności czasowej. Brak odczytów daje pustą jakość i 100% UNKNOWN
  w symulacji, nigdy automatycznie ABSENT.
- `known_energy_wh` sumuje PRESENT i ABSENT. `unknown_energy_wh` jest osobne.
  W `energy.categories` znajdują się czasy ON/OFF i energie dla wszystkich
  trzech kategorii. Ich sumy pokrywają cały okres.
- `baseline_wh` to ten sam odbiornik zawsze ON. `difference_wh` to baseline
  minus strategia (może być ujemna); procent jest `null` przy zerowej bazie.
  Różnice dla znanych okresów i UNKNOWN są rozdzielone. Żadna nie jest pomiarem
  ani dowodem faktycznie osiągniętej oszczędności.
- `shutdowns` / `energy.shutdown_count` to symulowane wyłączenia,
  `energy.restart_count` to ponowne włączenia. `short_returns` liczy tylko
  pewne krótkie powroty, w czasie nie większym niż `short_return_seconds`
  od wyłączenia (domyślnie 120 s). Pełne zdarzenia zawierają czas wyłączenia,
  czas zaobserwowanego powrotu, `is_short` i `uncertain`.
- OFF zawierający UNKNOWN powoduje oznaczenie powrotu jako niepewnego:
  nie znamy chwili rzeczywistego powrotu. `uncertain_returns` liczy wszystkie
  takie powroty; nie zalicza ich do pewnych krótkich powrotów.
- `recommend.status` to `recommended` albo `no_recommendation`, z `reasons`
  po polsku. `selection` zawiera pełną tabelę kandydatów, `ranking` tylko
  dopuszczalne identyfikatory. `evaluation` zawiera wyłącznie wybrany wariant
  i informację, czy nadal spełnia ograniczenia. Przy braku rekomendacji
  `selected_identifier` oraz `evaluation` są `null`; ograniczenia nie są luzowane.
- Ranking minimalizuje wyłącznie energię w znanych okresach. Remisy z tolerancją
  `1e-6 Wh`: mniej pewnych krótkich powrotów, mniej wyłączeń, dłuższe minimalne
  opóźnienie konfiguracji, następnie stabilny identyfikator. UNKNOWN nie premiuje
  kandydatów. Zgodne konfiguracje są deduplikowane przy rekomendacji.

## Błędy i limity

Format jest zgodny z backendem:

```json
{"error":{"code":"ANALYTICS_LIMIT_EXCEEDED","message":"Liczba konfiguracji przekracza limit 32"}}
```

| HTTP | Znaczenie |
| --- | --- |
| 401 / 403 | brak poprawnej sesji / brak uprawnień |
| 422 | niepoprawne daty, okresy, moce, TTL, konfiguracje, ograniczenia lub dodatkowe pola |
| 409 `ANALYTICS_DATA_QUALITY` | konflikty lub błędne dane blokują symulację; użyj quality |
| 413 `ANALYTICS_LIMIT_EXCEEDED` | przekroczony limit, bez obcinania historii |
| 503 `ANALYTICS_SOURCE_UNAVAILABLE` | niedostępna baza lub wymagany schemat |

Konfiguracja serwerowa (zmienne środowiska, nie pola HTTP):

| Ustawienie | Domyślna wartość | Znaczenie |
| --- | --- | --- |
| `ANALYTICS_MAX_DAYS` | 31 | długość okresu; przy recommend suma doboru i oceny, bez luki |
| `ANALYTICS_MAX_RECORDS` | 20000 | cały skanowany strumień urządzenia; devices: wszystkie occupancy |
| `ANALYTICS_MAX_CONFIGURATIONS` | 32 | liczba przesłanych kandydatów, przed deduplikacją |
| `ANALYTICS_SOURCE_KIND` | unknown | deklarowane pochodzenie danych |

Z powodu różnych offsetów i błędnych dat SQLite nie filtruje czasu tekstowo.
Limit rekordów dotyczy skanowanego strumienia także poza zakresem; skrócenie
okresu nie zawsze wystarczy. Odczyt pobiera najwyżej limit+1 rekordów i jawnie
odmawia dalszej analizy. Limity żądań są dodatkowo ograniczone schematem
(256 kandydatów, 64 okna harmonogramu, 16 grup, 24 przedziały na grupę).
Obliczenia i odczyty są synchronicznymi zależnościami/endpointami FastAPI,
wykonywanymi w puli wątków, poza asynchroniczną pętlą obsługi żądań.

## Wskazówki dla frontendu i testowanie

Frontend powinien najpierw pobrać urządzenia i jakość, a następnie zebrać jawny
TTL i model. Wyświetlać źródło, długość okresu, pokrycie danych, UNKNOWN oraz
oznaczenie „energia symulowana”. Rozdzielić dobór od późniejszej oceny,
pokazać odrzucone warianty i przyczyny. `no_recommendation` jest poprawnym
wynikiem HTTP 200. Nie tworzyć przycisku aktywacji strategii. Nie ukrywać
niespełnionych ograniczeń oceny ani energii UNKNOWN w „oszczędnościach”.

Testy używają tymczasowej syntetycznej bazy zgodnej ze schematem oraz osobnej
aplikacji FastAPI zawierającej tylko router analityki. Żądania przechodzą
przez ASGI, zależności, walidację i serializację. Bez produkcyjnego lifespan,
MQTT, seedowania i otwierania bazy stanowiska. Polecenie z katalogu repozytorium:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend -v
```

Import routera nie importuje `app.main`, `app.core.database` ani MQTT.
Rejestracja routera nie zmienia istniejącego lifespan backendu: jego zwykłe
uruchomienie nadal ma dotychczasowe skutki aplikacyjne. Dlatego testy analityki
nie uruchamiają `main.py` ani produkcyjnego serwera.

## Historia temperatury i wilgotności

`GET /api/analytics/environment` przyjmuje `metric=temperature` (jednostka
ściśle `°C`) albo `metric=humidity` (ściśle `%`). Wymagane są `device_id`,
daty ze strefą `from`/`to` i jawny `ttl_seconds` (od mikrosekundy do 365 dni).
Opcjonalne `lower_threshold` i `upper_threshold` muszą być skończone; dolny
nie może przekraczać górnego. Obowiązują istniejące tokeny, uprawnienia,
limity zakresu i rekordów oraz format błędów. Odczyt jest tylko do odczytu,
obliczenia odbywają się w puli wątków. Nie zmieniają symulacji occupancy.

Przykład żądania (syntetyczny czujnik `sensor`):

```powershell
Invoke-RestMethod -Headers $headers -Uri 'http://localhost:8000/api/analytics/environment?device_id=sensor&from=2026-01-01T08:00:00Z&to=2026-01-01T08:10:00Z&metric=temperature&ttl_seconds=120&lower_threshold=15&upper_threshold=25'
```

Dla pomiarów 10°C o 08:00 i 30°C o 08:05, bez wcześniejszych pomiarów,
odpowiedź ma poniższe wartości (wyciąg; `quality` zawiera także standardowe
szczegóły walidacji, duplikatów, konfliktów i odstępów):

```json
{
  "source": {"format": "SQLite measurements", "kind": "synthetic", "read_only": true, "location": "server-configured"},
  "device_id": "sensor",
  "metric": "temperature",
  "period": {"from": "2026-01-01T08:00:00+00:00", "to": "2026-01-01T08:10:00+00:00"},
  "ttl_seconds": 120,
  "lower_threshold": 15,
  "upper_threshold": 25,
  "unit": "°C",
  "valid_readings_count": 2,
  "minimum": 10,
  "maximum": 30,
  "time_weighted_mean": 20,
  "duration_seconds": 600,
  "known_seconds": 240,
  "unknown_seconds": 360,
  "coverage_percent": 40,
  "thresholds_evaluated": true,
  "below_seconds": 120,
  "within_seconds": 0,
  "above_seconds": 120,
  "quality": {"input_rows": 2, "selected_rows": 2, "rejected_rows": 0, "blocking_reasons": []}
}
```

Zasady interpretacji:

- Wartość obowiązuje do następnego pomiaru lub wygaśnięcia TTL, z przycięciem
  do `[from,to)`. Uwzględniany jest najpóźniejszy raport sprzed początku zakresu
  wyłącznie przez pozostały czas ważności. Nie interpolujemy ani nie uzupełniamy luk.
- `valid_readings_count`, `minimum` i `maximum` dotyczą poprawnych, unikalnych
  odczytów z czasem w `[from,to)`; duplikaty tego samego czasu/wartości/jednostki
  liczą się raz. Wcześniejszy raport oraz raport dokładnie w `to` nie są liczone.
- `time_weighted_mean = suma(wartość × czas ważności) / known_seconds`.
  Nie jest to średnia próbek. Przykład bez luk: 10°C przez 60 s, 20°C przez
  240 s i 30°C przez 300 s daje **24°C**, a nie 20°C.
- Bez odczytów w zakresie min/max są `null`, a licznik to 0. Ważny raport
  sprzed zakresu może nadal dać średnią czasową. Gdy brak jakichkolwiek aktualnych
  danych, średnia jest `null`, pokrycie 0%, a cały okres to `unknown_seconds`.
- Równość progowi zalicza się do `within_seconds`. Jeśli podano tylko jeden
  próg, druga granica jest nieograniczona. Jeśli nie podano żadnego, wszystkie
  trzy czasy progowe są `null` i `thresholds_evaluated=false` — nie oceniamy
  przekroczeń. Z progami czasy poniżej/wewnątrz/powyżej sumują się do czasu
  znanego; UNKNOWN jest zawsze osobną kategorią. Czasy są w sekundach.
- Jednostki są sprawdzane przez wspólny walidator CSV/SQLite. Nie konwertujemy
  `C`, `F` ani innych jednostek. Konflikty, błędne jednostki, wartości i daty
  blokują analizę błędem 409 `ANALYTICS_DATA_QUALITY`, zamiast arbitralnego
  wyboru lub usuwania danych. Obejmuje to całą grupę ostatniego czasu sprzed
  zakresu. Błędnych dat nie można umiejscowić, więc są zachowywane do walidacji.
- `quality` obejmuje również wcześniejszą grupę raportów; jej licznik wejścia
  może być większy od `valid_readings_count`. Pochodzenie `source.kind`
  pozostaje deklaracją serwera; dane syntetyczne nie stają się rzeczywistymi.

Lista `/api/analytics/devices` zachowuje dotychczasową semantykę (urządzenia
z occupancy). Dla czujników środowiskowych frontend przekazuje identyfikator
z istniejącej listy urządzeń backendu; nie należy ograniczać wyboru do listy occupancy.

## Energia elektryczna całej listwy

`GET /api/analytics/electricity` wymaga urządzenia z `devices.typ=power_strip`.
Nazwa lub identyfikator `listwa` nie zastępuje sprawdzenia typu. Urządzenia
ciepłownicze i inne typy są odrzucane błędem 422 `ANALYTICS_DEVICE_TYPE`.
Źródłem są wyłącznie `measurements.metric=energy` w **kWh** oraz `power`
w **W** tego urządzenia. Nie łączymy tych danych z energią cieplną ani
z innymi urządzeniami. Wynik dotyczy całej listwy, bez podziału na gniazda.

Parametry: `device_id`, daty ISO ze strefą `from`, `to` (`from < to`),
opcjonalnie skończone i nieujemne `price_per_kwh`. Nie ma TTL: licznik
kumulacyjny rozliczamy inaczej niż chwilowe pomiary środowiskowe.

```powershell
Invoke-RestMethod -Headers $headers -Uri 'http://localhost:8000/api/analytics/electricity?device_id=listwa&from=2026-01-01T08:00:00Z&to=2026-01-01T09:00:00Z&price_per_kwh=1.25'
```

Przykład syntetyczny: licznik 10 kWh o 08:00, 12 kWh o 08:30 i 13 kWh
o 09:00, maksymalna próbka mocy 60 W o 08:05. Wyciąg odpowiedzi (pominięto
pełne raporty jakości, pary i listę założeń):

```json
{
  "source": {"format": "SQLite measurements", "kind": "synthetic", "read_only": true, "location": "server-configured"},
  "device_id": "listwa",
  "device_type": "power_strip",
  "period": {"from": "2026-01-01T08:00:00+00:00", "to": "2026-01-01T09:00:00+00:00"},
  "energy_simulated": false,
  "scope": "whole_power_strip",
  "energy_unit": "kWh",
  "power_unit": "W",
  "energy_kwh": 3,
  "price_per_kwh": 1.25,
  "estimated_cost": 3.75,
  "max_power_w": 60,
  "max_power_at": "2026-01-01T08:05:00Z",
  "energy_readings_count": 3,
  "power_readings_count": 2,
  "accepted_pairs_count": 2,
  "skipped_pairs_count": 0,
  "counter_drops_count": 0,
  "observed_energy_bounds": {"start": "2026-01-01T08:00:00Z", "end": "2026-01-01T09:00:00Z"},
  "used_energy_bounds": {"start": "2026-01-01T08:00:00Z", "end": "2026-01-01T09:00:00Z"},
  "duration_seconds": 3600,
  "covered_seconds": 3600,
  "uncovered_seconds": 0
}
```

- Odczyty energii porządkujemy w UTC i sumujemy nieujemne różnice kolejnych
  poprawnych, unikalnych odczytów. **Nie używamy max-minus-min.** Stały licznik
  tworzy poprawną parę z przyrostem 0. Mniej niż dwa odczyty lub brak poprawnych
  par daje `energy_kwh=null` oraz `estimated_cost=null`.
- Oba końce pary muszą należeć do `[from,to]`. Odczyt dokładnie w `to` może
  zamknąć ostatnią parę. Nie używamy odczytu sprzed `from`, odczytu po `to`
  ani interpolacji granic. Przykładowo pierwszy odczyt o 08:10 dla zakresu
  od 08:00 pozostawia pierwsze 10 minut nieobjęte obliczeniem.
- Spadek oznacza reset lub anomalię. Pomijamy daną parę i jej czas, nie
  szacujemy utraconej energii. Następny nieujemny przyrost może zostać użyty.
  Przykład 10 → 12 → 1 → 4 daje **5 kWh**, jedną pominiętą parę i jeden
  spadek. `skipped_pairs` zawiera oba czasy, wartości licznika, czas trwania
  i `reason=counter_reset_or_anomaly`. `accepted_pairs` ma analogiczne pola
  oraz `increment_kwh`.
- `observed_energy_bounds` opisuje granice wszystkich unikalnych poprawnych
  odczytów energii w zakresie. `used_energy_bounds` opisuje skrajne końce
  zaakceptowanych par; wynosi `null`, gdy nie ma żadnej. Te granice mogą
  obejmować pominięte pary pośrodku. Dlatego `covered_seconds` jest **sumą
  czasów zaakceptowanych par**, nie różnicą skrajnych czasów.
  `uncovered_seconds = duration_seconds - covered_seconds` obejmuje
  niezamknięte brzegi okresu i czas par ze spadkiem.
- Długa przerwa między dwoma odczytami sama nie unieważnia przyrostu licznika
  kumulacyjnego. Zakładamy jego ciągłość w tej parze; ukrytych resetów lub
  innych błędów nie da się wykluczyć z samych dwóch odczytów. Pokrycie czasu
  **nie oznacza znajomości przebiegu mocy wewnątrz przerwy**. Nie rozkładamy
  energii równomiernie po czasie. Rozkład odstępów jest dostępny w jakości.
- `max_power_w` i `max_power_at` pochodzą z próbek mocy w `[from,to)`;
  przy remisie wybieramy najwcześniejszą próbkę. Nie jest to gwarantowane
  maksimum chwilowe między próbkami. Bez próbek oba pola są `null`.
- `estimated_cost = energy_kwh × price_per_kwh`, tylko dla zaakceptowanej
  energii. Bez ceny koszt jest `null`; stawka 0 jest dozwolona. Nie zakładamy
  waluty, opłat stałych ani taryf czasowych. Cena jest w jednostkach wybranej
  waluty na kWh, koszt w tej samej walucie.
- `quality.energy` i `quality.power` mają wspólny format raportu jakości.
  Liczniki `energy_readings_count`/`power_readings_count` dotyczą unikalnych
  poprawnych odczytów; liczniki jakości ujawniają także duplikaty. Konflikty
  (także przy `to` dla energii), błędne daty, wartości i jednostki blokują
  endpoint błędem 409. Nie wybieramy arbitralnie jednej wartości. Błędne daty
  wybranego strumienia są zachowywane do walidacji, ponieważ nie można
  umiejscowić ich w okresie. Przy poprawnych danych `skipped_pairs_count`
  liczy wyłącznie pary ze spadkiem — błędnych wierszy nie usuwamy po cichu.
- `energy_simulated=false` oznacza obliczenie z przyrostów licznika zamiast
  modelu ON/OFF. Nie dowodzi rzeczywistego pochodzenia: `source.kind` nadal
  jest `real`, `synthetic` lub `unknown` zgodnie z konfiguracją serwera.
  Zużycie nie jest oszczędnością, a koszt pozostaje szacunkiem.

Endpoint korzysta z istniejącego uwierzytelniania, odczytu `mode=ro` oraz
limitu czasu. Limit rekordów obowiązuje **łącznie** dla skanowanych strumieni
`energy` i `power` danego urządzenia, również poza okresem, przed deduplikacją.
Typ urządzenia i oba strumienie są odczytywane w jednej transakcji tylko do
odczytu. Obliczenia wykonuje synchroniczny endpoint w puli wątków. Nie są
uruchamiane MQTT, sterowanie ani zapisy do bazy.

## Podsumowanie ciepłomierza

`GET /api/analytics/heat` przyjmuje `device_id`, `from`, `to` (daty ISO
ze strefą, `from < to`) oraz wymagany dodatni `ttl_seconds` (od mikrosekundy
do 365 dni, bez domyślnej wartości). Wymaga `devices.typ=heat_meter`; nie
przyjmuje listwy ani nie miesza energii cieplnej z elektryczną. Nie przyjmuje
ceny. Jest to analiza zapisanej historii, bez symulacji i sterowania.

```powershell
Invoke-RestMethod -Headers $headers -Uri 'http://localhost:8000/api/analytics/heat?device_id=heat_meter_1&from=2026-01-01T08:00:00Z&to=2026-01-01T09:00:00Z&ttl_seconds=120'
```

Format odpowiada metrykom obecnego backendu:

| Metryka | Jednostka | Wynik |
| --- | --- | --- |
| `energy` | kWh | suma zaakceptowanych przyrostów licznika energii cieplnej |
| `power` | W | maksymalna zaobserwowana moc cieplna i czas odczytu, statystyki próbek |
| `temperature_1`, `temperature_2` | °C | liczba próbek, min/max, pierwsza i ostatnia data, ostatnia wartość |
| `flow` | l/min | liczba próbek, min/max, pierwsza i ostatnia data, ostatnia wartość |

Nie przypisujemy automatycznie kanałom temperatur znaczenia zasilania/powrotu
i nie wyliczamy energii z temperatur oraz przepływu. Odpowiedź zawiera
`energy_kind="thermal"`, `scope="heat_meter"`, `energy_simulated=false`,
`source`, `period` oraz osobny raport `quality` dla każdej metryki.
Oznaczenie `source.kind` pozostaje `synthetic`, `real` lub `unknown` zgodnie
z konfiguracją serwera. `false` przy symulacji nie dowodzi pochodzenia ze sprzętu.

Energia używa tych samych zasad przyrostów co `/electricity`: uporządkowanie
odczytów, deduplikacja, pary o obu końcach w `[from,to]`, dopuszczenie odczytu
końcowego, bez interpolacji i raportów sprzed zakresu. Spadek licznika pomija
daną parę, a długie przerwy wymagają założenia ciągłości licznika. Zwracane są
pary, spadki, granice danych oraz czas objęty i nieobjęty obliczeniem. Bez
poprawnych par energia jest `null`. Nie ma kosztu ani modelowanej energii ON/OFF.

Min/max, licznik i ostatni odczyt pozostałych metryk opisują próbki
w `[from,to)`. Dodatkowo każda ma `time_weighted_mean`, `known_seconds`,
`unknown_seconds` i `coverage_percent`. Średnia uwzględnia tylko czas aktualności
pomiaru do kolejnego odczytu lub wygaśnięcia TTL; ważny raport sprzed początku
zakresu wnosi jedynie pozostałą ważność. Nie interpolujemy luk. TTL nie jest
stosowany do kumulacyjnej energii. Brak metryki nie blokuje innych: licznik
wynosi 0, statystyki `null`, pokrycie 0%. Sam ważny wcześniejszy raport może
dać średnią czasową mimo braku próbek wewnątrz okresu.

`temperature_difference` opisuje **temperature_1 − temperature_2** tylko
we wspólnych przedziałach aktualności obu kanałów. Każdy odczyt wygasa niezależnie
według swojego czasu i tego samego jawnego TTL. Nowy odczyt jednego kanału nie
odświeża drugiego. Min/max i średnia ważona tej różnicy dotyczą wspólnych
przedziałów; `known_seconds` i `coverage_percent` są wspólnym pokryciem,
`unknown_seconds` resztą okresu, a `negative_seconds` czasem ściśle ujemnej
różnicy. Nie klasyfikujemy jej jako awarii. Gdy nie ma wspólnego aktualnego
przedziału, min/max, średnia i czas ujemnej różnicy są `null`, wspólny czas
wynosi 0, a nieznany czas obejmuje cały okres. Różnice mają jednostkę °C.

Przykładowy fragment odpowiedzi (pominięto średnie, pokrycia i różnicę):

```json
{
  "device_type": "heat_meter",
  "energy_kind": "thermal",
  "energy_simulated": false,
  "energy_kwh": 5,
  "measurements": {
    "temperature_1": {
      "unit": "°C", "readings_count": 2, "minimum": 55, "maximum": 65,
      "first_at": "2026-01-01T08:00:00Z", "last_at": "2026-01-01T08:30:00Z", "last_value": 65
    }
  }
}
```

Konflikty oraz błędne wartości, daty i jednostki blokują wynik kodem 409.
Obowiązują dotychczasowe tokeny, uprawnienia, format błędów i limity. Limit
rekordów obejmuje łącznie pięć skanowanych strumieni, przed deduplikacją.
Typ urządzenia i pomiary są odczytywane w jednej transakcji `mode=ro`.

Przypadek ręcznie policzony w testach: zakres 08:00–08:10, TTL 180 s,
temperature_1: 50°C o 07:59, 20°C o 08:01, 40°C o 08:04;
temperature_2: 30°C o 08:00, 50°C o 08:03. Różnica jest znana przez 360 s
(60% okresu), jej minimum to −30°C, maksimum 20°C, średnia −8⅓°C,
a czas ujemnej różnicy 300 s. Żadna z tych wartości nie stanowi diagnozy.
