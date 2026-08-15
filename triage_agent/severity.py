"""
Severity classification: a cheap rule-based keyword pass, an LLM reasoning pass,
and a combiner that lets the LLM override the rules when they disagree.

The rule-based pass is fast and free but context-blind -- it can't tell the
difference between "FATAL disk alert on a staging box with no customer impact"
and a real production outage, because both contain the word FATAL. The LLM pass
reads the whole log (scope, impact, whether it self-recovered, environment) and
reasons about actual severity, which is what makes it outperform keyword rules on
ambiguous/edge-case logs. See scripts/eval_severity.py for a measured comparison.
"""

from dataclasses import dataclass
from typing import Optional

from triage_agent.ollama_client import chat_json, OllamaError

SEVERITY_LEVELS = ["low", "medium", "high", "critical"]

# keyword -> severity, checked case-insensitively against the raw log text.
# First-match-wins by iteration order below (most severe keywords checked first).
CRITICAL_KEYWORDS = ["fatal", "oom", "outofmemory", "out of memory", "panic", "crash", "segfault"]
HIGH_KEYWORDS = ["error", "exception", "timeout", "deadlock", "refused", "unavailable", "failed"]
MEDIUM_KEYWORDS = ["warn", "retry", "rate limit", "429", "degraded"]
LOW_KEYWORDS = ["deprecated", "info", "debug", "no action required", "no customer impact"]


@dataclass
class SeverityResult:
    severity: str
    source: str  # "rule", "llm", or "combined"
    reasoning: str
    rule_severity: Optional[str] = None
    llm_severity: Optional[str] = None
    llm_confidence: Optional[float] = None
    overridden: bool = False

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "source": self.source,
            "reasoning": self.reasoning,
            "rule_severity": self.rule_severity,
            "llm_severity": self.llm_severity,
            "llm_confidence": self.llm_confidence,
            "overridden": self.overridden,
        }


def classify_rule_based(raw_log: str) -> tuple:
    """Returns (severity, matched_keyword) using simple case-insensitive keyword matching."""
    text = raw_log.lower()
    for kw in CRITICAL_KEYWORDS:
        if kw in text:
            return "critical", kw
    for kw in HIGH_KEYWORDS:
        if kw in text:
            return "high", kw
    for kw in MEDIUM_KEYWORDS:
        if kw in text:
            return "medium", kw
    for kw in LOW_KEYWORDS:
        if kw in text:
            return "low", kw
    return "medium", None  # default when nothing matches


SEVERITY_SYSTEM_PROMPT = """You are an SRE incident triage assistant. You will be given a raw \
application/error log (possibly with a stack trace). Classify its true operational severity \
by reasoning about the FULL context, not just keywords -- consider: environment \
(prod vs staging), scope (how many users/requests affected), whether it self-recovered or \
has a working fallback, whether the log level alone is misleading, and business impact.

Severity levels, in increasing order of urgency:
- low: no customer impact, informational, or already auto-resolved with no side effects.
- medium: a real bug or degraded behavior with bounded/partial impact.
- high: significant customer-facing impact or a failure mode likely to escalate.
- critical: production outage, data loss risk, or widespread customer-facing failure.

Respond with ONLY a JSON object of this exact shape:
{"severity": "low|medium|high|critical", "confidence": 0.0-1.0, "reasoning": "one or two sentences"}
"""


def classify_llm(raw_log: str) -> Optional[dict]:
    try:
        result = chat_json(SEVERITY_SYSTEM_PROMPT, raw_log)
    except OllamaError:
        return None
    severity = str(result.get("severity", "")).lower().strip()
    if severity not in SEVERITY_LEVELS:
        return None
    return {
        "severity": severity,
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": str(result.get("reasoning", "")).strip(),
    }


def classify_severity(raw_log: str) -> SeverityResult:
    rule_severity, matched_kw = classify_rule_based(raw_log)
    llm_result = classify_llm(raw_log)

    if llm_result is None:
        # LLM unavailable -- fall back to the rule pass alone.
        reasoning = (
            f"Rule-based only (LLM unavailable): matched keyword '{matched_kw}'."
            if matched_kw
            else "Rule-based only (LLM unavailable): no keyword matched, defaulted to medium."
        )
        return SeverityResult(
            severity=rule_severity,
            source="rule",
            reasoning=reasoning,
            rule_severity=rule_severity,
        )

    llm_severity = llm_result["severity"]
    overridden = llm_severity != rule_severity

    # Combine: LLM's contextual read wins on disagreement, since it reasons over
    # scope/impact/environment instead of a single keyword. We keep the rule
    # result alongside for transparency and for the eval comparison.
    return SeverityResult(
        severity=llm_severity,
        source="combined",
        reasoning=llm_result["reasoning"],
        rule_severity=rule_severity,
        llm_severity=llm_severity,
        llm_confidence=llm_result["confidence"],
        overridden=overridden,
    )
