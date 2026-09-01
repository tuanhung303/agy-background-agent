# Project: AGY Background Agent (Lite Mode Stop Verifier)

## Architecture
- `hooks/session-sage.py`: Hook entry point for Antigravity session-stop verification events.
- `sage/lite/runner.py`: Main lifecycle runner for Stop Hook Lite Mode. Manages mutation gating, session forking, quality verification, empirical proof validation, 3-strike circuit breaker, and knowledge base maintenance.
- `sage/lite/verifier.py`: Model executor and JSON verdict parser for Lite Mode with contextual action synthesis on rejection.
- `sage/lite/prompt.py`: Verifier and persona maintainer prompt builders with few-shot calibration examples.
- `sage/lite/gating.py`: Turn provenance analysis and mutation detection (file edits, bash execution, generated images).
- `sage/lite/fork.py`: Hermetic session forking, cloning SQLite DBs and brain logs into isolated environment (`SAGE_ISOLATED_HOME`).
- `sage/lite/proof_validator.py`: Empirical proof validation rules (rejects ungrounded self-report claims, requires executed commands or artifacts).
- `sage/lite/schemas.py`: Data models for `LiteVerdict` and turn provenance data.
- `sage/executor.py`: Subprocess execution of `agy` CLI in `SAGE_ISOLATED_HOME` with safe session discovery and zero MCP setup.
- `sage/guards.py`: Fast-path exits (subagent sessions, background work, destructive action filters).
- `sage/locking.py`: Conversation locking and structured audit logging.
- `sage/session_state.py`: Atomic session state persistence and strike tracking.
- `sage/transcript.py`: Transcript parsing, turn provenance extraction, background task tracking.
- `tests/`: 58 test modules asserting unit, integration, static analysis, and adversarial invariants (950+ tests).

## Code Layout
- `sage/lite/*.py`: Lite Mode Stop Verifier implementation modules.
- `sage/*.py`: Core library, execution isolation, and session utilities. Strictly <= 300 lines per file and zero semicolon statement packing.
- `hooks/*.py`: Antigravity hook scripts.
- `statusline/*.py`: Session statusline integration.
- `tests/test_*.py`: Unittest test cases (950+ tests).
- `scripts/`: Benchmark, hermetic test, telemetry, and verification harnesses.

## Feature Inventory
| # | Feature | Description | Milestone | Status |
|---|---------|-------------|-----------|--------|
| F1 | Semicolon Elimination & Modular Refactoring | Remove all semicolons, extracting helpers to ensure clean AST without compression | M1 | DONE |
| F2 | Static Analysis & AST Gate | Enforce syntax, line caps, docstrings, and no bare prints across all modules | M1 | DONE |
| F3 | Forked Conversation Isolation | Clone SQLite databases and brain logs into `SAGE_ISOLATED_HOME` without parent pollution | Lite Verifier | DONE |
| F4 | Mutation Gating & Zero-Delay Fast Path | Skip LLM evaluation on purely conversational turns with zero file mutations | Lite Verifier | DONE |
| F5 | Empirical Proof Validation | Enforce live artifact proofs and test command traces; reject ungrounded self-report claims | Lite Verifier | DONE |
| F6 | Contextual Rejection Directives | Synthesize domain-specific actionable instructions for the worker rather than boilerplate | Lite Verifier | DONE |
| F7 | 3-Strike Circuit Breaker | Limit consecutive rejections (`LITE_MAX_RETRIES=3`), failing open gracefully | Lite Verifier | DONE |
| F8 | Knowledge Base Maintenance | Update durable persona and memory upon verified PASS when requested | Lite Verifier | DONE |
| F9 | Subprocess Isolation & Parent Protection | Execute child `agy` CLI safely in isolated home with keychain and auth safety | Hardening | DONE |
| F10 | Zero MCP Dependency | Eliminate MCP bridge overhead and hermetically audit with native CLI and models | Refactor | DONE |

## Interface Contracts
### `sage.lite.verifier.run_lite_verification`
- Input: `parent_conv_id: str, fork_conv_id: str, user_prompt: str, last_agent_output: str, timeout: float, cwd: Optional[str], turn_execution_summary: Optional[str], image_manifest: Optional[list], turn_provenance: Optional[dict]`
- Output: `LiteVerdict(verdict="PASS"|"FAIL", action=str, comment=str, proof=list, update_knowledge=bool)`

### `sage.lite.gating.extract_turn_execution_provenance`
- Input: `steps: List[dict]`
- Output: `dict` containing `has_mutation: bool, mutation_reason: str, true_user_prompt: str, last_agent_output: str, tool_executions_summary: str, generated_images: list`

### `sage.lite.proof_validator.validate_empirical_proof`
- Input: `proof: List[str], turn_provenance: dict, user_prompt: str`
- Output: `Tuple[bool, str]` indicating validity and optional rejection reason.
