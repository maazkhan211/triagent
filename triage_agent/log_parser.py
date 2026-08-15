"""
Regex-based structured log parser.

Takes a raw log/stack-trace blob (pasted into the dashboard or POSTed to the API) and
pulls out the fields a triage pipeline needs: timestamp, log level, service/module,
exception type, and the stack trace body. Built to handle the common shapes seen
across Java, Python, and Node.js logs without requiring a fitted template model —
each incoming log here is a single one-off incident, not a high-volume stream, so
drain3-style online template mining doesn't add value; plain regex is more precise
and dependency-free for this shape of input.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


LOG_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b")

TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?Z?)"
)

# [service-name] bracket convention used by our synthetic/Java-style logs
SERVICE_BRACKET_RE = re.compile(r"\[([a-zA-Z0-9_\-]+(?:-service|-gateway|-pipeline|-api))\]")

# Fallback: any [xxx] right after level, common in log4j/logback layouts
GENERIC_BRACKET_RE = re.compile(r"\]\s*\[([a-zA-Z0-9_.\-]+)\]")

EXCEPTION_TYPE_PATTERNS = [
    # Java/Kotlin: package.path.ExceptionName: message
    re.compile(r"\b([a-zA-Z_][\w.]*\.(?:Exception|Error)\w*)\b"),
    # Python: TypeError: ... / KeyError: ...
    re.compile(r"^([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning)):", re.MULTILINE),
    # Node/JS: UnhandledPromiseRejectionWarning, TypeError, ReferenceError
    re.compile(r"\b([A-Z][a-zA-Z]*(?:Error|Exception|RejectionWarning)):"),
    # bare capitalized Error/Exception word anywhere
    re.compile(r"\b([A-Za-z_][\w]*(?:Error|Exception))\b"),
]

STACK_LINE_RE = re.compile(
    r"^\s*(at\s+\S+|\tat\s+\S+|File \"[^\"]+\", line \d+.*|\.\.\.\s*\d+\s*more)", re.MULTILINE
)


@dataclass
class ParsedLog:
    raw_log: str
    timestamp: Optional[str] = None
    level: Optional[str] = None
    service: Optional[str] = None
    exception_type: Optional[str] = None
    stack_trace_lines: list = field(default_factory=list)
    message_lines: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "service": self.service,
            "exception_type": self.exception_type,
            "stack_trace_lines": self.stack_trace_lines,
            "message_lines": self.message_lines,
            "raw_log": self.raw_log,
        }


def parse_log(raw_log: str) -> ParsedLog:
    raw_log = raw_log.strip("\n")
    parsed = ParsedLog(raw_log=raw_log)

    ts_match = TIMESTAMP_RE.search(raw_log)
    if ts_match:
        parsed.timestamp = ts_match.group(1)

    level_match = LOG_LEVEL_RE.search(raw_log)
    if level_match:
        level = level_match.group(1).upper()
        parsed.level = "WARN" if level == "WARNING" else level

    svc_match = SERVICE_BRACKET_RE.search(raw_log)
    if not svc_match:
        svc_match = GENERIC_BRACKET_RE.search(raw_log)
    if svc_match:
        parsed.service = svc_match.group(1)

    for pattern in EXCEPTION_TYPE_PATTERNS:
        exc_match = pattern.search(raw_log)
        if exc_match:
            parsed.exception_type = exc_match.group(1)
            break

    stack_lines = STACK_LINE_RE.findall(raw_log)
    # findall with a group returns the group; re-run finditer to get full matched lines
    parsed.stack_trace_lines = [m.group(0).strip() for m in STACK_LINE_RE.finditer(raw_log)]

    stack_line_set = set(parsed.stack_trace_lines)
    parsed.message_lines = [
        line.strip()
        for line in raw_log.splitlines()
        if line.strip() and line.strip() not in stack_line_set
    ]

    return parsed
