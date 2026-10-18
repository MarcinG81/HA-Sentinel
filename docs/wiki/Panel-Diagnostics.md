# Diagnostics

The state of Sentinel itself, as opposed to the health of your home.

| Section | Shows |
|---|---|
| **State** | Start time, state and reason (e.g. read-only storage), when the registries were last reconciled |
| **Ingestion queues** | Depth, capacity, received, dropped, oversize and coalesced counters, degraded flag |
| **Storage** | Database size, number of observations, schema version, mode (read-write / read-only) |
| **Coverage gaps** | The 5 most recent periods Sentinel did not observe (restarts, overload); older ones are in a diagnostics export |
| **Doctor** | Active findings, flapping checks with partial data, next scheduled check |
| **Performance** | p50 / p95 / p99 / max of Sentinel's own timings: state callback (µs), ingestion lag, database commits, rules, correlation, API (ms), and the highest queue depth |

Use these numbers in bug reports about performance. Home Assistant's own **Download
diagnostics** (Settings → Devices & services → HA Sentinel → ⋮) gives a redacted JSON with
the same kind of information.

**Degradation levels:** under load Sentinel protects Home Assistant first.
1. Queue above 80 % for 60 s: replay is paused.
2. Queue above 95 % or storage above 95 %: no new scans, exports or imports; only
   availability, audit and gap handling continue.
3. Database not writable: read-only, no changes accepted, a clear alarm.

It returns to normal only after 5 minutes below 50 %. Every dropped observation is recorded
as a coverage gap and followed by a resync, never silently ignored.

Sentinel cannot tell you that Home Assistant itself is down; use an external monitor for that.
