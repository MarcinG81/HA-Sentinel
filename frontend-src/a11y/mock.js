// Representative data for every panel view (accessibility audit and visual checks).
const subject = (n, e) => ({ uid: "u-" + e, entity_id: e, name: n, deleted: false });
const inc = (id, rule, sev, st, n, e, opened) => ({
  id, status: st, severity: sev, title_key: "rule." + rule, kind: "rule", member_count: 1, root: null,
  merged_into: null, top_hypothesis: null, rule_id: rule, subject: subject(n, e), opened_at: opened,
  updated_at: opened, resolved_at: null, revision: 1, suppressed_until: null, suppress_reason: null, acknowledged_at: null,
});
const infra = { ...inc("i0", "", "critical", "open", "MQTT 1", "sensor.mqtt_1", "2026-10-17T09:42:00Z"), kind: "infrastructure",
  title_key: "incident.infrastructure", member_count: 18, root: { uid: "config_entry:mqtt", label: "MQTT broker", type: "integration_entry" },
  top_hypothesis: { candidate_label: "MQTT broker", label: "strong", score: 0.97 } };
const items = [infra, inc("i1", "DOC001", "high", "open", "Czujnik salon", "sensor.salon_temp", "2026-10-17T09:42:03Z"),
  inc("i2", "DOC004", "warning", "acknowledged", "Bateria drzwi", "sensor.drzwi_battery", "2026-10-17T07:10:00Z")];
const hyps = [{ candidate_uid: "config_entry:mqtt", candidate_label: "MQTT broker", candidate_type: "integration_entry", score: 0.967, label: "strong",
  features: { T: 0.83, D: 1, P: 1, E: 1, K: 0 }, evidence_source: "config_entry_state:setup_retry", affected: 18, observable: 18,
  counter_evidence: [], reasons: [], graph_revision: 4 }];
const caps = ["inventory.read", "states.live", "history.read", "automation.import", "automation.lint", "graph.read",
  "automation.replay", "managed_yaml.restore", "network.probe", "storage.write"].map((c) => ({ capability_id: c, status: "supported", reason_code: null, contract_version: 1 }));
export const DATA = {
  "ha_sentinel/overview": { api_version: 1, revision: 7, state: "ready", reason_code: null, degradation_level: 0,
    health: { score: 83, score_partial: 83, coverage_pct: 92, coverage_complete_at: null, units: 42, maintenance_units: 1 },
    counters: { active_incidents: 3, active_findings: 3, entities: 187, queue_depth: 0, dropped_observations_total: 0 },
    modules: { doctor: "ready", config_history: "ready", autopilot: "ready", dependency_mapper: "ready", network: "ready" } },
  "ha_sentinel/capabilities": { capabilities: caps },
  "ha_sentinel/incidents/list": { items, next_cursor: null, revision: 7 },
  "ha_sentinel/incidents/get": { ...infra, hypotheses: hyps, counter_evidence: [], confirmed_cause: null, recurrence_of: "i0",
    change_hypotheses: [{ candidate_uid: "resource:r1", label: "pompa.yaml", minutes_before: 12, runnable: "Pompa nocą", relation: "controls", subject: subject("Pompa", "switch.pump") }],
    findings: [{ id: "f1", rule_id: "DOC001", rule_version: 1, status: "active", severity: "high", confidence: 1, coverage: "complete",
      subject: subject("MQTT 1", "sensor.mqtt_1"), first_seen: "2026-10-17T09:42:03Z", last_seen: "2026-10-17T09:42:03Z", resolved_at: null, details: { value: 12.5, threshold: 20, resolution: "rule_revised", missing: [{ entity_id: "light.gone" }], stale: [{ entity_id: "light.old", current: "light.new" }] },
      evidence: [{ kind: "availability.changed", received_at: "2026-10-17T09:42:03Z", occurred_at: "2026-10-17T09:42:03Z", payload: { from: "available", to: "unavailable" }, evidence_expired: false }] }] },
  "ha_sentinel/numeric/list": { numeric_sampling: false, items: [
    { id: "n1", subject_uid: "u3", subject_label: "sensor.lock_power", subject_deleted: false, purpose: "battery", scale_min: 0, scale_max: 255, threshold: null, sampling: true },
    { id: "n2", subject_uid: "u4", subject_label: "sensor.plug_lqi", subject_deleted: false, purpose: "signal", scale_min: 0, scale_max: 255, threshold: 60, sampling: false }] },
  "ha_sentinel/network/hosts/list": { probes_active: false, items: [
    { id: "h1", display_name: "NAS", keys: { mac: "aa:bb:cc:dd:ee:ff" }, addresses: [{ family: "ipv4", value: "192.168.1.10" }],
      links: [{ device_id: "d1", name: "Synology", missing: false }],
      suggestions: [{ device_id: "d2", name: "Drukarka", reasons: ["ip_match"], confidence: 0.3 }],
      reachability: "not_probed", ha_entities: { available: 3, unavailable: 1, unknown: 0 }, endpoints: [] }] },
  "ha_sentinel/health/components": { score: 85, score_partial: 85, units: [
    { unit: "u1", kind: "device", label: "Pompa", entities: 3, rules: ["DOC001"], weight: 3, penalty: "0.6", covered: true },
    { unit: "u2", kind: "entity", label: "sensor.temp", entities: 1, rules: [], weight: 1, penalty: "0", covered: false },
    { unit: "u3", kind: "device", label: "Gniazdko", entities: 2, rules: [], excluded: "maintenance" }] },
  "ha_sentinel/graph/revisions": { current: 4, items: [{ revision: 4, created_at: "2026-10-17T10:00:00Z" }, { revision: 3, created_at: "2026-10-16T10:00:00Z" }] },
  "ha_sentinel/identity/list": { items: [{ id: "s1", old: { uid: "o", entity_id: "sensor.garden_temp", deleted: true }, new: { uid: "n", entity_id: "sensor.garden_temp", deleted: false }, reason: "same_entity_id", created: "2026-10-17T10:00:00Z" }] },
  "ha_sentinel/backup/status": { available: true, service: "create_automatic", settings_path: "/config/backup", entities: { manager_state: { entity_id: "sensor.backup_backup_manager_state", state: "idle" }, last_successful: { entity_id: "sensor.backup_last_successful_automatic_backup", state: "2026-10-17T04:45:00+00:00" } } },
  "ha_sentinel/rules/list": { items: [
    { id: "r1", rule_id: "DOC005", subject_uid: "u1", subject_label: "sensor.freezer_temp", subject_deleted: false, subject_persistent: true, enabled: true, params: { period_s: 300 } },
    { id: "r3", rule_id: "DOC007", subject_uid: "u5", subject_label: "automation.morning", subject_deleted: false, subject_persistent: true, enabled: true, params: { days_mask: 31, minute: 420, tolerance_min: 15 } },
    { id: "r2", rule_id: "DOC001", subject_uid: "u2", subject_label: null, subject_deleted: true, enabled: false, params: {} }],
    ranges: { DOC001: { unavailable_s: [30, 3600] }, DOC002: { unknown_s: [60, 86400] }, DOC003: { flap_threshold: [2, 100] }, DOC004: { low_pct: [1, 99], clear_pct: [2, 100] }, DOC005: { period_s: [10, 86400], threshold_s: [0, 604800] }, DOC007: { days_mask: [1, 127], minute: [0, 1439], tolerance_min: [1, 240] }, DOC008: {}, DOC009: {} } },
  "ha_sentinel/maintenance/list": { items: [{ id: "w1", scope_kind: "device", scope_id: "device:x", label: "Gniazdko", reason: "wymiana", start: "2026-10-17T09:00:00Z", end: "2026-10-17T11:00:00Z", active: true }] },
  "ha_sentinel/resources/list": { items: [{ id: "r1", provider: "managed", kind: "automation", label: "automations (managed)", write_policy: "managed_write", snapshots: 3, updated_at: "2026-10-17T09:00:00Z" }] },
  "ha_sentinel/snapshots/list": { items: [] },
  "ha_sentinel/graph/search": { items: [], graph_revision: 1, nodes: 10, edges: 12, unresolved: 0 },
  "ha_sentinel/network/inventory": { level: "N1", probe_capability: { status: "supported", reason_code: null },
    devices: [{ device_id: "d1", name: "Hue bridge", manufacturer: "Signify", mac_hint: ["00:17:88:…"], has_configuration_url: true, linked_targets: ["t1"] }] },
  "ha_sentinel/network/targets/list": { items: [{ id: "t1", label: "Hue API", host: "192.168.1.50", port: 443, kind: "https", path: "/", allow_loopback: false,
    linked_device_id: "d1", enabled: true, status: "reachable", last_outcome: "open", failures: 0 }], allowlist: ["192.168.0.0/16"], interval_s: 60, active: true },
  "ha_sentinel/diagnostics": { boot_id: "b", started_at: "2026-10-17T09:30:00Z", state: "ready", reason_code: null,
    queues: { depth: 0, capacity: 10000, received_total: 5321, dropped_total: 0, degraded: false },
    storage: { db_bytes: 3145728, observations: 5321, schema_version: 6, mode: "read_write" }, coverage_gaps: [],
    doctor: { active_findings: 3, flapping_partial: 0, next_deadline: null }, metrics: { counters: {}, histograms: { ingestion_callback_us: { unit: "us", count: 6000, p50: 4.1, p95: 8.9, p99: 12.3, max: 210 }, writer_commit_ms: { unit: "ms", count: 61, p50: 3.2, p95: 9.8, p99: 14, max: 22 } }, queue_depth_max: 1000 } },
  "ha_sentinel/data/status": { storage: { db_bytes: 3145728, observations: 5321, schema_version: 14, aggregates: 1200, snapshots: 12, audit_bytes: 524288 }, audit_budget_bytes: 67108864, audit_blocked: true,
    retention: { retention_observations_days: 7, retention_incidents_days: 90, storage_max_total_mib: 1024 }, last_purge: null },
};
