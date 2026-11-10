"""The automated-run summary quotes each tool's result line correctly."""
from scripts import run_test_suites

BANDIT_TAIL = """Run metrics:
\tTotal issues (by severity):
\t\tUndefined: 0
\t\tLow: 31
\t\tMedium: 15
\t\tHigh: 0
\tTotal issues (by confidence):
\t\tUndefined: 0
\t\tLow: 0
\t\tMedium: 16
\t\tHigh: 30
Files skipped (0):
"""


def test_bandit_summary_quotes_severity_not_confidence():
    line = run_test_suites.key_line("bandit", BANDIT_TAIL)
    assert line == "severity Low: 31 / Medium: 15 / High: 0"
