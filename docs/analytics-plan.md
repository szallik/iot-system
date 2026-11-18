# Analityka użytkowania i symulacja strategii

**Zakres modułu:** wyłącznie analiza historii i symulacja. Maszyna stanów jest
silnikiem symulacji. Moduł nie steruje urządzeniami, nie publikuje MQTT, nie
aktywuje strategii i nie zmienia automatyzacji. Nie planujemy takich funkcji.

Główne źródło historii: [odczyt SQLite tylko do odczytu](analytics-sqlite.md).
Alternatywnie dostępny jest [import eksportów CSV](analytics-csv.md).

## Etap 1 — działający prototyp offline

Moduł `backend/app/analytics/core.py` korzysta wyłącznie z biblioteki standardowej.
Nie importuje FastAPI, modeli SQLAlchemy, konfiguracji bazy ani obsługi MQTT.
`reconstruct` odtwarza historię, `FixedDelayController` implementuje maszynę stanów,
a `simulate` prowadzi ją po osi czasu i oblicza metryki. Dane wejściowe dotyczą
jednego pomieszczenia i jednego logicznego źródła obecności.

Uruchomienie z katalogu głównego repozytorium w PowerShell:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_demo.py
```

Testy (bez dodatkowych zależności):

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend -v
```

### Kontrakt czasowy i aktualność danych

- Daty muszą zawierać strefę; rdzeń normalizuje je do UTC. Okres analizy oraz
  przedziały są lewostronnie domknięte: `[start, end)`. Wymagany jest `start < end`.
- Raport zawiera czas i ścisłą wartość logiczną. Obowiązuje od czasu raportu
  do kolejnego raportu lub do `czas + freshness` (wyłączny koniec).
  `freshness > 0` jest obowiązkowym parametrem, w demonstracji wynosi 2 minuty.
- Powtórny raport tej samej wartości odświeża ważność. Raport dokładnie przy
  wygaśnięciu poprzedniego nie tworzy luki. Raport innego pola telemetrii nie
  odświeża obecności. Cisza nie oznacza pustego pomieszczenia.
- Brak aktualnego raportu oznacza UNKNOWN. Ostatni raport sprzed początku okresu
  może ustalić stan początkowy, jeżeli nadal jest ważny. Brak interpolacji wstecz.
- Raporty są sortowane, identyczne duplikaty scalane; sprzeczne wartości z tym
  samym czasem są odrzucane. Raport na końcu okresu lub późniejszy nie wpływa
  na jego wyniki. Walidacja konfliktów dotyczy całego przekazanego zbioru.
- TTL to założenie analizy, nie potwierdzona cecha urządzenia. Przed analizą
  rzeczywistych danych należy sprawdzić, czy czujnik raportuje okresowo, czy tylko
  zmiany. W drugim przypadku potrzebny jest osobny mechanizm potwierdzania
  aktualności; nie wolno bezwarunkowo przedłużać ostatniej wartości.
  **Ważność 2 minuty jest założeniem demonstracji. Brak nowej wiadomości nie
  zawsze oznacza utratę łączności**, szczególnie przy raportowaniu tylko zmian.
  UNKNOWN oznacza brak aktualnej wiedzy według przyjętego TTL, nie diagnozę awarii.

### Reguła symulacji i metryki

Odbiornik jest początkowo ON. Pierwszy wiarygodny ABSENT rozpoczyna odliczanie;
kolejne raporty ABSENT go nie resetują. PRESENT anuluje odliczanie i włącza
odbiornik. UNKNOWN anuluje oczekujące wyłączenie i zachowuje stan zasilania.
Po odzyskaniu ABSENT wymagane jest pełne nowe opóźnienie. Już wyłączony odbiornik
pozostaje OFF podczas UNKNOWN, do otrzymania PRESENT.

Przy zbiegu terminów najpierw uwzględniamy nową obecność albo utratę aktualności,
potem termin wyłączenia. Powrót dokładnie w terminie wyłączenia zapobiega OFF.
Wygaśnięcie danych w tej samej chwili także zapobiega OFF. Koniec okresu nie
generuje przełączenia. Historia sprzed początku ustala tylko obecność; nie
odtwarzamy wcześniejszego stanu odbiornika ani wcześniejszego odliczania.

Wynik zawiera liczbę i chwile przejść do OFF, sumę czasu OFF przyciętą do okresu,
chwile krótkich powrotów i flagę OFF na końcu. Krótki powrót to pierwsza
zaobserwowana obecność po wyłączeniu, gdy czas od OFF jest **mniejszy lub równy**
`short_return_window` (domyślnie 2 minuty, dopuszczalne zero). Powrót po luce
oznacza czas obserwacji, nie znany rzeczywisty moment wejścia. Brak powrotu przed
końcem okresu jest obserwacją uciętą, nie dowodem braku późniejszego dyskomfortu.

### Deterministyczna demonstracja

2026-01-01, 08:00–09:00 UTC. Raport co minutę poza przerwą 08:31–08:40:
obecność 00–05, krótkie wyjście 05–08, powrót 08–15, długie wyjście 15–28,
powrót 28–30, wyjście od 30, UNKNOWN 32–40, przywrócone ABSENT 40–52,
obecność 52–60 (minuty od 08:00). Luka wypada dokładnie na terminie
wyłączenia strategii 2-minutowej, sprawdzając pierwszeństwo UNKNOWN.

| Opóźnienie | Liczba OFF | Czas OFF [min] | Krótkie powroty | Chwile OFF UTC |
| --- | --- | --- | --- | --- |
| 2 min | 3 | 22 | 1 | 08:07, 08:17, 08:42 |
| 5 min | 2 | 15 | 0 | 08:20, 08:45 |
| 10 min | 2 | 5 | 1 | 08:25, 08:50 |

To porównanie symulowane, nie rekomendacja wdrożenia. Sam czas OFF
nie jest pomiarem oszczędności. Model energii dodano w etapie 2 poniżej.
Nie zakładamy oddzielnego pomiaru mocy
gniazd listwy. Nie zmieniamy istniejących automatyzacji, API ani bazy.

### Ręczna weryfikacja i przegląd reguł

Demonstracja wypisuje wszystkie 51 raportów wejściowych chronologicznie, ich
terminy ważności, odtworzone przedziały oraz wszystkie zmiany stanu kontrolera
z czasem, poprzednim/nowym stanem i przyczyną. ON, WAITING oraz SUSPENDED
oznaczają włączone zasilanie; tylko OFF oznacza wyłączone. Powtórzenie raportu
bez zmiany stanu nie jest przejściem. Inicjalizacja ON i koniec okresu również
nie są przełączeniami. Raport pokazuje przedziały OFF i każdy zaobserwowany
powrót po OFF, z czasem od wyłączenia i kwalifikacją do krótkich powrotów.

| Opóźnienie | Przedziały OFF UTC `[od, do)` | OFF / znana nieobecność | OFF / UNKNOWN | Od OFF do kolejnych powrotów |
| --- | --- | --- | --- | --- |
| 2 min | 08:07–08:08; 08:17–08:28; 08:42–08:52 | 22 min | 0 min | 1 min (krótki), 11 min, 10 min |
| 5 min | 08:20–08:28; 08:45–08:52 | 15 min | 0 min | 8 min, 7 min |
| 10 min | 08:25–08:28; 08:50–08:52 | 5 min | 0 min | 3 min, 2 min (krótki) |

Strategia 10 minut daje jeden krótki powrót, ponieważ wyłącza o **08:50**, a
obecność wraca o **08:52**: różnica 2 minuty spełnia włączny próg `<= 2 min`.
Wcześniejszy powrót o 08:28 następuje 3 minuty po OFF i nie spełnia progu.
Strategia 5 minut wyłącza o 08:20 i 08:45; powroty o 08:28 i 08:52 są po
8 i 7 minutach. Dlatego żaden nie jest krótki. Metryka opisuje odstęp od OFF,
nie całkowitą długość nieobecności; nie musi maleć wraz ze wzrostem opóźnienia.

Raporty ABSENT co minutę podtrzymują ciągłą nieobecność, więc np. odliczanie
10 minut od 08:15 kończy się o 08:25 mimo kolejnych raportów. O 08:32 wygasa
ostatni raport z 08:30. Przy braku nowego raportu UNKNOWN ma pierwszeństwo
przed zaplanowanym na tę chwilę OFF strategii 2 minut: WAITING → SUSPENDED,
zasilanie pozostaje włączone. O 08:40 przywrócenie ABSENT rozpoczyna pełne nowe
odliczanie. Gdyby świeży ABSENT dotarł dokładnie przy wygaśnięciu, nie byłoby
UNKNOWN i termin wyłączenia pozostałby w mocy. PRESENT w tej chwili anuluje OFF.

Jeżeli raport wygasa, kiedy odbiornik jest już OFF, zasilanie pozostaje OFF
(nie ma przejścia kontrolera), ale segment czasu zmienia kategorię na
OFF/UNKNOWN. Przykład objęty testem: ABSENT 00:00, TTL 3 min, opóźnienie 2 min,
PRESENT 00:08: OFF przy znanej nieobecności `[00:02, 00:03)` = 1 min,
OFF przy UNKNOWN `[00:03, 00:08)` = 5 min. Odzyskanie ABSENT podczas OFF
nie tworzy kolejnego wyłączenia; dopiero PRESENT włącza odbiornik.

Wynik `Simulation` udostępnia `transitions`, `off_intervals`, `return_events`,
`off_known_absence` i `off_unknown`. Zachowany `off_duration` jest wyłącznie
łącznym czasem wyłączenia, równym sumie dwóch kategorii. Żadna z nich nie jest
potwierdzoną oszczędnością energii; szczególnie OFF/UNKNOWN nie potwierdza,
że pomieszczenie było puste. Przedziały są przycinane na końcu analizy bez
generowania sztucznego powrotu. W głównym scenariuszu OFF/UNKNOWN wynosi zero
dla wszystkich opóźnień; niezerowy przypadek sprawdzają osobne testy.

## Etap 2 — energia symulowana i wiele scenariuszy

`energy.py` dodaje `ReceiverModel(on_watts, off_watts)` oraz `compare_energy`.
Model opisuje **jeden odbiornik**, nie pomiar poszczególnych gniazd listwy.
Obie moce muszą być skończone i nieujemne; odrzucane są NaN, nieskończoności,
wartości ujemne i wartości nieliczbowe. OFF może pobierać moc czuwania.
Nie wymuszamy OFF <= ON: nietypowy model może dać ujemną różnicę energii.

Moc jest stała w każdym stanie, a energia jest całką po czasie:
`E [Wh] = P [W] * czas [s] / 3600`. Brak modelowania rozruchu, zmienności
obciążenia, opóźnień wykonania komend czy innych odbiorników. Wszystkie wyniki
są **symulowane** (`EnergyComparison.simulated=True`), nie zmierzone.

Odbiornik strategii zaczyna okres w ON; WAITING i SUSPENDED nadal pobierają
`on_watts`. Wariant odniesienia używa tego samego modelu, początku i końca
okresu, pozostając stale ON. Każde opóźnienie porównujemy z tym samym wariantem.
Początkowe ON nie jest liczone jako ponowne włączenie. Koniec analizy nie
generuje przełączenia ani powrotu; OFF może mieć ucięty koniec obserwacji.

`EnergyComparison` zwraca:

- `baseline_wh`, `strategy_wh`, `difference_wh = baseline_wh - strategy_wh`
  i `difference_percent = 100 * difference_wh / baseline_wh`.
  Przy zerowym odniesieniu procent to `None` (w tabeli `n/d`), również gdy
  strategia zużywa energię czuwania i różnica jest ujemna.
- `categories`: rozłączne PRESENT, ABSENT, UNKNOWN z czasem ON/OFF, energią
  odniesienia, strategii i różnicą. Kategorie pokrywają cały okres.
- `known_difference_wh` (PRESENT + ABSENT), `unknown_difference_wh` oraz
  `known_time_share` w skali 0–1. „Znane” oznacza znany stan obecności,
  obejmuje też wiarygodną nieobecność. Nie jest to udział czasu zajęcia pokoju.
- `shutdown_count`, `restart_count`, listy powrotów i szczegółowy wynik symulacji.

Różnica dla UNKNOWN jest wyłącznie wynikiem rachunku modelu: **nie potwierdza
ograniczenia zbędnego zużycia**, bo nie znamy obecności. Nawet różnica dla
znanych stanów nie jest pomiarem rzeczywistych oszczędności. Tabele nie wybierają
automatycznie najlepszej strategii; energia sama nie opisuje komfortu.

### Niepewne powroty

`ReturnEvent.uncertain=True`, jeśli jakakolwiek część danego okresu OFF miała
stan UNKNOWN. Flaga pozostaje ustawiona także po przywróceniu ABSENT, aż do
raportu PRESENT kończącego ten OFF. UNKNOWN przed wyłączeniem nie oznacza
niepewności późniejszego powrotu; kolejny okres OFF zaczyna z nową flagą.

Dla zgodności wcześniejsze `Simulation.short_returns` i `ReturnEvent.is_short`
nadal opisują odstęp od OFF do **raportu** obecności (`<= short_return_window`).
Nowe zestawienie energii rozdziela `certain_returns` i `uncertain_returns`.
Pole `EnergyComparison.short_returns` obejmuje tylko powroty bez UNKNOWN;
`uncertain_short_returns` zawiera osobno niepewne obserwacje spełniające próg.
Tabela pokazuje liczbę krótkich powrotów bez UNKNOWN oraz liczbę wszystkich
niepewnych powrotów (w nawiasie tych z krótkim odstępem obserwowanym).
Nie twierdzimy, że znamy rzeczywisty moment wejścia podczas luki w danych.

### Scenariusze i uruchomienie

Zachowano szczegółową demonstrację etapu 1. Za nią drukowane są cztery tabele
ze `scenarios.py`. Każdy okres to 2026-01-01, 08:00–09:00 UTC, TTL 2 min,
raporty co minutę z wyjątkiem wskazanych luk. Minuty poniżej liczymy od 08:00:

| Scenariusz | Dane wejściowe |
| --- | --- |
| Częste krótkie przerwy | ABSENT [05,08), [15,18), [25,28), [35,38), [45,48), [55,58) |
| Długa nieobecność | ABSENT [10,50) |
| Stała obecność | PRESENT przez cały okres |
| Luki danych | ABSENT [10,35), pominięte raporty [01,05) i [21,30); UNKNOWN [02,05) podczas ON i [22,30) podczas OFF |

Poza wskazaną nieobecnością raporty zawierają PRESENT. Luki nie są traktowane
jako pomiary rzeczywistej obecności. Dla każdego scenariusza porównujemy 2, 5,
10 minut. Domyślny model to ON 60 W, OFF 2 W; odniesienie wynosi 60 Wh.

| Scenariusz | Energia strategii 2 min [Wh] | 5 min [Wh] | 10 min [Wh] |
| --- | --- | --- | --- |
| Częste krótkie przerwy | 54,20 | 60,00 | 60,00 |
| Długa nieobecność | 23,27 | 26,17 | 31,00 |
| Stała obecność | 60,00 | 60,00 | 60,00 |
| Luki danych | 37,77 | 40,67 | 45,50 |

W scenariuszu luk znany stan obejmuje 49/60 okresu (81,67%). Każda strategia
ma 8 minut OFF/UNKNOWN, co daje różnicę modelowaną `58 * 8/60 = 7,7333 Wh`
w kategorii UNKNOWN. Każda ma jeden niepewny powrót o 08:35.

Polecenie domyślne pozostaje takie samo. Parametry można zmienić z PowerShell:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_demo.py --on-watts 60 --off-watts 2
```

Testy zachowują wcześniejsze przypadki i dodają ręczne rachunki: 60 W przez
godzinę = 60 Wh; pół godziny przy 60 W i pół przy 2 W = 31 Wh. Sprawdzają też
sumy kategorii czasu i energii, brak wyłączeń, zerowe odniesienie, ujemną
różnicę przy większej mocy OFF, dokładność sekundową, scenariusze i niepewność
powrotów. Żaden test ani demonstracja nie uruchamia MQTT lub bazy danych.

## Etap 3 — rekomendacja stałego opóźnienia

`recommendation.py` korzysta z niezmienionych reguł symulatora i modelu energii.
`recommend(periods, model, delays, constraints, ...)` przyjmuje konfigurowalne
opóźnienia i zestaw rozłącznych okresów. Każdy okres jest kompletną osią czasu
z PRESENT/ABSENT/UNKNOWN, zaczyna się od ON i nie dziedziczy odliczania z
poprzedniego scenariusza. Przerwy między okresami nie są analizowane. Dla ciągłej
historii należy przekazać jeden okres obejmujący również luki jako UNKNOWN.
Nakładające się okresy oraz puste zestawy są odrzucane. Wszyscy kandydaci są
oceniani na tej samej, raz zmaterializowanej historii.

`Constraints` określa maksymalną liczbę pewnych krótkich powrotów, wyłączeń,
niepewnych powrotów oraz minimalny udział znanego stanu. Liczby muszą być
nieujemnymi liczbami całkowitymi, udział skończoną wartością z [0,1]. Limity
zdarzeń dotyczą **sumy całego ocenianego zestawu okresów**, nie pojedynczego
scenariusza i nie dnia. Raport podaje zakresy UTC i sumę ich długości.
Udział znanych danych jest ważony czasem, a nie średnią procentów scenariuszy.
Limity są włączne: wartość równa maksimum/minimum jest dopuszczalna.

Każdy `Candidate` zawiera opóźnienie, sumę energii strategii w znanych okresach
(PRESENT + ABSENT), osobno energię UNKNOWN, liczbę wyłączeń, pewnych krótkich
powrotów, wszystkich niepewnych powrotów, udział znanych danych i wszystkie
powody odrzucenia. Pełna tabela obejmuje także odrzuconych kandydatów.

Ranking obejmuje tylko dopuszczalne warianty. Minimalizujemy **energię
symulowaną w znanych okresach**. Energia UNKNOWN nie wpływa na ranking ani
na rozstrzyganie remisów. Niepewne powroty nadal mogą wykluczyć wariant zgodnie
z limitem jakości danych. Domyślna tolerancja absolutna energii to `1e-6 Wh`,
konfigurowalna, skończona i nieujemna. Grupę remisu tworzą warianty oddalone
od najmniejszej pozostałej energii o najwyżej tolerancję. Nie łączymy łańcucha
przybliżonych remisów (A blisko B i B blisko C nie musi oznaczać A blisko C).
W grupie preferujemy kolejno mniej pewnych krótkich powrotów, mniej wyłączeń,
a następnie dłuższe opóźnienie. Ranking jest niezależny od kolejności wejścia.

`Recommendation.selected=None` oznacza **brak rekomendacji**. Zwracane są
konkretne przekroczenia dla każdego opóźnienia; ograniczenia nie są luzowane.
Duplikaty opóźnień są usuwane, wszystkie opóźnienia muszą być dodatnie.

### Dobór i niezależna późniejsza ocena

Demonstracja ocenia 1, 2, 3, 5, 7, 10 i 15 minut. Dane są **syntetyczne**:

- Dobór: cztery scenariusze etapu 2 przesunięte na 1–4 stycznia 2026,
  po jednej godzinie 08:00–09:00 UTC. Łącznie **4 godziny**, 229/240 czasu
  ze znanym stanem (95,42%).
- Ocena: inne scenariusze 5–6 stycznia, po godzinie 08:00–09:00 UTC,
  łącznie **2 godziny**. Pierwszy zawiera sześć przerw czterominutowych,
  drugi nieobecność 08:05–08:50 i brak raportów 08:25–08:40
  (UNKNOWN 08:26–08:40). Znany stan: 106/120 czasu (88,33%).

`evaluate_later(selection, periods)` ocenia wyłącznie wcześniej wybrane
opóźnienie, zachowując model, próg krótkiego powrotu i ograniczenia. Wymaga
okresów rozpoczynających się po zakończeniu wszystkich okresów doboru.
Nie uruchamia ponownej selekcji. Przy braku rekomendacji ocena jest niemożliwa;
przy naruszeniu limitów podczas oceny wynik informuje o tym bez zmiany ustawienia.
Te same limity liczbowe stosujemy do całych zestawów różnej długości, podanych
w raporcie; nie przeliczamy ich automatycznie na dzień ani na godzinę.

Dla ON 60 W / OFF 2 W wynik demonstracji jest następujący:

| Profil | Maks. krótkie / OFF / niepewne | Min. znane | Wybrane opóźnienie | Energia znana w doborze |
| --- | --- | --- | --- | --- |
| Bardziej oszczędny | 6 / 10 / 1 | 90% | 1 min | 164,233 Wh |
| Bardziej wymagający pod względem wygody | 0 / 4 / 0 | 95% | 15 min | 204,833 Wh |

W profilu oszczędnym 1 minuta ma najniższą energię znanych okresów i mieści
się w limitach (6 krótkich powrotów, 8 wyłączeń, 1 niepewny powrót).
W wymagającym profilu 1 i 2 minuty przekraczają trzy limity zdarzeń, a 3–10
minut narusza zakaz niepewnych powrotów. Jedynym dopuszczalnym wariantem jest
15 minut: 0 krótkich, 1 wyłączenie, 0 niepewnych powrotów.

Na późniejszym zestawie **oba ustawienia nie spełniają ograniczeń**:
znane dane zajmują tylko 88,33%, poniżej obu minimów. Dla 15 minut występuje
dodatkowo 1 niepewny powrót przy limicie 0. Opóźnienia pozostają zamrożone.
Jest to demonstracja procedury oceny, **nie dowód skuteczności na stanowisku**.
Próg krótkiego powrotu nie opisuje całej wygody; dłuższa nieobecność może
zmniejszać liczbę „krótkich” powrotów bez poprawy rzeczywistego komfortu.

Uruchomienie testów i demonstracji pozostaje bez zmian. Testy obejmują brak
dopuszczalnego wariantu, kolejność remisów i tolerancję, zbyt mało danych,
niepewne powroty, sumowanie limitów i ważenie pokrycia czasem, niezależność
wyboru od danych oceniających oraz brak wpływu poprawy wyłącznie energii UNKNOWN
na ranking. Zachowano wszystkie wcześniejsze testy.

## Etap 4 — strategia harmonogramowa

`strategies.py` definiuje niezmienne `FixedDelay` i `WeeklySchedule`. Obie
korzystają z jednej `DelayController`; `FixedDelayController` pozostaje aliasem
dla zgodności. Symulator i model energii nadal przyjmują samo `timedelta`.
Przy początku odliczania wybierane jest opóźnienie według lokalnego dnia i godziny.
Termin zapamiętujemy w UTC. Granica harmonogramu nie zmienia trwającego terminu.
UNKNOWN anuluje odliczanie, a nowe ABSENT dobiera nowe opóźnienie. PRESENT
oraz UNKNOWN dokładnie w terminie OFF zachowują dotychczasowe pierwszeństwo.

Przykładowy harmonogram, **nie zalecenie**: pn–pt `[08:00,16:00)` w
`Europe/Warsaw` = 10 minut; poza tym = 2 minuty. `ScheduleWindow` przyjmuje dni
0–6 (poniedziałek–niedziela), godziny HH:MM i dodatnie `timedelta`.
Przedziały są lewostronnie domknięte. Nakładanie na wspólnym dniu jest błędem;
przedziały sąsiednie są dozwolone. Godzina 24:00 jest dopuszczalna wyłącznie
jako koniec. Przedział przez północ trzeba rozbić, np. poniedziałek 22:00–24:00
oraz wtorek 00:00–02:00. Domyślne opóźnienie jest obowiązkowe i dodatnie.

Daty raportów, terminy i całkowanie energii pozostają w UTC. Konwersja do
lokalnej strefy służy tylko wyborowi opóźnienia. Wiosenna pominięta godzina
nie tworzy sztucznych chwil; obie jesienne realizacje powtórzonej godziny
pasują do tego samego przedziału lokalnego. Odliczanie zawsze mierzy rzeczywisty
czas w UTC, również przy zmianie czasu. W `backend/requirements.txt` zapisano
`tzdata==2026.6`, potrzebne na Windows bez systemowej bazy IANA. Brak danych
strefy daje czytelny błąd z instrukcją instalacji, bez zastępowania jej stałym
przesunięciem. Instalacja: `backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt`.

Wyniki energii udostępniają nazwę i konfigurację strategii. `Candidate` ma
`strategy_name`, `configuration` i stabilny `identifier`; `delay=None` dla
harmonogramu. `recommend` przyjmuje listę opóźnień lub konfiguracji strategii.
Cała niezmienna konfiguracja przechodzi do `evaluate_later`. Ranking nadal
używa wyłącznie energii znanych okresów. Po liczbie krótkich powrotów i OFF
remis rozstrzyga większe **minimalne skonfigurowane opóźnienie** (uwzględniając
domyślne), następnie leksykograficzny identyfikator. Dla stałych opóźnień
zachowuje to wcześniejsze zachowanie. Tolerancja pozostaje 1e-6 Wh.

Demonstracja zawiera dodatkowe wspólne 4 godziny syntetyczne: piątek 9 stycznia
2026, 15:00–17:00 i 18:00–19:00 lokalnie oraz sobotę 10 stycznia 10:00–11:00.
Pokazuje wybrane terminy, w tym start 15:55 → OFF 16:05 mimo granicy o 16:00.
Dla 60 W / 2 W energia wynosi: stałe 2 min = 146,233 Wh, 5 min = 164,600 Wh,
10 min = 191,667 Wh, harmonogram = 161,700 Wh. Wszystkie dane w tych okresach
są znane. Przy limitach 2 krótkich powrotów, 12 OFF, 90% znanych danych i
1 niepewnego powrotu wygrywa stałe 2 minuty — nie wymuszamy wygranej harmonogramu.
Na innych danych z 16–17 stycznia zamrożone 2 minuty dają 151,067 Wh i nadal
spełniają limity. To wyłącznie demonstracja syntetyczna.

Testy obejmują granice godzin, weekend, UTC latem i zimą, obie zmiany czasu,
przejście odliczania przez granice, wznowienie po UNKNOWN, walidację, brak
danych strefowych oraz zamrożenie całej konfiguracji. Zachowano poprzednie testy.

## Etap 5 — opóźnienia dobierane z historii

To **dobór parametrów z wcześniejszej historii**, a nie model przewidujący
obecność. Nie wymaga zewnętrznego AI ani chmury, nie gwarantuje oszczędności.
`contexts.py` definiuje `ContextPartition` i niezmienną `AdaptiveDelay`.
`adaptive.py` dobiera parametry, a `history_demo.py` generuje dane syntetyczne.
Strategie implementują wspólny kontrakt `Strategy.delay_at`; symulacja nadal
odbywa się wyłącznie w `DelayController`, bez kopiowania logiki maszyny stanów.

### Konteksty, zasób danych i fallback

Domyślne cztery grupy lokalne w Europe/Warsaw:

1. `workday_day`: poniedziałek–piątek `[08:00,16:00)`.
2. `workday_other`: poniedziałek–piątek `[00:00,08:00)` i `[16:00,24:00)`.
3. `weekend_day`: sobota–niedziela `[08:00,16:00)`.
4. `weekend_other`: sobota–niedziela pozostałe godziny.

Nazwy, dni, przedziały i strefa są konfigurowalne. Grupy muszą jednoznacznie
pokrywać całą dobę każdego dnia tygodnia — luki, nakładanie, duplikaty nazw
i niepoprawne godziny są odrzucane. UTC służy do raportów, długości okresów,
odliczania i energii. Kontekst wybieramy przy początku odliczania; jego zmiana
ani północ nie resetują maszyny. UNKNOWN anuluje odliczanie zgodnie z wcześniejszą
regułą. Po przywróceniu ABSENT wybieramy opóźnienie aktualnej grupy.

`DataRequirements` określa minimalną liczbę dni i kompletnych epizodów.
Domyślnie potrzeba **3 dni i 3 epizodów**. Dzień wiarygodny oznacza lokalną
datę z co najmniej **30 minutami znanego stanu w tej grupie**; ten dodatkowy
próg jest konfigurowalny. Sumujemy PRESENT i ABSENT, wykluczamy UNKNOWN.
Liczymy rzeczywisty czas UTC, także podczas zmiany czasu; jedna data nie
staje się dwoma dniami podczas jesiennego powtórzenia godziny.

Kompletny epizod to ciągła nieobecność poprzedzona PRESENT i zakończona PRESENT
w obrębie analizowanej historii. UNKNOWN w środku dyskwalifikuje epizod;
nieobecność rozpoczęta przed okresem albo kończąca się poza nim jest ucięta
i nie jest pełną obserwacją. Sąsiednie fragmenty ABSENT są scalane.
Cały epizod przypisujemy do kontekstu **jego początku**, nawet jeśli przekracza
granicę grupy. Jest to zasób obserwacji czujnika, nie pewność rzeczywistych wejść.

Jeżeli grupa nie spełnia któregokolwiek minimum, jej opóźnienie jest zamrożone
na jawnym `default_delay` (w demo 5 minut, może być spoza listy kandydatów).
Raport zawiera konkretne braki: dni, epizody lub całkowity brak danych.
Fallback nie omija globalnych ograniczeń; konfiguracja może nadal zostać
odrzucona. Przy zupełnie pustym okresie zwracamy brak rekomendacji i opis
braku danych. Dla okresu całkowicie UNKNOWN minimum jakości również może
wykluczyć wszystkie konfiguracje.

### Dobór całej konfiguracji

`train_adaptive` otrzymuje wyłącznie dane treningowe. Dla każdej grupy z
wystarczającymi danymi rozważa `[1,2,5,10,15]` minut, a dla pozostałych tylko
wartość domyślną. Ocenia pełny iloczyn konfiguracji — maksymalnie 625 dla
domyślnych czterech grup. Każda konfiguracja przechodzi przez **całą oś czasu**,
bez osobnych symulacji dla grup ani resetów na ich granicach. Globalne limity
krótkich powrotów, OFF, niepewnych powrotów i pokrycia danych obowiązują dla
całego ocenianego okresu. Domyślny limit rozmiaru przeszukiwania to 10000;
przekroczenie zgłasza błąd zamiast próbkować lub automatycznie zmieniać limity.

Ranking pozostaje wspólny: najmniejsza energia znanych okresów, tolerancja
1e-6 Wh; w grupie remisu mniej pewnych krótkich powrotów, mniej OFF, większe
minimalne opóźnienie konfiguracji, następnie stabilny identyfikator z pełną
konfiguracją. Kolejność kandydatów wejściowych nie wpływa na wynik. Energia
UNKNOWN nie jest premią ani kryterium remisu. Jeżeli nie ma dopuszczalnej
konfiguracji, wynik zawiera brak rekomendacji i przyczyny odrzucenia.

`AdaptiveTraining.configuration()` zwraca dokument JSON: definicje grup,
wybrane opóźnienia, okresy treningowe UTC i ich łączną długość, liczbę i daty
wiarygodnych dni, liczbę kompletnych epizodów, znany czas w grupach, wymagania
zasobu, fallbacki i ich powody, listę kandydatów, liczbę ocenionych konfiguracji,
model odbiornika, ograniczenia, próg krótkiego powrotu i tolerancję rankingu.
Przykładowy zapis znajduje się w `docs/analytics-adaptive-example.json`.
Dokument zawiera tylko informacje treningowe; wynik oceny nie zmienia konfiguracji.

### Wielodniowy eksperyment syntetyczny

Generator z ziarnem `20260105` nie zna strategii ani wyniku rankingu.
Tworzy zmienne przerwy (2–35 minut), nocne nieobecności przez granice dni
i luki raportowania także podczas OFF. Raport nominalnie co minutę, TTL demo
2 minuty. Trening: **5–18 stycznia 2026 lokalnie**, 14 dni (336 h),
UTC `[2026-01-04T23:00,2026-01-18T23:00)`. Ocena: **19–25 stycznia**, 7 dni
(168 h), UTC `[2026-01-18T23:00,2026-01-25T23:00)`.

Każdy z tych dwóch zestawów jest jedną ciągłą osią czasu i zaczyna symulację
w ON. Pierwszy stan w ocenie może wykorzystać świeży raport sprzed granicy,
ale nie odziedzicza stanu symulacji z treningu; to jawny, wspólny warunek początkowy
dla wszystkich strategii. Epizod urwany na granicy treningu nie jest pełny.

| Okres | Maks. pewne krótkie powroty | Maks. OFF | Min. znane | Maks. niepewne powroty |
| --- | --- | --- | --- | --- |
| Trening, 336 h | 20 | 180 | 97% | 6 |
| Ocena, 168 h | 10 | 90 | 97% | 3 |

Są to jawnie podane limity **całego okresu**, nie limity dzienne ani automatyczne
przeliczanie. `evaluate_later` przyjmuje opcjonalne ograniczenia oceny; gdy ich
nie podano, zachowuje poprzednie zachowanie. `evaluate_adaptive` wymaga
jawnych ograniczeń oceny i późniejszej osi czasu. Żadna funkcja oceny nie stroi
parametrów ponownie. Ustalony wcześniej harmonogram również pozostaje bez zmian.

Dla ON 60 W, OFF 2 W: najlepsze stałe opóźnienie na treningu to **5 minut**.
Wybrane opóźnienia adaptacyjne w kolejności grup: **1 / 2 / 2 / 1 min**.
Grupy mają odpowiednio 10/10/4/4 wiarygodnych dni i 39/38/15/14 kompletnych
epizodów; żadna nie potrzebuje wartości domyślnej. Na treningu energie
wynoszą: stała 13167,133 Wh, harmonogram 13090,767 Wh, adaptacyjna 12852,967 Wh;
wszystkie spełniają ograniczenia treningowe.

Ocena zamrożonych konfiguracji na identycznych późniejszych danych:

| Strategia | Energia razem [Wh] | Energia znana [Wh] | Energia UNKNOWN [Wh] | OFF | Pewne krótkie | Niepewne | Ograniczenia |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Stała 5 min | 6504,300 | 6451,500 | 52,800 | 41 | 7 | 1 | spełnione |
| Ustalony harmonogram | 6464,667 | 6411,867 | 52,800 | 41 | 4 | 1 | spełnione |
| Adaptacyjna 1/2/2/1 | 6343,833 | 6291,033 | 52,800 | 55 | 11 | 1 | niespełnione: 11 > 10 krótkich |

Znany stan w ocenie zajmuje 99,25% czasu. Adaptacja daje mniejszą energię
symulowaną, ale przekracza limit wygody; **nie uznajemy jej za zwycięzcę oceny**.
Wynik nie dowodzi skuteczności na stanowisku ani przyszłych oszczędności.
Energia UNKNOWN jest pokazywana osobno i nie stanowi potwierdzenia ograniczenia
zbędnego zużycia. Przy braku rekomendacji na treningu dany wariant nie jest
zastępowany arbitralnie wybraną konfiguracją podczas oceny.

Uruchomienie i opcjonalny zapis artefaktu:

```powershell
.\backend\.venv\Scripts\python.exe .\backend\analytics_demo.py --adaptive-output docs/analytics-adaptive-example.json
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend -v
```

Testy obejmują brak i niedobór danych, ucięte epizody i UNKNOWN, granice grup,
globalne ograniczenia, brak dopuszczalnej konfiguracji, deterministyczność,
niezależność od danych oceniających, niezmienność konfiguracji, sumowanie czasu
podczas zmiany czasu oraz regresję wielodniowego eksperymentu.

## Etap 6 — import measurements CSV i jakość danych

Dodano `backend/analytics_csv.py`, osobne polecenia `quality` i `simulate`
oraz importer zgodny z bieżącym eksportem `/api/export?dataset=measurements`.
Pełna instrukcja, definicje liczników i przykłady są w [analytics-csv.md](analytics-csv.md).
Jakość nie wymaga TTL; symulacja wymaga jawnego TTL, zakresu i modelu mocy.
Konflikty i błędy wybranego strumienia blokują symulację. Import nie uruchamia
bazy ani MQTT i nie stroi konfiguracji adaptacyjnej. Obecność może pochodzić
ze sprzętu, ale energia ON/OFF nadal jest symulowana.

## Etap 7 — SQLite jako główne źródło historii

Dodano `backend/analytics_db.py` z `devices`, `quality` i `simulate`.
Adapter wymaga jawnej ścieżki, używa `mode=ro`, uwzględnia pełną grupę
rekordów najpóźniejszej chwili sprzed zakresu i stosuje `[from,to)` po
normalizacji UTC. Nie importuje konfiguracji/SQLAlchemy backendu ani MQTT.
Wspólne `measurement_history.py` i `history_cli.py` obsługują również zachowany
CSV: walidacja, format jakości, dobór zamrożonej strategii i symulacja mają
jedną implementację. Instrukcja i reguły WAL: [analytics-sqlite.md](analytics-sqlite.md).

## Etap 8 — API historii i symulacji

Dodano uwierzytelnione `/api/analytics/devices`, `/quality`, `/compare` i
`/recommend`. Router korzysta z serwisów i bazy wyłącznie do odczytu; nie
uruchamia CLI. Wspólne `history_service.py` obsługuje symulację i serializację
również dla CSV/SQLite CLI. API przyjmuje konfiguracje jako JSON, a ścieżkę
bazy ustala wyłącznie serwer. Dobór i późniejsza ocena są rozdzielone, energia
UNKNOWN nie uczestniczy w rankingu. Są limity okresu, rekordów i konfiguracji.
Pełny kontrakt i przykłady: [analytics-api.md](analytics-api.md).

## Kolejne etapy — wyłącznie analiza i prezentacja

1. Historia energii zmierzonej: ustalić zakres pomiaru listwy, jednostki,
   restarty liczników i luki. Oddzielić wyniki pomiaru od symulacji ON/OFF;
   nie zakładać pomiaru każdego gniazda.
2. Rozszerzyć ocenę doboru z historii o wiele rozłącznych okresów, stabilność
   parametrów i analizę niepewności na danych o potwierdzonej jakości.
3. Prezentacja raportów w przyszłym frontendzie: historia, jakość, porównania
   symulacji i wyniki późniejszej oceny. Bez aktywowania strategii, komend
   urządzeń ani zmian automatyzacji.
