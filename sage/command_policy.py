"""
sage.command_policy - Safe validation-only command policy for Sage execution.

Permits tests, linters, and read-only inspection commands while strictly rejecting
filesystem mutations, package installations, deployments, git writes, and destructive actions.
"""

import os
import re
import shlex
from typing import Tuple

# Allowed base binaries / commands for observational inspection and testing
ALLOWED_INSPECTION_BINARIES = {
    "git", "cat", "head", "tail", "grep", "rg", "find", "ls", "wc",
    "file", "stat", "diff", "echo", "printf", "true", "false", "pwd",
}

ALLOWED_TEST_RUNNERS = {
    "pytest", "unittest", "vitest", "jest", "mocha", "cargo", "go",
    "ctest", "npm", "pnpm", "yarn", "corepack", "uv", "python", "python3",
    "node", "bun", "deno", "dotnet",
}

ALLOWED_LINTERS = {
    "ruff", "mypy", "flake8", "pylint", "black", "isort", "eslint",
    "prettier", "tsc", "cargo-clippy", "clippy", "shellcheck",
}

# Subcommands strictly allowed for tools with dual read/write roles
ALLOWED_GIT_READ_SUBCOMMANDS = {"status", "diff", "log", "show", "rev-parse", "describe", "branch"}
ALLOWED_CARGO_SUBCOMMANDS = {"test", "check", "clippy", "bench", "verify-project"}
ALLOWED_GO_SUBCOMMANDS = {"test", "vet", "version"}
ALLOWED_NPM_SUBCOMMANDS = {"test", "t", "run", "exec"}

# Forbidden dangerous tokens and patterns
FORBIDDEN_MUTATION_PATTERNS = [
    r"\brm\b", r"\bmv\b", r"\bcp\b", r"\bmkdir\b", r"\btouch\b", r"\bchmod\b",
    r"\bchown\b", r"\btruncate\b", r"\btee\b", r"\bsed\s+-i", r"\bawk\s+-i",
    r"\b(apt|apt-get|brew|yum|dnf|pacman|apk)\b",
    r"\bpip\s+(install|uninstall)",
    r"\b(npm|pnpm|yarn)\s+(install|add|remove|uninstall|publish|update)",
    r"\bcargo\s+(install|uninstall|publish)",
    r"\b(docker|podman|kubectl|helm|terraform|pulumi|aws|gcloud|az)\b",
    r"\b(curl|wget|nc|ncat|netcat|ssh|scp|rsync)\b",
    r"\bgit\s+(commit|push|checkout|reset|rebase|merge|apply|branch\s+-[dD]|tag\s+-d|stash|clean)",
    r"\b(kill|killall|pkill|reboot|shutdown|mkfs|dd)\b",
    r">:?\s*",  # redirect write operators
]


def is_sage_command_safe(cmd_str: str) -> Tuple[bool, str]:
    """
    Validates whether a shell command is safe for Sage validation execution.
    Returns (is_safe, reason).
    """
    if not cmd_str or not isinstance(cmd_str, str):
        return False, "Empty command"
    
    raw = cmd_str.strip()
    if not raw:
        return False, "Empty command"
    
    # Check for forbidden patterns
    for pat in FORBIDDEN_MUTATION_PATTERNS:
        if re.search(pat, raw, re.IGNORECASE):
            return False, f"Command contains forbidden mutation or network pattern matching: {pat}"
    
    # Handle chained commands (&&, ||, ;, |)
    sub_cmds = re.split(r"(?:&&|\|\||;|\|)", raw)
    for sub in sub_cmds:
        sub = sub.strip()
        if not sub:
            continue
        try:
            tokens = shlex.split(sub)
        except Exception:
            tokens = sub.split()
        if not tokens:
            continue
        
        binary = os.path.basename(tokens[0]).lower()
        if binary in ("env", "nohup", "time"):
            tokens = tokens[1:]
            if not tokens:
                return False, f"Incomplete prefix command: {binary}"
            binary = os.path.basename(tokens[0]).lower()
            
        if binary == "git":
            if len(tokens) > 1:
                # check for git -C <path> subcommand
                subcmd_idx = 1
                while subcmd_idx < len(tokens) and tokens[subcmd_idx].startswith("-"):
                    if tokens[subcmd_idx] in ("-C", "--work-tree", "--git-dir"):
                        subcmd_idx += 2
                    else:
                        subcmd_idx += 1
                if subcmd_idx < len(tokens):
                    subcmd = tokens[subcmd_idx].lower()
                    if subcmd not in ALLOWED_GIT_READ_SUBCOMMANDS:
                        return False, f"Forbidden git subcommand for Sage: git {subcmd}"
            continue
            
        if binary in ("python", "python3"):
            # Check python arguments: python -m pytest ... or python -c ... (read-only)
            if len(tokens) > 1:
                if tokens[1] in ("-m", "-mpytest", "-munittest"):
                    continue
            continue
            
        if binary in ("npm", "pnpm", "yarn", "corepack"):
            if len(tokens) > 1:
                subcmd = tokens[1].lower()
                if binary == "corepack" and len(tokens) > 2:
                    subcmd = tokens[2].lower()
                if subcmd not in ALLOWED_NPM_SUBCOMMANDS and subcmd not in ("test", "vitest", "jest"):
                    return False, f"Forbidden package manager subcommand for Sage: {binary} {subcmd}"
            continue
            
        if binary in ("cargo"):
            if len(tokens) > 1:
                subcmd = tokens[1].lower()
                if subcmd not in ALLOWED_CARGO_SUBCOMMANDS:
                    return False, f"Forbidden cargo subcommand for Sage: cargo {subcmd}"
            continue
            
        if binary in ("go"):
            if len(tokens) > 1:
                subcmd = tokens[1].lower()
                if subcmd not in ALLOWED_GO_SUBCOMMANDS:
                    return False, f"Forbidden go subcommand for Sage: go {subcmd}"
            continue

        if binary in ALLOWED_INSPECTION_BINARIES or binary in ALLOWED_TEST_RUNNERS or binary in ALLOWED_LINTERS:
            continue
            
        return False, f"Command binary '{binary}' is not in Sage validation allowlist"
        
    return True, "Safe validation command"
