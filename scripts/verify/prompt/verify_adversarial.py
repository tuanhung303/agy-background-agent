"""Strict evaluation oracle for controlled stop-verifier judgment fixtures."""
import json
from pathlib import Path


def load_cases(path=None):
    """Load cases, keeping expected decisions out of the model's input."""
    source = Path(path) if path else Path(__file__).with_name("cases.json")
    cases = json.loads(source.read_text())
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("Cases must be nonempty with unique ids")
    for case in cases:
        if case["expected"] not in ("PASS", "FAIL"):
            raise ValueError(f"Invalid oracle for {case['id']}")
        for field in ("request", "response", "evidence"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"Missing {field} for {case['id']}")
    return cases


def score_output(stdout, expected):
    """Reject malformed, empty, or wrong decisions without fail-open coercion."""
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError):
        return None, "invalid_json"
    if not isinstance(result, dict) or result.get("verdict") not in ("PASS", "FAIL"):
        return result, "invalid_verdict"
    if not all(isinstance(result.get(key), str) for key in ("action", "comment")):
        return result, "invalid_fields"
    proof = result.get("proof")
    if not isinstance(proof, list) or not all(isinstance(p, str) and p.strip() for p in proof):
        return result, "invalid_proof"
    if result["verdict"] == "PASS":
        if not proof or result["action"] or not result["comment"].strip():
            return result, "unsupported_pass"
    elif not result["action"].strip() or result["comment"] or proof:
        return result, "invalid_fail"
    if result["verdict"] != expected:
        return result, "false_rejection" if expected == "PASS" else "missed_failure"
    return result, "matched"
