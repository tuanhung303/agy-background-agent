#!/usr/bin/env python3
"""
sage_stats.py - Aggregate sage behavior from /tmp/agy_sage.log or /tmp/agy_stop_audit.log.

Gives tuning data instead of vibes: hold/fire/dedup rates, demotion-relevant
signals, breaker trips, recap yield. Usage:
    scripts/telemetry/sage_stats.py [--log PATH] [--days N] [--since YYYY-MM-DD]
"""
import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] (.*)$")

PATTERNS = [
    ("steers_fired", re.compile(r"Mid-turn (?:sage|advisor) triggered steer")),
    ("watchouts_emitted", re.compile(r"Mid-turn (?:sage|advisor) watchout emitted")),
    ("advice_deduped", re.compile(r"(?:Sage|Advisor) advice deduplicated")),
    ("sage_holds", re.compile(r"Mid-turn (?:sage|advisor) passed \(healthy\)")),
    ("cascade_errors", re.compile(r"(?:sage|advisor) unavailable")),
    ("breaker_trips", re.compile(r"circuit breaker open")),
    ("final_steers", re.compile(r"Final (?:sage|advisor)-first steer")),
    ("recaps", re.compile(r"Recap recorded")),
    ("user_yields", re.compile(r"Fresh user input detected")),
]


def main():
    ap = argparse.ArgumentParser()
    default_log = "/tmp/agy_sage.log" if os.path.exists("/tmp/agy_sage.log") else "/tmp/agy_stop_audit.log"
    ap.add_argument("--log", default=default_log)
    ap.add_argument("--days", type=int, default=None, help="only last N days")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound")
    args = ap.parse_args()

    cutoff = None
    if args.days:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    if args.since:
        cutoff = max(cutoff, args.since) if cutoff else args.since

    counts = Counter()
    modes = Counter()
    categories = Counter()
    total_lines = 0
    try:
        fh = open(args.log, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"cannot read {args.log}: {e}", file=sys.stderr)
        return 1
    with fh:
        for line in fh:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            total_lines += 1
            day, _time, msg = m.groups()
            if cutoff and day < cutoff:
                continue
            for key, pat in PATTERNS:
                if pat.search(msg):
                    counts[key] += 1
            pm = re.search(r"(?:Sage|Advisor) prompt mode: (\w+)", msg)
            if pm:
                modes[pm.group(1)] += 1
            cm = re.search(r"\[(?:STEER|WATCH)\·([a-z_]+)", msg)
            if cm:
                categories[cm.group(1)] += 1

    print(f"log={args.log}  lines={total_lines}" + (f"  since={cutoff}" if cutoff else ""))
    print("-" * 52)
    labels = {
        "steers_fired": "Mid-turn steers",
        "watchouts_emitted": "Mid-turn watchouts",
        "advice_deduped": "Advice deduplicated",
        "sage_holds": "Mid-turn holds",
        "cascade_errors": "Cascade errors",
        "breaker_trips": "Breaker trips",
        "final_steers": "Final steers",
        "recaps": "Recaps",
        "user_yields": "User yields",
    }
    for key, _pat in PATTERNS:
        print(f"{labels[key]:22} {counts[key]:>6}")
    print("-" * 52)
    fired = counts["steers_fired"] + counts["watchouts_emitted"]
    evals = fired + counts["sage_holds"] + counts["advice_deduped"]
    if evals:
        print(f"evaluations             {evals:>6}  (fire {fired / evals:.0%}, dedup {counts['advice_deduped'] / evals:.0%})")
    if modes:
        print(f"prompt modes            initial={modes['initial']} update={modes['update']}")
    if categories:
        top = ", ".join(f"{k}:{v}" for k, v in categories.most_common(5))
        print(f"categories              {top}")
    return 0

    print("-" * 52)
    fired = counts["steers_fired"] + counts["watchouts_emitted"]
    evals = fired + counts["advisor_holds"] + counts["advice_deduped"]
    if evals:
        print(f"evaluations             {evals:>6}  (fire {fired / evals:.0%}, dedup {counts['advice_deduped'] / evals:.0%})")
    if modes:
        print(f"prompt modes            initial={modes['initial']} update={modes['update']}")
    if categories:
        top = ", ".join(f"{k}:{v}" for k, v in categories.most_common(5))
        print(f"categories              {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
