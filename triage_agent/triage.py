"""
Orchestrates the full triage pipeline: parse -> classify severity -> reason about
root cause -> find similar past incidents -> assemble a structured report ->
persist the new incident into the knowledge base so it grows over time.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from triage_agent.analyze import analyze
from triage_agent.log_parser import parse_log
from triage_agent.severity import SeverityResult
from triage_agent.root_cause import RootCauseResult
from triage_agent.vectorstore import query_similar, add_incident, embed_record


@dataclass
class TriageReport:
    log_id: str
    timestamp: str
    parsed: dict
    severity: SeverityResult
    root_cause: Optional[RootCauseResult]
    similar_incidents: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "log_id": self.log_id,
            "timestamp": self.timestamp,
            "parsed": self.parsed,
            "severity": self.severity.to_dict(),
            "root_cause": self.root_cause.to_dict() if self.root_cause else None,
            "similar_incidents": self.similar_incidents,
        }

    def to_text(self) -> str:
        lines = [f"Triage Report — {self.log_id}", "=" * 40]

        p = self.parsed
        lines.append(
            f"Service: {p.get('service') or 'unknown'}   "
            f"Level: {p.get('level') or 'unknown'}   "
            f"Exception: {p.get('exception_type') or 'unknown'}"
        )

        sev = self.severity
        badge = sev.severity.upper()
        lines.append(f"\nSeverity: {badge}  (source: {sev.source})")
        if sev.overridden:
            lines.append(f"  Rule-based said '{sev.rule_severity}', LLM overrode to '{sev.llm_severity}'.")
        lines.append(f"  Reasoning: {sev.reasoning}")

        if self.root_cause:
            rc = self.root_cause
            lines.append(f"\nLikely root cause ({rc.layer}, confidence {rc.confidence:.2f}):")
            lines.append(f"  {rc.root_cause}")
        else:
            lines.append("\nLikely root cause: unavailable (LLM unreachable)")

        if self.similar_incidents:
            lines.append(f"\nSimilar past incidents ({len(self.similar_incidents)}):")
            for m in self.similar_incidents:
                lines.append(
                    f"  - [{m['log_id']}] {m['error_type']} on {m['service']} "
                    f"(similarity {m['similarity']:.2f}, was {m['severity']})"
                )
                if m.get("resolution"):
                    lines.append(f"      Resolution: {m['resolution']}")
        else:
            lines.append("\nSimilar past incidents: none found (this may be a novel issue)")

        return "\n".join(lines)


def triage_log(raw_log: str, top_k: int = 3, persist: bool = True) -> TriageReport:
    parsed = parse_log(raw_log)
    parsed_dict = parsed.to_dict()

    # One LLM call for both severity and root cause (see analyze.py for why).
    analysis = analyze(raw_log, parsed_dict)
    severity_result = analysis.severity
    root_cause_result = analysis.root_cause

    # Embed once, then reuse the same vector for the similarity query and the
    # write-back below -- the text being embedded is identical, so a second
    # embedding call would be pure waste.
    vector = embed_record(
        {
            "raw_log": raw_log,
            "error_type": parsed.exception_type or "",
            "service": parsed.service or "",
        }
    )

    similar = query_similar(
        raw_log=raw_log,
        error_type=parsed.exception_type,
        service=parsed.service,
        top_k=top_k,
        vector=vector,
    )

    log_id = f"NEW-{uuid.uuid4().hex[:8]}"
    timestamp = parsed.timestamp or datetime.now(timezone.utc).isoformat()

    report = TriageReport(
        log_id=log_id,
        timestamp=timestamp,
        parsed=parsed_dict,
        severity=severity_result,
        root_cause=root_cause_result,
        similar_incidents=similar,
    )

    if persist:
        add_incident(
            {
                "log_id": log_id,
                "timestamp": timestamp,
                "service": parsed.service or "unknown",
                "error_type": parsed.exception_type or "unknown",
                "layer": root_cause_result.layer if root_cause_result else "unknown",
                "severity": severity_result.severity,
                "raw_log": raw_log,
                "resolution": "",  # unresolved until an engineer follows up
            },
            vector=vector,
        )

    return report
