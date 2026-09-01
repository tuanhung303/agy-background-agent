"""sage.lite.proof_validator - Empirical proof validation and pseudo-proof disqualification."""
import os
import re
from typing import Any, Dict, List, Optional, Tuple

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
    r"\bgit\s+push\b",
    r"\bcommit\s+[a-f0-9]{6,}\b",
    r"\blint(?:er|ing)?\b",
    r"\bsyntax\s+(?:is\s+)?valid\b",
    r"\bsyntax\s+check(?:ed)?\b",
    r"\b(?:xml|json|sql|html)\s+(?:is\s+)?valid\b",
    r"\blooks\s+good\b",
    r"\b(?:written|created)\s+(?:query|file|script)\b",
)

DEFERRAL_PATTERNS = (
    r"\buser\s+can\b",
    r"\b(?:test|verify|run|apply)\s+later\b",
    r"\bwill\s+(?:test|verify|run|apply)\b",
    r"\bplease\s+(?:run|verify|test|check|apply)\b",
    r"\bdefer(?:red|ring)?\b",
    r"\btodo\b",
    r"\bmanual(?:ly)?\s+verif\w*\b",
)

EMPIRICAL_INDICATORS = (
    r"\.(?:png|jpg|jpeg|webp|gif|svg|md|xlsx|pptx|docx|pdf|txt|csv|py|sh|ts|js|json|ya?ml|tf|tfplan|dockerfile)\b",
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
    r"\bexit\s+code\s+\d+\b",
    r"\bresponse\s+code\s+\d+\b",
    r"\bplan\b",
    r"\bartifact\b",
    r"\b(?:slide\s+\d+|sheet\s+\d+|row\s+\d+|rows?\s+returned|\d+\s+rows?|sbc/|internal/|inspected|referenced)\b",
    r"\b(?:terraform|docker|kubectl|helm|ansible|sandbox|dry-run)\b",
)

PATH_PATTERN = re.compile(r"(/[a-zA-Z0-9_\-\.\/]+\.[a-zA-Z0-9_]+)")


def _extract_file_paths(text: str) -> List[str]:
    """Extracts absolute file paths from text string."""
    return PATH_PATTERN.findall(text)


def validate_empirical_proof(
    proof: List[str],
    turn_provenance: Optional[Dict[str, Any]] = None,
    user_prompt: str = "",
) -> Tuple[bool, str]:
    """Validates that proof array contains genuine empirical evidence and not disqualified pseudo-proof or stale artifacts."""
    from sage.lite.gating import is_plan_or_qa_intent

    if not proof or not isinstance(proof, list) or not any(str(p or "").strip() for p in proof):
        return False, "Proof array is empty. Verifiable domain evidence is required for completion across all task types."

    prompt_to_check = user_prompt or (turn_provenance.get("true_user_prompt", "") if isinstance(turn_provenance, dict) else "")
    is_plan_qa = bool(prompt_to_check and is_plan_or_qa_intent(prompt_to_check))

    turn_start_time = 0.0
    written_files = set()
    generated_images = set()
    if isinstance(turn_provenance, dict):
        turn_start_time = float(turn_provenance.get("turn_start_time") or 0.0)
        written_files = set(turn_provenance.get("written_files") or [])
        generated_images = set(turn_provenance.get("generated_images") or [])

    valid_proofs = []
    stale_reasons = []

    for item in proof:
        text = str(item or "").strip()
        if not text:
            continue
        text_lower = text.lower()

        # Immediate rejection for any deferral or outsourced verification claim
        is_deferred = any(re.search(pat, text_lower, re.IGNORECASE) for pat in DEFERRAL_PATTERNS)
        if is_deferred:
            continue

        if is_plan_qa:
            # For plan / QA / grill-me / research turns, citations of concrete questions, options, files, or artifacts count as valid evidence
            if len(text) > 10 and not any(re.search(pat, text_lower, re.IGNORECASE) for pat in DISQUALIFIED_PATTERNS):
                valid_proofs.append(item)
            elif any(re.search(pat, text_lower, re.IGNORECASE) for pat in EMPIRICAL_INDICATORS):
                valid_proofs.append(item)
            continue

        has_empirical = any(re.search(pat, text_lower, re.IGNORECASE) for pat in EMPIRICAL_INDICATORS)
        is_disqualified = any(re.search(pat, text_lower, re.IGNORECASE) for pat in DISQUALIFIED_PATTERNS)

        # Purely disqualified item without empirical channel
        if is_disqualified and not re.search(r"\.(?:png|jpg|jpeg|webp|svg)|curl|http|/tmp/|sqlite", text_lower, re.IGNORECASE):
            continue

        # Check for stale recycled disk artifacts
        is_stale = False
        if turn_start_time > 0:
            extracted_paths = _extract_file_paths(text)
            for path in extracted_paths:
                if os.path.isabs(path) and os.path.isfile(path):
                    try:
                        mtime = os.path.getmtime(path)
                        # If file was modified before the turn started (with 2s tolerance) and wasn't written this turn
                        if mtime < (turn_start_time - 2.0):
                            base = os.path.basename(path)
                            if path not in written_files and base not in written_files and base not in generated_images:
                                is_stale = True
                                stale_reasons.append(f"Artifact '{base}' is stale (generated in a prior turn)")
                                break
                    except OSError:
                        pass

        if is_stale:
            continue

        if has_empirical and not (is_disqualified and not re.search(r"\.(?:png|jpg|jpeg|webp|svg)|curl|http", text_lower, re.IGNORECASE)):
            valid_proofs.append(item)
        elif not is_disqualified and len(text) > 15 and ("verified" in text_lower or "output" in text_lower or "ran " in text_lower):
            valid_proofs.append(item)

    if not valid_proofs:
        if stale_reasons:
            return False, f"Proof rejected: {'; '.join(stale_reasons)}. Only artifacts produced in the current turn are acceptable."
        return False, "Proof contains only disqualified items (unit tests, typecheck, build logs, git push) or lacks concrete empirical evidence"

    return True, ""

