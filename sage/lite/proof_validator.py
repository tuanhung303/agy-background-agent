"""Deterministic proof integrity checks; the verifier model judges evidentiary sufficiency.

A successful check means no structural or local artifact contradiction was found.
It does not certify a claim through keywords, filenames, or a model's self-report.
"""
import os
import re
from typing import List, Tuple

# Ignore URL paths; quoted absolute paths may contain spaces.
PATH_PATTERN = re.compile(r"[`\"'](/[^`\"'\n]+)[`\"']|(?<![\w:/])(/[^\s`\"'<>]+)")
MEDIA_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tiff")


def _extract_file_paths(text: str) -> List[str]:
    """Extract local absolute paths without mistaking URL components for files."""
    return [next(part for part in match if part).rstrip(".,;:)]}") for match in PATH_PATTERN.findall(text)]


def validate_empirical_proof(proof: List[str]) -> Tuple[bool, str]:
    """Check structure and definite local media contradictions, not semantic sufficiency."""
    if not isinstance(proof, list) or not proof:
        return False, "Proof array is empty."
    if not all(isinstance(item, str) and item.strip() for item in proof):
        return False, "Proof contains a non-string or empty item."
    for item in proof:
        for path in _extract_file_paths(item):
            if not path.lower().endswith(MEDIA_EXTENSIONS):
                continue
            try:
                if not os.path.isfile(path) or os.path.getsize(path) == 0:
                    return False, f"Cited local media '{path}' is missing or empty."
            except OSError as exc:
                return False, f"Cited local media '{path}' could not be inspected: {exc}"
    return True, ""
