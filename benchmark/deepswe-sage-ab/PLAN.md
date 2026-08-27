# DeepSWE Local Harness — agy duo-agent improvement loop

## Purpose
Measure agy worker performance on the 2 hardest DeepSWE tasks, WITHOUT vs WITH
the sage background agent, then improve the real sage modules until measured
performance improves (fewer turns on a pass, or passing a harder task). No
one-off task-specific tricks.

## Tasks (hardest by reference-solution size, feature category)
1. koota-deferred-mutation-buffer   (pmndrs/koota @ 31cbe9a1a2, TS/pnpm/vitest; f2p=71 p2p=128)
2. valibot-recursive-schema-composition (open-circle/valibot @ 50016c77c8, TS/pnpm/vitest; f2p=10 p2p=209)

## Grading (local, no Docker)
Clone upstream repo at base_commit into /tmp/deepswe/<task>-<arm>/work,
agent works there, then:
- apply tests/test.patch (from benchmark tasks dir)
- run the exact test.sh commands with vitest junit reporter
- convert junit->CTRF is bypassed: we count node-ids directly from junit XML
  using the SAME "<classname>: <name>" composition as --use-suite-name.
- reward = f2p fraction and p2p keep-fraction per grader.py whitelist logic
  (missing-from-report == failed).

## Arms (each task runs twice)
- arm "sage-off": AGY_SAGE_DISABLED — plain agy worker, no background agent
- arm "sage-on": installed hooks live; measure turns + wall time + reward

Turn counting: number of PLANNER_RESPONSE steps in the brain transcript for
that conversation between dispatch time and completion marker.

## Rules
- Same instruction.md prompt verbatim for both arms.
- Same model ("Gemini 3.7 Flash (High)") both arms.
- Improvements must land in sage/ modules (general mechanisms), never parse
  the task text or hardcode APIs from these repos.
- Loop: run -> grade -> analyze failure modes in sage -> improve -> re-run.
