# Stop verifier judgment evaluation

The verifier uses domain knowledge to interpret evidence, investigate material risks beyond listed examples, and distinguish real defects from expected, recovered, or unrelated failures. Binding requirements retain their scope and exceptions. Speculative improvements alone do not justify rejection; material unknowns cannot be dismissed without evidence.

The verdict prompt and rejection guidance share `JUDGMENT_GUIDANCE` in `sage/lite/prompt.py`. Rejection synthesis receives both the full turn summary and the latest command so a single result does not hide recovery or unresolved earlier failures.

## Policy changes

This revision replaces the universal executive-presentation standard with the requested purpose and audience. It scopes HTTP errors to HTTP interfaces, data repeatability checks to repeatable operations, and performance checks to relevant query behavior. Visual and browser checks distinguish unexpected errors from explicitly tested error or empty states. It permits rerunning a failed or invalidated check and replaces an impossible guarantee about every hidden flaw with evidence about applicable obligations and consequential risks.

The current-turn-only freshness rule, `/plan` interview, sibling universe coverage, persistent topic verification paths, environment boundaries, and JSON verdict interface remain. The deterministic proof validator and runtime circuit-breaker behavior are unchanged. Their permissive string heuristics are a separate limitation; model evaluation results do not establish end-to-end evidence provenance enforcement.

## Run

Use the repository Python environment with the installed `agy` CLI and its configured authentication:

```sh
.venv/bin/python scripts/verify/all.py --topic prompt
.venv/bin/python scripts/verify/prompt/main.py --repeats 2
```

The topic runs 12 controlled synthetic cases against the configured verifier model, with the runtime's configured per-call timeout. These include expected HTTP denial versus broken authorized access, recovered execution versus an unrelated later check, factual versus contradicted empty-data findings, duplicate-delivery behavior inferred from an inspected queue contract, valid versus unsupported blockers, missing evidence, and the retained planning interview.

Each case uses a fresh CLI session in an isolated home and an empty temporary working directory. The model sees the fixture request, agent response, and recorded evidence; it does not see the case id or expected verdict. Fixtures are synthetic records, not actual service operations or image-inspection tests. The model is told to assess the record rather than execute fixture paths.

To compare with a saved trusted baseline prompt module:

```sh
git show <baseline-commit>:sage/lite/prompt.py > tmp/baseline-prompt.py
.venv/bin/python scripts/verify/prompt/main.py --baseline-prompt tmp/baseline-prompt.py --repeats 2
```

Optional flags include `--case <id>` (repeatable), `--model`, `--timeout`, and `--output <new-jsonl-path>`. Baseline modules are imported as Python code and must be trusted. An explicit output path must not already exist, preserving earlier evidence.

## Interpret evidence

Each JSONL row retains the model, fixture and prompt hashes, repetition number, duration, raw stdout/stderr, parsed verdict, and score. The runner rejects mocked verdict configuration. It calls the CLI directly so runtime fail-open defaults cannot create a successful evaluation.

A match requires the expected verdict and valid nonempty PASS proof or a nonempty FAIL action. Invalid JSON, malformed verdicts, missing fields, empty PASS proof, false rejections, and missed failures fail the suite. Timeout or execution failure stops evaluation and leaves remaining cases unevaluated. Never report this as a completed matrix or treat unavailable cases as passes.

Inspect rationales against the fixture evidence in addition to the automatic score. The oracle checks verdict and output structure, not whether every cited fact or corrective action is sound. Report false rejections and missed failures separately, and use repeated runs and new domains before claiming general reduction in false positives. These fixtures cover judgment on supplied records; they do not verify tool access, screenshot interpretation, real service behavior, or the complete hook lifecycle.

## Recorded evaluation

[The 2026-09-07 record](results/2026-09-07.json) retains 24/24 expected verdict matches for the revised prompt, covering all 12 cases twice with Gemini 3.8 Flash (Low) and a 20-second per-call budget. The baseline at `722f81f172fc854051bf9e665e9ea11c94b8d443` matched its first three cases, then timed out on the fourth. Its remaining 20 planned evaluations were not run. This incomplete comparison does not establish an improvement rate over the old prompt.
