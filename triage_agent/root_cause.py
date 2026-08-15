"""
LLM-based root cause reasoning: given a parsed log/stack trace, ask the model to
identify the likely root cause, which architectural layer is implicated, and how
confident it is. Output is constrained to a fixed JSON shape via the system prompt.
"""

from dataclasses import dataclass
from typing import Optional

from triage_agent.ollama_client import chat_json, OllamaError

VALID_LAYERS = ["database", "network", "application", "infrastructure", "cache", "external-api", "unknown"]

ROOT_CAUSE_SYSTEM_PROMPT = """You are an SRE debugging assistant. You will be given a parsed \
error log (level, service, exception type, stack trace, and the raw text). Identify the most \
likely root cause of the failure.

Respond with ONLY a JSON object of this exact shape:
{
  "root_cause": "one or two sentences describing the most likely underlying cause",
  "layer": "database|network|application|infrastructure|cache|external-api|unknown",
  "confidence": 0.0-1.0
}

Base "layer" on where the failure actually originates (e.g. a NullPointerException caused by a \
missing DB row is "application" logic, not "database"; a connection timeout to another service \
is "network"). If you cannot tell from the given information, use "unknown" and lower confidence.
"""


@dataclass
class RootCauseResult:
    root_cause: str
    layer: str
    confidence: float

    def to_dict(self) -> dict:
        return {"root_cause": self.root_cause, "layer": self.layer, "confidence": self.confidence}


def analyze_root_cause(parsed_log: dict) -> Optional[RootCauseResult]:
    user_prompt = (
        f"level: {parsed_log.get('level')}\n"
        f"service: {parsed_log.get('service')}\n"
        f"exception_type: {parsed_log.get('exception_type')}\n"
        f"message:\n{chr(10).join(parsed_log.get('message_lines', []))}\n"
        f"stack_trace:\n{chr(10).join(parsed_log.get('stack_trace_lines', []))}\n"
        f"raw_log:\n{parsed_log.get('raw_log', '')}"
    )
    try:
        result = chat_json(ROOT_CAUSE_SYSTEM_PROMPT, user_prompt)
    except OllamaError:
        return None

    layer = str(result.get("layer", "unknown")).lower().strip()
    if layer not in VALID_LAYERS:
        layer = "unknown"

    try:
        confidence = float(result.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return RootCauseResult(
        root_cause=str(result.get("root_cause", "")).strip(),
        layer=layer,
        confidence=confidence,
    )
