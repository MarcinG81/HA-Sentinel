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
| E0 | Kwalifikacja HA (B01–B03): lifecycle, panel, WS admin, reload automatyzacji | do zrobienia na żywym HA |
| **E1** | Scaffold i CI (B04), DTO/clock/enums (B05), SQLite + migracja v1 (B06), kolejki + writer + coverage (B07), inventory (B08) | **zrobione (bez metryk i purge)** |
| E2 | Config flow + capabilities (B09), Doctor DOC001–DOC005 (B10), incidents/outbox/ACK (B11), API + policy + rate limit (B16), panel overview (B17) | B09, B10, B16, B17 zrobione; B11 bez wysyłki powiadomień z outbox (persistent notification opt-in) |
| E3 | AST YAML (B12), graf + impact BFS (B13), snapshot/canonicalizer/diff (B14), lint AUT001–010 (B15), panel (B18) | **zrobione** (graf w pamięci, bez wersjonowania — to R1) |
| E4 | RCA (B19), provider M + journal (B20), sondy N1 (B21), replay (B22), eksport (B23), upgrade (B24) | B19, B20, B23 zrobione; retencja/purge (REQ10) zrobione |
| E5 | Load/soak (B25), E2E/a11y (B26), pilot (B27) | — |

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
