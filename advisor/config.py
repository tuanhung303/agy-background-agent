"""
advisor.config - Configuration constants and environment variable bindings.
"""

import os


def _safe_int(env_key, default_val):
    val = os.environ.get(env_key)
    if val is None:
        return default_val
    try:
        return int(val)
    except (ValueError, TypeError):
        return default_val


def _safe_float(env_key, default_val):
    val = os.environ.get(env_key)
    if val is None:
        return default_val
    try:
        return float(val)
    except (ValueError, TypeError):
        return default_val


def _safe_bool(env_key, default_val=True):
    val = os.environ.get(env_key)
    if val is None:
        return default_val
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    return default_val


def _load_env_overlay():
    """Optional KEY=VALUE overrides for hook settings.

    Hook processes do not inherit an interactive shell's exports (agy executes them
    outside the TUI environment), so profile tuning goes through a file instead.
    Precedence: real environment > overlay file > hardcoded defaults. Only AGY_*
    keys are accepted. Path: AGY_ADVISOR_ENV_FILE or ~/.config/agy/advisor.env
    """
    candidates = [
        os.environ.get("AGY_ADVISOR_ENV_FILE"),
        os.path.expanduser("~/.config/agy/advisor.env"),
        os.environ.get("AGY_STOP_AUDIT_ENV_FILE"),
        os.path.expanduser("~/.config/agy/stop_audit.env"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k.startswith("AGY_") and k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


_load_env_overlay()


DEFAULT_MODEL_FALLBACKS = (
    "Gemini 3.7 Flash (High)",
    "Gemini 3.7 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.1 Pro (High)",
)

REVIEWER_MODEL_SPEC = os.environ.get("AGY_ADVISOR_MODEL", os.environ.get("AGY_STOP_AUDIT_MODEL", "Gemini 3.7 Flash (Medium)"))
REVIEWER_EFFORT = os.environ.get("AGY_ADVISOR_EFFORT", os.environ.get("AGY_STOP_AUDIT_EFFORT", "high"))
REVIEWER_MODEL = REVIEWER_MODEL_SPEC
MAX_ITERATIONS = _safe_int("AGY_ADVISOR_MAX_ITERATIONS", _safe_int("AGY_STOP_AUDIT_MAX_ITERATIONS", 1))
LOG_FILE = os.environ.get("AGY_ADVISOR_LOG", os.environ.get("AGY_STOP_AUDIT_LOG", "/tmp/agy_advisor.log"))

# Triggers: Audit turns with >= 15 tool calls OR duration >= 600s (with >= 1 tool)
TURN_DURATION_THRESHOLD = _safe_float("AGY_ADVISOR_MIN_DURATION", _safe_float("AGY_STOP_AUDIT_MIN_DURATION", 600.0))
TOOL_CALL_THRESHOLD = _safe_int("AGY_ADVISOR_MIN_TOOLS", _safe_int("AGY_STOP_AUDIT_MIN_TOOLS", 15))
MIN_TOOLS_FOR_DURATION_TRIGGER = _safe_int("AGY_ADVISOR_MIN_TOOLS_FOR_TIME", _safe_int("AGY_STOP_AUDIT_MIN_TOOLS_FOR_TIME", 1))

# Gateway heartbeat notification interval (180s = 3 minutes)
GATEWAY_NOTIFY_INTERVAL = max(0.0, _safe_float("AGY_GATEWAY_NOTIFY_INTERVAL", _safe_float("HERMES_AGENT_NOTIFY_INTERVAL", 180.0)))

# Mid-turn advisor configuration
MID_TURN_ADVISOR_ENABLED = _safe_int("AGY_MID_TURN_ADVISOR_ENABLED", _safe_int("AGY_MID_TURN_VERIFIER_ENABLED", 1))
ADVISOR_TOOL_INTERVAL = _safe_int("AGY_ADVISOR_TOOL_INTERVAL", _safe_int("AGY_VERIFIER_TOOL_INTERVAL", 10))
MAX_MID_TURN_STEERS = _safe_int("AGY_MAX_MID_TURN_STEERS", _safe_int("AGY_MAX_MID_TURN_ADVICES", 0))
ADVISOR_STEER_MIN_CONFIDENCE = _safe_float("AGY_ADVISOR_STEER_MIN_CONFIDENCE", 0.7)
ADVISOR_ESCALATE_MIN_CONFIDENCE = _safe_float("AGY_ADVISOR_ESCALATE_MIN_CONFIDENCE", 0.85)
ADVISOR_MAX_ERROR_STREAK = _safe_int("AGY_ADVISOR_MAX_ERROR_STREAK", 3)

# Backward compatibility aliases
MID_TURN_VERIFIER_ENABLED = MID_TURN_ADVISOR_ENABLED
VERIFIER_TOOL_INTERVAL = ADVISOR_TOOL_INTERVAL

# Sensitive keyword triggers configuration
SENSITIVE_TRIGGER_ENABLED = _safe_bool("AGY_ADVISOR_SENSITIVE_TRIGGER", _safe_bool("AGY_STOP_AUDIT_SENSITIVE_TRIGGER", True))
DEFAULT_SENSITIVE_KEYWORDS = (
    "git", "gcloud", "aws", "az", "kubectl", "terraform",
    "docker", "gh", "gsutil", "bq", "ssh", "rsync", "helm", "pulumi",
)

FILE_EDITING_TOOLS = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "edit_file",
    "create_file",
    "apply_diff",
    "patch",
    "modify_file",
    "write_file",
    "run_command",
}
