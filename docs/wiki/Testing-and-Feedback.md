# Testing and Feedback

HA Sentinel 0.1.0 is the first public release. It needs real homes to find the rough edges.
Thank you for helping!

## What helps most

1. **False alarms.** Incidents for devices that are fine, or are *supposed* to be off. These
   help tune the rules most.
2. **Missed problems.** Something broke and Sentinel stayed quiet.
3. **Wrong grouping or wrong cause.** Incidents merged that shouldn't be, or a cause that
   makes no sense.
4. **Performance.** Number of entities, hardware (Pi, NUC, VM…), whether Home Assistant feels
   slower. Panel → Diagnostics → *Performance* shows Sentinel's own timings.
5. **Anything confusing** in the panel (English and Polish).

## How to report

Open an issue: https://github.com/MarcinG81/HA-Sentinel/issues

Please include:

- Home Assistant version and installation type (OS, Container, Core).
- Roughly how many entities and devices you have.
- What you expected and what happened; a screenshot of the incident helps.
- If possible, a **redacted export** (Panel → Settings → Export, scope *Diagnostics*).
  Names become pseudonyms and IP / MAC / hostnames are not included, but please look at it
  before posting.
- For start-up problems: entries with `ha_sentinel` from Settings → System → Logs.

## Known in 0.1.0

- On the first start, devices that were already unavailable show up as incidents dated at
  Sentinel's start time ("at least since").
- Rule settings are per entity; switching a rule off for a whole device is planned.
- Tested on Home Assistant 2026.5.1 and 2026.10.2.

## For developers

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component ruff home-assistant-frontend
.venv/bin/pytest                        # unit and integration tests on a real HA test instance
.venv/bin/pytest -m perf tests/perf -s  # standard-profile load test

cd frontend-src && npm ci
npm test && npm run build               # rebuilds the committed panel bundle
npm run a11y -- "/path/to/chrome"       # WCAG 2.1 AA audit of every view
```

Code and comments are in English; the original specification (Polish) and the plan live in
`docs/`. Pull requests are welcome; CI runs gitleaks, hassfest, HACS validation and the
tests on the lowest supported and the current Home Assistant version.
