#!/usr/bin/env python3
"""
statusline.statusline - Renders Antigravity statusline with accurate context window limits.
"""
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

DEFAULT_EFFECTIVE_MAX_CTX = 250_000


def format_tokens(num):
    if not num:
        return "0"
    if num >= 1_000_000:
        return f"{round(num / 1_000_000)}M"
    if num >= 1_000:
        return f"{round(num / 1_000)}k"
    return str(num)


def format_countdown(seconds):
    if seconds is None or seconds <= 0:
        return ""
    if seconds >= 86400:
        d = (int(seconds) + 86399) // 86400
        return f"[{d}d]"
    if seconds >= 3600:
        h = (int(seconds) + 3599) // 3600
        return f"[{h}h]"
    m = (int(seconds) + 59) // 60
    return f"[{m}m]"


def calculate_seconds_left(reset_time_str, fallback_seconds=0):
    if reset_time_str:
        try:
            target_dt = datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            return max(0, int((target_dt - now_dt).total_seconds()))
        except Exception:
            pass
    return max(0, int(fallback_seconds)) if fallback_seconds else 0


def visible_len(s):
    return len(re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", s))


def clean_model_name(name):
    if not name:
        return "agy"
    n = str(name)
    for old, new in [
        ("Gemini ", ""),
        ("gemini-", ""),
        (" (High)", " [h]"),
        (" (Medium)", " [m]"),
        (" (Low)", " [l]"),
        ("-high", " [h]"),
        ("-medium", " [m]"),
        ("-low", " [l]"),
        ("-hi", " [h]"),
        ("-med", " [m]"),
        ("-lo", " [l]"),
    ]:
        n = n.replace(old, new)
    return n.lower()


def is_agent_active(agent):
    if isinstance(agent, dict):
        if agent.get("alive") is False or agent.get("is_active") is False or agent.get("active") is False:
            return False
        if agent.get("exit_code") is not None and agent.get("exit_code") != 0:
            return False
        state = str(agent.get("state") or agent.get("status") or "").strip().lower()
        if state in {
            "completed", "done", "finished", "stopped", "killed",
            "dead", "errored", "failed", "canceled", "cancelled",
            "closed", "terminated", "idle",
        }:
            return False
        if state in {
            "running", "active", "working", "busy", "pending",
            "waiting_for_input", "waiting_for_dependents", "waiting_for_message", "in_progress",
        }:
            return True
        if agent.get("alive") is True or agent.get("is_active") is True or agent.get("active") is True:
            return True
        if agent.get("end_time") or agent.get("completed_at") or agent.get("finished_at"):
            return False
        return state not in {"", "unspecified"}
    elif isinstance(agent, str):
        return True
    return False


def get_checkpoint_count(data):
    if data.get("checkpoint_count") is not None:
        return int(data["checkpoint_count"])
    if data.get("compaction_count") is not None:
        return int(data["compaction_count"])

    tp = data.get("transcript_path") or data.get("transcriptPath")
    conv_id = data.get("conversation_id") or data.get("session_id")
    if not tp or not os.path.exists(tp):
        if conv_id:
            for base in ["~/.gemini/antigravity-cli/brain", "~/.gemini/antigravity/brain"]:
                candidate = os.path.expanduser(f"{base}/{conv_id}/.system_generated/logs/transcript.jsonl")
                if os.path.exists(candidate):
                    tp = candidate
                    break

    if not tp or not os.path.exists(tp):
        return 0

    count = 0
    try:
        with open(tp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"CHECKPOINT"' in line or '"type": "CHECKPOINT"' in line:
                    count += 1
    except Exception:
        pass
    return count


def get_fallback_model():
    try:
        settings_path = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
        if os.path.exists(settings_path):
            with open(settings_path) as f:
                cfg = json.load(f)
                return cfg.get("model", "Gemini 3.7 Flash (High)")
    except Exception:
        pass
    return "3.7 flash [h]"


def get_effective_max_context():
    env_val = os.environ.get("AGY_MAX_CONTEXT_TOKENS")
    if env_val:
        try:
            val = int(env_val)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return DEFAULT_EFFECTIVE_MAX_CTX


def safe_id(val):
    val_str = str(val) if val is not None else ""
    return f"{re.sub(r'[^a-zA-Z0-9_-]', '_', val_str)[:32]}_{hashlib.sha256(val_str.encode('utf-8')).hexdigest()[:8]}"


def get_sage_steer_badges(data):
    conv_id = (
        data.get("conversation_id")
        or data.get("session_id")
        or data.get("conversationId")
        or data.get("sessionId")
    )
    state = {}
    if conv_id:
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        legacy_file = f"/tmp/agy_advisor_{safe_id(conv_id)}.json"
        target_file = state_file if os.path.exists(state_file) else (legacy_file if os.path.exists(legacy_file) else None)
        if target_file:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}

    goal_c = 1 if (state.get("pinned_goal") or state.get("anchor_goal")) else 0
    sage_f = state.get("session_mid_turn_steers", state.get("mid_turn_steers", 0))
    sage_h = state.get("sage_holds", state.get("advisor_holds", 0))

    t_recap = 0
    tp = data.get("transcript_path") or data.get("transcriptPath")
    if not tp or not os.path.exists(tp):
        if conv_id:
            for base in ["~/.gemini/antigravity-cli/brain", "~/.gemini/antigravity/brain"]:
                cand = os.path.expanduser(f"{base}/{conv_id}/.system_generated/logs/transcript.jsonl")
                if os.path.exists(cand):
                    tp = cand
                    break
    if tp and os.path.exists(tp):
        try:
            with open(tp, "r", encoding="utf-8", errors="replace") as tf:
                for line in tf:
                    if '"type":"USER_INPUT"' in line or '"type": "USER_INPUT"' in line:
                        line_low = line.lower()
                        if '※ recap:' in line_low or '※ recap' in line_low or 'recap - ' in line_low or '[recap]' in line_low or '[recap·' in line_low:
                            t_recap += 1
        except Exception:
            pass

    recap_c = max(state.get("recap_count", 1 if state.get("recap_emitted") else 0), t_recap)

    sage_status = state.get("sage_status", state.get("advisor_status", "hold"))
    is_sage_running = sage_status in {"evaluating", "running"}

    # Sage badge: sage:g[G]/a[A]/p[P]/r[R][/err[N]] — goal pinned, advise fired, pass (healthy hold), recap
    # Badge metrics stay muted (\033[90m) like startup; 'sage:' lights up (\033[1;34m) only during active events.
    sage_label_color = "\033[1;34m" if is_sage_running else "\033[90m"
    err_streak = int(state.get("sage_error_streak", state.get("advisor_error_streak", 0)) or 0)
    err_seg = f"\033[31m/err[{err_streak}]\033[0m" if err_streak > 0 else ""
    sage_badge = (
        f"{sage_label_color}sage:\033[0m\033[90mg[{goal_c}]/a[{sage_f}]/p[{sage_h}]/r[{recap_c}]\033[0m{err_seg}"
    )

    return [sage_badge]


get_advisor_steer_badges = get_sage_steer_badges


def render_statusline(data):
    # 1. Model Info (Left)
    raw_model = ""
    if isinstance(data.get("model"), dict):
        raw_model = (
            data["model"].get("display_name")
            or data["model"].get("id")
            or data["model"].get("modelName")
            or data["model"].get("name")
        )
    elif isinstance(data.get("model"), str):
        raw_model = data["model"]

    if not raw_model:
        raw_model = (
            data.get("modelName")
            or data.get("model_name")
            or data.get("modelId")
            or get_fallback_model()
        )

    model_display = clean_model_name(raw_model)

    # 2. Active Subagents (Left)
    left_segments = [f"\033[1;34m{model_display}\033[0m"]
    subagents = data.get("subagents") or data.get("agents") or []
    if isinstance(subagents, list):
        active_agents = [a for a in subagents if is_agent_active(a)]
    else:
        active_agents = []
    active_count = len(active_agents)
    if active_count > 0:
        left_segments.append(f"\033[1;35magents:{active_count}\033[0m")

    # 3. Context Window (Right) & Checkpoint Count
    cw = data.get("context_window") if isinstance(data.get("context_window"), dict) else (data.get("context") if isinstance(data.get("context"), dict) else {})
    cur_usage = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else {}

    # The effective compaction limit for agy CLI runtime is 250k tokens.
    max_ctx = get_effective_max_context()

    if cur_usage:
        cur_ctx = (
            cur_usage.get("input_tokens", 0)
            + cur_usage.get("cache_read_input_tokens", 0)
            + cur_usage.get("cache_creation_input_tokens", 0)
            + cur_usage.get("output_tokens", 0)
        )
    elif cw.get("used_percentage") is not None and cw.get("context_window_size"):
        reported_max = cw.get("context_window_size", 1_048_576)
        cur_ctx = int(reported_max * (cw.get("used_percentage") / 100.0))
    elif cw.get("total_input_tokens") is not None:
        cur_ctx = cw.get("total_input_tokens", 0)
    elif cw.get("input_tokens") is not None:
        cur_ctx = cw.get("input_tokens", 0) + cw.get("output_tokens", 0)
    else:
        cur_ctx = cw.get("total_input_tokens", 0)

    ck_count = get_checkpoint_count(data)
    ck_str = f"\033[90m[\033[0m\033[35m{ck_count}\033[0m\033[90m]\033[0m" if ck_count > 0 else ""

    ctx_pct = (cur_ctx / max_ctx * 100) if max_ctx > 0 else 0
    ctx_color = "\033[36m" if ctx_pct < 70 else "\033[33m" if ctx_pct < 85 else "\033[31m"
    ctx_str = f"\033[90mctx:\033[0m{ctx_color}{format_tokens(cur_ctx)}/{format_tokens(max_ctx)}\033[0m{ck_str}"

    # 4. 5h Quota % and Countdown (Right)
    quota_data = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    used_pct_5h = 0.0
    reset_seconds_5h = 0

    for qk in ["gemini-5h", "3p-5h", "5h"]:
        if qk in quota_data and isinstance(quota_data[qk], dict):
            rem_frac = quota_data[qk].get("remaining_fraction", 1.0)
            used_pct_5h = (1.0 - rem_frac) * 100
            reset_seconds_5h = calculate_seconds_left(
                quota_data[qk].get("reset_time"),
                quota_data[qk].get("reset_in_seconds", 0),
            )
            break

    quota_color_5h = "\033[32m" if used_pct_5h < 70 else "\033[33m" if used_pct_5h < 90 else "\033[31m"
    countdown_5h = format_countdown(reset_seconds_5h)
    if countdown_5h:
        quota_5h_str = f"{quota_color_5h}{used_pct_5h:.0f}%\033[0m\033[90m{countdown_5h}\033[0m"
    else:
        quota_5h_str = f"{quota_color_5h}{used_pct_5h:.0f}%\033[0m"

    # 5. Weekly Quota % and Countdown (Right)
    quota_weekly_str = ""
    for wk in ["gemini-weekly", "3p-weekly", "weekly"]:
        if wk in quota_data and isinstance(quota_data[wk], dict):
            rem_frac_w = quota_data[wk].get("remaining_fraction", 1.0)
            used_pct_w = (1.0 - rem_frac_w) * 100
            reset_seconds_w = calculate_seconds_left(
                quota_data[wk].get("reset_time"),
                quota_data[wk].get("reset_in_seconds", 0),
            )
            countdown_w = format_countdown(reset_seconds_w)
            color_w = "\033[32m" if used_pct_w < 70 else "\033[33m" if used_pct_w < 90 else "\033[31m"
            if countdown_w:
                quota_weekly_str = f"\033[90mW:\033[0m{color_w}{used_pct_w:.0f}%\033[0m\033[90m{countdown_w}\033[0m"
            else:
                quota_weekly_str = f"\033[90mW:\033[0m{color_w}{used_pct_w:.0f}%\033[0m"
            break

    (adv_badge,) = get_advisor_steer_badges(data)
    right_segments = [adv_badge, ctx_str, quota_5h_str]
    if quota_weekly_str:
        right_segments.append(quota_weekly_str)

    # Terminal Width & Padding
    term_width = (
        data.get("terminal_width")
        or data.get("terminalWidth")
        or int(os.environ.get("COLUMNS", 0))
        or shutil.get_terminal_size((80, 20)).columns
    )

    left_str = " | ".join(left_segments)
    right_str = " | ".join(right_segments)

    padding = max(1, term_width - visible_len(left_str) - visible_len(right_str) - 1)
    return f"{left_str}{' ' * padding}{right_str}"


def main():
    try:
        raw = sys.stdin.read()
        if os.environ.get("AGY_STATUSLINE_DEBUG"):
            try:
                with open("/tmp/agy_statusline_dump.json", "w") as df:
                    df.write(raw)
            except Exception:
                pass

        data = {}
        if raw.strip():
            try:
                data = json.loads(raw)
            except Exception:
                data = {}

        output = render_statusline(data)
        sys.stdout.write(output)
        sys.stdout.flush()
    except Exception:
        sys.stdout.write("\033[1;34m3.7 flash [h]\033[0m")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
