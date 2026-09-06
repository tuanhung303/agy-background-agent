"""
sage.config - Configuration constants and environment variable bindings.
"""

import os


def _env_get(*keys, default=None):
    for k in keys:
        v = os.environ.get(k)
        if v is not None:
            return v
    return default


def _safe_cast_multi(keys, default_val, caster):
    val = _env_get(*keys)
    if val is None:
        return default_val
    try:
        return caster(val)
    except (ValueError, TypeError):
        return default_val


def _safe_int_multi(keys, default_val):
    return _safe_cast_multi(keys, default_val, int)


def _safe_float_multi(keys, default_val):
    return _safe_cast_multi(keys, default_val, float)


def _safe_bool_multi(keys, default_val=True):
    val = _env_get(*keys)
    if val is None:
        return default_val
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    return default_val


def _safe_int(key, default_val):
    return _safe_int_multi([key], default_val)


def _safe_float(key, default_val):
    return _safe_float_multi([key], default_val)


def _safe_bool(key, default_val=True):
    return _safe_bool_multi([key], default_val)


def _load_env_overlay():
    """Optional KEY=VALUE overrides for hook settings."""
    candidates = [
        os.environ.get("AGY_SAGE_ENV_FILE"),
        os.path.expanduser("~/.config/agy/sage.env"),
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

REVIEWER_MODEL_SPEC = _env_get("AGY_SAGE_MODEL", "AGY_ADVISOR_MODEL", "AGY_STOP_AUDIT_MODEL", default="Gemini 3.7 Flash (High)")
REVIEWER_EFFORT = _env_get("AGY_SAGE_EFFORT", "AGY_ADVISOR_EFFORT", "AGY_STOP_AUDIT_EFFORT", default="high")
REVIEWER_MODEL = REVIEWER_MODEL_SPEC
MAX_ITERATIONS = _safe_int_multi(("AGY_SAGE_MAX_ITERATIONS", "AGY_ADVISOR_MAX_ITERATIONS", "AGY_STOP_AUDIT_MAX_ITERATIONS"), 1)
MAX_PRIOR_REQUESTS = _safe_int_multi(("AGY_SAGE_MAX_PRIOR_REQUESTS", "AGY_ADVISOR_MAX_PRIOR_REQUESTS"), 5)
LOG_FILE = _env_get("AGY_SAGE_LOG", "AGY_ADVISOR_LOG", "AGY_STOP_AUDIT_LOG", default="/tmp/agy_sage.log")
TOOL_CALL_THRESHOLD = _safe_int_multi(("AGY_SAGE_MIN_TOOLS", "AGY_ADVISOR_MIN_TOOLS", "AGY_STOP_AUDIT_MIN_TOOLS"), 15)
TURN_DURATION_THRESHOLD = _safe_float_multi(("AGY_SAGE_MIN_DURATION", "AGY_ADVISOR_MIN_DURATION", "AGY_STOP_AUDIT_MIN_DURATION"), 600.0)
MIN_TOOLS_FOR_DURATION_TRIGGER = _safe_int_multi(("AGY_SAGE_MIN_TOOLS_FOR_TIME", "AGY_ADVISOR_MIN_TOOLS_FOR_TIME", "AGY_STOP_AUDIT_MIN_TOOLS_FOR_TIME"), 1)
MAX_BACKGROUND_TASK_AGE = _safe_float_multi(("AGY_MAX_BACKGROUND_TASK_AGE", "HERMES_MAX_BACKGROUND_TASK_AGE"), 1800.0)
SAGE_MODELS_DISCOVERY_TIMEOUT = _safe_float_multi(("AGY_SAGE_MODELS_DISCOVERY_TIMEOUT", "AGY_ADVISOR_MODELS_DISCOVERY_TIMEOUT"), 15.0)
SAGE_TIMEOUT_BUDGET = _safe_float_multi(("AGY_SAGE_TIMEOUT_BUDGET", "AGY_ADVISOR_TIMEOUT_BUDGET", "AGY_STOP_AUDIT_TIMEOUT_BUDGET"), 90.0)
SAGE_EXEC_TIMEOUT = _safe_float_multi(("AGY_SAGE_EXEC_TIMEOUT", "AGY_ADVISOR_EXEC_TIMEOUT", "AGY_STOP_AUDIT_EXEC_TIMEOUT"), 75.0)

LITE_MODE_ENABLED = _safe_bool_multi(("AGY_LITE_MODE_ENABLED", "AGY_LITE_MODE"), True)
LITE_MODE_TIMEOUT = _safe_float_multi(("AGY_LITE_MODE_TIMEOUT",), 20.0)
KB_MAINTENANCE_TIMEOUT = _safe_float_multi(("AGY_KB_MAINTENANCE_TIMEOUT", "AGY_LITE_KB_TIMEOUT"), 45.0)
LITE_MAX_RETRIES = _safe_int_multi(("AGY_LITE_MAX_RETRIES",), 3)

LITE_VERIFIER_MODEL_SPEC = _env_get(
    "AGY_LITE_VERIFIER_MODEL", "AGY_LITE_MODEL", "AGY_STOP_VERIFIER_MODEL",
    default="Gemini 3.8 Flash (Low)",
)
LITE_MODEL_CANDIDATES = tuple(
    dict.fromkeys([
        LITE_VERIFIER_MODEL_SPEC,
        "Gemini 3.8 Flash (Low)",
        "Gemini 3.8 Flash (Medium)",
        "Gemini 3.7 Flash (Low)",
        "Gemini 3.7 Flash (Medium)",
    ])
)


def get_real_user_home() -> str:
    """Returns the real user home directory, escaping isolated home if present."""
    home = os.environ.get("AGY_REAL_HOME") or os.environ.get("HOME") or os.path.expanduser("~")
    if "sage_isolated_home" in home:
        idx = home.find("/.gemini/antigravity-cli/sage_isolated_home")
        if idx != -1:
            return home[:idx]
    return os.path.realpath(os.path.expanduser(home))


SENSITIVE_TRIGGER_ENABLED = _safe_bool_multi(("AGY_SAGE_SENSITIVE_TRIGGER", "AGY_ADVISOR_SENSITIVE_TRIGGER", "AGY_STOP_AUDIT_SENSITIVE_TRIGGER"), True)

FILE_EDITING_TOOLS = {
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "edit_file", "create_file", "apply_diff", "patch", "modify_file",
    "write_file", "run_command", "bash", "exec", "terminal",
}

DEFAULT_TOOL_WEIGHTS = {
    "write_to_file": 2.0, "replace_file_content": 2.0, "multi_replace_file_content": 2.0,
    "edit_file": 2.0, "create_file": 2.0, "modify_file": 2.0, "write_file": 2.0,
    "apply_diff": 2.0, "patch": 2.0, "generate_image": 2.0,
    "run_command": 1.5, "bash": 1.5, "exec": 1.5, "terminal": 1.5,
    "invoke_subagent": 1.3, "send_message": 1.3, "manage_task": 1.3,
    "schedule": 1.3, "call_mcp_tool": 1.3,
    "view_file": 0.3, "grep_search": 0.3, "find_by_name": 0.3, "list_dir": 0.3,
    "read_url_content": 0.3, "search_web": 0.3, "list_resources": 0.3, "read_resource": 0.3,
}
DEFAULT_TOOL_WEIGHT_FALLBACK = 0.9


def get_tool_weight(tool_name):
    name = str(tool_name or "").lower().strip()
    return float(DEFAULT_TOOL_WEIGHTS.get(name, DEFAULT_TOOL_WEIGHT_FALLBACK))
