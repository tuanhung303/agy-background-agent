# Benchmark: Sage ON vs Sage OFF on Terminal-Bench 2.1 (LLM Inference Batching Scheduler)

## Overview

[Terminal-Bench 2.1](https://github.com/harbor-framework/terminal-bench-2-1) benchmarks autonomous agents across complex terminal and software engineering environments. Across the 89 tasks in the benchmark suite, `llm-inference-batching-scheduler` represents one of the most demanding systems optimization tasks.

The task requires designing and implementing a static-graph batching scheduler for LLM inference on specialized hardware accelerators (TPUs/RDUs). The scheduler must pack incoming inference requests into batches while respecting strict hardware granularity alignment (multiple of 64 tokens), a hard limit of at most 8 unique tensor shapes across all datasets, 100% request coverage, and beating four strict analytical cost and latency thresholds.

## Problem Formulation & Constraints

- **Input**:
  - `requests_bucket_1.jsonl` (800 requests)
  - `requests_bucket_2.jsonl` (800 requests)
- **Constraints**:
  - Max 8 unique global shapes `(seq_align, heads_align=32, hidden_align=4096)` combined across both buckets.
  - `seq_align` must be a multiple of 64 and $\ge \text{align}(\text{prompt\_len}, 64)$ for every request in the batch.
  - Batch consistency: all requests in a batch share identical tensor shape.
  - Complete 1-to-1 coverage: zero missing or duplicate `request_id` entries.
- **Strict Performance Thresholds**:
  - **Bucket 1**: Cost $\le 3.0 \times 10^{11}$, Pad Ratio $\le 0.055$, P95 Latency $\le 2.1 \times 10^6$ ms, Sequential Timecost $\le 2.7 \times 10^8$ ms.
  - **Bucket 2**: Cost $\le 4.8 \times 10^{10}$, Pad Ratio $\le 0.150$, P95 Latency $\le 2.1 \times 10^5$ ms, Sequential Timecost $\le 3.2 \times 10^7$ ms.

---

## Experimental Setup

Two workers were spawned simultaneously in Orca split panes via `orca-agy.sh` using Gemini 3.7 Flash:

1. **Arm 1 (Sage ON - `tb-sched-on`)**: Supervised with Sage background supervisor.
2. **Arm 2 (Sage OFF - `tb-sched-off`)**: Unsupervised baseline with `AGY_SAGE_DISABLED=1` via `--sage-off`.

Both workers were given identical self-contained task briefs and clean worktrees.

---

## Quantitative Results

### Performance Metrics Comparison

| Metric | Threshold | Baseline Packer | Arm 2: Sage OFF | Arm 1: Sage ON | Delta (Sage ON vs OFF) |
|---|---|---|---|---|---|
| **Bucket 1: Cost** | $\le 3.0 \times 10^{11}$ | $2.4830 \times 10^{12}$ | $2.9445 \times 10^{11}$ | **$2.8765 \times 10^{11}$** | **-2.3% cheaper (-6.80B)** |
| **Bucket 1: Pad Ratio** | $\le 0.055$ | $1.4363$ | **$0.05093$** | $0.05134$ | +0.8% |
| **Bucket 1: P95 Latency (ms)** | $\le 2.1 \times 10^6$ | $1.3157 \times 10^7$ | $2.0434 \times 10^6$ | $2.0434 \times 10^6$ | Parity |
| **Bucket 1: Sequential Timecost (ms)** | $\le 2.7 \times 10^8$ | $4.8973 \times 10^7$ | $2.6788 \times 10^8$ | **$2.3102 \times 10^8$** | **-13.8% faster (-36.86M ms)** |
| **Bucket 2: Cost** | $\le 4.8 \times 10^{10}$ | $1.6673 \times 10^{12}$ | **$4.5688 \times 10^{10}$** | $4.6797 \times 10^{10}$ | +2.4% |
| **Bucket 2: Pad Ratio** | $\le 0.150$ | $4.0430$ | $0.13696$ | **$0.13643$** | **-0.4% less padding** |
| **Bucket 2: P95 Latency (ms)** | $\le 2.1 \times 10^5$ | $3.4104 \times 10^6$ | **$1.9237 \times 10^5$** | $1.9534 \times 10^5$ | +1.5% |
| **Bucket 2: Sequential Timecost (ms)** | $\le 3.2 \times 10^7$ | $1.1463 \times 10^7$ | $3.1567 \times 10^7$ | **$2.9077 \times 10^7$** | **-7.9% faster (-2.49M ms)** |
| **Global Unique Shapes Used** | $\le 8$ | 8 | 7 shapes | **8 shapes (optimal)** | Full budget utilization |
| **Unit & Integration Tests** | 6/6 pass | 0/6 | 6/6 PASS | **6/6 PASS** | Full verification |

---

## Qualitative & Execution Analysis

### 1. Shape Allocation & Algorithmic Strategy
- **Sage ON**: The supervisor pinned the requirement to optimize shape selection alongside batch sizing. The worker utilized all 8 allowed shape slots `[64, 128, 192, 256, 384, 512, 640, 2048]`, incorporating the intermediate shape `256` to eliminate over-padding on medium-length prompts. It implemented a 1D Dynamic Programming partitioner (`dp_partition`) per prompt cluster that minimized a joint cost-latency-padding objective.
- **Sage OFF**: Selected only 7 shapes `[64, 128, 192, 384, 512, 640, 2048]`, leaving an unused shape slot. This caused requests with prompt lengths between 193 and 256 to be padded all the way to 384, directly contributing to the **13.8% slower sequential runtime**.

### 2. Artifact Quality & Code Discipline
- **Sage ON**: Authored a clean, modular, and self-contained optimizer script `optimize_scheduler.py` in `environment/task_file/scripts/`, allowing automated re-running, profiling, and parameter tuning.
- **Sage OFF**: Executed transient Python heredocs directly in shell stdin (`python3 - << 'EOF' ...`) without persisting the optimization tool script to the repository.

### 3. Verification & Safety Loop
- **Sage ON**: Sage's goal-pinning kept the test verification requirement active (`proven by pytest tests/test_outputs.py`). The worker ran the full test suite and checked both unit assertions and integration metrics before signaling `計画通り:`.
- **Sage OFF**: Relied on standard shell command inspection and `wc -l` checks.

---

## Conclusion

Sage supervision drove higher solution quality on complex algorithmic optimization:
1. **13.8% faster execution speed** and **2.3% lower compute cost** on the primary workload bucket by properly utilizing the global shape allocation budget.
2. **Reproducible engineering artifacts**: Produced a standalone optimization pipeline rather than throwaway inline scripts.
3. **100% test pass rate** verified against the official test suite.
