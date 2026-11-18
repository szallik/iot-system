# Analityka historii pomiarów

- Sprawdza jakość historii obecności i symuluje pracę odbiornika ze stałym opóźnieniem, harmonogramem lub zamrożoną strategią adaptacyjną.
- Porównuje strategie, wybiera rekomendację na wcześniejszym okresie i ocenia ją na późniejszym. Rekomendacja nie uruchamia automatyki.
- Analizuje temperaturę i wilgotność, zużycie energii całej listwy oraz pomiary ciepłomierza.
- API czyta historię z plikowej bazy SQLite. CLI działa offline bez MQTT. CSV obsługuje jakość, symulację i trzy analizy pomiarów. CLI SQLite służy do listowania czujników obecności, oceny jakości i symulacji.
- Frontend nie ma osobnego widoku analityki. Istniejące strony statystyk i ciepłomierza nadal korzystają z dotychczasowego API.

## Od pomiaru do wyniku

- **Źródło danych.** CSV służy do pracy z eksportem lub danymi demonstracyjnymi. API pobiera historię z bazy aplikacji. Oba korzystają z tych samych funkcji obliczeniowych dla obsługiwanych analiz.
- **Obecność i strategie.** Zacznij od sprawdzenia jakości historii. Luki po upływie TTL ograniczają ocenę, a sprzeczne odczyty mogą zablokować symulację. Następnie porównaj, kiedy strategie wyłączyłyby odbiornik, ile energii zużyłby model i jak często po wyłączeniu szybko wracała obecność. Ocena rekomendacji na późniejszym okresie pokazuje, jak wybrana strategia wypada na danych niewykorzystanych do wyboru.
- **Środowisko.** Użyj progów, by sprawdzić, jak długo temperatura lub wilgotność były poza zadanym zakresem. Pokrycie danych pokazuje, jaką część okresu da się ocenić.
- **Liczniki.** Analiza listwy podaje zużycie całej listwy i największą zarejestrowaną moc. Dla ciepłomierza przepływ i różnica temperatur uzupełniają wynik energii cieplnej. Różnicę temperatur można wyznaczyć tylko tam, gdzie oba odczyty są jednocześnie ważne.

## Pokaz z CSV w PowerShell

Uruchom z katalogu głównego projektu. Polecenia używają danych syntetycznych i zapisują raporty JSON w katalogu tymczasowym.

```powershell
$py = '.\backend\.venv\Scripts\python.exe'
$cli = '.\backend\analytics_csv.py'
& $py $cli simulate docs/examples/synthetic-measurements.csv --device-id SYNTHETIC_ROOM_A --source-kind synthetic --from 2026-01-05T08:00:00Z --to 2026-01-05T08:15:00Z --ttl-seconds 180 --strategy fixed --delay-minutes 2 --on-watts 60 --off-watts 2 --output "$env:TEMP/iot-presence-demo.json"
& $py $cli environment docs/examples/synthetic-environment.csv --device-id room --metric temperature --source-kind synthetic --from 2026-01-01T08:00:00Z --to 2026-01-01T08:10:00Z --ttl-seconds 180 --output "$env:TEMP/iot-temperature-demo.json"
```

**Obecność.** Model 60 W / 2 W z opóźnieniem 2 min daje jedno wyłączenie. W czasie o znanej obecności różnica względem stale włączonego odbiornika wynosi **5,8 Wh**. Stan obecności był nieznany przez 120 s.

**Temperatura.** Odczyty −2°C i 4°C mają po 180 s ważności. Średnia ważona czasem wynosi **1°C**, a pokrycie to 360 z 600 s (60%).

## API

W Swagger UI pod adresem `/docs` na działającym backendzie znajdziesz parametry, schematy żądań i odpowiedzi. Użyj tokenu z obecnego logowania (`POST /api/auth/login`) w nagłówku `Authorization: Bearer <token>`.

| Metoda | Ścieżka | Przeznaczenie |
|---|---|---|
| GET | `/api/analytics/devices` | Lista urządzeń z historią obecności. Listwy i ciepłomierze wybieraj przez `/api/devices`. |
| GET | `/api/analytics/quality` | Jakość historii obecności urządzenia w zadanym okresie. |
| POST | `/api/analytics/compare` | Porównanie strategii dla tego samego okresu i odbiornika. |
| POST | `/api/analytics/recommend` | Wybór strategii i sprawdzenie jej na późniejszym okresie. |
| GET | `/api/analytics/environment` | Temperatura lub wilgotność, średnia ważona, pokrycie i czas poza progami. |
| GET | `/api/analytics/electricity` | Przyrost energii całej listwy, moc szczytowa i opcjonalny koszt. |
| GET | `/api/analytics/heat` | Energia cieplna, moc, przepływ, temperatury i ich różnica. |

## Jak czytać wyniki

- Podawaj daty ISO 8601 ze strefą. Przedział obejmuje początek i wyklucza koniec. Odczyt licznika dokładnie na końcu może zamknąć ostatnią parę.
- TTL określa czas ważności odczytu. Po jego upływie stan jest nieznany. Pokrycie oznacza udział okresu z ważnymi danymi. Średnia ważona obejmuje tylko ten czas.
- `null` oznacza, że wyniku nie można wyznaczyć albo że pominięto opcjonalne obliczenie, np. koszt bez ceny. Zachowaj tę różnicę przy wyświetlaniu zera.
- Energia licznika w kWh to suma przyrostów między odczytami. Para ze spadkiem licznika jest pomijana. Energia symulowana w Wh pochodzi z modelu mocy ON/OFF. Różnica energii w czasie o nieznanej obecności nie stanowi potwierdzonej oszczędności.
- Przy analizie licznika CSV zadeklaruj `--device-type power_strip` lub `--device-type heat_meter`. API sprawdza typ w bazie.

## Testy i ograniczenia

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend
```

Limit rekordów jest stosowany przed wyborem okresu, więc krótki okres przy długiej historii też może zwrócić HTTP 413. Błędne ustawienia `ANALYTICS_*` mogą zatrzymać start backendu. Do serwowania panelu potrzebny jest build pod poprawną ścieżką `FRONTEND_DIST_PATH`. Zakres prób na Windows i obszary niesprawdzone na sprzęcie opisuje [raport audytu](analytics-integration-review.md).
