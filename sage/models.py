"""
sage.models - Dynamic model discovery, version cascade, and runtime fallbacks.
"""

import os
import re
import shutil
import subprocess
import time

from sage.config import DEFAULT_MODEL_FALLBACKS
from sage.locking import log_audit

_MODEL_CACHE = {"models": [], "timestamp": 0.0}
_WORKING_MODEL = {"model": None}
CACHE_TTL = 3600.0


_WORKING_MODEL_FILE = "/tmp/agy_sage_working_model.txt"
_LEGACY_WORKING_MODEL_FILE = "/tmp/agy_advisor_working_model.txt"


def parse_model_version(name):
    """Extracts ((major, minor), tier_rank, effort_rank) tuple from model string."""
    if not name or not isinstance(name, str):
        return ((0, 0), 0, 0)
    low = name.lower()
    m = re.search(r"(\d+)(?:\.(\d+))?", low)
    major, minor = (int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 0) if m else (0, 0)
    tier = 2 if "flash" in low else (1 if ("pro" in low or "opus" in low or "sonnet" in low) else 0)
    effort = 3 if "high" in low else (2 if "medium" in low else (1 if "low" in low else 0))
    return ((major, minor), tier, effort)


def model_sort_key(name):
    """Sorting key for models (highest version, tier, effort first)."""
    return parse_model_version(name)


def get_available_models(refresh=False):
    """Queries agy CLI for available models with TTL caching and fallback."""
    global _MODEL_CACHE
    now = time.time()
    if not refresh and _MODEL_CACHE["models"] and (now - _MODEL_CACHE["timestamp"] < CACHE_TTL):
        return list(_MODEL_CACHE["models"])
    models = []
    agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    try:
        env = dict(os.environ, HOME=os.path.expanduser("~"))
        env["PATH"] = f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}"
        res = subprocess.run([agy_bin, "models"], input="", capture_output=True, text=True, timeout=5, env=env)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("fetching") or line.startswith("#"):
                    continue
                parts = line.split("\t")
                name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                if name and name not in models:
                    models.append(name)
    except Exception as e:
        log_audit(f"Failed to query available models: {e}")
    if not models:
        models = list(DEFAULT_MODEL_FALLBACKS)
    _MODEL_CACHE["models"] = list(models)
    _MODEL_CACHE["timestamp"] = now
    return list(models)


def cache_working_model(model):
    """Caches the most recently verified working model name to memory and file."""
    if model and isinstance(model, str):
        cleaned = model.strip()
        _WORKING_MODEL["model"] = cleaned
        try:
            with open(_WORKING_MODEL_FILE, "w", encoding="utf-8") as f:
                f.write(cleaned)
        except Exception:
            pass
    else:
        _WORKING_MODEL["model"] = None
        for p in (_WORKING_MODEL_FILE, _LEGACY_WORKING_MODEL_FILE):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def get_cached_working_model():
    """Returns cached working model if any (checking memory then file)."""
    if _WORKING_MODEL.get("model"):
        return _WORKING_MODEL["model"]
    for p in (_WORKING_MODEL_FILE, _LEGACY_WORKING_MODEL_FILE):
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                    if val:
                        _WORKING_MODEL["model"] = val
                        return val
        except Exception:
            pass
    return None


def _expand_alias(alias, available, effort):
    """Expands a single alias token into an ordered list of candidate models."""
    low_alias = alias.lower()
    sorted_avail = sorted(available, key=model_sort_key, reverse=True)
    if low_alias in ("auto", "latest", "default"):
        target_effort = (effort or "high").lower()
        exact = [m for m in sorted_avail if "flash" in m.lower() and target_effort in m.lower()]
        other_flash = [m for m in sorted_avail if "flash" in m.lower() and m not in exact]
        other_models = [m for m in sorted_avail if m not in exact and m not in other_flash]
        return exact + other_flash + other_models
    if low_alias == "flash-high":
        return [m for m in sorted_avail if "flash" in m.lower() and "high" in m.lower()] + [m for m in sorted_avail if "flash" in m.lower()]
    if low_alias == "flash-medium":
        return [m for m in sorted_avail if "flash" in m.lower() and "medium" in m.lower()] + [m for m in sorted_avail if "flash" in m.lower()]
    if low_alias == "flash-low":
        return [m for m in sorted_avail if "flash" in m.lower() and "low" in m.lower()] + [m for m in sorted_avail if "flash" in m.lower()]
    if low_alias == "pro":
        return [m for m in sorted_avail if "pro" in m.lower()] + sorted_avail
    return [alias]


def _retier_model(name, effort):
    """Swap the effort suffix of a concrete model name to the requested tier.

    agy bakes reasoning effort into the model name ("... (High)"); a concrete
    spec like "Gemini 3.7 Flash (High)" therefore ignores any requested effort.
    Retier it so a routine call asking for medium resolves "(Medium)" first.
    Unknown/absent effort leaves the name untouched.
    """
    if not effort:
        return name
    low = str(effort).strip().lower()
    if low not in ("low", "medium", "high"):
        return name
    return re.sub(r"\((?:high|medium|low)\)\s*$", f"({low.capitalize()})", str(name).strip(), flags=re.I)


def resolve_model_candidates(spec=None, effort=None, max_candidates=4):
    """Resolves model spec, aliases, and fallback chain capped to max_candidates."""
    if spec is None:
        from sage.config import REVIEWER_MODEL_SPEC
        spec = os.environ.get("AGY_SAGE_MODEL") or os.environ.get("AGY_ADVISOR_MODEL") or REVIEWER_MODEL_SPEC
    if effort is None:
        effort = os.environ.get("AGY_SAGE_EFFORT") or os.environ.get("AGY_ADVISOR_EFFORT", "high")
    tokens = [t.strip() for t in str(spec).split(",") if t.strip()] or ["auto"]
    available = get_available_models()
    expanded = []
    aliases = {"auto", "latest", "default", "flash-high", "flash-medium", "flash-low", "pro"}
    is_auto = len(tokens) == 1 and tokens[0].lower() in ("auto", "latest", "default")
    for tok in tokens:
        if tok.lower() in aliases:
            expanded.extend(_expand_alias(tok, available, effort))
        else:
            expanded.append(_retier_model(tok, effort))
    if is_auto or not expanded:
        expanded.extend(DEFAULT_MODEL_FALLBACKS)
    else:
        expanded.extend(m for m in DEFAULT_MODEL_FALLBACKS if m not in expanded)
    candidates, seen = [], set()
    cached = get_cached_working_model()
    if is_auto and cached and cached in expanded:
        candidates.append(cached)
        seen.add(cached)
    for m in expanded:
        if m and m not in seen:
            seen.add(m)
            candidates.append(m)
            if max_candidates and len(candidates) >= max_candidates:
                break
    return candidates
