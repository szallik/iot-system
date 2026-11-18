# Analizy czujników z CSV

Uruchom w PowerShell z katalogu głównego projektu. Każde polecenie zapisuje raport JSON w katalogu tymczasowym. Pliki przykładów mają nagłówek `id,device_id,metric,value,unit,ts` i znaczniki czasu UTC. Opcja `--source-kind` przyjmuje `synthetic`, `real` lub `unknown` (domyślnie dla tych analiz). Pole `device_type_source=user_declared` oznacza, że typ licznika podał użytkownik.

```powershell
$python = '.\backend\.venv\Scripts\python.exe'
$cli = '.\backend\analytics_csv.py'
$period = @('--from','2026-01-01T08:00:00Z','--to','2026-01-01T08:10:00Z','--source-kind','synthetic')
& $python $cli environment docs/examples/synthetic-environment.csv @period --device-id room --metric temperature --ttl-seconds 180 --lower-threshold 0 --output "$env:TEMP/environment.json"
& $python $cli electricity docs/examples/synthetic-electricity.csv @period --device-id strip --device-type power_strip --price-per-kwh 0.8 --output "$env:TEMP/electricity.json"
& $python $cli heat docs/examples/synthetic-heat.csv @period --device-id meter --device-type heat_meter --ttl-seconds 180 --output "$env:TEMP/heat.json"
& $python $cli environment docs/examples/synthetic-invalid.csv @period --device-id room --metric humidity --ttl-seconds 180 --output "$env:TEMP/invalid-humidity.json"
& $python $cli electricity docs/examples/synthetic-invalid.csv @period --device-id strip --device-type power_strip --output "$env:TEMP/invalid-energy.json"
& $python $cli heat docs/examples/synthetic-invalid.csv @period --device-id meter --device-type heat_meter --ttl-seconds 180 --output "$env:TEMP/invalid-flow.json"
```

| Przykład | Ręcznie obliczony wynik dla 600 s |
|---|---|
| Temperatura | Odczyty −2°C i 4°C są ważne po 180 s. Średnia wynosi (−2 × 180 + 4 × 180) / 360 = 1°C. Poniżej progu jest 180 s, w zakresie 180 s, bez pokrycia 240 s. |
| Prąd | Licznik daje (12 − 10) + (4 − 1) = 5 kWh. Spadek 12 → 1 pomija 180 s. Koszt wynosi 5 × 0,8 = 4. Maksimum mocy to 100 W. Na końcu okresu odczyt 4 kWh zamyka parę, a 999 W wypada poza okres. |
| Ciepło | Przyrost wynosi 6 − 5 = 1 kWh. Przez 180 s przepływ wynosi 2 l/min, moc 500 W, a różnica temperatur −1 − 2 = −3°C. Pozostałe 420 s ma nieznaną różnicę temperatur. |
| Błędne dane | Trzy ostatnie polecenia zwracają kod 2 i zapisują `status=blocked` z kodem `invalid_value`. Wilgotność ma dwa błędne odczyty (101% i −1%), energia i przepływ po jednym. |
