"""HA Sentinel constants. Must not import homeassistant."""

from __future__ import annotations

DOMAIN = "ha_sentinel"
API_VERSION = 1
SCHEMA_VERSION = 3
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
}

# Domains excluded from observation by default (spec 5.1, 17.1)
PRIVACY_EXCLUDED_DOMAINS = frozenset(
    {"person", "zone", "device_tracker", "media_player", "camera", "text", "input_text"}
)
