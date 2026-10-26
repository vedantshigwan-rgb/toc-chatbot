from __future__ import annotations

import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import uvicorn
import weaviate
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from api.auth_routes import router as auth_router
from api.contextual_autocomplete_routes import (
    router as contextual_autocomplete_router,
)
from api.chat_routes import router as chat_router
from api.intent_routes import router as intent_router
from auth.dependencies import current_user_email, get_current_user
from config.settings import OPENAI_API_KEY
from intent.classifier import IntentClassifier
from mcp_controller import primary_workflow
from repositories.weaviate_metadata_repository import WeaviateMetadataRepository
from services.auth_security import validate_auth_configuration
from services.feedback_context_service import FeedbackContextService
from services.feedback_rewrite_service import FeedbackRewriteService
from services.flagged_faq_service import FlaggedFaqService
from services.greeting_service import GreetingService
from services.help_retrieval_service import HelpRetrievalError, HelpRetrievalService
from services.help_service import HelpService
from services.metadata_explorer_service import MetadataExplorerService
from services.metadata_query_service import MetadataQueryService
from services.metadata_response_service import MetadataResponseService
from utils.mongo_auth_store import MongoAuthStore
from utils.mongo_chat_store import MongoChatStore
from utils.mongo_workflow_store import MongoWorkflowStore
from utils.openai_embedder import OpenAIEmbedder
from utils.openai_pool import OpenAIPool
from utils.search_faiss_indexes import FAQIndexSearcher
from workflows.workflow_manager import WorkflowManager
from orchestration.request_db_execution_budget import RequestDatabaseExecutionPolicy


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
INDEX_BASE_PATH = PROJECT_ROOT / "utils" / "indexes"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "toc")
MONGO_WORKFLOW_COLLECTION = os.getenv("MONGO_WORKFLOW_COLLECTION", "workflows")
MONGO_CHAT_COLLECTION = os.getenv("MONGO_CHAT_COLLECTION", "chats")
MONGO_MESSAGE_COLLECTION = os.getenv("MONGO_MESSAGE_COLLECTION", "messages")
MONGO_AUTH_USER_COLLECTION = os.getenv("MONGO_AUTH_USER_COLLECTION", "users")

WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://localhost:8080")
WEAVIATE_COLLECTION = os.getenv("WEAVIATE_COLLECTION", "ColumnMetadata")


class QueryVariable(BaseModel):
    name: str = Field(..., min_length=1)
    value: Any
    source: Literal["input", "option"] = "input"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Variable name cannot be empty.")
        return cleaned

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Variable value cannot be null.")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Variable value cannot be empty.")
        return value


class AskRequest(BaseModel):
    """
    Explicit request-mode contract.

    normal_message -> only the 3-way LLM classifier may run
    help           -> Help flow directly
    metadata       -> Metadata flow directly
    feedback       -> reply-only feedback flow directly
    """

    message_type: Literal[
        "normal_message",
        "help",
        "metadata",
        "feedback",
    ] = "normal_message"

    input: str = Field(..., min_length=1)
    report_name: Optional[str] = None
    variables: List[QueryVariable] = Field(default_factory=list)
    session_id: Optional[str] = None
    chat_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None

    @field_validator("input")
    @classmethod
    def clean_input(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Input cannot be empty.")
        return cleaned

    @field_validator("report_name", "chat_id", "reply_to_message_id", "session_id")
    @classmethod
    def clean_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_mode(self) -> "AskRequest":
        if self.message_type in {"help", "metadata"} and not self.report_name:
            raise ValueError(f"report_name is required for {self.message_type} requests.")
        if self.message_type == "feedback":
            if not self.chat_id:
                raise ValueError("chat_id is required for feedback requests.")
            if not self.reply_to_message_id:
                raise ValueError("reply_to_message_id is required for feedback requests.")
        elif self.reply_to_message_id:
            raise ValueError("reply_to_message_id is only allowed when message_type is feedback.")
        return self



class EditMessageRequest(BaseModel):
    input: str = Field(..., min_length=1)

    # The mode selected in the frontend while editing.
    message_type: Optional[
        Literal["normal_message", "help", "metadata"]
    ] = None

    # None means the frontend did not send variables.
    # An empty list means the frontend explicitly sent no variables.
    variables: Optional[List[QueryVariable]] = None

    # Optional report override; otherwise retain the original report.
    report_name: Optional[str] = None

    @field_validator("input")
    @classmethod
    def clean_input(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Input cannot be empty.")
        return cleaned

    @field_validator("report_name")
    @classmethod
    def clean_report_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[STARTUP] Initializing simplified TOC backend...")

    app.state.pool = OpenAIPool()
    app.state.embedder = OpenAIEmbedder()
    app.state.faq_searcher = FAQIndexSearcher(
        base_path=INDEX_BASE_PATH,
        embedder=app.state.embedder,
    )

    app.state.workflow_store = MongoWorkflowStore(
        mongo_uri=MONGO_URI,
        database_name=MONGO_DATABASE,
        collection_name=MONGO_WORKFLOW_COLLECTION,
    )
    await app.state.workflow_store.initialize()

    app.state.chat_store = MongoChatStore(
        mongo_uri=MONGO_URI,
        database_name=MONGO_DATABASE,
        chats_collection_name=MONGO_CHAT_COLLECTION,
        messages_collection_name=MONGO_MESSAGE_COLLECTION,
    )
    await app.state.chat_store.initialize()

    validate_auth_configuration()
    app.state.auth_store = MongoAuthStore(
        database=app.state.chat_store.database,
        collection_name=MONGO_AUTH_USER_COLLECTION,
    )
    await app.state.auth_store.initialize()

    app.state.intent_classifier = IntentClassifier(pool=app.state.pool)
    app.state.greeting_service = GreetingService(pool=app.state.pool)

    # Help is explicit. If its static FAISS bundle is unavailable, keep
    # startup alive and return a controlled help-unavailable response.
    app.state.help_service = None
    try:
        help_retrieval = HelpRetrievalService(pool=app.state.pool)
        app.state.help_service = HelpService(
            pool=app.state.pool,
            retrieval_service=help_retrieval,
        )
    except HelpRetrievalError as exc:
        print("[STARTUP] Help resources unavailable:", str(exc))

    app.state.metadata_explorer_service = MetadataExplorerService(
        metadata_repository=None,
        default_top_k=5,
    )
    app.state.metadata_response_service = MetadataResponseService(pool=app.state.pool)
    app.state.metadata_query_service = MetadataQueryService(
        pool=app.state.pool,
        metadata_explorer_service=app.state.metadata_explorer_service,
        metadata_response_service=app.state.metadata_response_service,
    )

    app.state.feedback_context_service = FeedbackContextService(
        chat_store=app.state.chat_store,
    )
    app.state.feedback_rewrite_service = FeedbackRewriteService(pool=app.state.pool)
    app.state.flagged_faq_service = FlaggedFaqService()
    app.state.request_db_execution_policy = RequestDatabaseExecutionPolicy()

    app.state.weaviate_client = None
    app.state.metadata_repository = None
    try:
        additional_headers: Dict[str, str] = {}
        if OPENAI_API_KEY:
            additional_headers["X-OpenAI-Api-Key"] = OPENAI_API_KEY

        app.state.weaviate_client = weaviate.Client(
            url=WEAVIATE_URL,
            timeout_config=(5, 60),
            additional_headers=additional_headers,
        )
        repository = WeaviateMetadataRepository(
            client=app.state.weaviate_client,
            collection_name=WEAVIATE_COLLECTION,
        )
        health = await repository.healthcheck()
        if health.success and health.found:
            app.state.metadata_repository = repository
            app.state.metadata_explorer_service.set_metadata_repository(repository)
            print("[STARTUP] Weaviate metadata repository ready.")
        else:
            print("[STARTUP] Weaviate metadata repository unavailable:", health.message)
    except Exception as exc:
        app.state.weaviate_client = None
        app.state.metadata_repository = None
        print("[STARTUP] Weaviate metadata initialization failed:", str(exc))

    async def execute_primary_from_manager(**kwargs):
        return await primary_workflow(
            **kwargs,
            pool=app.state.pool,
            faq_searcher=app.state.faq_searcher,
            workflow_store=app.state.workflow_store,
            metadata_repository=app.state.metadata_repository,
        )

    # A small stub keeps WorkflowManager's help dependency explicit while
    # still allowing the application to start if help assets are missing.
    if app.state.help_service is None:
        class _UnavailableHelpService:
            async def respond(self, *, user_input: str, report_name: Optional[str] = None):
                return ("Help resources are currently unavailable.", [])
        app.state.help_service = _UnavailableHelpService()

    app.state.workflow_manager = WorkflowManager(
        intent_classifier=app.state.intent_classifier,
        primary_executor=execute_primary_from_manager,
        greeting_service=app.state.greeting_service,
        help_service=app.state.help_service,
        metadata_query_service=app.state.metadata_query_service,
        feedback_context_service=app.state.feedback_context_service,
        feedback_rewrite_service=app.state.feedback_rewrite_service,
        flagged_faq_service=app.state.flagged_faq_service,
        request_db_execution_policy=app.state.request_db_execution_policy,
    )

    print("[STARTUP] Simplified router ready: greeting/data_query/unknown + explicit help/metadata/feedback.")

    yield

    await app.state.pool.close()
    app.state.faq_searcher.clear_cache()
    await app.state.workflow_store.close()
    await app.state.chat_store.close()

    client = getattr(app.state, "weaviate_client", None)
    if client is not None:
        close_method = getattr(client, "close", None)
        if callable(close_method):
            close_method()

    print("[SHUTDOWN] Resources closed.")


app = FastAPI(
    title="TOC Chatbot API",
    description=(
        "Authenticated TOC API with a single Primary data workflow. "
        "The base intent classifier only decides greeting, data_query, and unknown."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(contextual_autocomplete_router)
app.include_router(intent_router)
app.include_router(chat_router)

@app.get("/health")
async def health(request: Request):
    return {
        "status": "healthy",
        "service": "TOC Chatbot API",
        "architecture": "primary_only",
        "base_intents": ["greeting", "data_query", "unknown"],
        "explicit_modes": ["help", "metadata", "feedback"],
        "features": {
            "authentication": "available",
            "primary_workflow": "available",
            "secondary_workflow": "removed",
            "metadata_repository": (
                "available"
                if getattr(request.app.state, "metadata_repository", None) is not None
                else "unavailable"
            ),
        },
    }




def _clean_report_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _same_report_name(first: Optional[str], second: Optional[str]) -> bool:
    first_clean = _clean_report_name(first)
    second_clean = _clean_report_name(second)
    if first_clean is None or second_clean is None:
        return first_clean == second_clean
    return first_clean.casefold() == second_clean.casefold()


def _report_lock_response(
    *,
    chat_id: Optional[str],
    request_mode: str,
    locked_report: str,
    requested_report: Optional[str],
    action: str = "report_change_not_allowed",
    message: Optional[str] = None,
) -> JSONResponse:
    requested = _clean_report_name(requested_report)
    response_message = message or (
        f"This conversation is locked to {locked_report}. "
        + (
            f"Start a new chat to use {requested}."
            if requested
            else "Continue using the same report, or start a new chat to choose another report."
        )
    )

    return JSONResponse(
        status_code=400,
        content={
            "workflow": "feedback" if request_mode == "feedback" else "manager",
            "workflow_used": "feedback" if request_mode == "feedback" else "manager",
            "status": "failed",
            "stage": "request_validation",
            "action": action,
            "message": response_message,
            "report_name": locked_report,
            "request_mode": request_mode,
            "chat_id": chat_id,
            "query_executed": False,
            "result_count": 0,
            "results": [],
            "error": {
                "category": "invalid_request",
                "code": action,
                "message": response_message,
                "retryable": False,
                "details": {
                    "locked_report_name": locked_report,
                    "requested_report_name": requested,
                },
            },
        },
    )

def _variables_payload(payload: AskRequest) -> list[dict[str, Any]]:
    return [
        {"name": v.name, "value": v.value, "source": v.source}
        for v in payload.variables
    ]


@app.post("/ask")
async def ask(
    payload: AskRequest,
    request: Request,
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    start_time = time.time()
    user_id = current_user_email(request)
    variables = _variables_payload(payload)
    # Help/Report Terms never consume input variables or sidebar filters,
    # including when a client calls /ask directly instead of using the UI.
    if payload.message_type in ("help", "metadata"):
        variables = []

    chat_store = getattr(request.app.state, "chat_store", None)
    manager = getattr(request.app.state, "workflow_manager", None)
    if chat_store is None or manager is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "stage": "resource_initialization",
                "message": "Application resources are unavailable.",
            },
        )

    chat_id: Optional[str] = None
    user_message_id: Optional[str] = None

    try:
        reply_context = None

        # ----------------------------------------------------------
        # A chat is permanently bound to the first non-empty report
        # used in that conversation. Do this check BEFORE storing the
        # new user message so a rejected cross-report request does not
        # pollute chat history.
        # ----------------------------------------------------------
        existing_chat = None
        locked_chat_report = None
        effective_report_name = _clean_report_name(payload.report_name)

        if payload.chat_id:
            existing_chat = await chat_store.get_chat(
                chat_id=payload.chat_id,
                user_id=user_id,
            )

            if existing_chat is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "failed",
                        "stage": "request_validation",
                        "action": "invalid_chat",
                        "message": "The requested chat does not exist for this user.",
                        "chat_id": payload.chat_id,
                    },
                )

            locked_chat_report = _clean_report_name(
                existing_chat.get("report_name")
            )

            if (
                locked_chat_report
                and effective_report_name
                and not _same_report_name(
                    locked_chat_report,
                    effective_report_name,
                )
            ):
                is_feedback = payload.message_type == "feedback"
                return _report_lock_response(
                    chat_id=payload.chat_id,
                    request_mode=payload.message_type,
                    locked_report=locked_chat_report,
                    requested_report=effective_report_name,
                    action=(
                        "feedback_report_mismatch"
                        if is_feedback
                        else "report_change_not_allowed"
                    ),
                    message=(
                        f"This feedback cannot be applied because the conversation is locked to "
                        f"{locked_chat_report}, but {effective_report_name} was sent. "
                        "Start a new chat to use the other report."
                        if is_feedback
                        else None
                    ),
                )

            # If the frontend omitted report_name for an already-bound
            # chat, keep the chat's authoritative report instead of
            # allowing the conversation to lose its report context.
            if locked_chat_report and not effective_report_name:
                effective_report_name = locked_chat_report

        # Feedback is the only mode allowed to use reply_to_message_id.
        if payload.message_type == "feedback":
            reply_context = await chat_store.get_reply_context(
                message_id=payload.reply_to_message_id,
                user_id=user_id,
            )
            target = reply_context.get("target_message") if reply_context else None
            if target is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "failed",
                        "stage": "request_validation",
                        "action": "invalid_reply_target",
                        "message": "The replied message could not be found.",
                    },
                )
            if target.get("chat_id") != payload.chat_id:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "failed",
                        "stage": "request_validation",
                        "action": "invalid_reply_target",
                        "message": "The replied message belongs to a different chat.",
                    },
                )
            if str(target.get("role") or "").lower() != "assistant":
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "failed",
                        "stage": "request_validation",
                        "action": "invalid_reply_target",
                        "message": "Feedback must reply to an assistant result.",
                    },
                )
            if str(target.get("workflow_used") or "") != "primary":
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "failed",
                        "stage": "request_validation",
                        "action": "invalid_reply_target",
                        "message": "Feedback can only modify a previous Primary data result.",
                    },
                )

            target_report = _clean_report_name(target.get("report_name"))
            if (
                target_report
                and effective_report_name
                and not _same_report_name(
                    target_report,
                    effective_report_name,
                )
            ):
                return _report_lock_response(
                    chat_id=payload.chat_id,
                    request_mode="feedback",
                    locked_report=target_report,
                    requested_report=effective_report_name,
                    action="feedback_report_mismatch",
                    message=(
                        f"This result belongs to {target_report}. "
                        f"It cannot be modified while {effective_report_name} is selected. "
                        "Start a new chat to use the other report."
                    ),
                )

            # The replied result is authoritative for feedback.
            if target_report:
                effective_report_name = target_report

        chat = await chat_store.ensure_chat(
            chat_id=payload.chat_id,
            user_id=user_id,
            initial_title=payload.input,
            report_name=effective_report_name,
        )
        chat_id = str(chat["chat_id"])

        stored_user = await chat_store.add_message(
            chat_id=chat_id,
            user_id=user_id,
            role="user",
            content=payload.input,
            report_name=effective_report_name,
            variables=jsonable_encoder(variables),
            reply_to_message_id=payload.reply_to_message_id,
            request_mode=payload.message_type,
        )
        user_message_id = str(stored_user["message_id"])

        if payload.message_type == "help":
            result = await manager.execute_help(
                user_input=payload.input,
                report_name=effective_report_name,
                variables=variables,
                session_id=payload.session_id,
            )
        elif payload.message_type == "metadata":
            result = await manager.execute_metadata(
                user_input=payload.input,
                report_name=effective_report_name,
                variables=variables,
                session_id=payload.session_id,
            )
        elif payload.message_type == "feedback":
            result = await manager.execute_feedback(
                user_input=payload.input,
                report_name=effective_report_name,
                variables=variables,
                email=user_id,
                chat_id=chat_id,
                reply_context=reply_context,
                session_id=payload.session_id,
                user_role=None,
            )
        else:
            result = await manager.execute(
                user_input=payload.input,
                report_name=effective_report_name,
                variables=variables,
                email=user_id,
                session_id=payload.session_id,
                chat_id=chat_id,
                user_role=None,
            )

        latency_ms = int((time.time() - start_time) * 1000)
        workflow_used = str(result.get("workflow_used") or result.get("workflow") or "manager")
        response_text = str(
            result.get("message")
            or result.get("output")
            or result.get("answer")
            or "The request completed."
        ).strip()
        outward_results = list(result.get("results") or [])

        stored_assistant = await chat_store.add_message(
            chat_id=chat_id,
            user_id=user_id,
            role="assistant",
            content=response_text,
            report_name=result.get("report_name") or effective_report_name,
            source_user_message_id=user_message_id,
            workflow_used=workflow_used,
            workflow_id=result.get("workflow_id"),
            status=result.get("status"),
            stage=result.get("stage"),
            result_count=int(result.get("result_count", len(outward_results)) or 0),
            table_rows=jsonable_encoder(outward_results),
            sql=result.get("sql"),
            sql_template=result.get("sql_template"),
            query_executed=result.get("query_executed"),
            action=result.get("action"),
            selected_example=result.get("selected_example"),
            faiss_examples_used=result.get("faiss_examples_used"),
            request_mode=payload.message_type,
            variable_names=jsonable_encoder(result.get("variable_names") or []),
            variable_values=jsonable_encoder(result.get("variable_values") or {}),
            query_without_values=result.get("query_without_values"),
            feedback_reexecution=jsonable_encoder(
                (result.get("diagnostics") or {}).get("feedback_reexecution") or {}
            ),
            original_feedback_input=result.get("original_feedback_input"),
            normalized_feedback_input=result.get("normalized_feedback_input"),
            variables=jsonable_encoder(result.get("variables") or variables),
            normalized_input=result.get("normalized_input"),
            resolved_user_input=result.get("resolved_user_input"),
            resolved_normalized_input=(
                result.get("resolved_normalized_input")
                or result.get("normalized_input")
            ),
        )
        assistant_message_id = str(stored_assistant["message_id"])

        response_content = {
            **result,
            "results": outward_results,
            "workflow_used": workflow_used,
            "request_mode": payload.message_type,
            "latency_ms": latency_ms,
            "chat_id": chat_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
        }

        return JSONResponse(status_code=200, content=jsonable_encoder(response_content))

    except Exception as exc:
        latency_ms = int((time.time() - start_time) * 1000)
        traceback.print_exc()
        logger.exception("[ASK] unexpected route failure")
        return JSONResponse(
            status_code=500,
            content={
                "workflow": "manager",
                "workflow_used": "manager",
                "status": "failed",
                "stage": "internal",
                "message": "The request could not be processed because of an internal API error.",
                "action": "retry",
                "query_executed": False,
                "result_count": 0,
                "results": [],
                "error": {
                    "category": "internal_error",
                    "code": "ask_route_failed",
                    "message": "The API route failed unexpectedly.",
                    "retryable": True,
                    "details": {"exception_type": type(exc).__name__},
                },
                "chat_id": chat_id,
                "user_message_id": user_message_id,
                "latency_ms": latency_ms,
            },
        )


@app.post("/chat/messages/{message_id}/edit")
async def edit_chat_message(
    message_id: str,
    payload: EditMessageRequest,
    request: Request,
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    user_id = current_user_email(request)
    chat_store = getattr(request.app.state, "chat_store", None)
    manager = getattr(request.app.state, "workflow_manager", None)

    if chat_store is None or manager is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "message": "Application resources are unavailable.",
            },
        )

    original = await chat_store.get_message(
        message_id=message_id,
        user_id=user_id,
    )

    if original is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Message not found."},
        )

    if original.get("role") != "user":
        return JSONResponse(
            status_code=400,
            content={"detail": "Only user messages can be edited."},
        )

    chat_id = str(original.get("chat_id") or "")
    history = await chat_store.list_messages(
        chat_id=chat_id,
        user_id=user_id,
    )

    latest_user = next(
        (item for item in reversed(history) if item.get("role") == "user"),
        None,
    )

    if not latest_user or latest_user.get("message_id") != message_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "Only the latest user message can be edited."},
        )

    # Keep feedback/Modify as separate operations.
    allowed_modes = {
        "normal_message",
        "help",
        "metadata",
    }

    request_mode = str(
        payload.message_type
        or original.get("request_mode")
        or "normal_message"
    ).strip().lower()

    if request_mode not in allowed_modes:
        return JSONResponse(
            status_code=400,
            content={
                "detail": f"Unsupported question mode: {request_mode}"
            },
        )

    if original.get("reply_to_message_id"):
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Feedback/reply messages cannot be edited with this endpoint."
            },
        )

    
    report_name = _clean_report_name(
        payload.report_name or original.get("report_name")
    )

    if payload.variables is not None:
        variables = _variables_payload(
            AskRequest(
                input=payload.input,
                message_type="normal_message",
                report_name=report_name,
                variables=payload.variables,
            )
        )
    else:
        variables = list(original.get("variables") or [])

    if request_mode in {"help", "metadata"}:
        variables = []


    # Update the existing MongoDB document. Do not call add_message()
    # for the user message: its message_id must remain unchanged.
    try:
        updated_user = await chat_store.edit_user_message(
            message_id=message_id,
            user_id=user_id,
            content=payload.input,
            request_mode=request_mode,
        )
    except ValueError as error:
        return JSONResponse(
            status_code=400,
            content={"detail": str(error)},
        )

    if updated_user is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Message not found."},
        )

    start_time = time.time()

    try:
        if request_mode == "help":
            result = await manager.execute_help(
                user_input=payload.input,
                report_name=report_name,
                variables=[],
                session_id=None,
            )
        elif request_mode == "metadata":
            result = await manager.execute_metadata(
                user_input=payload.input,
                report_name=report_name,
                variables=[],
                session_id=None,
            )
        else:
            result = await manager.execute(
                user_input=payload.input,
                report_name=report_name,
                variables=variables,
                email=user_id,
                session_id=None,
                chat_id=chat_id,
                user_role=None,
            )

        latency_ms = int((time.time() - start_time) * 1000)
        workflow_used = str(
            result.get("workflow_used") or result.get("workflow") or "manager"
        )
        response_text = str(
            result.get("message")
            or result.get("output")
            or result.get("answer")
            or "The request completed."
        ).strip()
        outward_results = list(result.get("results") or [])

        
        assistant_fields = {
            "content": response_text,
            "report_name": result.get("report_name") or report_name,
            "source_user_message_id": message_id,
            "workflow_used": workflow_used,
            "workflow_id": result.get("workflow_id"),
            "status": result.get("status"),
            "stage": result.get("stage"),
            "result_count": int(
                result.get("result_count", len(outward_results)) or 0
            ),
            "table_rows": jsonable_encoder(outward_results),
            "sql": result.get("sql"),
            "sql_template": result.get("sql_template"),
            "query_executed": result.get("query_executed"),
            "action": result.get("action"),
            "selected_example": result.get("selected_example"),
            "faiss_examples_used": result.get("faiss_examples_used"),
            "request_mode": request_mode,
            "variable_names": jsonable_encoder(
                result.get("variable_names") or []
            ),
            "variable_values": jsonable_encoder(
                result.get("variable_values") or {}
            ),
            "query_without_values": result.get("query_without_values"),
            "feedback_reexecution": jsonable_encoder(
                (result.get("diagnostics") or {}).get(
                    "feedback_reexecution"
                ) or {}
            ),
            "original_feedback_input": result.get(
                "original_feedback_input"
            ),
            "normalized_feedback_input": result.get(
                "normalized_feedback_input"
            ),
            "variables": jsonable_encoder(
                result.get("variables") or variables
            ),
            "normalized_input": result.get("normalized_input"),
            "resolved_user_input": result.get("resolved_user_input"),
            "resolved_normalized_input": (
                result.get("resolved_normalized_input")
                or result.get("normalized_input")
            ),
        }

        existing_assistant = (
            await chat_store.get_assistant_for_user_message(
                user_message_id=message_id,
                user_id=user_id,
                chat_id=chat_id,
            )
        )

        if existing_assistant:
            stored_assistant = (
                await chat_store.update_assistant_for_user_message(
                    user_message_id=message_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    fields=assistant_fields,
                )
            )

            if stored_assistant is None:
                raise RuntimeError(
                    "The existing assistant response could not be updated."
                )
        else:
            stored_assistant = await chat_store.add_message(
                chat_id=chat_id,
                user_id=user_id,
                role="assistant",
                **assistant_fields,
            )

        assistant_message_id = str(stored_assistant["message_id"])

        

        return JSONResponse(
            status_code=200,
            content=jsonable_encoder({
                **result,
                "results": outward_results,
                "workflow_used": workflow_used,
                "request_mode": request_mode,
                "latency_ms": latency_ms,
                "chat_id": chat_id,
                "user_message_id": message_id,
                "assistant_message_id": str(stored_assistant["message_id"]),
                "edited": True,
            }),
        )

    except Exception:
        logger.exception("[EDIT MESSAGE] Workflow rerun failed")
        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "stage": "internal",
                "message": (
                    "The edited message was saved, but the workflow "
                    "could not be rerun. Please retry."
                ),
                "chat_id": chat_id,
                "user_message_id": message_id,
                "edited": True,
            },
        )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=False)
