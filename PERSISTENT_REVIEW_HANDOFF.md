# Persistent review implementation handoff

## Current state at the requested 1% boundary

Codex usage now reports 99% used, 1% remaining. No reset credit was consumed.

The canonical teamplay skill has been updated. Gemini's runtime candidate is implemented in an isolated worktree and its implementation attempt exited 0. Codex independently reran the tests successfully, then reproduced acceptance defects. Overall status is **NEEDS_FIXES**. Do not integrate this candidate into the installed runtime yet.

A fresh independent Gemini 3.7 Flash (High) reviewer, assignment A5, is active. Do not edit candidate source until that reviewer finishes. The producer is no longer running.

## Goal and authorization

Implement persistent review and repair in agy-background-agent and the shared teamplay skill. Continue useful repairs while confirmed in-scope defects, unmet requirements, or consequential evidence gaps remain. All known in-scope bugs, including low severity, require verified resolution. Deferred and blocked findings remain incomplete. The user authorized Gemini 3.7 implementation and asked for a handoff near 1% quota.

The skill's reusable review rules are already written. They still need final review for agreement with the repaired runtime. No guarantee of absent undiscovered bugs is intended.

## Primary references

- [Accepted plan](/Users/__blitzzz/Documents/GitHub/agy-background-agent/REVIEW_LOOP_PLAN.md). Its proposal-only status and old Gemini 3.8 preference predate implementation authorization.
- [Detailed implementation instructions](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/implementation-task.md).
- [Current session](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/session/session.yaml), revision 5.
- [Confirmed lead findings](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/lead-findings.md).
- [Lead diagnostic results](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/lead-diagnostics.json).
- [Lead test verification](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/lead-verification.json).
- [Producer report](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/implementation-report.md), which is not acceptance proof.
- [Producer progress](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/implementation-progress.md).
- [Baseline manifest](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/implementation-baseline/manifest.json).
- [Original MMM orchestration source](/Users/__blitzzz/Documents/GitHub/seeda/MMM_REVIEW_ORCHESTRATION.md).

## Updated shared skill

Canonical source: `/Users/__blitzzz/Documents/GitHub/agentic/skills/teamplay/`.

Actual changes include `SKILL.md` and new `references/review-loop.md`. The entry includes persistent continuation, evidence-based closure, independent challenge, and review_path. These source changes are already visible through the Codex skill symlink. Do not claim that means the runtime is integrated.

## Runtime and existing changes

Worktree: `/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/worktrees/persistent-review-loop-20260915`.

Branch: `blitzzz/persistent-review-loop`.

Base commit: `74b0366caa2bc0868b32712c5802c31f1e2548e5`.

Installed source: `/Users/__blitzzz/Documents/GitHub/agy-background-agent`, branch main. Installed hook symlinks point into this source. Runtime edits have remained in the worktree.

A pre-existing first-person steering edit in source `sage/lite/prompt.py` was copied into the worktree before implementation. Preserve it. Baseline manifest records the original SHA-256. The source copy was confirmed unchanged during recovery.

The central agentic repository contains unrelated edits, a deleted old teamplay playbook, and untracked starter/tests. Baseline copies and staged/unstaged patches are in implementation-baseline. Never restore or apply those repository-wide patches wholesale. Current runtime changes are uncommitted; merging the branch alone will not transfer them. Inspect and transfer only the verified task delta while preserving the initial prompt edit.

## Verified and unverified results

Codex verified imports came from the worktree, then ran the full suite: 293 passed and 369 subtests passed. Stop-verifier topic: 74 passed and 5 subtests passed. git diff --check passed. Worker catalog receipt records 187 conformant concepts. These checks do not establish the missing acceptance behavior.

Codex reproduced four concerns in current code: runner cache ignores changed referenced artifacts; coverage gaps disappear from verifier context; scope validation accepts prefix-sibling workspaces and missing assignment identity; automatic discovery conflicts with explicit binding and rollback requirements. See lead-findings.md for locations, observations, and repair instructions. Extra unexecuted hypotheses there must be investigated rather than presented as established failures.

No real-model multi-repair acceptance, fresh independent implementation verdict, or installed-hook integration smoke has passed. Do not substitute a mocked helper test for actual runner or worker behavior.

## Exact producer and reviewer references

Producer A3 conversation: `03441158-a22c-4efa-89b2-a7bb73c791ad`.

Latest producer task: `/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/worktrees/persistent-review-loop-20260915/tmp/dispatch/20260915-220424-dc97dc9a-Resume-exactly-A3-The-initial-CLI-attemp`. Exit code 0; PID 87496 is finished. The earlier attempt exited 3 with no output or edits. One exact-session retry produced this candidate.

[Producer receipt](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/implementation-dispatch.json).

Reviewer A5 conversation: `2e37319d-0563-41a8-b5b4-8e4677a2756d`.

Reviewer PID: `19151`, alive at last check.

Reviewer task: `/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/worktrees/persistent-review-loop-20260915/tmp/dispatch/20260915-223118-f9774907-Assignment-A5-context-revision-5-Session`.

[Reviewer receipt](/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/independent-review-dispatch.json).

Reviewer prompt: `/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/independent-implementation-review-task.md`.

Expected review report: `/Users/__blitzzz/Documents/GitHub/agy-background-agent/tmp/agy/persistent-review-plan-20260915/independent-implementation-review.md`. It was not yet present at the last check.

Reviewer CLI log: `/Users/__blitzzz/.gemini/antigravity-cli/log/cli-20260915_223118.log`.

A5 has requirements and current source, without producer reports or the lead findings. Preserve this independence. Model on every dispatch and resume is explicitly `Gemini 3.7 Flash (High)`.

## Next actions

1. Check reviewer PID, exit.code, logs, and actual report together. Do not start duplicate review or implementation while it is alive and productive. Collect exact task results using the agy extract-dispatch-result.sh helper.
2. Reconcile A5 findings with lead-findings.md through evidence. Keep accepted findings open regardless of severity; reject false findings with proof.
3. Write a bounded repair brief and resume the producer's exact conversation, not the reviewer's or the topic's latest session. Use `AGY_MODEL='Gemini 3.7 Flash (High)'` on every call. Capture the NEW task path and update session/handoff metadata.
4. Repair in the isolated runtime and canonical skill ownership scopes. Follow the full original brief. Add regressions that actually fail on the current candidate and pass after the fix; exercise runtime binding, artifact invalidation, coverage gaps, scopes, no-progress recovery, and emitted hook decisions.
5. Verify stable integrated code and independently challenge it again after fixes. Real-model/native-hook checks must use candidate imports and disposable state. Distinguish unavailable from success and retain failure receipts.
6. Regenerate and strictly validate the central skill catalog after skill edits. Preserve unrelated generated changes.
7. Only after acceptance closes, deliberately integrate the verified runtime delta into the installed source while preserving existing edits. No automatic commit, push, merge, deployment, hook installation, model substitution, global setting changes, or reset-credit consumption is authorized by this handoff.
8. Smoke the actual installed Stop and PostInvocation paths in a disposable task after integration. Verify explicit activation and rollback, more than three productive rounds, and a final accepted stop with current evidence.

Resume template, run from the worktree after writing the concrete repair brief:

```sh
AGY_MODEL='Gemini 3.7 Flash (High)' DISPATCH_TIMEOUT=0 \
  /Users/__blitzzz/Documents/GitHub/agentic/skills/agy/scripts/antigravity-dispatch.sh \
  --prompt-file /absolute/path/to/repair-brief.md \
  --resume 03441158-a22c-4efa-89b2-a7bb73c791ad --detach
```

The wrapper still passes a 10-minute print timeout even with outer DISPATCH_TIMEOUT=0. A cutoff ends an attempt, not task acceptance. Collect artifacts and choose an evidence-based recovery. Do not endlessly retry a silent run.
