#!/usr/bin/env python3
"""
optimize_scheduler.py
---------------------
Batching scheduler optimizer for LLM inference requests.
Uses 1D Dynamic Programming per aligned prompt-length cluster to find
the optimal batch partition that minimizes compute cost, padding ratio,
p95 latency, and sequential execution time.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections import defaultdict
from typing import Any, Dict, List

from cost_model import HEADS, HIDDEN, CostModel, align, CostConsts


# Global 8 shapes across both buckets
GLOBAL_SHAPES: List[int] = [64, 128, 192, 256, 384, 512, 640, 2048]


def load_requests(path: str) -> List[Dict[str, Any]]:
    reqs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                reqs.append(json.loads(line))
    return reqs


def get_batch_cost(
    cm: CostModel, reqs_in_batch: List[Dict[str, int]], s_max: int
) -> tuple[float, float, int]:
    """Compute decode cost, batch execution latency, and decode pad tokens."""
    c = cm.c
    k = len(reqs_in_batch)
    g_max = max(r["gen_len"] for r in reqs_in_batch)

    # Decode cost
    a, n = s_max, g_max
    sum_sq = cm._sum_sq_arith(a, n)
    sum_lin = n * a + n * (n - 1) / 2
    cost_decode_per_req = c.Kd_attn * sum_sq + (c.Kd_mlp * HIDDEN) * sum_lin
    cost_decode = k * cost_decode_per_req + c.Kbatch_overhead_cost

    # Batch sequential latency
    sum_sq_lat = cm._sum_sq_arith(a, n)
    sum_lin_lat = n * a + n * (n - 1) / 2
    lat_decode = c.Td_attn * sum_sq_lat + (c.Td_mlp * HIDDEN) * sum_lin_lat
    max_prefill_lat = max(cm._prefill_lat(r["prompt_len"]) for r in reqs_in_batch)
    batch_lat = max_prefill_lat + lat_decode + c.Tbatch_overhead_ms

    # Padding decode tokens
    pad_decode = sum(g_max - r["gen_len"] for r in reqs_in_batch)

    return cost_decode, batch_lat, pad_decode


def dp_partition(
    cm: CostModel,
    s_reqs: List[Dict[str, Any]],
    s_val: int,
    max_batch_size: int,
    alpha: float,
    beta: float,
) -> List[List[Dict[str, Any]]]:
    """Find optimal batch partitioning for requests having the same aligned prompt length."""
    n = len(s_reqs)
    dp = [float("inf")] * (n + 1)
    parent = [-1] * (n + 1)
    dp[0] = 0.0

    for i in range(n):
        if dp[i] == float("inf"):
            continue
        for j in range(i + 1, min(n + 1, i + 1 + max_batch_size)):
            batch = s_reqs[i:j]
            cost_dec, b_lat, pad_dec = get_batch_cost(cm, batch, s_val)
            obj = cost_dec + alpha * b_lat + beta * pad_dec
            if dp[i] + obj < dp[j]:
                dp[j] = dp[i] + obj
                parent[j] = i

    batches: List[List[Dict[str, Any]]] = []
    curr = n
    while curr > 0:
        prev = parent[curr]
        batches.append(s_reqs[prev:curr])
        curr = prev
    batches.reverse()
    return batches


def optimize_plan_for_bucket(
    cm: CostModel,
    reqs: List[Dict[str, Any]],
    shapes: List[int],
    max_batch_size: int,
    alpha: float,
    beta: float,
) -> List[Dict[str, Any]]:
    # Group requests by aligned prompt length
    by_s: defaultdict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in reqs:
        by_s[align(r["prompt_len"], cm.g)].append(r)

    all_batches: List[List[Dict[str, Any]]] = []
    for s in sorted(by_s.keys()):
        # Sort by gen_len within each prompt group
        s_reqs = sorted(by_s[s], key=lambda r: r["gen_len"])
        batches = dp_partition(cm, s_reqs, s, max_batch_size, alpha, beta)
        all_batches.extend(batches)

    # Build plan entries
    plan: List[Dict[str, Any]] = []
    for bid, b_reqs in enumerate(all_batches, 1):
        s_max = max(align(r["prompt_len"], cm.g) for r in b_reqs)
        chosen_shape = min(sh for sh in sorted(shapes) if sh >= s_max)
        batch_id = f"b-{bid:04d}"
        for r in b_reqs:
            plan.append({
                "request_id": r["request_id"],
                "batch_id": batch_id,
                "shape": {
                    "seq_align": chosen_shape,
                    "heads_align": HEADS,
                    "hidden_align": HIDDEN,
                },
            })
    return plan


def write_plan(path: str, plan: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in plan:
            f.write(json.dumps(entry) + "\n")


def run_optimizer() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    in_b1 = root / "input_data" / "requests_bucket_1.jsonl"
    in_b2 = root / "input_data" / "requests_bucket_2.jsonl"
    out_b1 = root / "output_data" / "plan_b1.jsonl"
    out_b2 = root / "output_data" / "plan_b2.jsonl"

    reqs1 = load_requests(str(in_b1))
    reqs2 = load_requests(str(in_b2))

    cm = CostModel(granularity=64)

    # Bucket 1: alpha=100.0, beta=1e6, max_bs=4
    plan1 = optimize_plan_for_bucket(
        cm=cm,
        reqs=reqs1,
        shapes=GLOBAL_SHAPES,
        max_batch_size=4,
        alpha=100.0,
        beta=1_000_000.0,
    )

    # Bucket 2: alpha=1000.0, beta=5e6, max_bs=4
    plan2 = optimize_plan_for_bucket(
        cm=cm,
        reqs=reqs2,
        shapes=GLOBAL_SHAPES,
        max_batch_size=4,
        alpha=1000.0,
        beta=5_000_000.0,
    )

    write_plan(str(out_b1), plan1)
    write_plan(str(out_b2), plan2)

    # Verification
    req1_idx = {r["request_id"]: r for r in reqs1}
    req2_idx = {r["request_id"]: r for r in reqs2}
    m1 = cm.plan_metrics(req1_idx, plan1)
    m2 = cm.plan_metrics(req2_idx, plan2)

    print("=== Plan Bucket 1 Metrics ===")
    for k in ["cost", "pad_ratio", "p95_latency_ms", "sequential_timecost"]:
        print(f"  {k}: {m1[k]}")

    print("=== Plan Bucket 2 Metrics ===")
    for k in ["cost", "pad_ratio", "p95_latency_ms", "sequential_timecost"]:
        print(f"  {k}: {m2[k]}")


if __name__ == "__main__":
    run_optimizer()
