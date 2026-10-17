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
| E4 | RCA (B19), provider M + journal (B20), sondy N1 (B21), replay (B22), eksport (B23), upgrade (B24) | — |
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
