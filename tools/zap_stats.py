"""Summarise /tmp/fbc_csc_timing.csv into a clean before/after table.

Usage on the box:
    python3 /tmp/zap_stats.py

The CSV is written by the plugin's ZapInterceptor every time a zap
completes (WRAP fired and evTunedIn observed). Columns:
    epoch,attr,result,delta_ms
Where result is HIT, MISS, EXT, or ? (when cfg.enabled was off at
zap time).
"""

import csv
import os
import sys


CSV = "/tmp/fbc_csc_timing.csv"


def main():
    if not os.path.exists(CSV):
        print("No data yet at %s - zap a few times first." % CSV)
        return 1

    rows = []
    with open(CSV) as fh:
        for r in csv.DictReader(fh):
            try:
                r["delta_ms"] = float(r["delta_ms"])
                rows.append(r)
            except (ValueError, KeyError):
                continue

    if not rows:
        print("CSV has header but no data rows yet.")
        return 1

    buckets = {}  # (attr, result) -> list of delta_ms
    for r in rows:
        key = (r["attr"], r["result"])
        buckets.setdefault(key, []).append(r["delta_ms"])

    print()
    print("%-12s %-6s %5s %8s %8s %8s %8s" % (
        "attr", "result", "n", "min", "median", "mean", "max"))
    print("-" * 60)
    for key in sorted(buckets):
        v = sorted(buckets[key])
        n = len(v)
        median = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0
        mean = sum(v) / n
        print("%-12s %-6s %5d %8.1f %8.1f %8.1f %8.1f" % (
            key[0], key[1], n, v[0], median, mean, v[-1]))
    print()
    print("Total samples: %d" % len(rows))
    print("Tip: toggle the master switch and collect more samples for a before/after.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
