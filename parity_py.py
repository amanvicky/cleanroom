"""Helper for parity-check.mjs: reads tree.json (file list produced by Node),
scans it with the Python engine, writes py_report.json. Also dumps the rule
table and POPULAR list so the checker can assert the two engines are the same
engine, not just the same output on two samples."""

import json
import sys

import triage


def rule_table():
    rows = []
    for rid, sev, cat, pat, flags, exts in triage.TEXT_RULES:
        rows.append({
            "rule": rid, "severity": sev, "category": cat, "pattern": pat,
            "flags": flags,
            "exts": None if exts is None else sorted(exts),
        })
    return rows


def main(tree_path, out_path):
    with open(tree_path, "r", encoding="utf-8") as fh:
        tree = json.load(fh)
    report = triage.scan_tree([(f["path"], f["text"]) for f in tree])
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "report": report,
            "popular": triage.POPULAR,
            "rules": rule_table(),
            "weights": triage.WEIGHTS,
            "verdict_steps": triage.VERDICT_STEPS,
        }, fh, ensure_ascii=True)
    print("py engine scanned %d files" % len(tree))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
