# HA Sentinel — plan realizacji

Źródło: `HA_Sentinel_Specyfikacja_Techniczna_PL.pdf` v1.1 (2026-10-17). Numery B.., T.., AC.. odnoszą się do specyfikacji.

## Zasady implementacyjne

- Kod domenowy (`core/`, `repositories/`, `modules/*/rules`) **nie importuje `homeassistant`** — testy jednostkowe działają bez HA na kontrolowanym zegarze (rozdz. 23.1).
- Wszystkie importy HA są w `__init__.py`, `config_flow.py`, `adapters/`, `websocket_api.py`, `sensor.py` (warstwa compatibility, rozdz. 2.1).
- SQLite obsługuje jeden dedykowany wątek-worker; pętla HA nigdy nie wykonuje blokującego I/O (rozdz. 2.2, 7.1).
- Każdy handler WS wymaga admina (ADR 05, T08).

## Etapy

| Etap | Zakres | Status |
|---|---|---|
| E0 | Kwalifikacja HA (B01–B03): lifecycle, panel, WS admin, reload automatyzacji | **zrobione** na żywym HA 2026.10.2 |
| **E1** | Scaffold i CI (B04), DTO/clock/enums (B05), SQLite + migracja v1 (B06), kolejki + writer + coverage (B07), inventory (B08) | **zrobione (bez metryk i purge)** |
| E2 | Config flow + capabilities (B09), Doctor DOC001–DOC005 (B10), incidents/outbox/ACK (B11), API + policy + rate limit (B16), panel overview (B17) | B09, B10, B16, B17 zrobione; B11 bez wysyłki powiadomień z outbox (persistent notification opt-in) |
| E3 | AST YAML (B12), graf + impact BFS (B13), snapshot/canonicalizer/diff (B14), lint AUT001–010 (B15), panel (B18) | **zrobione** (graf w pamięci, bez wersjonowania — to R1) |
| E4 | RCA (B19), provider M + journal (B20), sondy N1 (B21), replay (B22), eksport (B23), upgrade (B24) | **zrobione** (B19–B24, retencja/purge REQ10) |
| E5 | Load/soak (B25), E2E/a11y (B26), pilot (B27) | B25 (koszt przetwarzania), B26 zrobione; soak 24/72 h, CPU na sprzęcie referencyjnym i B27 (pilot) wymagają prawdziwych instalacji |

## Krytyczna ścieżka MVP

B01 → B04 → B05 → B06 → B07 → B08 → B10 → B11 → B16 → B17

## Pierwszy przyrost (ta iteracja)

1. Scaffold: `manifest.json`, `hacs.json`, `pyproject.toml`, tłumaczenia PL/EN.
2. `core/models.py` — Observation, Finding, Incident, Job (UUID, mikrosekundy UTC, walidacja).
3. `core/clock.py` — `Clock` z implementacją systemową i testową.
4. `repositories/db.py` + `migrations/v1.sql` — WAL, FK, `schema_migrations` z checksum, worker-thread.
5. `core/ingestion.py` — dwie kolejki bounded (2000 kontrolna / 8000 telemetria), drop counter, gap, batch writer (500 / 1 s).
6. `core/inventory.py` — EntityUid z registry ID, aliasy, tombstones.
7. `modules/doctor/` — DOC001 (120 s), DOC002 (10 min), DOC003 (flapping z coverage), DOC004 (bateria z histerezą), health score z Decimal half-up (AC DOC 05).
8. HA glue: single-instance config flow, `async_setup_entry`/`unload`, adapter `state_changed`, WS `ha_sentinel/capabilities` i `ha_sentinel/overview` z `require_admin`, sensory diagnostyczne.
9. Testy: T01-lite, T03, T04, T05, T07, T08, AC DOC 05.

## Otwarte decyzje (E0)

- Najniższy wspierany patch HA 2026.10.x — po testach B01.
- Adapter zmian rejestrów: event `entity_registry_updated` vs fallback snapshot co 15 min.
- Rejestracja panelu (`panel_custom`) — test na stable (S10).

## Decyzje podjęte w implementacji

- Kod, komentarze i docstringi wyłącznie po angielsku; dokumentacja projektowa po polsku.
- Python 3.14 (wymóg HA 2026.10.x).
- Klucze options: kropki ze specyfikacji zamienione na `_` (np. `doctor_unavailable_s`) — wymóg kluczy tłumaczeń HA.
- Odmowa dla non-admina: kod `forbidden` (spec 15.2) z własnego dekoratora `_require_admin`, sprawdzanego przed logiką handlera; nieaktywny użytkownik też jest odrzucany.
- Panel: web component TS bez zależności runtime, bundel esbuild w `frontend/ha-sentinel.js`, cache-busting przez `?v=<sha256>` zamiast nazwy z hashem (stała ścieżka statyczna rejestrowana raz).
- Subskrypcja (`ha_sentinel/subscribe`) w MVP wysyła tylko numery rewizji z `resync_required` — klient pobiera widok ponownie; delty to R1.
- Widoki Historia konfiguracji / Autopilot / Mapa / Sieć pokazują uczciwie „niedostępne w tej wersji” z capability i powodem — bez pozornych przycisków (spec 25 DoD).
- Migracja v2: kolumny ACK/suppress w `incidents` (test v1→v2 z kopią zapasową).
- DOC003 debounce: wejście w `unavailable` w ciągu 10 s od poprzedniego liczonego wejścia jest scalane.
- Health score: pokrycie C liczone ze wspólnego źródła `ha.states`; przez pierwsze 15 min po starcie lub luce H = unknown (brak pełnego okna) — overview zwraca `coverage_complete_at`, a panel pokazuje odliczanie.

## Weryfikacja na żywym HA (2026-10-17)

HA 2026.10.2 uruchomiony lokalnie: config flow, blokada drugiej instancji, panel „Sentinel” w lewym pasku (`require_admin`), bundel 200 `text/javascript`, wszystkie komendy WS OK, zero „Detected blocking call”, restart HA → ponowne otwarcie DB i luka pokrycia. Uwaga środowiskowa: na macOS + Python 3.14 `pymicro-vad`/`pyspeex-noise` (asystent głosowy HA) nie mają wheeli — bez związku z Sentinel.

## E3 — decyzje i odstępstwa

- Parser: `yaml.compose` (bez konstrukcji obiektów) + własny walker; dozwolone wyłącznie tagi rdzenia YAML i tagi HA (`!secret`, `!include*`, `!input`, `!env_var`). Klucze scalania `<<` są odrzucane jako nieznany tag.
- Import z jawnymi hasłami/tokenami jest domyślnie odrzucany (`validation_failed` + `details.paths`); z `redact_secrets=true` zapisywane jest tylko zredagowane drzewo, bez oryginału, `restorable=false`.
- Wszystkie snapshoty MVP mają `restorable=false` — zapis plików M (B20) to R1. Panel nie pokazuje przycisku „Przywróć”, tylko pobranie YAML i instrukcję ręcznego przywrócenia (AC HK 05).
- `autopilot/analyze` zwraca wynik synchronicznie (executor, timeout 5 s, limit 1000 automatyzacji) zamiast `job_id` — do zmiany na job przy replay (R1).
- Graf: identyfikatory węzłów spoza Inventory to `device:<id>`, `area:<id>`, `config_entry:<id>`, `entity:<registry id>` (nie UUID). Graf trzymany w pamięci, przebudowa z debounce 2 s po zmianach rejestrów i importach; wersjonowanie w DB (`graph_revisions`) to R1.
- Kompletność analizy: węzeł z szablonem (trigger/warunek/cel akcji) liczy się jako zrozumiany tylko częściowo.
- Głębokie linki panelu: podścieżki HA (`/ha-sentinel/history`, `/autopilot`, `/map` …), bo router HA usuwa fragment `#`.
- Hash treści importu liczony w przeglądarce czystym SHA-256 w TS, gdy `crypto.subtle` jest niedostępne (HA po HTTP w LAN).
- Migracje: v2 (ACK/suppress), v3 (metadane snapshotów); na żywym HA przetestowana migracja v2→v3 z kopią `sentinel.v2.bak.db`.

## E4 / B19 — korelacja i RCA

- Incydenty wyprowadza korelator z wszystkich aktywnych findings (po każdym commicie findings i co 30 s); `upsert_finding` zapisuje już tylko findings.
- Grupowanie: DOC001/DOC002 z początkiem w oknie 120 s i wspólnym przodkiem typu device / integration_entry / network_host / service_endpoint (relacje provided_by, managed_by, depends_on, uses_endpoint; confidence ≥ 0,5; maks. 3 krawędzie). Korzeń grupy = najbliższy wspólny przodek (suma skoków, potem uid).
- Incydenty grupowe zachowują członków do pełnego powrotu; pojedyncze są ponownie grupowane i scalane (`resolved` + `merged_into`), nigdy „rozwiązywane ciszą”.
- Cechy: T liczone względem startu kandydata (dowód) lub najwcześniejszego skutku; E=1 dla wpisu integracji w stanie błędu/ponawiania lub czujnika łączności „off”, 0,5 dla niedostępnej encji statusu; K tylko przy ciągłym pokryciu. Hipoteza `change_related` (zmiana konfiguracji na ścieżce w 30 min) — jeszcze nie, bo MVP nie zapisuje do HA.
- Ocena admina (`incidents/confirm_cause`) zapisywana osobno i zachowywana przy przeliczeniach rankingu.
- Zadania commitu findings i okresowej korelacji są śledzone (nie background), więc HA czeka na nie przy wyładowaniu.

## E4 / B23 — eksport, redakcja, usuwanie, retencja

- `export/create` → job (maks. 2 ciężkie naraz), ZIP z `manifest.json` (wersje, zakresy, hashe plików), limit 20 MiB, TTL 24 h; pobieranie `GET /api/ha_sentinel/exports/{id}` przez sesję HA (`hass.fetchWithAuth`), admin + właściciel, `Cache-Control: no-store`, bez ścieżek serwera w odpowiedziach.
- Redakcja: pseudonimy HMAC-SHA256 z losową solą per eksport (pseudonimizacja, nie anonimizacja). YAML tylko w eksporcie niezredagowanym; jawne sekrety i tak nie istnieją, bo import je odrzuca albo redaguje (T19).
- Usuwanie: zakresy observations / incidents / config_history przez SQL w workerze; „all” = wyładowanie wpisu, usunięcie `sentinel*.db*`, `blobs/`, `exports/`, ponowny start. Zadanie „all” jest globalne (nie zadaniem wpisu), bo HA czeka na zadania wpisu przy wyładowaniu.
- Purge co godzinę partiami po 5000: obserwacje (retencja), zamknięte incydenty i findings (aktywne nigdy), snapshoty > 30 dni poza najnowszym, joby > 24 h, audyt > 180 dni, osierocone bloby i stare eksporty, checkpoint WAL bez VACUUM. Budżet rozmiaru (soft/hard) i DOC010 — jeszcze nie.
- Po restarcie joby `queued`/`running` przechodzą w `interrupted` (bez ślepego ponawiania).
- Testy: heartbeat WS serwera HA zamyka bezczynnego klienta testowego przy masowym odpalaniu timerów — klienta WS tworzyć po przesunięciu czasu.

## E4 / B20 — pliki zarządzane i bezpieczne przywracanie

- Zapis domyślnie wyłączony (opcja `managed_write_enabled`); katalog z allowlisty `managed_dir` (domyślnie `ha_sentinel_managed`); include w `configuration.yaml` dodaje admin ręcznie — Sentinel nigdy go nie edytuje.
- Rejestracja wymaga: ścieżki względnej bez `..`, `.yaml/.yml`, braku symlinków w całej ścieżce (lstat każdego segmentu), realpath w allowliście, zwykłego pliku, właściciela = proces HA, braku zapisu dla grupy/innych, ≤ 1 MiB, braku jawnych sekretów, jawnego potwierdzenia wyłącznej własności.
- Preview → plan (`base_hash` bieżącego pliku, `target_hash`, diff, lint, wpływ, `expected_ids`, `plan_hash`, TTL 10 min, `policy_revision` = hash opcji). Apply tylko przez tego samego, nadal aktywnego admina, dla planu `previewed`, przed wygaśnięciem i bez zmiany polityki.
- Apply: snapshot „before” + operacja `prepared` + audyt w jednej transakcji (bez tego brak zapisu — T13) → temp + fsync → ponowne sprawdzenie hasha tuż przed `os.replace` → fsync katalogu → `file_written` → `automation.reload` → weryfikacja widoczności id → `applied`. Błąd reloadu/weryfikacji → rollback tylko gdy plik ma nadal `after_hash`; obca zmiana → `manual_recovery_required` bez nadpisania.
- Recovery przy starcie: plany `applying` rozstrzygane z fazy dziennika i rzeczywistego hasha pliku (`applied` / `manual_reload_required` / `failed` / `manual_recovery_required`), nigdy ponowny apply; usuwane osierocone pliki tymczasowe.
- Fault injection (T14) w 9 punktach: plik zawsze w całości stary albo nowy.
- Znalezione testami: (1) wyjątek dziennika po udanym reloadzie wywołałby rollback — `try` zawężony do samego reloadu; (2) lambda zwracająca kursor `PRAGMA wal_checkpoint` zostawiała aktywną instrukcję i psuła każdy kolejny COMMIT — naprawione, a `DatabaseWorker.run` odrzuca zwracanie kursorów.
- Weryfikacja na żywym HA 2026.10.2: rejestracja, edycja z zewnątrz, sync, podgląd, przywrócenie z prawdziwym `automation.reload`.

## E4 / B21 — Network Discovery N0/N1

- N0 domyślnie: inwentarz urządzeń HA z tożsamością sieciową (MAC skrócony, IP nigdy jako tożsamość), zero sond (AC NET 04 — test sprawdza licznik sond po 10 min).
- N1 opt-in (`network_active_probes`): wyłącznie cele zatwierdzone przez admina (tabela `network_targets`, migracja v4, audyt). TCP, HTTP, HTTPS (TLS weryfikowany, brak opcji pominięcia).
- Walidacja: składnia celu (bez schematów, użytkownika, ścieżki w hoście; ścieżka HTTP bez CR/LF), adresy po DNS: blokada metadanych chmury, link-local, multicast, unspecified, broadcast, reserved, loopback bez zgody, spoza allowlisty (domyślnie RFC1918 + ULA). Literały IP sprawdzane już przy zatwierdzaniu.
- Anti-rebinding: każdy adres z odpowiedzi DNS musi przejść, połączenie idzie na zwalidowany IP. HTTP: bez przekierowań, ≤ 64 KiB, 3 s.
- Limity wspólne dla skanu cyklicznego i ręcznego: 30 połączeń/min, 4 równolegle, 1 próba/cel/min; jitter ±10%.
- Stany: odmowa TCP = host osiągalny, usługa zamknięta (nie awaria sieci); 3 timeouty → `unreachable` + finding NET001; 2 sukcesy zamykają.
- RCA: endpoint może być ręcznie powiązany z urządzeniem (`device → endpoint`, `depends_on`, provenance manual); nieosiągalny endpoint = dowód bezpośredni E=1.
- Collector N2, skan CIDR i adaptery routerów — R2 (osobny pakiet).
- Znalezione na żywym HA: własny walidator w schemacie formularza opcji psuł serializację (HTTP 500) — walidacja CIDR przeniesiona do kroku flow; dodany test flow opcji przez HTTP API.

## E4 / B22 — ograniczony replay

- Interpreter czysty, logika trójwartościowa (true / false / unknown). Obsługiwane: triggery `state` (from/to) i `numeric_state` (przejście progu), warunki `state` / `numeric_state` / and / or / not, `if`, `choose`, akcje-warunki. `delay`, `wait_*`, `repeat`, `parallel`, `for:`, atrybuty i szablony → unsupported z JSON Pointer; plan akcji zatrzymuje się w tym miejscu.
- Historia z Recordera (`get_significant_states`, adapter klasy A: brak recordera → `recorder_unavailable`, zmiana sygnatury → `history_contract`); limity 50 encji, okno ≤ 24 h, ≤ 10 000 zdarzeń, timeout 5 s.
- Raport: okno, liczba zdarzeń, encje bez stanu początkowego, obsługiwane / wszystkie triggery, wersja interpretera, lista nieodtworzonych fragmentów. Planowane akcje nie zmieniają kolejnych stanów (brak modelu urządzeń).
- T10: test z prawdziwym Recorderem i szpiegiem `call_service` — zero wywołań usług.

## E4 / B24 — upgrade, downgrade, uszkodzone dane

- Nieudana migracja: transakcja wycofana, plik i kopia `sentinel.vN.bak.db` nienaruszone, Sentinel działa na pustej bazie w pamięci (Doctor dalej wykrywa awarie, nic nie zapisuje), Repairs `migration_failed` (T16).
- Uszkodzony plik: zostaje bez zmian do diagnostyki, ten sam tryb ograniczony, Repairs `database_corrupted`. Nowszy schemat: tylko odczyt, Repairs `unsupported_schema` (T17). Brak miejsca: tylko odczyt, Repairs `insufficient_space_for_migration`.
- Repairs wyłącznie dla domeny Sentinel; zgłoszenia znikają, gdy problem ustąpi.
- Diagnostyka HA (`diagnostics.py`) z allowlisty pól: bez encji, nazw, adresów i ścieżek.
- `docs/RUNBOOK.md`: instalacja, opcje, plik zarządzany, upgrade, downgrade krok po kroku, uszkodzona baza, HA nie startuje / frontend nie działa, presja obciążenia i miejsca, usuwanie danych i deinstalacja. `docs/COMPATIBILITY.md`: macierz zgodności i dowody kwalifikacji.

## Po E4 — luki R1 domknięte

- Powiadomienia (14.3): opt-in, tylko lokalne powiadomienia trwałe HA, outbox w transakcji incydentu (migracja v5), stały identyfikator na incydent, ponowienia 5/30/120 s i co 10 min do 24 h, cooldown 30 min dla nawrotów (eskalacja do critical przełamuje), odwołanie po rozwiązaniu.
- Okna serwisowe (8.2, migracja v6): encja / urządzenie / integracja, auto-wygaszanie; Doctor odkłada dojrzewanie awarii do końca okna, health score wyłącza jednostki i liczy je osobno.
- DOC009 (aktualizacja dostępna, kara 0, nigdy auto-upgrade) i DOC010 (presja magazynu: 80% / 95% budżetu, histereza do 75%, próbkowanie co 5 min).
- Degradacja (21.2): poziom 1 (kolejka > 80% przez 60 s) wstrzymuje replay; poziom 2 (kolejka > 95% lub magazyn ≥ 95%) blokuje skany, eksporty i importy; poziom 3 przy braku trwałości; powrót po 5 min spokoju poniżej 50%.
- Znaleziony błąd: Sentinel obserwował własne encje (np. `health_score` = unknown w rozgrzewce → DOC002). Encje platformy `ha_sentinel` są wykluczone z obserwacji i grafu.

## E5 / B26 — dostępność

- `frontend-src/a11y/`: harness z atrapami danych dla każdego widoku + `run.mjs` (Chrome przez CDP na wbudowanym WebSocket Node, axe-core 4.10.3, WCAG 2.1 A/AA), 9 widoków × jasny / ciemny / 360 px. Kod wyjścia 1 przy naruszeniu; w CI razem z testem kontrolnym (wstrzyknięte naruszenia muszą zostać wykryte).
- Pierwszy przebieg „bez naruszeń” był fałszywy (axe nie wchodził do shadow DOM przy kontekście `ShadowRoot`) — wykryte testem kontrolnym, naprawione (audyt całego dokumentu).
- Realne naruszenia kontrastu naprawione: plakietki z kolorowym tekstem (np. ostrzeżenie 1,96:1) → kolor tylko w kropce/ramce; akcent i tło przycisków liczone `color-mix()` z kolorem tekstu motywu (AA w jasnym i ciemnym). Tokeny harnessu odczytane z żywego HA 2026.10.2 (`--primary-color: #009ac7`).
- Metryki (20.1): koszt callbacku, lag ingestion, commit writera, czas reguł, korelacji i API (p50/p95/p99/max, rezerwuar 2048), maks. głębokość kolejki; w diagnostyce Sentinel i HA.

## E5 / B25 — obciążenie

- `tests/perf/test_load_standard.py` (marker `perf`, poza zwykłą serią): profil standard, budżety ze spec 21.1 jako asercje; raport w `docs/perf/REPORT.md`.
- Krytyczny błąd znaleziony testem: snapshot startowy > 2000 stanów przepełniał kolejkę kontrolną → resync → znów przepełnienie (nieskończona pętla przy każdym starcie domu w profilu standard). Naprawione osobną ścieżką „baseline” poza kolejkami na żywo.
- Pułapka pomiarowa: `freezer` (freezegun) zamraża też `perf_counter` — test wydajności nie używa `freezer`, a writer jest opróżniany jawnie (`async_flush`).
- Nadzór modułów (4.3): writer ponawia po 5/15/60 s z jitterem, po 5 awariach w 10 min wstrzymanie do decyzji admina (`modules/resume`).
- Łańcuch dostaw (16.3): workflow wydania (zip dla HACS, SHA-256, SBOM CycloneDX z `npm sbom`, kontrola zgodności wersji z tagiem i bundla ze źródłami), gitleaks i `npm audit` w CI, pokrycie testami w CI.

## Kryteria wydania — pokrycie (spec 23.3)

Pokrycie gałęzi (2026-10-17, 306 testów): całość 91%; polityka `websocket_api` 94%; dziennik `apply` 92%, `managed` 97%, `change_services` 90%; parser `yaml_ast` 97%, `autopilot/model` 95%; moduły domenowe ≥ 80% (`replay` 93%). CI wymaga ≥ 90% całości. Dodane testy ścieżek bezpieczeństwa: cofnięcie uprawnień admina przed skutkiem ubocznym, unieważnienie planów po zmianie polityki, `internal_error` bez śladu stosu, `not_ready` dla każdej komendy bez runtime, kursor związany z użytkownikiem i filtrem, recovery przy starcie po awarii po zapisie (AC HK 03).

## Luki względem specyfikacji (przegląd PDF v1.1, 2026-10-17)

Pełny przegląd rozdziałów 1–29 względem kodu. Realizacja po kolei, każdy pakiet = kod + testy + tłumaczenia PL/EN + commit.

### MVP 0.1 — braki

| # | Pakiet | Rozdział | Status |
|---|---|---|---|
| G1 | Kontrakt heartbeat dla DOC005: deklaracja encji i okresu (API + UI), próg ręczny, T06 | 8.1, 8.4 | **zrobione** |
| G2a | Komplet opcji 27.1: działające przełączniki modułów, `ingestion.allowed_domains`, `retention.incidents_days`, sekcje formularza | 27.1 | **zrobione** |
| G2b | Źródła liczbowe: `ingestion.numeric_sampling` z wyborem encji i celu, ręczne mapowanie baterii (wspólna podstawa dla DOC008) | 5.1, 27.1 | **zrobione** |
| G3 | Kreator szkiców Autopilota: state / numeric_state trigger, warunek state, `light.turn_on/off`, mode, cooldown → YAML przez ten sam parser i lint, eksport | 11.1, AC AUT 04 | **zrobione** |
| G4 | N0: model hosta (`NetworkHost`, adresy, identity_keys) i ręczne powiązanie host ↔ urządzenie HA, sugestie z hostname/IP tylko jako sugestie | 13.1, 13.2, AC NET 01 | **zrobione** |

### R1 1.0 — braki

| # | Pakiet | Rozdział | Status |
|---|---|---|---|
| G5 | Nadpisania reguł per obiekt (`rule_id`, `subject_uid`, `enabled`, `params`, `scope.kind`), override `unavailable_s`, wagi jednostek 1..5 w health score | 8.3, 27.1, 27.2 | **zrobione** |
| G6 | Historia konfiguracji typu S: snapshot ustawień i nadpisań Sentinel po każdej zmianie, diff, eksport | 10.1, 27.1 | do zrobienia |
| G7 | DOC006 missing reference + nieaktualny alias w imporcie (wyklucz tombstone w oknie migracji i niepełny import) | 8.1, 22.1 | do zrobienia |
| G8 | DOC007 missed expectation: kalendarz i tolerancja użytkownika, brak dowodu = unknown | 2.3, 8.1 | do zrobienia |
| G9 | DOC008 weak signal: tylko gdy adapter/użytkownik poda skalę i próg; brak porównań między technologiami | 8.1 | **zrobione** (z G2b) |
| G10 | Graf wersjonowany: `graph_revisions`, zapytanie o starą rewizję (AC MAP 05), pełny rebuild co 24 h, `continuation_cursor` | 6.3, 7.2, 12.2 | do zrobienia |
| G11 | Korelacja: `recurrence_of` dla nawrotu po resolved, hipoteza `change_related` (snapshot zasobu na ścieżce w ostatnich 30 min) | 9.1, 9.2 | do zrobienia |
| G12 | Uzgadnianie rejestrów co 15 min (fallback i świeżość w UI), `suggested_same_as` przy delete/recreate urządzenia | 5.1, 6.1 | do zrobienia |
| G13 | Backup HA: stan encji backup i link; `backup.create` jako jawna operacja admina (przyjęcie ≠ ukończenie) | 10.5 | do zrobienia |
| G14 | Budżety klas danych: audyt (limit blokuje mutacje), 50 snapshotów na zasób, agregaty minutowe/godzinowe telemetrii | 7.3 | do zrobienia |

### Wymaga prawdziwych instalacji (poza kodem)

- B27 pilot: 2 tygodnie, 5 instalacji, ręczna ocena fałszywych alarmów, raport precision ≥ 90% (1.2, 23.3).
- T21: soak 24 h standard i 72 h; CPU/RAM na sprzęcie referencyjnym 4× ARM64 / 4 GB / SSD (21.1).
- Test utraty zasilania na filesystemie referencyjnym (23.2).
- Prawdziwy adres repo i `codeowners` w `manifest.json` (18.1).

### R2 — świadomie poza zakresem

AI (17.2, T22), collector N2 i jego API (13.4, 15.4), adaptery routerów, skan CIDR, analiza logów.

### G1 — decyzje

- Kontrakt heartbeat = włączone nadpisanie DOC005 dla encji (`period_s`, opcjonalny `threshold_s`); tabela `rule_overrides` (migracja v7), audyt, rosnący `policy_revision`. Nadpisanie zostaje przy UID po usunięciu encji (tombstone).
- Raport = `state_changed` albo `state_reported` (ta sama wartość) w stanie dostępnym; śledzone tylko encje z kontraktem (`async_track_state_report_event`), zapis co najwyżej raz na 5 s. Niedostępność nie jest raportem — wcześniej przejście do `unavailable` błędnie odnawiało heartbeat.
- Nadpisania: DOC001 `unavailable_s`, DOC002 `unknown_s`, DOC003 `flap_threshold`, DOC004 `low_pct`/`clear_pct`, `enabled` dla DOC001–005 i DOC009. Wyłączenie reguły zamyka aktywny finding z powodem `rule_disabled`; trwające liczniki czasu zachowują stary próg.
- Środowisko dev: lokalne HA na Pythonie 3.14 nie ma natywnego `pymicro_vad` (assist pipeline), przez co frontend HA wisiał na `get_services`. Do testów na żywo atrapy modułów w `PYTHONPATH`; kod Sentinel bez zmian.

### G2a — decyzje

- Przełączniki modułów działają: wyłączony moduł odpowiada `unsupported_capability` z `reason=module_disabled` (mapa prefiksów komend w API), panel pokazuje kartę „moduł wyłączony” zamiast pustego widoku. Autopilot domyślnie wyłączony (27.1).
- Doctor wyłączony: aktywne findings zamykane przy starcie (`module_disabled`), brak timerów, wynik zdrowia `unknown` (nigdy „zdrowy” bez reguł).
- Mapa zależności wyłączona: `graph/query` niedostępne, korelacja bez wspólnych przodków (same incydenty pojedyncze); wyszukiwanie obiektów zostaje (służy innym kartom jako wyszukiwarka inwentarza).
- `ingestion.allowed_domains`: pusta lista = wszystkie domeny poza wykluczeniami prywatności; wykluczenia zawsze mają pierwszeństwo.
- Formularz opcji w sekcjach (`data_entry_flow.section`), zapis płaski w `entry.options`; błąd CIDR jako błąd formularza (`base`).

### G2b + G9 — decyzje

- Tabela `numeric_sources` (migracja v8): encja, cel `battery` / `signal`, skala, próg (tylko sygnał). Audyt każdej zmiany.
- Mapowanie baterii jest częścią obserwacji bazowej (nie wymaga opcji): wartość przeliczona ze skali na %, dalej zwykła reguła DOC004. Ręczne mapowanie ma pierwszeństwo przed `device_class`.
- Sygnał wymaga `ingestion.numeric_sampling`. DOC008 (INFO, kara 0): dwie próbki poniżej progu w odstępie ≥ 30 min; powrót powyżej progu + 5% skali. Wartości porównywane tylko ze skalą danego źródła — RSSI i LQI nigdy ze sobą.
- Usunięcie źródła zamyka wynikający z niego finding (`source_removed`), nie udaje powrotu do zdrowia.
- Znaleziony błąd: `async_flush()` mógł wrócić, gdy writer w tle trzymał już pobraną partię, której Doctor jeszcze nie przetworzył. Partie są teraz serializowane blokadą.

### G3 — decyzje

- `autopilot/draft`: zakres kreatora MVP (11.1) — jeden trigger state / numeric_state, do 5 warunków state, 1–5 akcji `light.turn_on` / `light.turn_off` (tylko encje `light.*` istniejące w HA), tryb, cooldown (tylko z `single`, jako `delay` na końcu). Nieznane pola żądania odrzucane.
- Szkic jest budowany jako drzewo, serializowany do YAML i przepuszczany przez ten sam bezpieczny parser, ekstrakcję i lint co import (AC AUT 04, test porównuje wyniki obu ścieżek). Deterministyczne `id` z treści.
- Wynik = eksport YAML albo zapis jako importowany snapshot. Kreator nic nie instaluje w HA i nie wywołuje usług (test ze spy).
- Panel: czytelne komunikaty błędów walidacji (nieznana encja, zakres, pole), nie ogólne „operacja nie powiodła się”.

### G4 — decyzje

- Migracja v9: `network_hosts`, `network_host_keys` (MAC, hostname; unikalne globalnie — konflikt = `identity_conflict`, bez automatycznego scalania), `network_host_addresses` (atrybut, nie tożsamość; zmiana IP zostawia ten sam host — AC NET 01), `network_host_links` (tylko potwierdzone przez admina, z zapisanym dowodem), `network_targets.host_id`.
- Sugestie powiązań z rejestru urządzeń HA: ten sam MAC (0,9), nazwa hosta z `configuration_url` (0,5), IP (0,3 — DHCP). Sugestia nigdy nie tworzy powiązania.
- Dwa osobne stany hosta: sieć (najgorszy status zatwierdzonych endpointów; w N0 `not_probed`) i Home Assistant (dostępność encji powiązanych urządzeń). Żaden nie jest wyprowadzany z drugiego.
- Graf: urządzenie → host `depends_on` (manual, 1,0), host → endpoint `uses_endpoint`. Usunięcie hosta zdejmuje powiązanie z celów sond.

### G5 — decyzje

- Wagi jednostek 1..5 (migracja v10, `unit_weights`; brak wiersza = 1), audyt każdej zmiany. `health/components` pokazuje każdą jednostkę: etykietę, aktywne reguły, karę, pokrycie, wagę i wykluczenia (8.3); karta w Przeglądzie domyślnie pokazuje tylko jednostki istotne.
- Znaleziony błąd: encje bez wpisu w rejestrze z poprzednich startów zostawały „aktywne” obok nowej tożsamości tej samej encji (dziesiątki duplikatów w wyniku zdrowia). Przy ładowaniu inwentarza dostają teraz tombstone (6.1). Nadpisania i źródła liczbowe zostają przy tombstonie (27.1); panel ostrzega, że ustawienie dla encji bez unikalnego ID wygaśnie przy restarcie.
