"""
sage.ladder - Verification-depth ladder for pins and steers.

A claim is not proven by its easiest rung. When a pinned goal or a steer in a
deep task (complex_code/multi_file) names only static or unit checks, this
module appends the next unproven rung (integration, then smoke) to the emitted
text so progress demands rise with depth instead of stopping at green unit
tests.
"""

import re

TIERS = (
    ("static", re.compile(r"\b(syntax|lint|ruff|typecheck|type[- ]check|tsc|mypy|compile)\b", re.I)),
    ("unit", re.compile(r"\b(unit ?tests?|pytest|unittest|vitest|jest|npm test|pnpm -F \S+ test)\b", re.I)),
    ("integration", re.compile(r"\b(integration ?tests?|--integration|cross-module)\b", re.I)),
    ("smoke", re.compile(r"\b(smoke|live run|live test|end-to-end|e2e)\b", re.I)),
)

_SUFFIX = {
    "none": "Escalate: name and run at least unit tests plus one integration test before moving on.",
    "static": "Escalate: static checks alone prove nothing runtime; run the unit suite, then an integration test.",
    "unit": "Escalate: unit-green is partial proof; run an integration test before declaring the leg proven.",
    "integration": "Escalate: finish with a smoke test against the real artifact before declaring done.",
}
_TIER_INDEX = {t: i for i, (t, _) in enumerate(TIERS)}


def deepest_tier(*texts) -> str:
    """Highest verification tier named across the given texts ('none' if none)."""
    joined = " ".join(str(t or "") for t in texts)
    best = "none"
    for tier, pat in TIERS:
        if pat.search(joined):
            best = tier
    return best


def _rank(tier: str) -> int:
    return _TIER_INDEX.get(tier, -1)


def next_rung_suffix(*texts) -> str:
    """Suffix demanding the next unproven rung, or '' when depth suffices."""
    best = max((_TIER_INDEX.get(deepest_tier(t), -1) for t in texts), default=-1)
    if best >= len(TIERS) - 1:
        return ""
    tier = TIERS[best][0] if best >= 0 else "none"
    return _SUFFIX[tier]
