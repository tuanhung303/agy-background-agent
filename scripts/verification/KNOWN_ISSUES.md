# Known issues & accepted limitations

1. **Orca `dispatch --inject` blocked for agy tabs** (`agent_prompt_blocked`):
   Orca does not recognize agy's ready state after the workspace-trust dialog.
   Workaround: tracked dispatch (no `--inject`) + `orca terminal send` of the spec.
   Consequence: no `worker_done` authority for agy workers — poll artifacts
   (SUMMARY.md, tests) instead. Worth reporting upstream to Orca.

2. **Hook env isolation**: hook processes do not inherit a shell's exports when
   agy runs them outside the TUI environment. Profile tuning must use the config
   overlay file (`~/.config/agy/sage.env`, `AGY_SAGE_ENV_FILE`, or legacy `~/.config/agy/advisor.env`);
   `scripts/benchmarks/bench_env.sh` only works for direct headless runs from that shell.

3. **Turn-boundary clear may log repeatedly** (observed 5x) until an early-exit
   path persists the new turn_key. Idempotent and harmless; noise only.

4. **parallelize firing is probabilistic** (model judgment). Silent hold on
   healthy serial work is correct; see scripts/bench_scenarios/ for the trigger
   scenario and observation points.
