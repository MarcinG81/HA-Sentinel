# Performance report — standard profile (B25)

Date: 2026-10-17 · Sentinel 0.1.0-dev · HA 2026.10.2 test instance (`pytest-homeassistant-custom-component`)

Machine: Linux arm64, Python 3.14.4 — **not the reference hardware** (4× ARM64, 4 GB RAM, local SSD). Numbers below are processing costs on a developer machine; they are not a promise for every installation (spec 21.1).

Profile: 2000 registered entities in 2 domains (4000 states), 300 devices, 300 imported automations; 60 rounds of 100 state changes (≈5 % availability transitions) plus a burst of 1000 changes before the writer runs. Run: `SENTINEL_PERF_REPORT=report.json pytest -m perf tests/perf -s`.

| Measure | Result | Budget (spec 21.1) | |
|---|---|---|---|
| State callback p99 | 17.833 µs (max 2314.583 µs) | ≤ 1000 µs | pass |
| Dropped observations | 0 | 0 in standard | pass |
| Start incl. inventory of 4000 states | 0.266 s | ≤ 60 s | pass |
| Overview API p95 | 10.4 ms | ≤ 300 ms | pass |
| Graph neighbourhood p95 | 1.22 ms | ≤ 500 ms | pass |
| Full graph rebuild | 0.103 s (4603 nodes, 3201 edges) | ≤ 10 s, off the loop | pass |
| Lint of 300 automations | 0.112 s | ≤ 5 s | pass |
| Writer commit p95 (batch ≤ 500) | 7.607 ms | ≤ 100 ms | pass |
| Rules per batch p95 | 0.709 ms | ≤ 100 ms per small batch | pass |

Burst: live queue peaked at 370 of 10 000 (no overload policy triggered).

## Not measured yet (still required before R1)

- Average CPU over 30 minutes and peak RAM versus a baseline HA without Sentinel on the reference hardware.
- 24 h standard and 72 h soak (T21): resource growth beyond retention.
- Event-loop lag p95 versus baseline (≤ 10 ms) and end-to-end observation → finding p95 (≤ 5 s) in real time; the test drains the writer explicitly instead of waiting for its 1 s timer.

## Defect found by this test

Before the fix, any installation with more states than the 2000-entry control queue overflowed it with the startup snapshot, which requested a resync, which overflowed again — an endless resync loop on every start of a standard-size home. The startup/resync baseline now has its own path outside the live queues, newer snapshots supersede older ones, and a resync never starts while a baseline is still pending.
