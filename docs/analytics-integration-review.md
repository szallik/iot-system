# Audyt integracji analityki · `ecb4007` względem `e1a17c7`

**Tak. Istniejący frontend działa w sprawdzonym zakresie i nie wymaga zmian, aby włączyć analitykę.** W Edge przeszedł test logowania i otwarcia 11 widoków z rzeczywistym backendem. Pokazanie nowych analiz w panelu wymaga osobnej integracji UI.

## Co sprawdzono

| Dowód | Zakres i wynik |
|---|---|
| Test stron | Edge headless, produkcyjny build, lokalny Uvicorn, świeża SQLite. Logowanie PIN-em przez formularz. Otworzyły się pulpit, urządzenia, szczegóły listwy, sterowanie, ciepłomierz, dostęp, użytkownicy, automatyzacje, historia, statystyki i raporty. Każdy widok wyświetlił własny nagłówek. Podczas próby nie zaobserwowano wyjątków JS ani odpowiedzi aplikacji HTTP ≥400. |
| Test sesji i startu | Produkcyjny `app.main:app` wykonał tworzenie tabel i inicjalizację danych. Na czas próby zastąpiono tylko start/stop MQTT. Token uzyskany przez UI dał HTTP 200 dla `GET /api/analytics/devices`. |
| Test kontraktu | Porównano wygenerowane OpenAPI obu rewizji. Wszystkie 15 wcześniejszych ścieżek wraz z operacjami oraz 18 wcześniejszych schematów zachowało identyczne definicje. Po integracji są 22 ścieżki i 54 schematy. |
| Testy automatyczne | Windows, `backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -t backend` — 169/169 OK. Obejmują m.in. uprawnienia, limity, błędy jakości, odczyt SQLite, okresy, TTL, reset licznika i zgodność CSV z API. |
| Build i zależności | W tymczasowej kopii przeszły `npm ci`, `tsc --noEmit` i produkcyjny build Vite. Backendowy `pip check` przeszedł. |
| Ocena z kodu | Panel nadal wywołuje dotychczasowe API. Nowy prefiks ma własną zależność autoryzacji. Login zachował pola, weryfikację hasła/PIN-u i format JWT. Rejestracja routera poprzedza montowanie statycznego UI. |
| Niesprawdzone | Wdrożenie na Raspberry Pi, broker i urządzenia fizyczne, historia produkcyjna i obciążenie. Próba stron obejmowała renderowanie i początkowe odczyty. Pełne scenariusze edycji, eksportu i sterowania pozostają do sprawdzenia. |
