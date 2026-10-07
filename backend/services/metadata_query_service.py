from __future__ import annotations

import json
import os
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from services.metadata_explorer_service import (
    MetadataExplorerError,
    MetadataExplorerService,
)
from services.metadata_response_service import (
    MetadataResponseService,
    MetadataResponseServiceError,
)


# ============================================================
# STRUCTURED RESOLUTION RESULT
# ============================================================


class MetadataCandidateResolution(BaseModel):
    """
    Structured decision made from metadata candidates.

    IMPORTANT:
    The model selects only candidate ranks. The actual physical
    metadata is always copied later from the original Weaviate
    candidate list, never from model output.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    decision: Literal[
        "resolved",
        "ambiguous",
        "not_found",
    ]

    selected_ranks: list[int] = Field(
        default_factory=list,
    )

    reason: str = Field(
        ...,
        min_length=1,
        max_length=400,
    )


# ============================================================
# ERROR
# ============================================================


class MetadataQueryServiceError(RuntimeError):
    """
    Raised when a metadata_query question cannot be
    answered safely.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:

        super().__init__(message)

        self.code = code
        self.details = details or {}


# ============================================================
# SERVICE
# ============================================================


class MetadataQueryService:
    """
    Answers metadata_query questions using ONLY report-scoped
    Weaviate ColumnMetadata.

    This service does NOT:
    - execute SQL
    - query Oracle
    - call Primary
    - call Secondary
    - use business FAQ FAISS
    - use Help FAISS
    - search the public web

    Flow:

        user question + selected report
            -> report-scoped Weaviate metadata search
              (reuses MetadataExplorerService)
            -> candidate resolution
              (resolved / ambiguous / not_found)
            -> grounded explanation for the resolved
               candidate(s) only
              (reuses MetadataResponseService)

    The LLM used for candidate resolution is given ONLY the user
    question, the selected report, and the retrieved candidate
    metadata. It has no tools, cannot query the database, cannot
    search the web, and cannot invent schema: it may only select
    from the ranks it was supplied.
    """

    def __init__(
        self,
        *,
        pool: Any,
        metadata_repository: Optional[Any] = None,
        metadata_explorer_service: Optional[
            MetadataExplorerService
        ] = None,
        metadata_response_service: Optional[
            MetadataResponseService
        ] = None,
        model: Optional[str] = None,
        default_top_k: int = 5,
    ) -> None:

        self.pool = pool

        # Reuse the caller's existing, correctly late-bound
        # MetadataExplorerService/MetadataResponseService
        # instances when supplied. Only build new ones as a
        # fallback (e.g. for standalone/unit-test usage).
        self.metadata_explorer_service = (
            metadata_explorer_service
            if metadata_explorer_service is not None
            else MetadataExplorerService(
                metadata_repository=(
                    metadata_repository
                ),
                default_top_k=default_top_k,
            )
        )

        self.metadata_response_service = (
            metadata_response_service
            if metadata_response_service is not None
            else MetadataResponseService(
                pool=pool,
            )
        )

        self.model = (
            model
            or os.getenv(
                "METADATA_QUERY_MODEL",
                "gpt-5-mini",
            )
        )

        self.default_top_k = max(
            1,
            default_top_k,
        )

    # ========================================================
    # PUBLIC
    # ========================================================

    async def respond(
        self,
        *,
        user_input: str,
        report_name: str,
        preselected_metadata_records: Optional[
            list[dict[str, Any]]
        ] = None,
    ) -> dict[str, Any]:
        """
        Resolve a metadata_query question against report-scoped
        Weaviate metadata and return a structured outcome.

        preselected_metadata_records is used ONLY by the
        clarification-resume path (Step 11): when the caller
        already has trusted candidate record(s) - e.g. the one the
        user selected out of a previously-checkpointed ambiguous
        set - Weaviate is never re-queried; those records are used
        exactly as if they were the (only) retrieval result.
        """

        cleaned_report = str(
            report_name or ""
        ).strip()

        # ====================================================
        # REPORT CONTEXT REQUIRED
        # ====================================================
        #
        # Weaviate must never be queried when the selected
        # report/context is missing.

        if not cleaned_report:

            return {
                "status": "clarification_required",
                "action": "clarification_required",
                "message": (
                    "I need the relevant data context to "
                    "answer that metadata question. Please "
                    "select one and try again."
                ),
                "reason_code": "report_context_required",
                "matches": [],
                "selected_match": None,
            }

        cleaned_input = str(
            user_input or ""
        ).strip()

        if not cleaned_input:

            raise MetadataQueryServiceError(
                "User input cannot be empty.",
                code="empty_user_input",
            )

        # ====================================================
        # REPORT-SCOPED WEAVIATE SEARCH
        # ====================================================
        #
        # Skipped entirely when trusted candidate(s) were already
        # supplied (clarification resume).

        if preselected_metadata_records:

            metadata_records = list(
                preselected_metadata_records
            )

        else:

            try:

                metadata_records = (
                    await self
                    .metadata_explorer_service
                    .explore(
                        report_name=cleaned_report,
                        user_query=cleaned_input,
                        top_k=self.default_top_k,
                    )
                )

            except MetadataExplorerError as error:

                raise MetadataQueryServiceError(
                    (
                        "The report metadata could "
                        "not be retrieved."
                    ),
                    code=error.code,
                    details=error.details,
                ) from error

        if not metadata_records:

            return {
                "status": "not_found",
                "action": "metadata_not_found",
                "message": (
                    "I could not find metadata in "
                    f"{cleaned_report} that answers "
                    "that question."
                ),
                "matches": [],
                "selected_match": None,
            }

        candidates_by_rank = (
            self._index_by_rank(
                metadata_records
            )
        )

        # ====================================================
        # RESOLUTION
        # ====================================================
        #
        # A single retrieved candidate is treated as an
        # unambiguous, deterministic match. Two or more
        # candidates require the lightweight LLM resolver
        # to decide resolved / ambiguous / not_found.

        if len(metadata_records) == 1:

            resolved_records = metadata_records

        else:

            resolution = (
                await self._resolve_candidates(
                    user_input=cleaned_input,
                    report_name=cleaned_report,
                    metadata_records=(
                        metadata_records
                    ),
                )
            )

            if resolution.decision == "not_found":

                return {
                    "status": "not_found",
                    "action": "metadata_not_found",
                    "message": (
                        "I could not find metadata "
                        f"in {cleaned_report} that "
                        "answers that question."
                    ),
                    "matches": (
                        self._public_matches(
                            metadata_records
                        )
                    ),
                    "selected_match": None,
                }

            if resolution.decision == "ambiguous":

                return {
                    "status": (
                        "clarification_required"
                    ),
                    "action": (
                        "clarification_required"
                    ),
                    "message": (
                        "I found more than one "
                        "possible metadata concept "
                        "for that question. Please "
                        "clarify which one you mean."
                    ),
                    "reason_code": (
                        "ambiguous_metadata_match"
                    ),
                    "matches": (
                        self._public_matches(
                            metadata_records
                        )
                    ),
                    "selected_match": None,
                    # Internal only - never forwarded to the
                    # frontend. Lets the caller checkpoint full
                    # candidate state so a resumed request can skip
                    # Weaviate entirely (Step 11).
                    "candidate_records": (
                        metadata_records
                    ),
                }

            # decision == "resolved"
            #
            # Never trust a rank invented by the model. It
            # must exist in the actual Weaviate candidates.

            resolved_records = [
                candidates_by_rank[rank]
                for rank in (
                    resolution.selected_ranks
                )
                if rank in candidates_by_rank
            ]

            if not resolved_records:

                raise MetadataQueryServiceError(
                    (
                        "Metadata resolver selected "
                        "a candidate that was not "
                        "supplied."
                    ),
                    code=(
                        "resolver_candidate_"
                        "not_found"
                    ),
                    details={
                        "selected_ranks": (
                            resolution
                            .selected_ranks
                        ),
                        "available_ranks": sorted(
                            candidates_by_rank
                            .keys()
                        ),
                    },
                )

        # ====================================================
        # GROUNDED EXPLANATION
        # ====================================================
        #
        # Generated only from the resolved candidate(s),
        # never from the full retrieved candidate list.

        try:

            response_message = (
                await self
                .metadata_response_service
                .respond(
                    user_input=cleaned_input,
                    report_name=cleaned_report,
                    metadata_records=(
                        resolved_records
                    ),
                )
            )

        except MetadataResponseServiceError as error:

            raise MetadataQueryServiceError(
                (
                    "The metadata was found but "
                    "the explanation could not be "
                    "generated."
                ),
                code=error.code,
                details=error.details,
            ) from error

        public_matches = (
            self._public_matches(
                metadata_records
            )
        )

        public_resolved = (
            self._public_matches(
                resolved_records
            )
        )

        return {
            "status": "success",
            "action": "metadata_response",
            "message": response_message,
            "matches": public_matches,
            "selected_match": (
                public_resolved[0]
                if public_resolved
                else None
            ),
        }

    # ========================================================
    # CANDIDATE RESOLUTION (LLM)
    # ========================================================

    async def _resolve_candidates(
        self,
        *,
        user_input: str,
        report_name: str,
        metadata_records: list[
            dict[str, Any]
        ],
    ) -> MetadataCandidateResolution:

        candidate_payload: list[
            dict[str, Any]
        ] = []

        for record in metadata_records:

            candidate_payload.append(
                {
                    "rank": record.get(
                        "rank"
                    ),
                    "module": record.get(
                        "module"
                    ),
                    "table_name": record.get(
                        "table_name"
                    ),
                    "column_name": record.get(
                        "column_name"
                    ),
                    "data_type": record.get(
                        "data_type"
                    ),
                    "description": record.get(
                        "description"
                    ),
                    "text": record.get(
                        "text"
                    ),
                    "units": (
                        record.get("units")
                        or []
                    ),
                    "filterable": bool(
                        record.get(
                            "filterable",
                            False,
                        )
                    ),
                    "vector_distance": (
                        record.get(
                            "vector_distance"
                        )
                    ),
                }
            )

        system_prompt = """
You are a metadata resolution component for a generic
enterprise data assistant.

You receive:
- a user's metadata question
- a selected data context
- candidate metadata objects retrieved from the configured
  metadata store

Your task is to determine whether the supplied candidates
contain enough information to answer the question.

You must use only the supplied candidates.

You must not invent:
- fields
- tables
- reports
- definitions
- datatypes
- abbreviations
- relationships
- business meanings

Candidates are ordered by vector similarity, but vector rank
is only retrieval evidence. Do not assume the first candidate
is correct.

DECISION RULES:

1. If exactly one candidate clearly answers the question,
   select it and set decision to "resolved".
2. More than one candidate may be included in "resolved" only
   when they are genuinely complementary parts of the same
   answer, not competing interpretations.
3. If two or more candidates plausibly answer the question
   with different meanings and the intended one cannot be
   determined safely from the supplied wording and metadata,
   set decision to "ambiguous".
4. If none of the supplied candidates adequately answer the
   question, set decision to "not_found".
5. selected_ranks must contain only ranks that were actually
   supplied.
6. selected_ranks must be empty unless decision is "resolved".
7. reason must be a short factual explanation based only on
   the supplied candidates.

OUTPUT

Return ONLY this JSON structure:

{
  "decision": "resolved",
  "selected_ranks": [1],
  "reason": "brief factual reason"
}

decision must be exactly one of: "resolved", "ambiguous",
"not_found".

No Markdown.
No code fences.
No additional fields.
""".strip()

        payload = {
            "selected_context": report_name,
            "user_question": user_input,
            "candidates": candidate_payload,
        }

        user_prompt = (
            "Determine whether the supplied "
            "candidates answer the metadata "
            "question.\n\n"
            + json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )

        try:

            client = (
                await self.pool.get_client()
            )

        except Exception as error:

            raise MetadataQueryServiceError(
                (
                    "Could not acquire the "
                    "OpenAI client."
                ),
                code=(
                    "client_acquisition_failed"
                ),
                details={
                    "exception_type": (
                        type(error).__name__
                    ),
                },
            ) from error

        try:

            async with self.pool.semaphore:

                response = (
                    await client.responses.create(
                        model=self.model,
                        instructions=(
                            system_prompt
                        ),
                        input=user_prompt,
                    )
                )

        except Exception as error:

            raise MetadataQueryServiceError(
                (
                    "Metadata candidate "
                    "resolution failed."
                ),
                code=(
                    "metadata_resolver_"
                    "model_failed"
                ),
                details={
                    "exception_type": (
                        type(error).__name__
                    ),
                    "exception_message": (
                        str(error)
                    ),
                },
            ) from error

        response_text = str(
            getattr(
                response,
                "output_text",
                "",
            )
            or ""
        ).strip()

        if not response_text:

            raise MetadataQueryServiceError(
                (
                    "Metadata resolver returned "
                    "an empty response."
                ),
                code=(
                    "empty_resolver_response"
                ),
            )

        cleaned_response = (
            self._clean_json_response(
                response_text
            )
        )

        try:

            resolution = (
                MetadataCandidateResolution
                .model_validate_json(
                    cleaned_response
                )
            )

        except ValidationError as error:

            raise MetadataQueryServiceError(
                (
                    "Metadata resolver returned "
                    "an invalid structured "
                    "response."
                ),
                code=(
                    "invalid_resolver_response"
                ),
                details={
                    "validation_errors": (
                        error.errors()
                    ),
                    "raw_response": (
                        response_text
                    ),
                },
            ) from error

        except ValueError as error:

            raise MetadataQueryServiceError(
                (
                    "Metadata resolver returned "
                    "invalid JSON."
                ),
                code="invalid_resolver_json",
                details={
                    "raw_response": (
                        response_text
                    ),
                },
            ) from error

        return resolution

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _index_by_rank(
        metadata_records: list[
            dict[str, Any]
        ],
    ) -> dict[int, dict[str, Any]]:

        indexed: dict[
            int, dict[str, Any]
        ] = {}

        for index, record in enumerate(
            metadata_records,
            start=1,
        ):

            supplied_rank = record.get(
                "rank"
            )

            try:
                rank = int(supplied_rank)

            except (TypeError, ValueError):
                rank = index

            if rank in indexed:
                rank = index

            indexed[rank] = record

        return indexed

    @staticmethod
    def _public_matches(
        metadata_records: list[
            dict[str, Any]
        ],
    ) -> list[dict[str, Any]]:
        """
        Reduce internal candidate records to a small,
        stable, generic public shape. Only fields that
        actually have a value are included.
        """

        public_fields = (
            "rank",
            "module",
            "table_name",
            "column_name",
            "data_type",
            "description",
        )

        matches: list[
            dict[str, Any]
        ] = []

        for record in metadata_records:

            match: dict[str, Any] = {}

            for field in public_fields:

                value = record.get(field)

                if value not in (
                    None,
                    "",
                ):
                    match[field] = value

            matches.append(match)

        return matches

    @staticmethod
    def _clean_json_response(
        response_text: str,
    ) -> str:

        cleaned = str(
            response_text or ""
        ).strip()

        if cleaned.startswith(
            "```json"
        ):
            cleaned = cleaned[
                len("```json"):
            ].strip()

        elif cleaned.startswith(
            "```"
        ):
            cleaned = cleaned[
                len("```"):
            ].strip()

        if cleaned.endswith(
            "```"
        ):
            cleaned = cleaned[
                :-len("```")
            ].strip()

        return cleaned
