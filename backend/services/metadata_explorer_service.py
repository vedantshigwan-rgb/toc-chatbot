from __future__ import annotations

from typing import Any, Optional

from repositories.weaviate_metadata_repository import (
    WeaviateMetadataRepository,
)


class MetadataExplorerError(RuntimeError):
    """
    Raised when report metadata cannot be retrieved safely.
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


class MetadataExplorerService:
    """
    Report-wide metadata exploration using ONLY
    Weaviate ColumnMetadata.

    No YAML.
    No Primary workflow.
    No Secondary workflow.
    No Oracle business query.
    """

    def __init__(
        self,
        *,
        metadata_repository: Optional[
            WeaviateMetadataRepository
        ] = None,
        default_top_k: int = 5,
    ) -> None:

        self.metadata_repository = (
            metadata_repository
        )

        self.default_top_k = max(
            1,
            default_top_k,
        )

    # ============================================================
    # LATE REPOSITORY BINDING
    # ============================================================

    def set_metadata_repository(
        self,
        metadata_repository: (
            WeaviateMetadataRepository
        ),
    ) -> None:

        self.metadata_repository = (
            metadata_repository
        )

    # ============================================================
    # PUBLIC
    # ============================================================

    async def explore(
        self,
        *,
        report_name: str,
        user_query: str,
        top_k: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Vector-search ColumnMetadata inside one report/module.

        Returns best metadata matches first.
        """

        cleaned_report = str(
            report_name or ""
        ).strip()

        cleaned_query = str(
            user_query or ""
        ).strip()

        if not cleaned_report:
            raise MetadataExplorerError(
                (
                    "A report name is required "
                    "for metadata search."
                ),
                code="report_name_required",
            )

        if not cleaned_query:
            raise MetadataExplorerError(
                (
                    "A metadata question is required."
                ),
                code="metadata_query_required",
            )

        repository = (
            self.metadata_repository
        )

        if repository is None:
            raise MetadataExplorerError(
                (
                    "The metadata repository "
                    "is currently unavailable."
                ),
                code="metadata_repository_unavailable",
            )

        search_limit = (
            max(
                1,
                int(top_k),
            )
            if top_k is not None
            else self.default_top_k
        )

        try:

            result = (
                await repository
                .search_metadata_by_module(
                    module=cleaned_report,
                    semantic_query=(
                        cleaned_query
                    ),
                    top_k=search_limit,
                )
            )

        except Exception as error:

            raise MetadataExplorerError(
                (
                    "The metadata vector search "
                    "failed unexpectedly."
                ),
                code="metadata_search_failed",
                details={
                    "exception_type": (
                        type(error).__name__
                    ),
                    "exception_message": (
                        str(error)
                    ),
                },
            ) from error

        if not result.success:

            raise MetadataExplorerError(
                (
                    result.message
                    or (
                        "The metadata repository "
                        "could not complete the search."
                    )
                ),
                code=(
                    result.error_code
                    or "metadata_search_failed"
                ),
                details={
                    "operation": (
                        result.operation
                    ),
                    "diagnostics": (
                        result.diagnostics
                    ),
                    "exception_type": (
                        result.exception_type
                    ),
                },
            )

        if not result.found:

            return []

        records: list[
            dict[str, Any]
        ] = []

        for rank, record in enumerate(
            result.records,
            start=1,
        ):

            records.append(
                {
                    "rank": rank,

                    "uuid": (
                        record.uuid
                    ),

                    "custom_id": (
                        record.custom_id
                    ),

                    "module": (
                        record.module
                    ),

                    "table_name": (
                        record.table_name
                    ),

                    "column_name": (
                        record.column_name
                    ),

                    "data_type": (
                        record.data_type
                    ),

                    "description": (
                        record.description
                    ),

                    "text": (
                        record.text
                    ),

                    "distinct_values": list(
                        record.distinct_values
                        or []
                    ),

                    "units": list(
                        record.units
                        or []
                    ),

                    "role_access": list(
                        record.role_access
                        or []
                    ),

                    "used": (
                        record.used
                    ),

                    "filterable": (
                        record.filterable
                    ),

                    "vector_distance": (
                        record.vector_distance
                    ),
                }
            )

        return records