"""
Combined severity + root-cause analysis in a SINGLE LLM call.

Why this exists alongside severity.py/root_cause.py:

The pipeline originally made two separate LLM calls -- one for severity, one for
root cause. On CPU-only Ollama that measured at 104s + 59s = 163s per triage,
which is far too slow to be useful during an incident. Both calls read the same
log and need the same context, so merging them into one prompt removes an entire
model pass (and, on a cold model, an entire model load).

severity.classify_llm() is deliberately left intact and is still what
scripts/eval_severity.py measures, because the rules-vs-LLM eval needs severity
judged in isolation -- if the model were also reasoning about root cause in the
same breath, the eval would no longer be measuring the severity classifier on its
own. So: the eval keeps the isolated prompt, the app uses this faster combined one.
"""

from dataclasses import dataclass
from typing import Optional

from triage_agent.ollama_client import chat_json, OllamaError
from triage_agent.root_cause import RootCauseResult, VALID_LAYERS
from triage_agent.severity import SEVERITY_LEVELS, SeverityResult, classify_rule_based

COMBINED_SYSTEM_PROMPT = """You are an SRE incident triage assistant. You will be given a raw \
application error log (possibly with a stack trace). Do TWO things in one pass.

1. Classify its true operational severity by reasoning about the FULL context, not just \
keywords -- consider environment (prod vs staging), scope (how many users/requests are \
affected), whether it self-recovered or has a working fallback, and business impact. A scary \
keyword in a harmless context is NOT severe, and a mild-sounding log describing a total outage IS.
   - low: no customer impact, informational, or already auto-resolved.
   - medium: a real bug or degraded behavior with bounded/partial impact.
   - high: significant customer-facing impact or a failure likely to escalate.
   - critical: production outage, data loss risk, or widespread customer-facing failure.

2. Identify the most likely root cause and which architectural layer it originates in. \
Base the layer on where the failure ACTUALLY starts (e.g. a NullPointerException caused by a \
missing DB row is "application" logic, not "database"; a connection timeout to another \
service is "network").

Respond with ONLY a JSON object of this exact shape:
{
  "severity": "low|medium|high|critical",
  "severity_confidence": 0.0-1.0,
  "severity_reasoning": "one or two sentences",
  "root_cause": "one or two sentences describing the most likely underlying cause",
  "layer": "database|network|application|infrastructure|cache|external-api|unknown",
  "root_cause_confidence": 0.0-1.0
}
"""


@dataclass
class AnalysisResult:
    severity: SeverityResult
    root_cause: Optional[RootCauseResult]


def _build_user_prompt(parsed_log: dict) -> str:
    return (
        f"level: {parsed_log.get('level')}\n"
        f"service: {parsed_log.get('service')}\n"
        f"exception_type: {parsed_log.get('exception_type')}\n"
        f"message:\n" + "\n".join(parsed_log.get("message_lines", [])) + "\n"
        f"stack_trace:\n" + "\n".join(parsed_log.get("stack_trace_lines", [])) + "\n"
        f"raw_log:\n{parsed_log.get('raw_log', '')}"
    )


def _float_or(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze(raw_log: str, parsed_log: dict) -> AnalysisResult:
    """One LLM call producing both severity and root cause, plus the rule-based
    severity pass for comparison/transparency."""
    rule_severity, matched_kw = classify_rule_based(raw_log)

    try:
        result = chat_json(COMBINED_SYSTEM_PROMPT, _build_user_prompt(parsed_log))
    except OllamaError:
        # LLM unreachable -- degrade to the rule pass alone rather than failing.
        reasoning = (
            f"Rule-based only (LLM unavailable): matched keyword '{matched_kw}'."
            if matched_kw
            else "Rule-based only (LLM unavailable): no keyword matched, defaulted to medium."
        )
        return AnalysisResult(
            severity=SeverityResult(
                severity=rule_severity,
                source="rule",
                reasoning=reasoning,
                rule_severity=rule_severity,
            ),
            root_cause=None,
        )

    llm_severity = str(result.get("severity", "")).lower().strip()
    if llm_severity not in SEVERITY_LEVELS:
        # Model returned something unusable for severity; keep the rule verdict.
        severity = SeverityResult(
            severity=rule_severity,
            source="rule",
            reasoning=f"LLM returned an invalid severity ({llm_severity!r}); fell back to rules.",
            rule_severity=rule_severity,
        )
    else:
        severity = SeverityResult(
            severity=llm_severity,
            source="combined",
            reasoning=str(result.get("severity_reasoning", "")).strip(),
            rule_severity=rule_severity,
            llm_severity=llm_severity,
            llm_confidence=_float_or(result.get("severity_confidence"), 0.5),
            overridden=llm_severity != rule_severity,
        )

    layer = str(result.get("layer", "unknown")).lower().strip()
    if layer not in VALID_LAYERS:
        layer = "unknown"

    root_cause_text = str(result.get("root_cause", "")).strip()
    root_cause = (
        RootCauseResult(
            root_cause=root_cause_text,
            layer=layer,
            confidence=_float_or(result.get("root_cause_confidence"), 0.5),
        )
        if root_cause_text
        else None
    )

    return AnalysisResult(severity=severity, root_cause=root_cause)
