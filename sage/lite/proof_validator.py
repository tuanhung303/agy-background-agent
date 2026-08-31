"""sage.lite.proof_validator - Empirical proof validation and pseudo-proof disqualification."""
import re
from typing import List, Tuple

DISQUALIFIED_PATTERNS = (
    r"\b\d+/\d+\s+pre-push\b",
    r"\bpre-push\b",
    r"\b\d+/\d+\s+tests?\b",
    r"\b\d+/\d+\s+pass(?:ed)?\b",
    r"\bunit\s+tests?\b",
    r"\btype\s*check(?:ing)?\b",
    r"\btsc\b",
    r"\bvite\s+build\b",
    r"\bnpm\s+(?:run\s+)?build\b",
    r"\bwebpack\b",
    r"\bgit\s+push\b",
    r"\bcommit\s+[a-f0-9]{6,}\b",
    r"\blint(?:er|ing)?\b",
)

EMPIRICAL_INDICATORS = (
    r"\.(?:png|jpg|jpeg|webp|gif|svg|md|xlsx|pptx|docx|pdf|txt|csv|py|sh|ts|js|json|ya?ml)\b",
    r"\bscreenshot\b",
    r"\bimage\b",
    r"\bbrowser\b",
    r"\bdom\b",
    r"\bcurl\b",
    r"\bhttp://\b",
    r"\bhttps://\b",
    r"\blocalhost\b",
    r"\b127\.0\.0\.1\b",
    r"\bsqlite(?:3)?\b",
    r"\bquery\b",
    r"\bdatabase\b",
    r"\b/tmp/\b",
    r"\bstdout\b",
    r"\boutput:\b",
    r"\bexit\s+code\s+0\b",
    r"\bresponse\s+code\s+200\b",
    r"\bplan\b",
    r"\bartifact\b",
    r"\b(?:slide|sheet|row|sbc/|internal/|inspected|referenced)\b",
)


def validate_empirical_proof(proof: List[str]) -> Tuple[bool, str]:
    """Validates that proof array contains genuine empirical evidence and not disqualified pseudo-proof."""
    if not proof or not isinstance(proof, list):
        return False, "Proof array is empty. At least one empirical verification channel (screenshot, browser check, or live runtime execution) is required"

    valid_proofs = []
    for item in proof:
        text = str(item or "").strip().lower()
        if not text:
            continue
        has_empirical = any(re.search(pat, text, re.IGNORECASE) for pat in EMPIRICAL_INDICATORS)
        is_disqualified = any(re.search(pat, text, re.IGNORECASE) for pat in DISQUALIFIED_PATTERNS)
        if has_empirical and not (is_disqualified and not re.search(r"\.(?:png|jpg|jpeg|webp|svg)|curl|http", text, re.IGNORECASE)):
            valid_proofs.append(item)
        elif not is_disqualified and len(text) > 15 and ("verified" in text or "output" in text or "ran " in text):
            valid_proofs.append(item)

    if not valid_proofs:
        return False, "Proof contains only disqualified items (unit tests, typecheck, build logs, git push) or lacks concrete empirical evidence"

    return True, ""
