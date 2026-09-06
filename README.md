<h1 align="center">agy-background-agent</h1>

<p align="center">
  A background stop verifier for Antigravity coding agents.<br>
  Validates empirical proof, prevents premature completion, and guards against drift.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-234-15803D?style=flat-square" alt="234 tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail when they enter repetitive error loops, drift from task requirements, claim completion on unverified self-reports, or stop early on mock tests. This repository provides Antigravity hooks operating in Lite Mode Stop Verifier to intercept premature stops and enforce empirical proof before allowing session completion.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Hook System

- [hooks/session-sage.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/session-sage.py:1): Lifecycle hook entry point. Directly executes the Lite Mode Stop Verifier (`run_lite_stop_audit()`) at session stop time. Evaluates turn provenance, gates mutations, and validates empirical proof before permitting session exit.
- [hooks/sage-enforce.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/sage-enforce.py:1): Zero-delay `PreToolUse` pass-through hook for Antigravity, immediately returning allow with zero latency.
- [hooks/command-timer.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/command-timer.py:1): Command execution duration tracker. Categorizes runs into 5 tiers (`0-10s OK`, `10-30s IMPROVE_NEXT_TIME`, `30-90s ADJUST_FILTER`, `90-900s HEAVY_RECOMMEND_BACKGROUND`, `>900s FORBIDDEN_EXCEEDED_LIMIT`) and injects ephemeral context feedback.

## Lite Mode Stop Verifier Architecture

The Lite Mode Stop Verifier (`sage/lite/`) provides an isolated, hermetic quality gate with zero MCP dependencies:

1. **Mutation Gating & Turn Provenance** ([sage/lite/gating.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/gating.py)): Extracts turn execution provenance from the transcript. If the turn contains no workspace mutations or file edits (e.g. pure question-answering or diagnostic queries), the verifier bypasses execution instantly with zero latency.
2. **Forked Session Isolation** ([sage/lite/fork.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/fork.py), [sage/executor.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/executor.py)): Clones the parent conversation SQLite database and brain artifacts into an isolated execution directory (`~/.gemini/antigravity-cli/sage_isolated_home`), preventing any pollution of the parent session history.
3. **Stop Gate Verification Cascade** ([sage/lite/verifier.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/verifier.py), [sage/lite/prompt.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/prompt.py)): Evaluates the worker's actions against the user's explicit Definition of Done (DoD) using fast Gemini models. Returns structured JSON verdicts (`PASS` or `FAIL`).
4. **Empirical Proof Validation** ([sage/lite/proof_validator.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/proof_validator.py)): Strictly rejects ungrounded self-report claims ("all tests pass", "verified manually"). Requires concrete proof (executed test binaries, command outputs, screenshot captures, or live DOM assertions).
5. **Contextual Action Directives** ([sage/lite/verifier.py:116](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/verifier.py#L116-L198)): When rejecting a premature stop, synthesizes specific imperative instructions (e.g. recommending test modules under `scripts/verify/<topic>/`) rather than generic rejection messages.
6. **3-Strike Circuit Breaker** ([sage/lite/runner.py:87](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py#L87-L91), [sage/config.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/config.py)): Tracks consecutive verification failures (`LITE_MAX_RETRIES=3`). If the worker fails to satisfy the quality gate after 3 attempts, the verifier fails open to allow clean stop without trapping the user.

