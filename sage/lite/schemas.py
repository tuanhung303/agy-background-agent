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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "action": self.action,
            "comment": self.comment,
            "proof": self.proof,
            "update_knowledge": self.update_knowledge,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LiteVerdict":
        if not data or not isinstance(data, dict):
            return cls(verdict="PASS", action="", comment="", proof=[], update_knowledge=False)
        raw_v = str(data.get("verdict") or "").strip().upper()
        verdict: Literal["PASS", "FAIL"] = "FAIL" if raw_v == "FAIL" else "PASS"
        action = str(data.get("action") or "").strip()
        comment = str(data.get("comment") or data.get("recap") or "").strip()
        raw_proof = data.get("proof") or []
        if isinstance(raw_proof, str):
            proof = [raw_proof.strip()] if raw_proof.strip() else []
        elif isinstance(raw_proof, list):
            proof = [str(p).strip() for p in raw_proof if str(p).strip()]
        else:
            proof = []
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
        )
