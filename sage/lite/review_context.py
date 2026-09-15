"""sage.lite.review_context - Adapter for loading, validating, and formatting lead-owned review records."""
import hashlib, json, os
from typing import Any, Dict, List, Optional, Tuple
from sage.config import LITE_MAX_RECORD_BYTES, LITE_REVIEW_RECORD_PATH

VALID_FINDING_STATES = {"proposed", "confirmed", "fix_reported", "verified", "rejected", "withdrawn", "blocked"}
VALID_SEVERITIES = {"critical", "major", "minor", "low"}
RESOLVED_FINDING_STATES = {"verified", "rejected", "withdrawn"}


def resolve_review_record_path(workspace_root: Optional[str] = None, explicit_path: Optional[str] = None, allow_workspace_discovery: bool = True) -> Optional[str]:
    """Resolves review record path from explicit arg, env, or workspace standard path."""
    candidate = explicit_path or LITE_REVIEW_RECORD_PATH
    if candidate:
        expanded = os.path.realpath(os.path.expanduser(candidate))
        return expanded if os.path.exists(expanded) else candidate
    if allow_workspace_discovery and workspace_root and os.path.isdir(workspace_root):
        standard = os.path.join(workspace_root, "review-state.json")
        if os.path.exists(standard):
            return standard
    return None


def load_review_record(record_path: str, max_bytes: int = LITE_MAX_RECORD_BYTES) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Loads and structurally validates a version-1 review record JSON file."""
    if not record_path or not os.path.exists(record_path):
        return None, f"Review record not found at {record_path}"
    try:
        size = os.path.getsize(record_path)
        if size > max_bytes:
            return None, f"Review record exceeds max allowed size ({size} bytes > {max_bytes} bytes)"
        with open(record_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, "Review record root must be a JSON object"
        if data.get("schema_version") not in (1, "1", "1.0"):
            return None, f"Unsupported schema_version: {data.get('schema_version')} (expected 1)"
        if not data.get("session_id") or not isinstance(data["session_id"], str):
            return None, "Review record requires a non-empty string session_id"
        return data, None
    except Exception as exc:
        return None, f"Failed to read/parse review record: {exc}"


def validate_review_record_scope(record: Dict[str, Any], expected_workspace: Optional[str] = None, expected_assignment: Optional[str] = None) -> List[str]:
    """Validates workspace and assignment scope consistency."""
    errors = []
    if expected_workspace and record.get("workspace"):
        rec_ws = os.path.realpath(os.path.expanduser(str(record["workspace"])))
        exp_ws = os.path.realpath(os.path.expanduser(expected_workspace))
        if rec_ws != exp_ws:
            if not (exp_ws.startswith(rec_ws.rstrip(os.sep) + os.sep) or rec_ws.startswith(exp_ws.rstrip(os.sep) + os.sep)):
                errors.append(f"Workspace mismatch: record specifies '{record.get('workspace')}', expected '{expected_workspace}'")
    scope_type = record.get("scope_type", "lead")
    if scope_type not in ("lead", "assignment"):
        errors.append(f"Invalid scope_type '{scope_type}'; must be 'lead' or 'assignment'")
    rec_assignment = record.get("assignment_id")
    if scope_type == "assignment" and not rec_assignment:
        errors.append("Assignment-scoped review record requires a non-empty string assignment_id")
    if expected_assignment:
        if not rec_assignment:
            errors.append(f"Assignment ID missing: expected '{expected_assignment}', but record specifies none")
        elif rec_assignment != expected_assignment:
            errors.append(f"Assignment ID mismatch: record specifies '{rec_assignment}', expected '{expected_assignment}'")
    return errors


def inspect_findings_and_requirements(record: Dict[str, Any]) -> Dict[str, Any]:
    """Audits findings, requirements, obligations, and evidence integrity."""
    findings, reqs = record.get("findings", []), record.get("requirements", [])
    obligations, cov_gaps = record.get("review_obligations", []), record.get("coverage_gaps", [])
    open_f, res_f, malformed_f, missing_ev = [], [], [], []

    for f in findings:
        if not isinstance(f, dict):
            malformed_f.append(f"Invalid finding entry: {f}")
            continue
        fid, state = f.get("id") or "UNKNOWN_ID", f.get("state", "proposed")
        if state not in VALID_FINDING_STATES:
            malformed_f.append(f"Finding {fid} has invalid state '{state}'")
        if state == "verified":
            if not f.get("verification_evidence"):
                missing_ev.append(f"Finding {fid} is marked 'verified' without verification_evidence")
            res_f.append(f)
        elif state in RESOLVED_FINDING_STATES:
            if not f.get("disposition_reason") and state in ("rejected", "withdrawn"):
                missing_ev.append(f"Finding {fid} is '{state}' without disposition_reason")
            res_f.append(f)
        else:
            open_f.append(f)

    unmet_reqs = [r for r in reqs if isinstance(r, dict) and r.get("status", "pending") != "verified"]
    clean_obs = [str(x) for x in obligations if str(x).strip()] if isinstance(obligations, list) else []
    clean_gaps = [str(x) for x in cov_gaps if str(x).strip()] if isinstance(cov_gaps, list) else []
    has_unres = bool(open_f or unmet_reqs or clean_obs or clean_gaps or missing_ev or malformed_f)

    return {
        "open_findings": open_f, "resolved_findings": res_f, "malformed_findings": malformed_f,
        "missing_evidence": missing_ev, "unmet_requirements": unmet_reqs,
        "open_obligations": clean_obs, "coverage_gaps": clean_gaps, "has_unresolved_work": has_unres,
    }


def compute_review_state_fingerprint(record_path: Optional[str], record_data: Optional[Dict[str, Any]] = None, workspace_root: Optional[str] = None) -> str:
    """Computes a composite fingerprint of review record and referenced primary artifacts."""
    hasher = hashlib.sha256()
    effective_data = record_data
    if effective_data is None and record_path and os.path.exists(record_path):
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    effective_data = loaded
        except Exception:
            pass
    if effective_data:
        hasher.update(json.dumps(effective_data, sort_keys=True).encode("utf-8"))
    elif record_path and os.path.exists(record_path):
        try:
            with open(record_path, "rb") as f:
                hasher.update(f.read())
        except Exception:
            pass
    if effective_data and isinstance(effective_data.get("artifacts"), list):
        for art in effective_data["artifacts"]:
            if isinstance(art, dict) and art.get("path"):
                art_path = str(art["path"])
                if workspace_root and not os.path.isabs(art_path):
                    art_path = os.path.join(workspace_root, art_path)
                elif not os.path.isabs(art_path) and record_path:
                    art_path = os.path.join(os.path.dirname(os.path.abspath(record_path)), art_path)
                if os.path.exists(art_path) and os.path.isfile(art_path):
                    try:
                        mtime, fsize = os.path.getmtime(art_path), os.path.getsize(art_path)
                        with open(art_path, "rb") as af:
                            file_content_hash = hashlib.sha256(af.read()).hexdigest()
                        hasher.update(f"{art_path}:{mtime}:{fsize}:{file_content_hash}".encode("utf-8"))
                    except Exception:
                        pass
    return hasher.hexdigest()


def format_review_context_for_prompt(record_path: Optional[str], workspace_root: Optional[str] = None, expected_assignment: Optional[str] = None) -> Optional[str]:
    """Generates the structured review context block for injection into verifier prompt."""
    if not record_path:
        return None
    record, err = load_review_record(record_path)
    if err:
        return f"<review_record_error>\nReview record bound at '{record_path}' could not be loaded: {err}\nRequire the agent to correct the review record format and path.\n</review_record_error>"
    scope_errors = validate_review_record_scope(record, workspace_root, expected_assignment)
    if scope_errors:
        return f"<review_record_error>\nReview record scope mismatch: {'; '.join(scope_errors)}\n</review_record_error>"

    audit = inspect_findings_and_requirements(record)
    lines = [
        "<review_state_context>",
        f"Session: {record.get('session_id')} (Revision {record.get('revision', 1)})",
        f"Scope: {record.get('scope_type', 'lead')} (Assignment: {record.get('assignment_id', 'overall')})",
        f"Workspace: {record.get('workspace', 'N/A')}", "",
    ]
    if audit["open_findings"]:
        lines.append(f"Open Findings ({len(audit['open_findings'])} remaining):")
        for f in audit["open_findings"]:
            fid, st, sev = f.get("id", "F?"), f.get("state", "open"), f.get("severity", "major")
            lines.append(f"  - [{fid}] ({st.upper()}, {sev}) {f.get('title', 'Untitled')} (Owner: {f.get('owner', 'unassigned')})")
            if f.get("reproduction_evidence"):
                lines.append(f"    Reproduction: {f.get('reproduction_evidence')}")
        lines.append("")
    else:
        lines.extend(["Open Findings: 0 (All identified findings are resolved)", ""])

    if audit["resolved_findings"]:
        lines.append(f"Resolved Findings ({len(audit['resolved_findings'])}):")
        for f in audit["resolved_findings"]:
            lines.append(f"  - [{f.get('id', 'F?')}] ({f.get('state', 'resolved').upper()}) {f.get('title', 'Untitled')}")
        lines.append("")

    if audit["unmet_requirements"]:
        lines.append(f"Unmet Requirements ({len(audit['unmet_requirements'])}):")
        for r in audit["unmet_requirements"]:
            lines.append(f"  - [{r.get('id', 'R?')}] ({r.get('status', 'pending')}) {r.get('description', '')}")
        lines.append("")

    if audit["open_obligations"]:
        lines.append(f"Outstanding Review Obligations ({len(audit['open_obligations'])}):")
        for ob in audit["open_obligations"]:
            lines.append(f"  - {ob}")
        lines.append("")

    if audit["coverage_gaps"]:
        lines.append(f"Outstanding Coverage Gaps ({len(audit['coverage_gaps'])}):")
        for cg in audit["coverage_gaps"]:
            lines.append(f"  - {cg}")
        lines.append("")

    if audit["missing_evidence"]:
        lines.append("MISSING EVIDENCE / STRUCTURAL FLAWS:")
        for me in audit["missing_evidence"]:
            lines.append(f"  - WARNING: {me}")
        lines.append("")

    if audit["malformed_findings"]:
        lines.append("MALFORMED FINDING STRUCTURES:")
        for mf in audit["malformed_findings"]:
            lines.append(f"  - ERROR: {mf}")
        lines.append("")

    prior_attempts = record.get("prior_attempts", [])
    if isinstance(prior_attempts, list) and prior_attempts:
        lines.append(f"Prior Repair Attempts: {len(prior_attempts)}")
        for att in prior_attempts[-3:]:
            if isinstance(att, dict):
                lines.append(f"  - Attempt {att.get('attempt', '?')} ({att.get('outcome', '?')}): {att.get('progress_summary', '')}")
        lines.append("")

    lines.extend([
        "Evaluation Guidance for Persistent Review:",
        "- Do NOT return PASS if open findings, unmet requirements, open obligations, coverage gaps, or missing evidence remain.",
        "- Even low-severity confirmed bugs must be repaired and verified before final acceptance.",
        "- If the agent made real progress on an open finding, note progress and provide the next step.",
        "- If consecutive attempts repeat the same failure without progress, steer toward a method change.",
        "</review_state_context>",
    ])
    return "\n".join(lines)
