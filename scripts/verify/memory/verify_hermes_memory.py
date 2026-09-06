#!/usr/bin/env python3
"""
verify_hermes_memory.py - Empirical verification of Hermes memory system.

Verifies:
1. tools/memory_tool.py: character limits (2200 memory, 1375 user), delimiter ("\n§\n"), block headers.
2. agent/system_prompt.py: volatile prompt injection calls and memory guidance.
3. ~/.hermes/memories/: on-disk live MEMORY.md and USER.md adherence to limits and delimiter format.
"""

import ast
import os
import sys
from pathlib import Path


def verify_memory_tool_invariants(hermes_root: Path) -> dict:
    tool_file = hermes_root / "tools" / "memory_tool.py"
    assert tool_file.exists(), f"tools/memory_tool.py not found at {tool_file}"

    # 1. AST Static Verification
    with open(tool_file, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "ENTRY_DELIMITER" and isinstance(node.value, ast.Constant):
                        assignments["ENTRY_DELIMITER"] = node.value.value
                    elif target.id == "MEMORY_BLOCK_HEADERS" and isinstance(node.value, ast.Dict):
                        keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                        values = [v.value for v in node.value.values if isinstance(v, ast.Constant)]
                        assignments["MEMORY_BLOCK_HEADERS"] = dict(zip(keys, values))

    assert assignments.get("ENTRY_DELIMITER") == "\n§\n", (
        f"Expected ENTRY_DELIMITER='\\n§\\n', got {repr(assignments.get('ENTRY_DELIMITER'))}"
    )
    assert assignments.get("MEMORY_BLOCK_HEADERS") == {
        "memory": "MEMORY (your personal notes)",
        "user": "USER PROFILE (who the user is)",
    }, f"Unexpected MEMORY_BLOCK_HEADERS: {assignments.get('MEMORY_BLOCK_HEADERS')}"

    # 2. Dynamic Runtime Verification
    sys.path.insert(0, str(hermes_root))
    try:
        from tools.memory_tool import (
            ENTRY_DELIMITER,
            MEMORY_BLOCK_HEADERS,
            MemoryStore,
        )

        store = MemoryStore()
        assert store.memory_char_limit == 2200, f"Expected 2200, got {store.memory_char_limit}"
        assert store.user_char_limit == 1375, f"Expected 1375, got {store.user_char_limit}"

        # Test block rendering
        sample_entries = ["Invariant 1: <= 199 lines", "Invariant 2: zero semicolons"]
        rendered = store._render_block("memory", sample_entries)
        assert "MEMORY (your personal notes)" in rendered, "Rendered block missing header"
        assert "\n§\n" in rendered, "Rendered block missing delimiter"
        assert "═" * 46 in rendered, "Rendered block missing separator"

        return {
            "entry_delimiter": ENTRY_DELIMITER,
            "memory_headers": MEMORY_BLOCK_HEADERS,
            "memory_char_limit": store.memory_char_limit,
            "user_char_limit": store.user_char_limit,
            "render_test": "PASS",
        }
    finally:
        if str(hermes_root) in sys.path:
            sys.path.remove(str(hermes_root))


def verify_system_prompt_injection(hermes_root: Path) -> dict:
    prompt_file = hermes_root / "agent" / "system_prompt.py"
    assert prompt_file.exists(), f"agent/system_prompt.py not found at {prompt_file}"

    with open(prompt_file, "r", encoding="utf-8") as f:
        src = f.read()

    assert 'format_for_system_prompt("memory")' in src, (
        "agent/system_prompt.py missing format_for_system_prompt('memory') call"
    )
    assert 'format_for_system_prompt("user")' in src, (
        "agent/system_prompt.py missing format_for_system_prompt('user') call"
    )
    assert "MEMORY_GUIDANCE" in src, "agent/system_prompt.py missing MEMORY_GUIDANCE"
    assert "USER_PROFILE_GUIDANCE" in src, "agent/system_prompt.py missing USER_PROFILE_GUIDANCE"

    # Find line ranges
    lines = src.splitlines()
    mem_inj_lines = [i + 1 for i, l in enumerate(lines) if "format_for_system_prompt" in l]

    return {
        "memory_injection_lines": mem_inj_lines,
        "prompt_injection_status": "PASS",
    }


def verify_disk_memories(memories_dir: Path) -> dict:
    results = {}
    if not memories_dir.exists():
        results["status"] = "DIR_NOT_FOUND"
        return results

    memory_md = memories_dir / "MEMORY.md"
    user_md = memories_dir / "USER.md"

    if memory_md.exists():
        with open(memory_md, "r", encoding="utf-8") as f:
            mem_raw = f.read()
        mem_entries = [e.strip() for e in mem_raw.split("\n§\n") if e.strip()]
        mem_len = len(mem_raw)
        results["memory_md"] = {
            "entries_count": len(mem_entries),
            "char_count": mem_len,
            "char_limit": 2200,
            "within_limit": mem_len <= 2200,
        }
        assert mem_len <= 2200, f"MEMORY.md exceeds 2200 chars: {mem_len}"

    if user_md.exists():
        with open(user_md, "r", encoding="utf-8") as f:
            user_raw = f.read()
        user_entries = [e.strip() for e in user_raw.split("\n§\n") if e.strip()]
        user_len = len(user_raw)
        results["user_md"] = {
            "entries_count": len(user_entries),
            "char_count": user_len,
            "char_limit": 1375,
            "within_limit": user_len <= 1375,
        }
        assert user_len <= 1375, f"USER.md exceeds 1375 chars: {user_len}"

    results["status"] = "PASS"
    return results


def main():
    hermes_root = Path(os.path.expanduser("~/.hermes/hermes-agent"))
    memories_dir = Path(os.path.expanduser("~/.hermes/memories"))

    print("Empirically verifying Hermes Memory System...")

    # 1. tools/memory_tool.py
    res_tool = verify_memory_tool_invariants(hermes_root)
    print(f"✓ tools/memory_tool.py verified: limits={res_tool['memory_char_limit']}/{res_tool['user_char_limit']} chars, delimiter={repr(res_tool['entry_delimiter'])}")

    # 2. agent/system_prompt.py
    res_prompt = verify_system_prompt_injection(hermes_root)
    print(f"✓ agent/system_prompt.py verified: injection lines={res_prompt['memory_injection_lines']}")

    # 3. Disk files
    res_disk = verify_disk_memories(memories_dir)
    print(f"✓ disk files verified at {memories_dir}: status={res_disk.get('status')}")
    if "memory_md" in res_disk:
        print(f"  - MEMORY.md: {res_disk['memory_md']['char_count']}/{res_disk['memory_md']['char_limit']} chars ({res_disk['memory_md']['entries_count']} entries)")
    if "user_md" in res_disk:
        print(f"  - USER.md: {res_disk['user_md']['char_count']}/{res_disk['user_md']['char_limit']} chars ({res_disk['user_md']['entries_count']} entries)")

    print("\nALL HERMES MEMORY INVARIANTS VERIFIED CLEANLY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
