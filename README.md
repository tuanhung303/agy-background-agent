# agy-background-agent

A background stop verifier for Antigravity agents. It checks whether requested work is complete, identifies supported blockers, and returns model-generated steering when required work remains. Python 3.10+, no runtime Python dependencies, and an authenticated `agy` CLI are required.

## How it works

1. Recover the active request, earlier constraints, and relevant tool evidence. Audit active requests even when the worker made no edits, including premature deferrals on read-only tasks.
2. Fork the conversation database into an isolated home, preserving parent conversation history.
3. Ask Gemini to judge the requested outcome and evidence. Distinguish expected failures, recovery, genuine blockers, optional improvements, and excluded work from unfinished authorized work.
4. Validate the verdict structure and definite contradictions in cited absolute local media paths. A missing or empty image triggers one model reconsideration with the original context and the observed contradiction.
5. Inject the model's task-specific action for an incomplete result. Complete, blocked, unavailable, and retry-exhausted states remain distinct in persisted state and the statusline.

The model generates every corrective action. There is no scripted repair fallback, automatic planning interview, required verification directory, or file-age threshold. Explicit user requirements still apply. Evidence is reused while its relevant target and state remain valid; changes that invalidate it require a new check.

Model execution and proof reconsideration share one deadline, defaulting to 20 seconds. Lite candidates are Gemini 3.8 Flash (Low), then Medium; `AGY_LITE_VERIFIER_MODEL` selects the first candidate. Failed audits allow a clean stop with an unavailable status and no invented steering. Three rejection strikes and bounded duplicate-action replays prevent endless loops. Changed evidence triggers a new audit.

## Modules

| Path | Responsibility |
|---|---|
| [hooks/session-sage.py](hooks/session-sage.py) | Stop and PostInvocation entry point, recursion protection |
| [sage/lite/runner.py](sage/lite/runner.py) | Lifecycle, fork ownership, persisted state, rejection delivery |
| [gating.py](sage/lite/gating.py) and [evidence.py](sage/lite/evidence.py) | User context, call/result attribution, bounded evidence excerpts |
| [prompt.py](sage/lite/prompt.py) and [verifier.py](sage/lite/verifier.py) | Contextual judgment, model execution, proof reconsideration |
| [proof_validator.py](sage/lite/proof_validator.py) | Structural checks and definite local media contradictions |
| [hooks/sage-enforce.py](hooks/sage-enforce.py) | PreToolUse pass-through |
| [hooks/command-timer.py](hooks/command-timer.py) | Command duration feedback |

## Verify

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify/all.py --topic stop_verifier
.venv/bin/python scripts/verify/prompt/main.py
```

See [evaluation instructions and retained evidence](scripts/verify/prompt/README.md) and the [review report](REVIEW_stop_verifier.md). Real-model tests require working provider access and consume model usage.

Model judgment remains probabilistic. Bounded summaries can omit details, and ambiguous tool-result attribution remains unknown. Local media checks establish existence, not image content; relative citations rely on model inspection. The fork isolates conversation state, while the inspection-only prompt is not a filesystem sandbox. Active read-only requests incur model latency. The historical SVG in `assets/` predates this architecture and is not a current verification record.
