# Persistent review and repair plan

Status: proposal only, 2026-09-15. No runtime or shared skill changes have been applied.

## Recommendation

Merge the reusable review rules from [MMM_REVIEW_ORCHESTRATION.md](/Users/__blitzzz/Documents/GitHub/seeda/MMM_REVIEW_ORCHESTRATION.md) into teamplay, then make the stop verifier support the same completion contract. I interpret "this repo" as `agy-background-agent`, the current workspace.

Continue for as many useful repair rounds as the accepted outcome requires. Completion means all confirmed in-scope defects are verified resolved and the agreed acceptance checks hold on the final relevant state. Severity determines repair order, not permission to leave known bugs open. This establishes a defensible completion boundary; it cannot prove that no undiscovered bug exists.

The lead owns task completion. A worker may finish its bounded assignment while the lead continues the larger review loop.

## What the source gets right

The strongest rules are bounded file ownership, retained dispatch receipts, independent criticism, verification of actual behavior, and reopening acceptance when later evidence contradicts a green result. The partial-week export example is useful because matching request dates and passing tests still failed to establish matching numerical coverage.

The source also avoids repeated green test runs without a reason. Preserve that rule. Extend coverage through concrete counterexamples, boundary cases, and related paths implicated by a defect's mechanism.

Before generalizing, tighten these gaps:

- `fixed` must mean implementation reported, with a separate `verified` state before closure.
- "Once stable" needs observable acceptance evidence, complete finding dispositions, and a final independent challenge.
- No clean-review count or elapsed duration proves completion. A later contradiction reopens the affected acceptance even after earlier approvals.
- False findings need an evidence-backed rejection or withdrawal. Review disagreement needs investigation, not majority voting or cosmetic fixes.
- Budget exhaustion, stalled progress, unavailable verification, and external blockers need distinct incomplete outcomes and resumable context.

An independent Gemini source review reinforced the ownership, evidence, and false-finding checks. Two of its proposals are intentionally excluded: allowing a deferred finding to count as complete, and requiring earlier progress without regression before permitting another repair. Deferral closes no requirement unless the user explicitly changes its scope. A failed repair or new regression creates work to investigate, not permission to abandon it. Unmet acceptance or missing evidence can require continuation even before a defect has a confirmed source location.

Keep Seeda's tenant rules, exact task paths, model choices, polling intervals, 4% quota threshold, and local-before-staging restriction in their existing project or dispatch sources. Retain the export incident as an example, not a universal requirement to run browser checks for every task.

## Current runtime facts

These observations come from the current working tree, not historical test results.

| Current behavior | Consequence for this request |
|---|---|
| [runner.py:86](/Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py:86) checks the rejection limit before executing the next audit; the configured default is three | Productive repairs can reach a clean-stop path before their next review |
| [runner.py:72](/Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py:72) hashes the transcript and bounds identical rejection replays | Duplicate hook events have protection, but transcript changes alone do not demonstrate progress or current artifact validity |
| [session_state.py:76](/Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/session_state.py:76) retains counters and the latest rejection | There is no structured cross-round finding or acceptance record connected to teamplay |
| [runner.py:129](/Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py:129) permits stopping when verification is unavailable, recording an unavailable status | Honest status reporting exists; sustained work still needs an explicit recovery or handoff policy |
| [runner.py:36](/Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py:36) skips child sessions and active background work | The lead must collect and verify delegated results; a worker exit cannot close the parent task |
| The verifier forks the producer conversation | It has useful context but does not satisfy a blind independent-review requirement |

Existing `prompt.py` guidance already distinguishes required work, optional improvements, real blockers, stale evidence, and unsupported claims. Preserve those decisions and its model-generated corrective actions.

Both this repo's `sage/lite/prompt.py` and the shared teamplay source have pre-existing edits. Teamplay's starter is also untracked in the central repository, and its former playbook is deleted. Build on the working versions and preserve unrelated changes.

The installed hook symlinks currently resolve into this checkout. The Codex teamplay symlink resolves into the central skill directory. Develop runtime changes in an isolated checkout, then integrate verified changes deliberately because editing this checkout can affect subsequent live hook invocations.

## 1. Merge the reusable contract into teamplay

Own changes in `/Users/__blitzzz/Documents/GitHub/agentic/skills/teamplay/`.

Keep `SKILL.md` as the short entry point. Add the continuation and completion rules there, with a required direct reference to `references/review-loop.md` when coordinating sustained review and repair. Put detailed records, recovery examples, and the adversarial acceptance examples in that reference. Preserve the current session and ownership protocol.

Proposed core text:

> Treat each worker result as a reported attempt until the lead verifies its acceptance against current artifacts. Continue review and repair while any confirmed in-scope defect, unmet requirement, or consequential evidence gap remains actionable. Resolve all confirmed in-scope defects, including low-severity ones; keep disputed findings open until evidence supports their disposition. Reuse valid evidence and recheck affected behavior after relevant changes or contradictions. Before completion, verify the integrated result and obtain independent criticism that attempts to falsify the accepted outcome. A new confirmed defect reopens the affected acceptance. Do not stop because a worker exited, a fixed number of rounds elapsed, or one test suite passed. When progress stalls, change the investigation or assignment using the evidence. Resource limits and genuine blockers require an explicit incomplete handoff with remaining work and next actions. Only the lead closes the overall task after every required assignment and acceptance obligation is accounted for.

The detailed reference should define:

- Finding states: `proposed`, `confirmed`, `fix_reported`, `verified`, `rejected`, `withdrawn`, `blocked`. Reopening retains the same ID and prior evidence. Only `verified`, `rejected`, and `withdrawn` resolve a finding. A blocked confirmed bug remains open.
- A stable finding ID, reproduction or source trace, affected requirement, owner, evidence references, repair reference, independent verification, and disposition reason.
- A scope and coverage matrix suited to the task, including negative and boundary cases. Expand it when a discovered mechanism implicates other in-scope paths. Track unrelated defects separately; do not silently broaden authorized edits.
- A final independent challenge of the weakest remaining assumption. For numerical outputs, verify semantics and conservation. For runtime or UI behavior, exercise the active consumer. For prose-only work, source review may suffice.
- Same-session repair continuity, fresh context for independent review, stable-file integration checks, and resumable handoffs that preserve exact worker references.

No automatic minimum number of review rounds. If the final challenge is clean and all obligations are verified, finish. If it exposes another issue, continue without a productive-round cap.

## 2. Connect teamplay evidence to the verifier

Use one lead-owned review record, referenced from `session.yaml`. Proposed filename: `review-state.json`. JSON keeps the runtime stdlib-only; avoid adding a YAML parser or a second orchestration service.

Keep assignment ownership in the existing session. The review record owns findings and acceptance evidence. Link the records through session ID, context revision, and assignment IDs rather than maintaining competing copies of their statuses.

The versioned record should include:

- Explicit session, workspace, and audit scope: overall lead task or one worker assignment.
- Accepted outcomes and the relevant final artifact or deployment identities, including uncommitted content where applicable.
- Finding dispositions and evidence references, coverage gaps, outstanding review obligations, and references to uncollected assignments.
- Prior repair attempts, verified progress or new diagnostic evidence, blockers, and handoff information.

Bind this record through an explicit per-run path and assignment scope. Do not find it by scanning unrelated sessions or resuming the topic's latest conversation blindly. The lead alone writes the authoritative record; hooks retain their own audit receipts and propose updates.

Suggested new adapter: `sage/lite/review_context.py`. It validates the version, task identity, scope, file bounds, and evidence references, then supplies a bounded context to the existing verifier. Record omissions explicitly. A ledger entry is a claim supported by referenced artifacts, not proof merely because its status says `verified`.

Make the bridge optional for ordinary standalone tasks. Once a task explicitly binds a required review record, missing, malformed, mismatched, or stale data cannot silently downgrade it to a weaker completion check. Preserve the incomplete obligation and identify the record problem.

## 3. Replace the total-strike exit with progress-sensitive persistence

Primary files: `sage/lite/runner.py`, `sage/config.py`, `sage/session_state.py`, `sage/lite/prompt.py`, and `sage/lite/schemas.py`. Update `sage/lite/verifier.py` for context transport and `statusline/statusline.py` for distinct outcomes.

Introduce an explicit persistent-loop mode for sustained teamplay tasks. Retain existing bounded behavior for unbound callers during the initial rollout. Separate these controls:

| Situation | Proposed behavior |
|---|---|
| Confirmed work remains and repairs or investigation produce useful evidence | Continue; total past rejections do not trigger a stop |
| Duplicate hook notification for identical audited state | Deduplicate delivery without another model call or strike; unresolved work remains unresolved |
| Repeated attempt makes no supported progress | Ask the model to identify the failed approach and next useful change; the lead can narrow scope, change method, or assign fresh independent investigation |
| Repeated no-progress recovery also fails | Record `stalled` with evidence and a concrete handoff; do not claim an external blocker without evidence |
| Verification is unavailable | Bound provider recovery, preserve unresolved work, and report `unavailable`; never invent a corrective action or a success |
| Time, quota, or context boundary prevents further work | Preserve a checkpoint and explicit handoff; retain active assignment ownership and exact collection instructions |
| Genuine external blocker | Finish independent feasible work, then record the dependency and resumption condition |
| User cancels or changes scope | Respect that instruction and retain the disposition of affected work |

Retain bounded model-call deadlines, concurrency locks, child recursion protection, and duplicate-delivery protection. A long-running task consists of bounded calls and useful rounds, not one unbounded hook call.

The model judges progress from evidence and writes task-specific steering. Mechanical checks establish identity, freshness, counters, and definite contradictions. New prose, a touched file, or a changed transcript hash alone must not reset the no-progress counter.

Keep total rounds as telemetry. Track consecutive unsupported repeats separately. Reset the latter only when evidence supports progress on an open obligation or resolves a material uncertainty. Preserve unresolved finding history across repair turns and explicit continuation handoffs; isolate unrelated requests.

Audit caching must cover the relevant artifact identities, review-record revision, assignment scope, and configuration as well as the transcript. A file edited by another worker can invalidate earlier acceptance without changing the audited transcript. Unknown relevant identity should trigger fresh inspection instead of cached success.

The stop hook remains an auditor. The teamplay lead owns dispatch, independent review, and integration. A fresh worker is only selected within the applicable backend and model authorization. A stalled hook must not recursively spawn its own reviewer fleet.

## 4. Implement in bounded assignments

1. Codex establishes the shared record contract, completion rules, baseline snapshots, and acceptance scenarios.
2. A skill worker owns `teamplay/SKILL.md`, the new reference and record example, plus narrowly necessary starter changes. The current lightweight starter remains backward compatible; do not force extra files onto every small task.
3. A runtime worker owns persistence, record intake, and runner behavior after the shared contract is fixed. Keep overlapping prompt/schema changes sequenced within this assignment.
4. An independent evaluator owns scenario fixtures and expected outcomes. It receives requirements and artifacts without implementation verdicts. The evaluator must not repair the implementation it reviews.
5. Codex integrates the changes, verifies actual artifacts and emitted hook decisions, and resolves review findings through the original owners.

Use agy with `AGY_MODEL='Gemini 3.8 Flash (Medium)'` on every implementation dispatch and resume, as required by the current workspace instructions. Retain exact dispatch and conversation references. Keep independent review separate and disclose any unavailable independence.

## 5. Acceptance and evaluation

Before implementation, establish baseline behavior on the current working versions. Test runtime state transitions separately from probabilistic model judgment, then test complete worker interactions.

| Scenario | Required result |
|---|---|
| More than three productive repair rounds, followed by a clean result | All necessary rounds occur; stop only after final acceptance |
| Worker says done or offers to finish later with an open assigned bug | Task-specific continuation |
| Only a low-severity confirmed in-scope defect remains | Continue until its fix is verified |
| A reviewer suggests deferring an accepted bug, or a repair introduces a regression | Keep acceptance open and investigate; no unilateral scope waiver |
| Tests pass but the actual export uses the wrong date basis | Acceptance reopens and follows the faulty aggregation path |
| Wrong-project typecheck or a test with incorrect expected arithmetic | Do not accept the green result as proof of the required behavior |
| Reviewer flags unreachable legacy code | Investigate and reject or withdraw with evidence; no unnecessary repair |
| Another worker changes an accepted artifact without changing this transcript | Relevant cached acceptance is invalidated |
| Unrelated content changes or repeated completion prose | Do not count this as progress on an unresolved obligation |
| Duplicate Stop and PostInvocation events | No duplicate audit or uncontrolled steering loop |
| One worker's assignment is verified while other assignments remain open | Worker can finish; parent task remains open |
| Context handoff, compaction, or restart | Open findings, evidence, assignment scope, and exact worker references survive |
| True blocker, unavailable verifier, user cancellation, or exhausted resources | Distinct truthful status; no verified-completion claim |
| Explanation-only task or fully accepted small edit | Stop promptly without imposing a repair ceremony |

Run relevant unit and integration suites while repairing. After integration stabilizes, run the full local suite and the relevant verification topic once:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify/all.py --topic stop_verifier
```

Extend the existing real-model and native-hook harnesses under `scripts/verify/prompt/` for repeated repair and handoff behavior. Compare baseline and candidate with identical tasks, model settings, permissions, and budgets in disposable workspaces. Reserve unseen variants, repeat proportionately to variability, and independently inspect actual repair bytes and emitted Stop/PostInvocation decisions. Synthetic verdict matches alone do not establish that a worker follows steering successfully.

Record premature stops, erroneous continuations, resolved and remaining defects, completed acceptance coverage, recovery behavior, latency, and known usage. Preserve failed and unevaluated runs. A longer run or more tests is not itself a better result.

For the shared skill, run starter tests if its behavior changes, verify reference links and backward compatibility, then run from `/Users/__blitzzz/Documents/GitHub/agentic/skills/`:

```sh
uv run scripts/gen_catalog.py
uv run ~/.hermes/skills/validate/scripts/okf_validate.py .okf --strict
```

Review generated diffs against the existing dirty baseline. Do not restore or sweep unrelated skill changes into this work.

## Delivery order and completion evidence

Ship as three reviewable units: shared contract and skill, runtime support, then integrated evaluation and rollout documentation. Stage them so persistent behavior is enabled for the intended task only after its runtime support exists.

Pilot on one isolated multi-defect task and verify the actual configured hook path, model, persisted findings, repeated continuation decisions, final artifact contents, and final accepted stop. After integration into the installed checkout, smoke the installed Stop and PostInvocation paths with a disposable task. Keep rollback to bounded mode available during rollout.

The current work delivers this plan only. Runtime tests and proposed behavior evaluations have not been run because no implementation changes were made.
