"""
sage.models - Dynamic model discovery, version cascade, and runtime fallbacks.
"""

import json
import os
import re
import shutil
import subprocess
import time

from sage.config import DEFAULT_MODEL_FALLBACKS, SAGE_MODELS_DISCOVERY_TIMEOUT
from sage.locking import log_audit

_MODEL_CACHE = {"models": [], "timestamp": 0.0}
_WORKING_MODEL = {"model": None}
CACHE_TTL = 3600.0

_MODEL_CACHE_FILE = "/tmp/agy_available_models.json"
_WORKING_MODEL_FILE = "/tmp/agy_sage_working_model.txt"
_LEGACY_WORKING_MODEL_FILE = "/tmp/agy_advisor_working_model.txt"

KNOWN_ALIASES = {
    "auto", "latest", "default", "flash", "gemini-flash", "flash-high", "flash-hi",
    "flash-medium", "flash-med", "flash-low", "flash-lo", "flash-lite", "flash-light",
    "pro", "gemini-pro", "pro-high", "pro-hi", "pro-medium", "pro-med", "pro-low", "pro-lo",
}


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
    """Queries agy CLI for available models with persistent disk cache and fallback."""
    global _MODEL_CACHE
    now = time.time()
    if not refresh and _MODEL_CACHE["models"] and (now - _MODEL_CACHE["timestamp"] < CACHE_TTL):
        return list(_MODEL_CACHE["models"])
    if not refresh and os.path.exists(_MODEL_CACHE_FILE):
        try:
            with open(_MODEL_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("models") and (now - data.get("timestamp", 0) < CACHE_TTL):
                _MODEL_CACHE["models"] = list(data["models"])
                _MODEL_CACHE["timestamp"] = data.get("timestamp", now)
                return list(data["models"])
        except Exception:
            pass
    models = []
    agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    try:
        env = dict(os.environ, HOME=os.path.expanduser("~"))
        env["PATH"] = f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}"
        res = subprocess.run([agy_bin, "models"], input="", capture_output=True, text=True, timeout=SAGE_MODELS_DISCOVERY_TIMEOUT, env=env)
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
    try:
        with open(_MODEL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"models": models, "timestamp": now}, f)
    except Exception:
        pass
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


def _find_matching_model(name, available):
    """Finds a matching model name in available models via exact, case-insensitive, or slug match."""
    if not name or not isinstance(name, str):
        return None
    raw = name.strip()
    if not raw:
        return None
    for m in available:
        if m == raw:
            return m
    raw_low, raw_slug = raw.lower(), re.sub(r"[^a-z0-9]", "", raw.lower())
    for m in available:
        if m.lower() == raw_low or (raw_slug and re.sub(r"[^a-z0-9]", "", m.lower()) == raw_slug):
            return m
    return None


def _retier_model(name, effort):
    """Swap the effort suffix of a concrete model name to the requested tier."""
    if not effort:
        return name
    low = str(effort).strip().lower()
    if low not in ("low", "medium", "high"):
        return name
    return re.sub(r"\((?:high|medium|low)\)\s*$", f"({low.capitalize()})", str(name).strip(), flags=re.I)


def _expand_alias(alias, available, effort=None):
    """Expands a single alias token into an ordered list of candidate models."""
    if not alias or not isinstance(alias, str):
        return []
    low = alias.strip().lower().replace("_", "-")
    sorted_avail = sorted(available or DEFAULT_MODEL_FALLBACKS, key=model_sort_key, reverse=True)
    target_eff = {"med": "medium", "hi": "high", "lo": "low"}.get(effort, effort or "high").lower()
    tier = "pro" if ("pro" in low and "flash" not in low) else "flash"
    for eff in ("high", "medium", "low"):
        if eff in low or (eff == "medium" and "med" in low) or (eff == "high" and "hi" in low) or (eff == "low" and "lo" in low):
            target_eff = eff
            break

    if low in KNOWN_ALIASES or tier in low:
        exact = [m for m in sorted_avail if tier in m.lower() and target_eff in m.lower()]
        other_tier = [m for m in sorted_avail if tier in m.lower() and m not in exact]
        other_all = [m for m in sorted_avail if m not in exact and m not in other_tier]
        return (exact + other_tier + other_all) or list(DEFAULT_MODEL_FALLBACKS)

    matched = _find_matching_model(alias, sorted_avail)
    if matched:
        return [_retier_model(matched, effort)]
    if parse_model_version(alias) != ((0, 0), 0, 0) or "(" in alias:
        return [_retier_model(alias, effort)]
    exact = [m for m in sorted_avail if target_eff in m.lower()]
    return (exact + [m for m in sorted_avail if m not in exact]) or list(DEFAULT_MODEL_FALLBACKS)


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
    is_auto = len(tokens) == 1 and tokens[0].lower().replace("_", "-") in ("auto", "latest", "default")
    for tok in tokens:
        clean_tok = tok.lower().replace("_", "-")
        if clean_tok in KNOWN_ALIASES:
            expanded.extend(_expand_alias(tok, available, effort))
        else:
            matched = _find_matching_model(tok, available)
            if matched:
                expanded.append(_retier_model(matched, effort))
            else:
                ver = parse_model_version(tok)
                if ver != ((0, 0), 0, 0) or "(" in tok:
                    expanded.append(_retier_model(tok, effort))
                else:
                    expanded.extend(_expand_alias(tok, available, effort))
    if is_auto or not expanded:
        expanded.extend(DEFAULT_MODEL_FALLBACKS)
    else:
        expanded.extend(_retier_model(m, effort) for m in DEFAULT_MODEL_FALLBACKS if m not in expanded)
    candidates, seen = [], set()
    cached = get_cached_working_model()
    if is_auto and cached and cached in expanded:
        candidates.append(cached)
        seen.add(cached)
    for m in expanded:
        if not m or m in seen or m.strip().lower().replace("_", "-") in KNOWN_ALIASES:
            continue
        seen.add(m)
        candidates.append(m)
        if max_candidates and len(candidates) >= max_candidates:
            break
    if not candidates:
        for m in DEFAULT_MODEL_FALLBACKS:
            ret = _retier_model(m, effort)
            if ret not in seen:
                seen.add(ret)
                candidates.append(ret)
                if max_candidates and len(candidates) >= max_candidates:
                    break
    return candidates
