import asyncio
from pathlib import Path

import pytest

from agents.question_normalizer import (
    QuestionNormalizationError,
    normalize_question,
)
from core.models import FeedbackExecutionContext
from services.feedback_rewrite_service import FeedbackRewriteService
from services.request_preprocessor import (
    prepare_normalized_request,
    prepare_request,
)
from workflows.workflow_manager import WorkflowManager


ROOT = Path(__file__).resolve().parents[1]


def test_single_variable_normalization_uses_structured_pair():
    variables = [
        {
            "name": "buyer_name",
            "value": "Nanaware, Mr. Nitin S",
            "source": "input",
        }
    ]

    normalized = normalize_question(
        "Show excess stock for /buyer_name {Nanaware, Mr. Nitin S}",
        variables,
    )

    assert normalized == "Show excess stock for buyer name"


def test_multiple_variables_normalize_without_touching_fraction_slash():
    variables = [
        {
            "name": "buyer_name",
            "value": "Nanaware, Mr. Nitin S",
            "source": "input",
        },
        {
            "name": "child_item",
            "value": "273.04.713.50",
            "source": "input",
        },
        {
            "name": "child_description",
            "value": 'HOSE ASSEMBLY, 1-1/2" X 630L, ST X 902',
            "source": "input",
        },
    ]

    question = (
        "Show excess stock for /buyer_name {Nanaware, Mr. Nitin S} "
        "and /child_item {273.04.713.50} and "
        '/child_description {HOSE ASSEMBLY, 1-1/2" X 630L, ST X 902}'
    )

    normalized = normalize_question(question, variables)

    assert normalized == (
        "Show excess stock for buyer name and child item "
        "and child description"
    )
    assert "1-12" not in normalized


def test_variable_value_mismatch_is_rejected():
    variables = [
        {
            "name": "buyer_name",
            "value": "Nanaware, Mr. Nitin S",
            "source": "input",
        }
    ]

    with pytest.raises(QuestionNormalizationError) as exc:
        normalize_question(
            "Show excess stock for /buyer_name {Wrong Buyer}",
            variables,
        )

    assert exc.value.code == "variable_value_mismatch"


def test_prepare_request_keeps_original_and_normalized_separate():
    original = "Show excess stock for /buyer_name {Nanaware, Mr. Nitin S}"
    prepared = prepare_request(
        user_input=original,
        report_name="Inventory Report",
        variables=[
            {
                "name": "buyer_name",
                "value": "Nanaware, Mr. Nitin S",
                "source": "input",
            }
        ],
    )

    assert prepared.user_input == original
    assert prepared.normalized_input == "Show excess stock for buyer name"
    assert prepared.query_without_values == prepared.normalized_input


def test_prepare_normalized_request_does_not_rebuild_variable_syntax():
    prepared = prepare_normalized_request(
        normalized_input="give buyers for child item",
        report_name="Inventory Report",
        variables=[
            {
                "name": "child_item",
                "value": "010.10.162.00.01",
                "source": "input",
            },
            {
                "name": "org_name",
                "value": "ACD - SPARES",
                "source": "option",
            },
        ],
    )

    assert prepared.user_input == "give buyers for child item"
    assert prepared.normalized_input == "give buyers for child item"
    assert prepared.variable_names == ["child_item"]
    assert prepared.variable_values == {
        "child_item": "010.10.162.00.01"
    }
    assert prepared.variables_key == "child_item"
    assert prepared.extra_filter_names == ["org_name"]


def test_feedback_variable_merge_current_value_overrides_previous():
    previous = [
        {
            "name": "child_item",
            "value": "OLD",
            "source": "input",
        },
        {
            "name": "org_name",
            "value": "OLD ORG",
            "source": "option",
        },
    ]
    current = [
        {
            "name": "child_item",
            "value": "NEW",
            "source": "input",
        },
        {
            "name": "buyer_name",
            "value": "ABC",
            "source": "input",
        },
    ]

    merged = WorkflowManager._merge_feedback_variables(
        previous,
        current,
    )

    assert merged == [
        {
            "name": "org_name",
            "value": "OLD ORG",
            "source": "option",
        },
        {
            "name": "child_item",
            "value": "NEW",
            "source": "input",
        },
        {
            "name": "buyer_name",
            "value": "ABC",
            "source": "input",
        },
    ]


def test_feedback_rewrite_cleanup_is_plain_text_not_json_contract():
    assert FeedbackRewriteService._clean_model_text(
        '```text\nNormalized question: "give buyers for child item"\n```'
    ) == "give buyers for child item"

    assert FeedbackRewriteService._clean_model_text(
        '"give buyers for child item"'
    ) == "give buyers for child item"


def test_primary_sql_generation_uses_normalized_question():
    text = (
        ROOT / "workflows" / "primary_workflow.py"
    ).read_text(encoding="utf-8")
    assert "new_question=query_without_values" in text
    assert "new_question=prepared_request.user_input" not in text


def test_feedback_flow_rewrites_question_merges_variables_and_enters_prepared_primary():
    class FakeContextService:
        async def get_context(self, **_kwargs):
            return FeedbackExecutionContext(
                message_id="assistant-1",
                workflow_id="workflow-old",
                resolved_question="show today's total stock of child item",
                original_user_input=(
                    "Show today's total stock of "
                    "/child_item {010.10.162.00.01}"
                ),
                report_name="Inventory Report",
                variables=[
                    {
                        "name": "child_item",
                        "value": "010.10.162.00.01",
                        "source": "input",
                    }
                ],
                selection_source="explicit_reply",
            )

    class FakeRewriteService:
        async def rewrite(self, **kwargs):
            assert kwargs["previous_normalized_question"] == (
                "show today's total stock of child item"
            )
            assert kwargs["normalized_feedback"] == (
                "give buyers for this item"
            )
            return "give buyers for child item"

    class FakeFlaggedFaqService:
        def __init__(self):
            self.payload = None

        async def record_feedback_candidate(self, **kwargs):
            self.payload = kwargs
            return {"stored": True}

    captured = {}

    async def fake_primary_executor(**kwargs):
        captured.update(kwargs)
        prepared = kwargs["prepared_request"]
        return {
            "workflow": "primary",
            "status": "success",
            "stage": "completed",
            "message": "done",
            "query_executed": True,
            "result_count": 1,
            "results": [{"BUYER_NAME": "ABC"}],
            "variables": prepared.normalized_variables,
            "variable_names": prepared.variable_names,
            "variable_values": prepared.variable_values,
            "normalized_input": prepared.normalized_input,
            "query_without_values": prepared.query_without_values,
            "sql_template": (
                "SELECT BUYER_NAME FROM T WHERE CHILD_ITEM = {child_item}"
            ),
            "sql": (
                "SELECT BUYER_NAME FROM T WHERE CHILD_ITEM = "
                "'010.10.162.00.01'"
            ),
            "workflow_id": "workflow-new",
            "diagnostics": {},
        }

    flagged = FakeFlaggedFaqService()
    manager = WorkflowManager(
        intent_classifier=object(),
        primary_executor=fake_primary_executor,
        greeting_service=object(),
        help_service=object(),
        metadata_query_service=object(),
        feedback_context_service=FakeContextService(),
        feedback_rewrite_service=FakeRewriteService(),
        flagged_faq_service=flagged,
    )

    result = asyncio.run(
        manager.execute_feedback(
            user_input="give buyers for this item",
            report_name="Inventory Report",
            variables=[],
            email="user@example.com",
            chat_id="chat-1",
            reply_context={"target_message": {"message_id": "assistant-1"}},
        )
    )

    prepared = captured["prepared_request"]
    assert prepared.normalized_input == "give buyers for child item"
    assert prepared.variable_names == ["child_item"]
    assert prepared.variable_values == {
        "child_item": "010.10.162.00.01"
    }
    assert prepared.variables_key == "child_item"
    assert result["resolved_normalized_input"] == (
        "give buyers for child item"
    )
    assert result["request_mode"] == "feedback"
    assert flagged.payload["new_normalized_question"] == (
        "give buyers for child item"
    )
