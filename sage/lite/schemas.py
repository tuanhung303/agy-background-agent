"""sage.lite.schemas - Data models for Lite Mode Stop Hook verification."""
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional


@dataclass
class LiteVerdict:
    verdict: Literal["PASS", "FAIL"]
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LiteVerdict":
        if not data or not isinstance(data, dict):
            return cls(verdict="PASS", action="")
        raw_v = str(data.get("verdict") or "").strip().upper()
        verdict: Literal["PASS", "FAIL"] = "FAIL" if raw_v == "FAIL" else "PASS"
        action = str(data.get("action") or "").strip()
        return cls(verdict=verdict, action=action)
