# Entities

Sentinel adds a small set of diagnostic entities under the **HA Sentinel** device. It does not
create one entity per finding and does not put the graph into attributes, so it adds very
little to the Recorder.

| Entity | Meaning |
|---|---|
| `sensor.ha_sentinel_health_score` | Health score 0–100. **unknown** while coverage is below 80 % (first 15 min, after a gap) or when Doctor is off |
| `sensor.ha_sentinel_coverage` | Coverage in % |
| `sensor.ha_sentinel_active_incidents` | Number of open incidents |
| `sensor.ha_sentinel_dropped_observations` | Observations dropped under overload (each drop is also a coverage gap) |
| `sensor.ha_sentinel_queue_depth` | Current ingestion queue depth |
| `binary_sensor.ha_sentinel_sentinel_degraded` | On when Sentinel itself is degraded (overload, read-only storage, failed module) |

Entity IDs can differ if you renamed them. Values are updated at most every 30 s; the
degraded state changes immediately.

You can use them in your own dashboards and automations, for example a notification to your
phone when `active_incidents` rises. Sentinel itself sends nothing outside Home Assistant.

**Repairs:** Sentinel raises Repairs issues for a damaged database, a database written by a
newer Sentinel, a failed upgrade and not enough disk space to upgrade.
