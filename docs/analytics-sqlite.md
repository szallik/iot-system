# Analityka z SQLite — główne źródło historii, tylko odczyt

`backend/analytics_db.py` jest głównym wejściem do analizy historii backendu.
Dotychczasowy `backend/analytics_csv.py` pozostaje dostępny do eksportów CSV.
Oba adaptery używają wspólnej walidacji, raportu jakości, symulatora i modelu
energii. Żaden nie uruchamia `main.py`, klienta MQTT, seedowania ani tworzenia tabel.

## Lokalizacja bazy backendu

W `backend/app/core/config.py` klasa `Settings` ma domyślnie:

```python
DATABASE_URL: str = "sqlite:///./data/iot_system.db"
```

`BaseSettings` może otrzymać inną wartość z otoczenia lub `.env`
(`SettingsConfigDict(env_file=".env")`). Ścieżka względna jest rozwiązywana
względem katalogu, z którego uruchomiono backend. Jeśli był to `backend`,
typowa lokalizacja to `backend/data/iot_system.db`; nie jest ona gwarantowana.
Adapter analityki **nie odczytuje tej konfiguracji ani `.env`** — wymaga jawnej
ścieżki do istniejącego pliku. Podczas implementacji nie otwierano rzeczywistej
bazy projektu, testowano tylko bazy tymczasowe.

## Polecenia Windows / PowerShell

Uruchamiaj z katalogu głównego `iot-room-system`. Poniżej `$analyticsDb` jest
przykładową ścieżką, którą trzeba zastąpić właściwą ścieżką do bazy lub jej
spójnej kopii. Polecenia nie używają automatycznie domyślnej bazy backendu.

```powershell
$analyticsDb = 'C:\Dane\IoT\historia.db'
```

Urządzenia mające pomiary `occupancy` (bez TTL i bez zakresu):

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_db.py devices "$analyticsDb" --source-kind unknown --output devices.json
```

Każde urządzenie ma `readings` (liczba wszystkich rekordów occupancy, również
duplikatów i błędów), zakres poprawnych pomiarów UTC, liczniki błędnych dat
i wartości oraz wspólny raport jakości. Urządzenie mające wyłącznie temperaturę
nie pojawi się na liście. Nie jest wymagana tabela `devices`: lista pochodzi
bezpośrednio z `measurements`, więc obejmuje również historyczne identyfikatory.

Jakość dla jednego urządzenia i okresu — nadal bez TTL:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_db.py quality "$analyticsDb" --device-id czujnik_obecnosci --metric occupancy --from 2026-01-05T08:00:00Z --to 2026-01-05T18:00:00Z --source-kind unknown --output quality-db.json
```

Symulacja ze stałym opóźnieniem:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_db.py simulate "$analyticsDb" --device-id czujnik_obecnosci --from 2026-01-05T08:00:00Z --to 2026-01-05T18:00:00Z --ttl-seconds 180 --strategy fixed --delay-minutes 5 --on-watts 60 --off-watts 2 --source-kind unknown --output simulation-db.json
```

TTL 180 s i moce 60/2 W są **przykładami**, nie ustawieniami rekomendowanymi
dla konkretnego urządzenia. TTL oraz moc ON i OFF są obowiązkowe; nie ma
automatycznego TTL 2 min. Zakres wymaga dat ze strefą; koniec jest wyłączny.
Domyślna metryka to occupancy; quality pozwala podać inne metryki, ale symulacja
obecności wymaga occupancy. Urządzenie wybiera się dokładnym `device_id`.

Harmonogram z pliku konfiguracji:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_db.py simulate "$analyticsDb" --device-id czujnik_obecnosci --from 2026-01-05T08:00:00Z --to 2026-01-05T18:00:00Z --ttl-seconds 180 --strategy schedule --strategy-config docs/examples/example-schedule.json --on-watts 60 --off-watts 2 --source-kind unknown --output schedule-db.json
```

Zamrożona adaptacja:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_db.py simulate "$analyticsDb" --device-id czujnik_obecnosci --from 2026-02-01T00:00:00Z --to 2026-02-02T00:00:00Z --ttl-seconds 180 --strategy adaptive --strategy-config docs/analytics-adaptive-example.json --on-watts 60 --off-watts 2 --source-kind unknown --output adaptive-db.json
```

Wczytywane są istniejące opóźnienia i grupy, **bez uczenia na analizowanym
okresie i bez zmiany artefaktu**. Załączony artefakt pochodzi z danych
syntetycznych. Samo uruchomienie polityki nie jest dowodem jej przydatności
na stanowisku ani niezależną walidacją treningu; trzeba zadbać o rozdzielenie
okresów i ocenę jakości danych.

## Gwarancje odczytu i zgodność ze schematem

Adapter używa biblioteki standardowej `sqlite3`, URI `file:...?...mode=ro`
(w kodzie `Path.as_uri() + "?mode=ro"`) i jawnie sprawdza istnienie pliku.
Brak pliku, tabeli `measurements` albo którejś z kolumn
`id,device_id,metric,value,unit,ts` daje czytelny błąd. Pusta baza nie powstaje.
Spacje, polskie znaki i `#` w ścieżce są kodowane przez URI.

Połączenie ma jedną transakcję odczytu i jest zawsze zamykane. Nie importuje
silnika SQLAlchemy aplikacji, nie wykonuje jej PRAGMA `journal_mode=WAL`,
nie zmienia konfiguracji, danych ani schematu. `PRAGMA table_info` jedynie
odczytuje strukturę. Zapis przez takie połączenie jest blokowany przez SQLite.
Zapytania filtrujące urządzenie i metrykę są parametryzowane.

Nie używamy `immutable=1`, ponieważ mogłoby to pominąć aktualne dane w WAL.
SQLite odczytuje zatwierdzone dane z dziennika WAL zgodnie ze swoim protokołem
blokad i plików współdzielonych; tryb `ro` nie oznacza ignorowania WAL ani
braku aktywności mechanizmów blokowania/SHM. Do powtarzalnej analizy najlepiej
użyć spójnego snapshotu wykonanego narzędziem SQLite. Samo skopiowanie `.db`
z działającego backendu WAL może pominąć zatwierdzone dane z `-wal`.
Adapter nie wykonuje checkpointu ani kopii rzeczywistej bazy.

Testy potwierdzają brak zmian zawartości i mtime głównego pliku, brak zmian
schematu i odmowę DELETE/CREATE. Osobny test WAL potwierdza odczyt zatwierdzonego
rekordu z WAL bez zmiany zawartości plików bazy i WAL.

## Zakres czasu i jakość

`ts` w modelu projektu jest tekstem. Adapter pobiera wyłącznie strumień
`device_id + metric` i interpretuje daty wspólnym parserem, normalizując je do UTC.
Nie porównuje tekstów dat alfabetycznie, co byłoby błędne przy różnych offsetach.
Następnie wybiera:

- wszystkie rekordy w `[from,to)`;
- wszystkie rekordy o najpóźniejszej chwili UTC **ściśle przed** `from`, również
  gdy mają różne zapisy offsetu, duplikaty lub sprzeczne wartości;
- rekordy wybranego strumienia z niepoprawną datą — nie można uczciwie ustalić,
  czy należą do zakresu, więc są raportowane i konserwatywnie blokują symulację.

Jest to pełny przegląd jednego strumienia, nie całej bazy. W pierwszej wersji
koszt pamięci i czasu rośnie z rozmiarem historii tego urządzenia i metryki.
Najpóźniejszy wcześniejszy odczyt jest wybierany przed walidacją wartości;
błędnego odczytu nie zastępujemy starszym poprawnym. Przy równym czasie
wszystkie konflikty pozostają widoczne. Wcześniejszy raport wpływa na stan
początkowy tylko jeśli spełnia podany TTL. Brak świeżych danych daje UNKNOWN,
nigdy brak obecności. Odbiornik zaczyna symulację w ON jak we wcześniejszych etapach.

Walidacja i format `quality` są wspólne z CSV: dokładne 0/1 (w tym 0.0/1.0),
daty ze strefą, duplikaty, konflikty, zakres, odstępy i największe przerwy.
Konflikty oraz błędne wybrane dane blokują symulację. Szczegóły liczników
opisano w [analytics-csv.md](analytics-csv.md). `input_rows` dla SQLite oznacza
wiersze przekazane po selekcji zakresu wraz z poprzednikiem i niepoprawnymi
datami; nie jest liczbą rekordów całej bazy. `source.scanned_stream_rows`
i `excluded_by_time` pokazują rozmiar przeglądu i liczbę pominiętych dat.
`quality.time_range` może zaczynać się przed żądanym zakresem przez poprzednika.

Numery `record` w diagnostyce są ordinalami od 2, tak jak w CSV z nagłówkiem.
Odpowiadające rzeczywiste identyfikatory znajdują się w `measurement_ids`
(`measurement_ids[record-2]`). Dla tej samej uporządkowanej listy pomiarów
raport jakości i wynik symulacji CSV/SQLite są identyczne, poza metadanymi źródła.

Wynik JSON zawiera żądany zakres, źródło, deklarację pochodzenia, TTL, model
odbiornika, konfigurację strategii i jakość. `--source-kind synthetic|real|unknown`
domyślnie wynosi **unknown**; SQLite samo w sobie nie dowodzi rzeczywistych
pomiarów. CSV zachowuje wcześniejsze nazwy `synthetic|hardware|unspecified`.
Nawet dla `real` obecność jest odczytana z bazy, natomiast energia z modelu
ON/OFF pozostaje **symulowana**, co raport oznacza jawnie. UNKNOWN nie jest
potwierdzeniem ograniczenia zbędnego zużycia.

## Testy i bezpieczna demonstracja

Pełne testy z katalogu repozytorium:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend -v
```

Demonstracja tworzy **wyłącznie tymczasową bazę syntetyczną**, uruchamia
devices/quality/simulate i usuwa swoje pliki. Z katalogu repozytorium:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m tests.demo_sqlite
Pop-Location
```

Baza używa schematu odpowiadającego modelom Device/Measurement. Dla przykładu:
8 wybranych wierszy, 7 unikalnych i 1 duplikat; energia odniesienia 15 Wh,
strategii 7,266667 Wh (ON 60 W, OFF 2 W, TTL 180 s, opóźnienie 2 min).
SHA-256 bazy pozostaje niezmienione. Żaden test nie otwiera bazy backendu.

Kody zakończenia: 0 dla sporządzonego raportu (w quality sprawdź również
`blocking_reasons`), 2 dla błędu lub zablokowanej symulacji. JSON diagnostyczny
zachowuje powody błędu. Ścieżka wyniku nie może nadpisać bazy, jej plików
`-wal`, `-shm`, `-journal` ani konfiguracji strategii. Inny istniejący JSON
wynikowy zostanie zastąpiony; jego katalog musi istnieć.
