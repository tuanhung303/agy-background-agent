"""sage.lite.schemas - Data models for Lite Mode Stop Hook verification."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class LiteVerdict:
    verdict: Literal["PASS", "FAIL"]
    action: str = ""
    comment: str = ""
    proof: List[str] = field(default_factory=list)
    update_knowledge: bool = False
    completion: Literal["complete", "blocked", "incomplete", "stalled", "unavailable", "timed_out"] = "complete"
    progress_observed: bool = False
    progress_summary: str = ""
    unresolved_findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "action": self.action,
            "comment": self.comment,
            "proof": self.proof,
            "update_knowledge": self.update_knowledge,
            "completion": self.completion,
            "progress_observed": self.progress_observed,
            "progress_summary": self.progress_summary,
            "unresolved_findings": self.unresolved_findings,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LiteVerdict":
        """Parse model output without coercing malformed decisions into completion."""
        if not isinstance(data, dict) or data.get("verdict") not in ("PASS", "FAIL"):
            raise ValueError("Expected a PASS or FAIL verdict object")
        verdict = data["verdict"]
        if verdict == "FAIL":
            action = data.get("action")
            raw_comp = data.get("completion", "incomplete")
            if not isinstance(raw_comp, str) or raw_comp.strip().lower() not in ("incomplete", "stalled"):
                completion = "incomplete"
            else:
                completion = raw_comp.strip().lower()
            if not isinstance(action, str) or not action.strip():
                raise ValueError("FAIL requires a corrective action")

            raw_progress = data.get("progress_observed")
            if raw_progress is None:
                raw_progress = data.get("progress")
            if isinstance(raw_progress, bool):
                progress_observed = raw_progress
            elif isinstance(raw_progress, str):
                progress_observed = raw_progress.strip().lower() in ("true", "1", "yes", "on", "enable", "enabled")
            elif isinstance(raw_progress, (int, float)):
                progress_observed = bool(raw_progress)
            else:
                progress_observed = False

            progress_summary = str(data.get("progress_summary") or "").strip()
            raw_unres = data.get("unresolved_findings", [])
            unresolved_findings = [str(x).strip() for x in raw_unres if str(x).strip()] if isinstance(raw_unres, list) else []

            return cls(
                verdict="FAIL",
                action=action.strip(),
                completion=completion,
                progress_observed=progress_observed,
                progress_summary=progress_summary,
                unresolved_findings=unresolved_findings,
            )
        if not all(isinstance(data.get(key, ""), str) for key in ("action", "comment")):
            raise ValueError("Verdict action and comment must be strings")
        action = data.get("action", "").strip()
        comment = data.get("comment", "").strip()
        raw_proof = data.get("proof", [])
        if not isinstance(raw_proof, list) or not all(isinstance(p, str) and p.strip() for p in raw_proof):
            raise ValueError("Verdict proof must be an array of nonempty strings")
        proof = [p.strip() for p in raw_proof]
        completion = data.get("completion", "complete" if verdict == "PASS" else "incomplete")
        if verdict == "PASS":
            if action or not comment or not proof or completion not in ("complete", "blocked"):
                raise ValueError("PASS requires comment, proof, and complete or blocked completion")
        raw_kb = data.get("update_knowledge")
        if raw_kb is None:
            raw_kb = data.get("requires_knowledge_update")
        if raw_kb is None:
            raw_kb = data.get("knowledge_update")
        if isinstance(raw_kb, bool):
            update_knowledge = raw_kb
        elif isinstance(raw_kb, str):
            update_knowledge = raw_kb.strip().lower() in ("true", "1", "yes", "on", "enable", "enabled")
        elif isinstance(raw_kb, (int, float)):
            update_knowledge = bool(raw_kb)
        else:
            update_knowledge = False
        return cls(
            verdict=verdict,
            action=action,
            comment=comment,
            proof=proof,
            update_knowledge=update_knowledge,
            completion=completion,
        )
