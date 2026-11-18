# Historia CSV i jakość danych — tryb offline

Importer odpowiada aktualnemu endpointowi `GET /api/export?dataset=measurements`
z `backend/app/routers/access_reports.py` i modelowi `Measurement`.
Nie importuje routera ani modeli, nie otwiera SQLite, nie uruchamia FastAPI/MQTT.
Test zgodności nagłówka odczytuje kod eksportera jako AST, bez wykonywania go.

Eksport ma kodowanie UTF-8, separator przecinek, standardowe cytowanie CSV,
nagłówek `id,device_id,metric,value,unit,ts` i porządek malejący po czasie.
Importer obsługuje również BOM i przestawioną kolejność tych samych kolumn;
nie obsługuje eksportów `events` i `access_attempts` ani CSV z innym separatorem.
Wymaga wszystkich sześciu unikalnych kolumn, bez dodatkowych.

## Raport jakości bez TTL

Z katalogu głównego repozytorium w PowerShell:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_csv.py quality docs/examples/synthetic-measurements.csv --device-id SYNTHETIC_ROOM_A --metric occupancy --source-kind synthetic --output quality.json
```

`docs/examples/synthetic-measurements.csv` jest **wyłącznie syntetyczny**,
zgodny z formatem eksportera. Nazwa pliku i identyfikatory urządzeń to jawnie
oznaczają; komentarzy nie dopisano do CSV, żeby zachować format backendu.
Zawiera dwa urządzenia, metrykę temperatury, identyczny duplikat, luki i raport
sprzed przykładowego początku analizy. Wynik: 10 rekordów, 8 wybranych,
2 odfiltrowane, 1 odrzucony duplikat, 7 unikalnych zaakceptowanych raportów.

`--device-id` jest obowiązkowe, dopasowanie identyfikatora i `--metric` jest
dokładne. Domyślna metryka to `occupancy`. W trybie jakości można podać np.
`temperature` lub `power`. Dane różnych urządzeń ani metryk nie są łączone.
Daty/wartości innych, poprawnie ustrukturyzowanych strumieni są odfiltrowane
przed walidacją; raport nie ocenia ich jakości.

Wartości są parsowane jako liczby dziesiętne; obecność akceptuje wyłącznie
liczbowe 0 i 1, także eksportowane `0.0` i `1.0`. Teksty `true`, `false`,
ułamki, NaN, nieskończoność i wartości zaokrąglające się do 1 w float są
odrzucane. Wymagane jest dodatnie całkowite `id` oraz jednostka `bool` dla
`occupancy`, zgodnie z zapisem backendu. Inne metryki wymagają skończonej
wartości mieszczącej się w float i niepustej jednostki.
Daty muszą być poprawnym ISO 8601 ze strefą (`Z` lub przesunięcie), są
normalizowane do UTC. Daty bez strefy nie otrzymują automatycznie Europe/Warsaw.

JSON jakości zawiera:

- `input_rows`: rekordy po nagłówku; `selected_rows`: poprawnie ustrukturyzowane
  wiersze pasujące do urządzenia i metryki, także z błędnymi wartościami/datami.
- `filtered_rows`: inne urządzenia/metryki; `unclassified_malformed_rows`:
  zła liczba pól lub uszkodzony rekord CSV, którego nie można bezpiecznie przypisać.
- `accepted_unique_rows` i `rejected_rows`. Zachodzi
  `input_rows = filtered_rows + accepted_unique_rows + rejected_rows`.
  Odrzucone obejmują błędne wiersze, nadmiarowe duplikaty oraz wszystkie wiersze
  sprzecznych znaczników czasu; żaden wiersz nie jest liczony tu dwukrotnie.
- `issues` z numerem rekordu (nagłówek = 1), kodem i błędnym polem;
  oddzielne liczniki niepoprawnych dat i wartości. Jeden wiersz może mieć
  kilka diagnostyk, ale tylko raz zwiększa liczbę odrzuconych.
- `duplicates`: ten sam czas UTC, wartość i jednostka; `id` może się różnić.
  Duplikaty identycznej obserwacji są jawnie scalane. `conflicts`: różne
  wartości/jednostki dla tego samego czasu w wybranym strumieniu, wraz
  z numerami rekordów. Diagnostyki duplikatów mogą obejmować również konflikt,
  więc ich liczników nie należy sumować z `conflict_rows`.
- Zakres czasu poprawnych wybranych rekordów, także konfliktów; przy braku
  poprawnych rekordów `null`. Zakres pliku nie jest automatycznie zakresem symulacji.
- Rozkład odstępów między różnymi, posortowanymi poprawnymi znacznikami UTC
  (również konfliktowymi): min/max, średnia, mediana, p90/p95 metodą nearest-rank,
  histogram i 10 największych przerw z czasami początku i końca. Duplikaty nie
  tworzą sztucznych odstępów zero. Dla <2 znaczników statystyki są `null`/puste.

Gdy uszkodzone cytowanie uniemożliwia dalsze odczytanie rekordów, raport ma
`parse_complete=false`; licznik wejściowy dotyczy tylko rozpoznanego prefiksu
łącznie z wadliwym rekordem, nie jest deklaracją rozmiaru całego pliku.
Zły nagłówek lub nieczytelny plik daje JSON `status=blocked` z błędem.

**Odstępy nie wystarczają do rozróżnienia raportowania cyklicznego i zdarzeniowego.**
Cisza nie dowodzi utraty łączności ani nieobecności. Raport jakości nie ustala
TTL, nie rekomenduje go na podstawie mediany i nie interpretuje największych
przerw jako awarii. Trzeba sprawdzić sposób raportowania konkretnego czujnika.

## Symulacja z jawnym TTL

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_csv.py simulate docs/examples/synthetic-measurements.csv --device-id SYNTHETIC_ROOM_A --source-kind synthetic --from 2026-01-05T08:00:00Z --to 2026-01-05T08:15:00Z --ttl-seconds 180 --strategy fixed --delay-minutes 2 --on-watts 60 --off-watts 2 --output simulation.json
```

180 sekund to **wyłącznie jawne założenie tego przykładu**. Dla danych
sprzętowych parametr nie ma wartości domyślnej. Zakres `[from,to)` oraz obie
moce również trzeba podać. Metryka symulacji musi być `occupancy`.
Próg krótkiego powrotu pozostaje konfigurowalny przez `--short-return-seconds`
(domyślnie 120, zgodnie z wcześniejszym modułem; to nie TTL).

Importer zachowuje cały wybrany strumień. Odtwarzanie korzysta z ostatniego
raportu sprzed `from`, jeśli jest w pliku i jeszcze nie wygasł. JSON podaje
`last_pre_start_report` i `pre_start_report_is_fresh`. Dla przykładu raport
07:59 jest ważny o 08:00 przy TTL 180 s. Jeżeli eksport backendu odcięto
dokładnie na początku analizy, importer nie odzyska brakującego raportu:
należy eksportować również wcześniejsze dane. Brak aktualnego raportu
oznacza UNKNOWN, nigdy ABSENT. Nawet pusty wybrany strumień daje cały okres
UNKNOWN, zachowuje początkowe ON i nie generuje automatycznego OFF.

Symulację blokują **wszystkie konflikty w wybranym strumieniu**, także poza
żądanym zakresem. Nie wybieramy arbitralnie ostatniego lub pierwszego wiersza.
Konserwatywnie blokujemy też niepoprawne wybrane wartości/daty/id/jednostki
i uszkodzone rekordy CSV — nie interpolujemy przez odrzucony błąd. W JSON
pozostaje pełny raport jakości i przyczyny blokady; pole `result` nie powstaje.
Identyczne duplikaty same w sobie nie blokują symulacji.

## Zamrożony harmonogram lub adaptacja

`docs/examples/example-schedule.json` zawiera przykładową, niezalecaną
konfigurację pn–pt 08:00–16:00 = 10 minut, poza tym 2 minuty, Europe/Warsaw.

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_csv.py simulate docs/examples/synthetic-measurements.csv --device-id SYNTHETIC_ROOM_A --source-kind synthetic --from 2026-01-05T08:00:00Z --to 2026-01-05T08:15:00Z --ttl-seconds 180 --strategy schedule --strategy-config docs/examples/example-schedule.json --on-watts 60 --off-watts 2 --output schedule-simulation.json
```

Dla już wyuczonej adaptacji użyj `--strategy adaptive --strategy-config
docs/analytics-adaptive-example.json` zamiast opcji strategii powyżej. Import
wymaga artefaktu `schema_version=1`, `status=recommended` i odczytuje jedynie
zamrożoną konfigurację grup. **Nie trenuje ani nie modyfikuje adaptacji na CSV**.
Ta opcja jest odtworzeniem danej polityki, nie niezależną oceną skuteczności:
nie dowodzi rozdzielenia dat treningu i danych CSV. Przy ocenie naukowej trzeba
zachować późniejszy, rozłączny okres zgodnie z procedurą `evaluate_adaptive`.

## Metadane i interpretacja

Każdy JSON zawiera źródło i SHA-256 pliku, urządzenie, metrykę, zakres czasu,
TTL, konfigurację strategii, model odbiornika i jakość danych. Dla samej jakości
TTL/strategia/model mają `null`. `--source-kind hardware|synthetic|unspecified`
jest deklaracją użytkownika (domyślnie `unspecified`), nie automatyczną oceną
pochodzenia danych. Nazwa pliku sama nie zmienia tej deklaracji.

Wynik symulacji zawiera przedziały obecności, przejścia stanów, przedziały OFF,
energie i ich różnice według kategorii, udział znanych danych, wyłączenia,
ponowne włączenia oraz pewne/niepewne powroty. Energia z modelu ON/OFF zawsze
ma oznaczenie **symulowana**, nawet dla obecności ze sprzętu. Nie korzystamy
z pomiarów mocy listwy ani nie zakładamy pomiarów każdego gniazda. UNKNOWN
nie daje potwierdzonych oszczędności. Odbiornik zaczyna analizę w ON.

Polecenie `quality` kończy się kodem 0, jeśli sporządzono raport, nawet gdy
wykryto błędy danych. Należy sprawdzić `quality.blocking_reasons`.
`simulate` przy blokadzie kończy się kodem 2 i zapisuje JSON diagnostyczny.
Błędy argumentów (np. brak TTL) mają kod 2 i pomoc CLI bez raportu.
Ścieżka wyniku jest obowiązkowa i nie może wskazywać źródłowego CSV lub pliku
konfiguracji. Inny istniejący plik wynikowy zostanie zastąpiony; katalog musi istnieć.

Dotychczasowa demonstracja syntetyczna `backend/analytics_demo.py` pozostaje
niezmieniona. Testy importu, CLI i regresji:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend -v
```
