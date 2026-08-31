"""
sage.ledger - Domain-neutral validation ledger, criterion taxonomy, and evidence freshness.
"""

from dataclasses import asdict, dataclass, field
import os
from typing import Any, Dict, List, Optional, Tuple

LEDGER_SCHEMA_VERSION = "sage_validation_v1"
VALID_STATUSES = {"verified", "contradicted", "unverified", "not_applicable"}
VALID_CATEGORIES = {
    "requested_deliverable", "functional_behavior", "negative_behavior",
    "integrity_provenance", "runtime_behavior", "adjacent_regression", "rollback_recovery",
}


@dataclass
class Criterion:
    id: str
    claim: str
    applicability: str = "required"
    required_evidence: str = "test_execution"
    scope_paths: List[str] = field(default_factory=list)
    category: str = "functional_behavior"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Criterion":
        return cls(
            id=str(data.get("id") or ""),
            claim=str(data.get("claim") or ""),
            applicability=str(data.get("applicability") or "required"),
            required_evidence=str(data.get("required_evidence") or "test_execution"),
            scope_paths=list(data.get("scope_paths") or []),
            category=str(data.get("category") or "functional_behavior"),
        )


@dataclass
class Evidence:
    criterion_id: str
    kind: str
    locator: str
    observation: str
    result: str
    source_step: int = 0
    workspace_fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evidence":
        return cls(
            criterion_id=str(data.get("criterion_id") or ""),
            kind=str(data.get("kind") or "command_output"),
            locator=str(data.get("locator") or ""),
            observation=str(data.get("observation") or ""),
            result=str(data.get("result") or "unverified"),
            source_step=int(data.get("source_step", 0)),
            workspace_fingerprint=str(data.get("workspace_fingerprint") or ""),
        )


class ValidationLedger:
    def __init__(self, version: str = LEDGER_SCHEMA_VERSION):
        self.version = version
        self.criteria: Dict[str, Criterion] = {}
        self.evidence: List[Evidence] = []
        self.status: Dict[str, str] = {}
        self.next_validation: str = ""
        self.workspace_fingerprint: str = ""
        self.head_sha: str = ""

    def add_criterion(self, criterion: Criterion, initial_status: str = "unverified") -> None:
        self.criteria[criterion.id] = criterion
        self.status[criterion.id] = initial_status if initial_status in VALID_STATUSES else "unverified"

    def record_evidence(self, ev: Evidence, resulting_status: Optional[str] = None) -> None:
        self.evidence.append(ev)
        cid = ev.criterion_id
        if cid in self.criteria:
            if resulting_status in VALID_STATUSES:
                self.status[cid] = resulting_status
            elif ev.result == "pass":
                self.status[cid] = "verified"
            elif ev.result == "fail":
                self.status[cid] = "contradicted"
            else:
                self.status[cid] = "unverified"
        self._refresh_next_validation()

    def invalidate_scoped_paths(self, modified_paths: List[str], new_head_sha: Optional[str] = None) -> List[str]:
        invalidated = []
        clean_mods = {os.path.normpath(str(p)).lstrip("./") for p in modified_paths if str(p).strip()}
        head_changed = bool(new_head_sha and self.head_sha and new_head_sha != self.head_sha)
        if new_head_sha:
            self.head_sha = new_head_sha
        for cid, crit in self.criteria.items():
            if self.status.get(cid) != "verified":
                continue
            crit_paths = {os.path.normpath(str(p)).lstrip("./") for p in crit.scope_paths if str(p).strip()}
            touched = False
            if crit_paths:
                touched = any(cp == mp or cp.startswith(mp + "/") or mp.startswith(cp + "/") for cp in crit_paths for mp in clean_mods)
            elif head_changed or clean_mods:
                touched = True
            if touched:
                self.status[cid] = "unverified"
                invalidated.append(cid)
        self._refresh_next_validation()
        return invalidated

    def seed_criteria_from_task(self, user_prompt: str, complexity: str, pinned_goal: Optional[str] = None, workspace_root: Optional[str] = None) -> None:
        if self.criteria:
            return
        comp = str(complexity or "").strip().lower()
        if comp in ("simple_qa", "qa"):
            self.add_criterion(Criterion(id="crit_qa_answer", claim="Provide direct, accurate response to user inquiry", applicability="required", required_evidence="file_inspection", category="requested_deliverable"), "verified")
            return
        goal = " ".join([ln.strip() for ln in str(pinned_goal or user_prompt or "").splitlines() if ln.strip()])[:200]
        self.add_criterion(Criterion(id="crit_deliverable", claim=f"Primary objective satisfied: {goal}", applicability="required", required_evidence="test_execution", category="requested_deliverable"), "unverified")
        self.add_criterion(Criterion(id="crit_functional_tests", claim="All functional requirements and negative/failure boundary cases pass", applicability="required", required_evidence="test_execution", category="functional_behavior"), "unverified")
        self.add_criterion(Criterion(id="crit_integrity_regression", claim="No adjacent regressions across existing test suites and workspace invariants", applicability="required", required_evidence="test_execution", category="adjacent_regression"), "unverified")
        self._refresh_next_validation()

    def merge_model_criteria(self, model_criteria_list: List[Dict[str, Any]]) -> None:
        for item in model_criteria_list:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cid = str(item["id"]).strip()
            crit = Criterion(
                id=cid, claim=str(item.get("claim") or item.get("description") or cid),
                applicability=str(item.get("applicability") or "required"),
                required_evidence=str(item.get("required_evidence") or "test_execution"),
                scope_paths=list(item.get("scope_paths") or []),
                category=str(item.get("category") or "functional_behavior"),
            )
            stat = str(item.get("status") or "unverified").lower()
            self.criteria[cid] = crit
            if cid not in self.status or self.status[cid] == "unverified":
                self.status[cid] = stat if stat in VALID_STATUSES else "unverified"
        self._refresh_next_validation()

    def check_completion(self, complexity: str) -> Tuple[bool, str, List[str]]:
        if str(complexity or "").strip().lower() in ("simple_qa", "qa"):
            return True, "Simple Q&A exempt from validation ledger checks", []
        gaps = []
        for cid, crit in self.criteria.items():
            if crit.applicability != "required":
                continue
            st = self.status.get(cid, "unverified")
            if st in ("contradicted", "unverified"):
                gaps.append(f"{cid} ({st.upper()}: {crit.claim})")
        if gaps:
            return False, f"Required criteria unverified or contradicted ({len(gaps)} remaining): " + "; ".join(gaps[:3]), gaps
        return True, "All required criteria verified", []

    def _refresh_next_validation(self) -> None:
        for cid, crit in self.criteria.items():
            if crit.applicability == "required" and self.status.get(cid) in ("unverified", "contradicted"):
                paths = ", ".join(crit.scope_paths[:3])
                self.next_validation = f"Verify {crit.category} on [{paths}] via targeted test execution" if paths else f"Verify {crit.claim} via project test suite and live inspection"
                return
        self.next_validation = "All required criteria verified"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "criteria": [c.to_dict() for c in self.criteria.values()],
            "evidence": [e.to_dict() for e in self.evidence[-50:]],
            "status": dict(self.status),
            "next_validation": self.next_validation,
            "workspace_fingerprint": self.workspace_fingerprint,
            "head_sha": self.head_sha,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationLedger":
        ledger = cls(version=str(data.get("version") or LEDGER_SCHEMA_VERSION))
        for cd in data.get("criteria", []):
            if isinstance(cd, dict):
                c = Criterion.from_dict(cd)
                ledger.criteria[c.id] = c
        for ed in data.get("evidence", []):
            if isinstance(ed, dict):
                ledger.evidence.append(Evidence.from_dict(ed))
        ledger.status = dict(data.get("status", {}))
        ledger.next_validation = str(data.get("next_validation") or "")
        ledger.workspace_fingerprint = str(data.get("workspace_fingerprint") or "")
        ledger.head_sha = str(data.get("head_sha") or "")
        return ledger
