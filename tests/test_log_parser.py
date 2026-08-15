import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage_agent.log_parser import parse_log


def test_parses_java_style_log():
    raw = (
        "2026-08-10T22:00:00.000Z ERROR [checkout-service] db.pool.HikariPool - "
        "Connection is not available, request timed out after 30000ms.\n"
        "java.sql.SQLTransientConnectionException: Connection is not available\n"
        "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:197)\n"
        "\tat checkout.repository.OrderRepository.save(OrderRepository.java:88)"
    )
    parsed = parse_log(raw)
    assert parsed.timestamp == "2026-08-10T22:00:00.000Z"
    assert parsed.level == "ERROR"
    assert parsed.service == "checkout-service"
    assert parsed.exception_type == "SQLTransientConnectionException"
    assert len(parsed.stack_trace_lines) == 2


def test_parses_python_style_traceback():
    raw = (
        "2026-08-10T22:00:00.000Z ERROR [analytics-pipeline] app - "
        "Traceback (most recent call last):\n"
        '  File "/app/pipeline/transform.py", line 88, in normalize_event\n'
        '    region = payload["geo"]["region"]\n'
        "KeyError: 'region'"
    )
    parsed = parse_log(raw)
    assert parsed.exception_type == "KeyError"
    assert parsed.service == "analytics-pipeline"
    assert any("transform.py" in line for line in parsed.stack_trace_lines)


def test_warn_level_normalized():
    raw = "2026-08-10T22:00:00.000Z WARNING [order-service] app - retrying request"
    parsed = parse_log(raw)
    assert parsed.level == "WARN"


def test_handles_log_with_no_recognizable_fields():
    parsed = parse_log("just some plain text with no structure")
    assert parsed.timestamp is None
    assert parsed.level is None
    assert parsed.exception_type is None
