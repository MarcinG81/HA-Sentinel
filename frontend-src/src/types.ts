// Minimal shapes of the HA frontend objects the panel relies on. We deliberately do not
// import private HA frontend modules (spec 14.1).

export interface HassConnection {
  subscribeMessage<T>(
    callback: (msg: T) => void,
    message: Record<string, unknown>,
  ): Promise<() => Promise<void>>;
  addEventListener(event: string, cb: () => void): void;
  removeEventListener(event: string, cb: () => void): void;
}

export interface Hass {
  callWS<T>(message: Record<string, unknown>): Promise<T>;
  fetchWithAuth(path: string, init?: RequestInit): Promise<Response>;
  connection: HassConnection;
  language: string;
  locale?: { language?: string };
  config: { time_zone: string };
  user?: { is_admin: boolean };
  themes?: { darkMode?: boolean };
}

export interface Capability {
  capability_id: string;
  status: "supported" | "unavailable" | "unsupported" | "disabled";
  reason_code: string | null;
  contract_version: number;
}

export interface Overview {
  api_version: number;
  revision: number;
  degradation_level: number;
  state: string;
  reason_code: string | null;
  health: {
    score: number | null;
    score_partial: number | null;
    coverage_pct: number | null;
    coverage_complete_at: string | null;
    units: number;
    maintenance_units: number;
  };
  counters: {
    active_incidents: number;
    active_findings: number;
    entities: number;
    queue_depth: number;
    dropped_observations_total: number;
  };
  modules: Record<string, string>;
}

export interface Subject {
  uid: string | null;
  entity_id: string | null;
  name: string | null;
  deleted: boolean;
}

export type Severity = "critical" | "high" | "warning" | "info";
export type IncidentStatus = "open" | "acknowledged" | "suppressed" | "resolved";

export interface Hypothesis {
  candidate_uid: string;
  candidate_label: string;
  candidate_type: string;
  score: number;
  label: "strong" | "possible" | "none";
  features: { T: number; D: number; P: number; E: number; K: number };
  evidence_source: string;
  affected: number;
  observable: number;
  counter_evidence: { kind: string; count?: number; sample?: string[] }[];
  reasons: string[];
  graph_revision: number;
}

export interface Incident {
  id: string;
  status: IncidentStatus;
  severity: Severity;
  title_key: string;
  kind: "infrastructure" | "rule";
  member_count: number;
  root: { uid: string; label: string; type: string | null } | null;
  merged_into: string | null;
  top_hypothesis: { candidate_label: string; label: Hypothesis["label"]; score: number } | null;
  rule_id: string;
  subject: Subject;
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  revision: number;
  suppressed_until: string | null;
  suppress_reason: string | null;
  acknowledged_at: string | null;
}

export interface Evidence {
  kind: string | null;
  received_at: string | null;
  occurred_at: string | null;
  payload: Record<string, unknown> | null;
  evidence_expired: boolean;
}

export interface FindingDetail {
  id: string;
  rule_id: string;
  rule_version: number;
  status: string;
  severity: Severity;
  confidence: number;
  coverage: "complete" | "partial" | "missing";
  subject: Subject;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  details?: FindingDetails;
  evidence: Evidence[];
}

export interface FindingRef {
  entity_id: string;
  current?: string;
  pointer?: string;
}

export interface FindingDetails {
  resolution?: string;
  value?: number;
  threshold?: number;
  entries?: number;
  latest_version?: string;
  ratio?: number;
  outcome?: string;
  resource_label?: string;
  imported_at?: string;
  missing_count?: number;
  stale_count?: number;
  missing?: FindingRef[];
  stale?: FindingRef[];
}

export interface IncidentDetail extends Incident {
  findings: FindingDetail[];
  hypotheses: Hypothesis[];
  counter_evidence: { kind: string; count?: number; sample?: string[]; candidate_uid: string }[];
  confirmed_cause: { candidate_uid: string; verdict: "confirmed" | "rejected"; by: string; at: string } | null;
  change_hypotheses?: { candidate_uid: string; label: string; minutes_before: number; runnable: string; relation: string; subject: Subject }[];
  recurrence_of?: string | null;
}

export interface IncidentPage {
  items: Incident[];
  next_cursor: string | null;
  revision: number;
}

export interface Diagnostics {
  boot_id: string;
  started_at: string;
  state: string;
  reason_code: string | null;
  registry_synced_at?: string | null;
  queues: Record<string, number | boolean>;
  storage: { db_bytes: number; observations: number; schema_version: number; mode: string };
  coverage_gaps: { source_id: string; scope: string; from: string; to: string | null; status: string; reason: string }[];
  doctor: { active_findings: number; flapping_partial: number; next_deadline: string | null };
  metrics?: {
    counters: Record<string, number>;
    histograms: Record<string, { unit: string; count: number; p50: number | null; p95: number | null; p99: number | null; max: number | null }>;
    queue_depth_max: number;
  };
}

export interface ChangeEvent {
  stream_epoch: string;
  from_revision: number;
  to_revision: number;
  resync_required: boolean;
}

// --- E3: configuration history, autopilot, graph ---------------------------------------

export type ResourceKind = "automation" | "script" | "blueprint" | "sentinel_options";

export interface Resource {
  id: string;
  provider: string;
  kind: ResourceKind;
  label: string;
  write_policy: string;
  snapshots: number;
  updated_at: string | null;
}

export interface SnapshotMeta {
  id: string;
  resource_id: string;
  canonical_hash: string;
  raw_hash: string | null;
  created_at: string;
  restorable: boolean;
  raw_available: boolean;
  redacted_paths: string[];
  validation: { completeness?: number; runnables?: number; unsupported?: number };
}

export interface ExportText {
  snapshot_id: string;
  label: string;
  kind: ResourceKind;
  source: "raw" | "rendered";
  redacted_paths: string[];
  restorable: boolean;
  content: string;
}

export interface DiffResult {
  semantic: { op: "add" | "remove" | "replace"; path: string; old?: unknown; new?: unknown }[];
  semantic_changed: boolean;
  textual_changed: boolean | null;
  textual: string[] | null;
}

export interface SpanInfo {
  line: number;
  column: number;
  end_line: number;
  end_column: number;
}

export interface LintFinding {
  rule_id: string;
  level: "error" | "warning" | "info" | "risk" | "unsupported";
  message_key: string;
  pointer: string;
  span: SpanInfo | null;
  runnable: string | null;
  params: Record<string, unknown>;
  heuristic: boolean;
}

export interface LintReport {
  snapshot_id: string;
  ruleset_version: number;
  completeness: number;
  runnables: { key: string; label: string; kind: string; mode: string; triggers: number; calls: number; templates: number; unsupported: number }[];
  findings: LintFinding[];
  templates: number;
  truncated: boolean;
  inventory_complete: boolean;
  spans_available: boolean;
}

export interface GraphNode {
  uid: string;
  type: string;
  label: string;
  external_ref: string | null;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  from: string;
  to: string;
  relation: string;
  provenance: string;
  confidence: number;
  evidence_ref: string | null;
}

export interface GraphQueryResult {
  found: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  graph_revision: number;
  current_revision?: number;
  continuation_cursor?: string | null;
  unresolved_count: number;
  built_at: string | null;
}

export interface GraphSearch {
  items: GraphNode[];
  graph_revision: number;
  nodes: number;
  edges: number;
  unresolved: number;
}

export interface WsError {
  code?: string;
  message?: string;
  details?: { reason?: string; module?: string; line?: number | null; paths?: string[]; param?: string; entity_id?: string; min?: number; max?: number };
}

// --- B23: export, jobs, data management -------------------------------------------------

export interface JobInfo {
  job_id: string;
  kind: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  progress: number | null;
  cancel_requested: boolean;
  error_code: string | null;
  updated_at: string;
  result: { export_id?: string; bytes?: number; scopes?: string[]; redacted?: boolean; expires_at?: string } | null;
}

export interface DataStatus {
  storage: { db_bytes: number; observations: number; schema_version: number };
  retention: Record<string, number>;
  last_purge: (Record<string, number | string> & { at: string }) | null;
}

// --- B20: managed files ------------------------------------------------------------------

export interface RestorePreview {
  plan_id: string;
  plan_hash: string;
  resource_id: string;
  base_hash: string;
  target_hash: string;
  expires_at: string;
  state: string;
  reload: boolean;
  diff: { semantic: DiffResult["semantic"]; textual: string[] };
  validation: { parser: string; lint: Record<string, number>; completeness: number };
  impact: { automations: string[]; entities: string[] };
  expected_ids: string[];
}

// --- B21: network --------------------------------------------------------------------------

export interface NetworkDevice {
  device_id: string;
  name: string | null;
  manufacturer: string | null;
  mac_hint: string[];
  has_configuration_url: boolean;
  linked_targets: string[];
}

export interface NetworkTarget {
  id: string;
  label: string;
  host: string;
  port: number;
  kind: "tcp" | "http" | "https";
  path: string;
  allow_loopback: boolean;
  linked_device_id: string | null;
  host_id: string | null;
  enabled: boolean;
  status: "unknown" | "reachable" | "service_closed" | "unreachable";
  last_outcome: string | null;
  failures: number;
}
