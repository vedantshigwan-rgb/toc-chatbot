from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_three_base_intents_declared():
    text = (ROOT / "intent" / "models.py").read_text(encoding="utf-8")
    assert 'GREETING = "greeting"' in text
    assert 'DATA_QUERY = "data_query"' in text
    assert 'UNKNOWN = "unknown"' in text
    for removed in ["GENERAL =", "HELP =", "METADATA_QUERY =", "VALUE_LOOKUP =", "FEEDBACK =", "RESULT_ANALYSIS ="]:
        assert removed not in text


def test_secondary_runtime_removed():
    assert not (ROOT / "secondary").exists()
    assert not (ROOT / "workflows" / "secondary_workflow.py").exists()
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '@app.post("/secondary/prepare")' not in main


def test_explicit_request_modes_exist():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    for mode in ['"normal_message"', '"help"', '"metadata"', '"feedback"']:
        assert mode in main
    assert "reply_to_message_id is required for feedback requests" in main


def test_feedback_is_explicit_reply_only():
    text = (ROOT / "services" / "feedback_context_service.py").read_text(encoding="utf-8")
    assert 'REUSABLE_WORKFLOW_TYPES = {"primary"}' in text
    assert 'selection_source = "latest_reusable"' not in text
