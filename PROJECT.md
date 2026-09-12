# AGY Background Agent

The active entry point is `hooks/session-sage.py`, which runs the Lite stop audit on Stop or a completed PostInvocation. The lifecycle runner owns the conversation lock, isolated fork, bounded retries, state transitions, and response emission. Child sessions, empty conversations, and active background work bypass the audit. An active request is audited even when it contains no mutations.

## Responsibility boundaries

- `sage/user_context.py`, `sage/lite/gating.py`, and `sage/lite/evidence.py` preserve the user's request and extract relevant events. Results match by call ID or unambiguous position; unknown attribution remains unknown. Structured process status and bounded output tails survive long output.
- `sage/lite/prompt.py` supplies one contextual review contract. Scope, deferral, evidence sufficiency, causal freshness, and corrective wording are model judgments.
- `sage/lite/verifier.py` executes configured models and parses verdicts. One reconsideration can address a definite proof contradiction using the same context, fork, and total deadline. Failed execution produces an unavailable result with no action.
- `sage/lite/proof_validator.py` checks proof structure and missing or empty absolute local media. It does not classify tasks, demand interviews, or impose timestamp freshness.
- `sage/lite/runner.py` distinguishes incomplete, blocked, unavailable, complete, and retry-exhausted states. It cleans the fork after execution. Unexpected execution exceptions and fork failures are recorded as unavailable.
- `sage/guards.py` emits Stop or PostInvocation responses and tags injected steering so it does not become a new user goal.

Old knowledge-update fields remain in the schema for compatibility. The Lite runner does not run automatic knowledge maintenance.

## Interfaces

`run_lite_verification(parent_conv_id, fork_conv_id, user_prompt, last_agent_output, timeout, cwd, turn_execution_summary, image_manifest, turn_provenance)` returns `LiteVerdict`.

Model results use `verdict=PASS` with `completion=complete|blocked`, a comment and nonempty proof; or `verdict=FAIL` with `completion=incomplete` and a nonempty action. Malformed decisions do not become verified completion. The internal unavailable result uses `PASS` for fail-open transport compatibility; the runner explicitly records `completion=unavailable` and emits no steering.

`extract_turn_execution_provenance(steps)` returns the active request, last response, mutation metadata, execution summary, latest terminal output, and separately tracked read, written, and image paths.

`validate_empirical_proof(proof)` returns `(valid, diagnostic)`. A valid result means no deterministic contradiction was found, not that the claim is true.

## Verification

Source modules under `sage/` remain at most 300 lines with no semicolon statement packing. Local checks cover schema integrity, context boundaries, output attribution, fork isolation, unavailable states, retries, and exact propagation of model actions. Mocked model tests establish wiring only.

The `prompt` topic separately evaluates actual model judgments on synthetic records, with an independent 15-case steering matrix and a native hook runner that executes real CSV fixtures. See [commands and limits](scripts/verify/prompt/README.md).
