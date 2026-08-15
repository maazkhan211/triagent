"""
Evaluates severity classification accuracy on a curated set of edge cases
(data/eval_edge_cases.json) where keyword-based rules are known to be misleading
(e.g. "FATAL" on a misconfigured staging alert, "retry" on a total outage).

Compares:
  - rule-only  (severity.classify_rule_based)
  - LLM-only   (severity.classify_llm)
against each case's true_severity (assigned by reasoning through the full scenario),
and writes a markdown report to eval_results/severity_eval.md.

Run:
    python scripts/eval_severity.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage_agent.config import EVAL_EDGE_CASES_PATH, ROOT_DIR
from triage_agent.severity import classify_rule_based, classify_llm

OUT_PATH = ROOT_DIR / "eval_results" / "severity_eval.md"


def main():
    with open(EVAL_EDGE_CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    rows = []
    rule_correct = 0
    llm_correct = 0

    for case in cases:
        raw_log = case["raw_log"]
        true_sev = case["true_severity"]

        rule_sev, matched_kw = classify_rule_based(raw_log)
        llm_result = classify_llm(raw_log)
        llm_sev = llm_result["severity"] if llm_result else None

        rule_ok = rule_sev == true_sev
        llm_ok = llm_sev == true_sev
        rule_correct += int(rule_ok)
        llm_correct += int(llm_ok)

        rows.append(
            {
                "case_id": case["case_id"],
                "true_severity": true_sev,
                "rule_severity": rule_sev,
                "rule_matched_keyword": matched_kw,
                "rule_correct": rule_ok,
                "llm_severity": llm_sev,
                "llm_reasoning": llm_result["reasoning"] if llm_result else "(LLM unavailable)",
                "llm_correct": llm_ok,
                "why_rules_fail": case.get("why_rules_fail", ""),
            }
        )
        status_rule = "correct" if rule_ok else "WRONG"
        status_llm = "correct" if llm_ok else "WRONG"
        print(f"{case['case_id']}: true={true_sev:9s} rule={rule_sev:9s}[{status_rule:7s}] llm={llm_sev or '?':9s}[{status_llm:7s}]")

    n = len(cases)
    rule_acc = rule_correct / n * 100
    llm_acc = llm_correct / n * 100

    print(f"\nRule-based accuracy: {rule_correct}/{n} ({rule_acc:.1f}%)")
    print(f"LLM-based accuracy:  {llm_correct}/{n} ({llm_acc:.1f}%)")
    print(f"Improvement: {llm_acc - rule_acc:+.1f} percentage points")

    write_report(rows, rule_correct, llm_correct, n)
    print(f"\nFull report written to {OUT_PATH}")


def write_report(rows, rule_correct, llm_correct, n):
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rule_acc = rule_correct / n * 100
    llm_acc = llm_correct / n * 100

    lines = [
        "# Severity Classifier Eval: Rules vs LLM on Edge Cases",
        "",
        f"- Cases: {n}",
        f"- Rule-based accuracy: **{rule_correct}/{n} ({rule_acc:.1f}%)**",
        f"- LLM-based accuracy: **{llm_correct}/{n} ({llm_acc:.1f}%)**",
        f"- Improvement: **{llm_acc - rule_acc:+.1f} points**",
        "",
        "These cases were deliberately built so that a naive keyword rule "
        "(e.g. `FATAL` -> critical, `retry` -> low) is misled by a keyword that "
        "doesn't match the real-world severity once you read the full context "
        "(environment, blast radius, whether it self-recovered).",
        "",
        "| Case | True | Rule | Rule OK? | Matched keyword | LLM | LLM OK? | Why rules fail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['case_id']} | {r['true_severity']} | {r['rule_severity']} | "
            f"{'✅' if r['rule_correct'] else '❌'} | `{r['rule_matched_keyword']}` | "
            f"{r['llm_severity']} | {'✅' if r['llm_correct'] else '❌'} | {r['why_rules_fail']} |"
        )

    lines.append("")
    lines.append("## LLM reasoning per case")
    for r in rows:
        lines.append(f"\n**{r['case_id']}** (true: {r['true_severity']}, LLM said: {r['llm_severity']})")
        lines.append(f"> {r['llm_reasoning']}")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
