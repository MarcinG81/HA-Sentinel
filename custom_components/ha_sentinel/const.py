"""HA Sentinel constants. Must not import homeassistant."""

from __future__ import annotations

DOMAIN = "ha_sentinel"
API_VERSION = 1
SCHEMA_VERSION = 9
# Shortest DOC005 period is 10 s and its deadline 3 periods: one stored heartbeat per 5 s.
HEARTBEAT_MIN_INTERVAL_S = 5
DATA_DIR = "ha_sentinel"
DB_FILENAME = "sentinel.db"

# Ingestion (spec 5.2)
CONTROL_QUEUE_SIZE = 2000
TELEMETRY_QUEUE_SIZE = 8000
MAX_DTO_BYTES = 8 * 1024
MAX_PAYLOAD_BUDGET_BYTES = 32 * 1024 * 1024
TELEMETRY_LAST_SAMPLE_MAP = 1000
WRITER_BATCH_SIZE = 500
WRITER_FLUSH_INTERVAL_S = 1.0
UNLOAD_FLUSH_TIMEOUT_S = 5.0

# Lifecycle (spec 4.1)
STARTUP_GRACE_S = 120
REGISTRY_RECONCILE_INTERVAL_S = 15 * 60

# Doctor (spec 8.1, 27.1)
DOC001_UNAVAILABLE_S = 120
DOC001_RECOVERY_STABLE_S = 30
DOC002_UNKNOWN_S = 10 * 60
DOC003_FLAP_WINDOW_S = 60 * 60
DOC003_FLAP_THRESHOLD = 6
DOC003_DEBOUNCE_S = 10
DOC004_LOW_PCT = 20
DOC004_CLEAR_PCT = 25
DOC004_CONFIRM_S = 30 * 60
COVERAGE_WINDOW_S = 15 * 60
COVERAGE_MIN_PCT = 80

# Diagnostic sensors (spec 14.3)
SENSOR_THROTTLE_S = 30

# Options (spec 27.1; dots replaced by underscores for HA translation keys)
OPT_MODULES_DOCTOR = "modules_doctor"
OPT_MODULES_CONFIG_HISTORY = "modules_config_history"
OPT_MODULES_AUTOPILOT = "modules_autopilot"
OPT_MODULES_DEPENDENCY_MAPPER = "modules_dependency_mapper"
OPT_MODULES_NETWORK = "modules_network"
OPT_STORAGE_MAX_TOTAL_MIB = "storage_max_total_mib"
OPT_RETENTION_OBSERVATIONS_DAYS = "retention_observations_days"
OPT_RETENTION_INCIDENTS_DAYS = "retention_incidents_days"
OPT_DOCTOR_STARTUP_GRACE_S = "doctor_startup_grace_s"
OPT_DOCTOR_UNAVAILABLE_S = "doctor_unavailable_s"
OPT_MANAGED_WRITE_ENABLED = "managed_write_enabled"
OPT_MANAGED_DIR = "managed_dir"
OPT_MANAGED_RELOAD = "managed_reload_after_apply"
OPT_NETWORK_ACTIVE_PROBES = "network_active_probes"
OPT_NETWORK_INTERVAL_S = "network_interval_s"
OPT_NETWORK_ALLOWLIST = "network_allowlist"
OPT_NOTIFICATIONS_ENABLED = "notifications_enabled"
OPT_INGESTION_ALLOWED_DOMAINS = "ingestion_allowed_domains"
OPT_INGESTION_NUMERIC_SAMPLING = "ingestion_numeric_sampling"

DEFAULT_OPTIONS: dict[str, object] = {
    OPT_MODULES_DOCTOR: True,
    OPT_MODULES_CONFIG_HISTORY: True,
    OPT_MODULES_AUTOPILOT: False,
    OPT_MODULES_DEPENDENCY_MAPPER: True,
    OPT_MODULES_NETWORK: True,
    OPT_STORAGE_MAX_TOTAL_MIB: 1024,
    OPT_RETENTION_OBSERVATIONS_DAYS: 7,
    OPT_RETENTION_INCIDENTS_DAYS: 90,
    OPT_DOCTOR_STARTUP_GRACE_S: STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S: DOC001_UNAVAILABLE_S,
    # Writing managed files is off until the admin enables it (spec 18.2 step 6).
    OPT_MANAGED_WRITE_ENABLED: False,
    OPT_MANAGED_DIR: "ha_sentinel_managed",
    OPT_MANAGED_RELOAD: True,
    # Level N0 by default: no active probes until the admin opts in (spec 13.1).
    OPT_NETWORK_ACTIVE_PROBES: False,
    OPT_NETWORK_INTERVAL_S: 60,
    OPT_NETWORK_ALLOWLIST: "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7",
    # Notifications stay in the panel unless the admin opts in (spec 14.3).
    OPT_NOTIFICATIONS_ENABLED: False,
    # Empty list = every domain except the privacy exclusions (decision in docs/PLAN.md).
    OPT_INGESTION_ALLOWED_DOMAINS: [],
    # Extra numeric samples need an explicit entity and purpose (spec 27.1).
    OPT_INGESTION_NUMERIC_SAMPLING: False,
}

# Domains excluded from observation by default (spec 5.1, 17.1)
PRIVACY_EXCLUDED_DOMAINS = frozenset(
    {"person", "zone", "device_tracker", "media_player", "camera", "text", "input_text"}
)
