"""
Quick CLI to run the full triage pipeline on a log, straight from the terminal.

Usage:
    python scripts/demo_cli.py path/to/log.txt
    python scripts/demo_cli.py --paste          # then paste a log, end with Ctrl-Z (Windows) / Ctrl-D
    echo "some log" | python scripts/demo_cli.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage_agent.triage import triage_log


def main():
    args = sys.argv[1:]

    if args and args[0] not in ("--paste", "-"):
        raw_log = Path(args[0]).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw_log = sys.stdin.read()
    else:
        print("Paste your log, then press Ctrl-Z + Enter (Windows) or Ctrl-D (Unix):")
        raw_log = sys.stdin.read()

    if not raw_log.strip():
        print("No log input provided.")
        sys.exit(1)

    print("\nRunning triage pipeline (this calls the local LLM twice, may take a bit)...\n")
    report = triage_log(raw_log, persist=True)
    print(report.to_text())


if __name__ == "__main__":
    main()
