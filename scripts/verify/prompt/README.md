# Stop verifier evaluation

The prompt asks the model to judge the active request, authorization, actual results, and remaining feasible work. It applies domain knowledge to consequential gaps without prescribing a workflow for every task. Corrective actions come directly from the verdict model. A definite contradiction in cited local media triggers one reconsideration with the original evidence and the contradiction, within the same deadline.

Planning interviews, new test directories, sibling enumeration, and new screenshots are required only when the task or relevant failure mechanism requires them. A planning label and artifact age alone create no requirement. Existing authorization, explicit interviews, external boundaries, and checks invalidated by relevant changes still matter.

## Run model cases

```sh
.venv/bin/python scripts/verify/prompt/main.py
.venv/bin/python scripts/verify/prompt/main.py --cases scripts/verify/prompt/steering_cases.json --model 'Gemini 3.8 Flash (Medium)' --repeats 2
```

The default matrix has 28 regression cases. The independent steering matrix has 15 cases covering supplied planning decisions versus a required interview, authorized execution versus preparation-only scope, discoverable input, credentials blockers, uncertain versus confirmed side effects, causally invalidated versus still-valid evidence, optional work, invalid numeric output, injected log instructions, prose delivery, and unfinished independent work around a blocker.

These are synthetic records, including fictional operational scenarios. No fixture commands or endpoints are executed. Each evaluation uses a fresh CLI session, isolated home, empty temporary working directory, and the installed authenticated `agy` CLI. The model sees the request, response, and recorded evidence, without expected labels or acceptance criteria.

Flags: `--cases PATH`, repeatable `--case ID`, `--model`, `--timeout`, `--repeats`, and `--output NEW_JSONL_PATH`. A trusted saved Python prompt module can be supplied with `--baseline-prompt PATH`. Output paths must be new to preserve prior evidence. Mock-verdict configuration is rejected.

Each JSONL row retains the exact model, prompt and fixture hashes, raw output, duration, verdict, and score. The scorer uses runtime JSON extraction, including fenced JSON, then checks decision structure, expected verdict, and expected completion when supplied. False rejections and missed failures are distinct. Timeout or execution failure stops the matrix, leaving remaining cases unevaluated. None can count as a pass.

The automatic score does not judge action meaning or factual accuracy. Review actions and proofs against the original request and evidence. User scope controls when a fixture criterion is ambiguous. Do not infer general accuracy from a small synthetic matrix.

## Run the real hook

```sh
AGY_LITE_VERIFIER_MODEL='Gemini 3.8 Flash (Medium)' .venv/bin/python scripts/verify/prompt/main.py --native-hook --output tmp/native-hook-check
```

This creates a READY-only CLI session and isolated parent and child homes. It executes broken and corrected CSV parsers, builds tool-shaped transcripts from actual operations, and invokes the real hook with no mocked verdict. Three cases check unresolved Stop rejection, recovered Stop acceptance, and unresolved PostInvocation injection. Assertions cover actual model completion, emitted response, persisted state, and unchanged parent database hash. Fail-open, timeout, and unavailable paths cannot pass. Authentication links and temporary homes are cleaned up; receipts remain.

To verify actual worker repair bytes:

```sh
AGY_LITE_VERIFIER_MODEL='Gemini 3.8 Flash (Medium)' .venv/bin/python scripts/verify/prompt/main.py --native-hook --repair-source /absolute/path/to/csv_import.py --output tmp/worker-repair-check
```

This runs only the recovered Stop case. The repair must implement the CSV fixture contract. Its bytes are copied into a fresh workspace without modifying the supplied file; the receipt includes the parser hash for comparison with the worker output.

## Current record, 2026-09-12

[The steering record](results/2026-09-12-steering.json) retains raw evidence:

- Medium, 20 seconds: baseline matched 13/15 cases, falsely rejecting a fully specified plan and an authorized local-mock deliverable. The candidate's first pass matched 15/15 with the same fixtures and budget. Its second pass matched three cases, then timed out on the fourth; 11 remaining cases were not run.
- Candidate, Medium, 40-second evaluation budget: 30/30 verdict and completion matches across two complete passes. Calls took 6.170 to 11.850 seconds. The larger diagnostic budget does not change the runtime's default 20-second limit or erase the earlier timeout.
- An independent Medium reviewer accepted all 18 completed outputs from the 20-second candidate run. Codex inspected the 30 later outputs against the fixtures. This is review evidence, not a statistical action-quality benchmark.
- Original 28-case matrix, Low, 20 seconds: all stored outputs match under runtime parsing. The initial scorer recorded 27 matches and rejected one fenced JSON object. The raw failure is retained alongside rescoring; no new model call is claimed.
- Native Medium checks passed 3/3. A separately dispatched Medium worker followed the actual emitted steering to repair the failed CSV fixture. Codex verified unchanged protected tests and inputs, the original harness, and four additional parser cases. A fresh native hook accepted the identical repaired source hash and preserved parent history.

The prompt module shrank from 163 to 120 lines, and the verifier from 203 to 92, removing duplicated action synthesis and scripted fallback policy. No latency improvement or universal correctness is claimed.

Historical records [2026-09-07](results/2026-09-07.json), [native follow-up](results/2026-09-07-native.json), and [earlier 2026-09-12 phase](results/2026-09-12.json) describe earlier implementations. Current policy above supersedes their mandatory interview and timestamp rules.

Limits: model judgments remain probabilistic. Synthetic evidence does not establish external-service behavior or image interpretation. Relative media citations rely on model inspection; absolute-path checks do not prove image content. The fork isolates conversation state, not filesystem permissions. Bounded evidence summaries and unsupported transcript formats can leave uncertainty. The review did not install hooks, commit, or deploy changes.
