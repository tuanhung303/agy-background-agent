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
    completion: Literal["complete", "blocked", "incomplete", "unavailable"] = "complete"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "action": self.action,
            "comment": self.comment,
            "proof": self.proof,
            "update_knowledge": self.update_knowledge,
            "completion": self.completion,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LiteVerdict":
        """Parse model output without coercing malformed decisions into completion."""
        if not isinstance(data, dict) or data.get("verdict") not in ("PASS", "FAIL"):
            raise ValueError("Expected a PASS or FAIL verdict object")
        verdict = data["verdict"]
        if verdict == "FAIL":
            action = data.get("action")
            if not isinstance(action, str) or not action.strip() or data.get("completion", "incomplete") != "incomplete":
                raise ValueError("FAIL requires a corrective action and incomplete completion")
            # Preserve an explicit rejection even if the model adds explanatory
            # fields contrary to the requested output format.
            return cls(verdict="FAIL", action=action.strip(), completion="incomplete")
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
