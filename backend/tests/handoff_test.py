"""Unit tests for first-class agent handoff events (no live pipeline)."""
from orchestrator import AGENT_DISPLAY, handoff_message


HAPPY_PATH = [
    ("explorer", "planner", "surface"),
    ("planner", "evaluator", "flows"),
    ("evaluator", "generator", "evaluation"),
    ("generator", "runner", "specs"),
    ("runner", "healer", "executions"),
    ("healer", "reporter", "healer_actions"),
    ("reporter", "operator", "report"),
]


def test_handoff_message_uses_display_names():
    msg = handoff_message("explorer", "planner", "6 pages, 3 forms")
    assert msg == "Explorer → Planner: 6 pages, 3 forms"


def test_handoff_message_operator_and_feedback():
    msg = handoff_message("evaluator", "planner", "2 high-severity coverage gap(s), 0 PRD gap(s)")
    assert msg.startswith("Evaluator → Planner:")
    assert "→" in msg
    assert AGENT_DISPLAY["reporter"] == "Reporter"
    assert AGENT_DISPLAY["operator"] == "Operator"


def _is_subsequence(required, actual):
    i = 0
    for item in actual:
        if i < len(required) and item == required[i]:
            i += 1
    return i == len(required)


def test_happy_path_is_subsequence_of_replan_trace():
    replan_trace = [
        ("explorer", "planner", "surface"),
        ("planner", "evaluator", "flows"),
        ("evaluator", "planner", "feedback"),
        ("planner", "evaluator", "flows"),
        ("evaluator", "generator", "evaluation"),
        ("generator", "runner", "specs"),
        ("runner", "healer", "executions"),
        ("healer", "reporter", "healer_actions"),
        ("reporter", "operator", "report"),
    ]
    assert _is_subsequence(HAPPY_PATH, replan_trace)
    assert not _is_subsequence(HAPPY_PATH, replan_trace[:-1])
