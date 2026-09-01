"""
sage - Lite Mode Stop Verifier Package for Antigravity (AGY).
"""

__version__ = "0.3.0"

from sage.config import (
    DEFAULT_MODEL_FALLBACKS,
    FILE_EDITING_TOOLS,
    LITE_MAX_RETRIES,
    LITE_MODE_ENABLED,
    LITE_MODE_TIMEOUT,
    LOG_FILE,
    get_tool_weight,
)
from sage.executor import (
    CONV_DB_DIR,
    SAGE_CLI_DIR,
    SAGE_ISOLATED_HOME,
    ensure_isolated_home,
    extract_json_from_llm_output,
)
from sage.git import (
    get_git_diff,
    get_head_sha,
    resolve_workspace_root,
)
from sage.guards import (
    check_payload_and_lifecycle,
    emit_continue_response,
    emit_recap_response,
    fail_safe_exit,
    is_post_invocation,
    is_subagent_payload,
    is_subagent_session,
)
from sage.locking import (
    acquire_conversation_lock,
    atomic_write_json,
    cleanup_stale_tmp_files,
    log_audit,
    release_lock,
    safe_id,
)
from sage.models import (
    cache_working_model,
    get_available_models,
    get_cached_working_model,
    resolve_model_candidates,
)
from sage.sanitizer import (
    clean_user_prompt,
    sanitize_tool_output,
)
from sage.session_state import (
    load_and_sync_session_state,
    save_session_state,
)
from sage.transcript import (
    _read_transcript_steps,
    get_active_background_tasks,
    get_active_external_panes,
    get_active_subagents,
    get_active_turn_identity,
    get_transcript_path,
    has_active_background_tasks,
    has_active_subagents,
    is_post_invocation_completion_candidate,
)
from sage.lite.runner import run_lite_stop_audit

__all__ = [
    "DEFAULT_MODEL_FALLBACKS",
    "FILE_EDITING_TOOLS",
    "LITE_MAX_RETRIES",
    "LITE_MODE_ENABLED",
    "LITE_MODE_TIMEOUT",
    "LOG_FILE",
    "get_tool_weight",
    "CONV_DB_DIR",
    "SAGE_CLI_DIR",
    "SAGE_ISOLATED_HOME",
    "ensure_isolated_home",
    "extract_json_from_llm_output",
    "get_git_diff",
    "get_head_sha",
    "resolve_workspace_root",
    "check_payload_and_lifecycle",
    "emit_continue_response",
    "emit_recap_response",
    "fail_safe_exit",
    "is_post_invocation",
    "is_subagent_payload",
    "is_subagent_session",
    "acquire_conversation_lock",
    "atomic_write_json",
    "cleanup_stale_tmp_files",
    "log_audit",
    "release_lock",
    "safe_id",
    "cache_working_model",
    "get_available_models",
    "get_cached_working_model",
    "resolve_model_candidates",
    "clean_user_prompt",
    "sanitize_tool_output",
    "load_and_sync_session_state",
    "save_session_state",
    "_read_transcript_steps",
    "get_active_background_tasks",
    "get_active_external_panes",
    "get_active_subagents",
    "get_active_turn_identity",
    "get_transcript_path",
    "has_active_background_tasks",
    "has_active_subagents",
    "is_post_invocation_completion_candidate",
    "run_lite_stop_audit",
]
