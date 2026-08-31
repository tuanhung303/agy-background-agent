"""
sage.evidence_store - Per-conversation normalized evidence persistence and retention cleanup.

Stores run manifests, evaluation histories, and evidence receipts under:
  /tmp/agy/sage/<safe-conv-id>/ (configurable via AGY_SAGE_EVIDENCE_DIR)
"""

from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from sage.locking import safe_id

DEFAULT_EVIDENCE_ROOT = os.environ.get("AGY_SAGE_EVIDENCE_DIR", "/tmp/agy/sage")
DEFAULT_MAX_AGE_SECONDS = 7 * 86400  # 7 days
DEFAULT_MAX_BYTES_PER_CONV = 10 * 1024 * 1024  # 10 MB


def get_evidence_dir(conv_id: str) -> str:
    """Returns the evidence directory path for a parent conversation."""
    base = os.environ.get("AGY_SAGE_EVIDENCE_DIR", DEFAULT_EVIDENCE_ROOT)
    cid = safe_id(conv_id) if conv_id else "default"
    path = os.path.join(base, cid)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def save_manifest(conv_id: str, manifest_data: Dict[str, Any]) -> str:
    """Writes manifest.json atomically in the conversation's evidence directory."""
    edir = get_evidence_dir(conv_id)
    mpath = os.path.join(edir, "manifest.json")
    
    merged = dict(manifest_data)
    merged.setdefault("conversation_id", conv_id)
    merged.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    
    tmp_path = f"{mpath}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, mpath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return mpath


def append_evaluation(conv_id: str, evaluation_record: Dict[str, Any]) -> None:
    """Appends an evaluation record to evaluations.jsonl."""
    edir = get_evidence_dir(conv_id)
    epath = os.path.join(edir, "evaluations.jsonl")
    
    rec = dict(evaluation_record)
    rec.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    rec.setdefault("conversation_id", conv_id)
    
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with open(epath, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def append_evidence(conv_id: str, evidence_record: Dict[str, Any]) -> None:
    """Appends an evidence receipt to evidence.jsonl."""
    edir = get_evidence_dir(conv_id)
    epath = os.path.join(edir, "evidence.jsonl")
    
    rec = dict(evidence_record)
    rec.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    rec.setdefault("conversation_id", conv_id)
    
    # Hash large observations/excerpts if provided
    obs = str(rec.get("observation") or "")
    if len(obs) > 500:
        rec["observation_hash"] = hashlib.sha256(obs.encode("utf-8")).hexdigest()
        rec["observation"] = obs[:500] + f" ... [truncated, total {len(obs)} chars]"
        
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with open(epath, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read_evidence(conv_id: str) -> List[Dict[str, Any]]:
    """Reads all evidence receipts from evidence.jsonl."""
    edir = get_evidence_dir(conv_id)
    epath = os.path.join(edir, "evidence.jsonl")
    if not os.path.exists(epath):
        return []
    records = []
    try:
        with open(epath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return records


def read_evaluations(conv_id: str) -> List[Dict[str, Any]]:
    """Reads all evaluation records from evaluations.jsonl."""
    edir = get_evidence_dir(conv_id)
    epath = os.path.join(edir, "evaluations.jsonl")
    if not os.path.exists(epath):
        return []
    records = []
    try:
        with open(epath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return records


def cleanup_evidence_store(
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    max_bytes_per_conv: int = DEFAULT_MAX_BYTES_PER_CONV,
) -> None:
    """Prunes stale or oversized evidence directories."""
    base = os.environ.get("AGY_SAGE_EVIDENCE_DIR", DEFAULT_EVIDENCE_ROOT)
    if not os.path.exists(base) or not os.path.isdir(base):
        return
    now = time.time()
    try:
        for entry in os.listdir(base):
            p = os.path.join(base, entry)
            if not os.path.isdir(p):
                continue
            try:
                mtime = os.path.getmtime(p)
                if now - mtime > max_age_seconds:
                    shutil.rmtree(p, ignore_errors=True)
                    continue
                # Size check
                total_size = sum(
                    os.path.getsize(os.path.join(p, f))
                    for f in os.listdir(p)
                    if os.path.isfile(os.path.join(p, f))
                )
                if total_size > max_bytes_per_conv:
                    # Truncate jsonl files if oversized
                    for jf in ("evaluations.jsonl", "evidence.jsonl"):
                        jpath = os.path.join(p, jf)
                        if os.path.exists(jpath) and os.path.getsize(jpath) > max_bytes_per_conv // 2:
                            with open(jpath, "r", encoding="utf-8", errors="ignore") as rf:
                                lines = rf.readlines()
                            keep = lines[-200:]
                            with open(jpath, "w", encoding="utf-8") as wf:
                                wf.writelines(keep)
            except Exception:
                pass
    except Exception:
        pass
