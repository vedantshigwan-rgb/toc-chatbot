from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

from core.models import (
    ColumnMetadataRecord,
    MetadataRepositoryResult,
)


DEFAULT_RETURN_PROPERTIES = [
    "custom_id",
    "text",
    "columnName",
    "tableName",
    "module",
    "dataType",
    "description",
    "distinctValues",
    "units",
    "used",
    "filterable",
    "roleAccess",
]


class WeaviateMetadataRepository:
    """
    Repository for the Weaviate v3 ColumnMetadata class.

    This implementation is compatible with weaviate-client 3.26.7.
    The v3 client is synchronous, so blocking GraphQL `.do()` calls are
    executed in worker threads to avoid blocking FastAPI's event loop.

    The Weaviate client is supplied by the application lifespan.
    This repository does not create or close the client.
    """

    def __init__(
        self,
        *,
        client,
        collection_name: str = "ColumnMetadata",
    ) -> None:
        self.client = client
        self.collection_name = (
            str(collection_name).strip()
            or "ColumnMetadata"
        )

    # ========================================================
    # Public health operation
    # ========================================================

    async def healthcheck(
        self,
    ) -> MetadataRepositoryResult:
        """Check whether the configured Weaviate client is ready."""

        try:
            is_ready = await asyncio.to_thread(
                self.client.is_ready
            )

        except Exception as error:
            return self._error_result(
                operation="healthcheck",
                error=error,
                error_code="weaviate_healthcheck_failed",
            )

        if not is_ready:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation="healthcheck",
                message="Weaviate is not ready.",
                error_code="weaviate_not_ready",
            )

        return MetadataRepositoryResult(
            success=True,
            found=True,
            operation="healthcheck",
            message="Weaviate is ready.",
        )

    # ========================================================
    # Exact table lookup
    # ========================================================

    async def find_exact_table(
        self,
        *,
        module: str,
        table_name: str,
    ) -> MetadataRepositoryResult:
        """
        Determine whether a table exists in ColumnMetadata.

        Because ColumnMetadata stores one object per column, a table is
        considered present when at least one exact column-metadata object
        exists for that module and table.
        """

        normalized_module = self._clean_required(
            module,
            field_name="module",
        )
        normalized_table = self._clean_required(
            table_name,
            field_name="table_name",
        )

        operation = "find_exact_table"

        validation_error = self._validate_inputs(
            operation=operation,
            module=normalized_module,
            table_name=normalized_table,
        )

        if validation_error is not None:
            return validation_error

        try:
            where_filter = self._and_filter(
                self._equal_text_filter(
                    "module",
                    normalized_module,
                ),
                self._equal_text_filter(
                    "tableName",
                    normalized_table,
                ),
            )

            response = await self._execute_get_query(
                where_filter=where_filter,
                limit=100,
            )

            records = self._records_from_response(
                response
            )

            exact_records = [
                record
                for record in records
                if self._same_identifier(
                    record.module,
                    normalized_module,
                )
                and self._same_identifier(
                    record.table_name,
                    normalized_table,
                )
            ]

            return MetadataRepositoryResult(
                success=True,
                found=bool(exact_records),
                operation=operation,
                records=exact_records,
                message=(
                    "Table metadata found."
                    if exact_records
                    else "Table metadata was not found."
                ),
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                    "record_count": len(exact_records),
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code="weaviate_table_lookup_failed",
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                },
            )

    # ========================================================
    # Exact column lookup
    # ========================================================

    async def find_exact_column(
        self,
        *,
        module: str,
        table_name: str,
        column_name: str,
    ) -> MetadataRepositoryResult:
        """
        Find an exact column under a module and table.

        Weaviate performs the initial filtered lookup. The repository then
        performs a second exact comparison in Python so tokenized text-filter
        behavior cannot make a partial match appear valid.
        """

        normalized_module = self._clean_required(
            module,
            field_name="module",
        )
        normalized_table = self._clean_required(
            table_name,
            field_name="table_name",
        )
        normalized_column = self._clean_required(
            column_name,
            field_name="column_name",
        )

        operation = "find_exact_column"

        validation_error = self._validate_inputs(
            operation=operation,
            module=normalized_module,
            table_name=normalized_table,
            column_name=normalized_column,
        )

        if validation_error is not None:
            return validation_error

        try:
            where_filter = self._and_filter(
                self._equal_text_filter(
                    "module",
                    normalized_module,
                ),
                self._equal_text_filter(
                    "tableName",
                    normalized_table,
                ),
                self._equal_text_filter(
                    "columnName",
                    normalized_column,
                ),
            )

            response = await self._execute_get_query(
                where_filter=where_filter,
                limit=100,
            )

            records = self._records_from_response(
                response
            )

            exact_records = [
                record
                for record in records
                if self._same_identifier(
                    record.module,
                    normalized_module,
                )
                and self._same_identifier(
                    record.table_name,
                    normalized_table,
                )
                and self._same_identifier(
                    record.column_name,
                    normalized_column,
                )
            ]

            return MetadataRepositoryResult(
                success=True,
                found=bool(exact_records),
                operation=operation,
                records=exact_records,
                message=(
                    "Column metadata found."
                    if exact_records
                    else "Column metadata was not found."
                ),
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                    "column_name": normalized_column,
                    "candidate_count": len(records),
                    "exact_record_count": len(exact_records),
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code="weaviate_column_lookup_failed",
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                    "column_name": normalized_column,
                },
            )

    # ========================================================
    # Retrieve all table columns
    # ========================================================

    async def get_table_columns(
        self,
        *,
        module: str,
        table_name: str,
        limit: int = 500,
    ) -> MetadataRepositoryResult:
        """Retrieve all available column metadata for a table."""

        normalized_module = self._clean_required(
            module,
            field_name="module",
        )
        normalized_table = self._clean_required(
            table_name,
            field_name="table_name",
        )

        operation = "get_table_columns"

        validation_error = self._validate_inputs(
            operation=operation,
            module=normalized_module,
            table_name=normalized_table,
        )

        if validation_error is not None:
            return validation_error

        if limit <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "The table-column lookup limit must "
                    "be greater than zero."
                ),
                error_code="invalid_limit",
                diagnostics={
                    "limit": limit,
                },
            )

        try:
            where_filter = self._and_filter(
                self._equal_text_filter(
                    "module",
                    normalized_module,
                ),
                self._equal_text_filter(
                    "tableName",
                    normalized_table,
                ),
            )

            response = await self._execute_get_query(
                where_filter=where_filter,
                limit=limit,
            )

            records = self._records_from_response(
                response
            )

            exact_records = [
                record
                for record in records
                if self._same_identifier(
                    record.module,
                    normalized_module,
                )
                and self._same_identifier(
                    record.table_name,
                    normalized_table,
                )
            ]

            exact_records.sort(
                key=lambda record: (
                    record.column_name.casefold()
                )
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(exact_records),
                operation=operation,
                records=exact_records,
                message=(
                    "Table columns retrieved."
                    if exact_records
                    else "No columns were found for the table."
                ),
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                    "record_count": len(exact_records),
                    "limit": limit,
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code=(
                    "weaviate_table_columns_lookup_failed"
                ),
                diagnostics={
                    "module": normalized_module,
                    "table_name": normalized_table,
                    "limit": limit,
                },
            )






    async def get_table_columns_any_module(
        self,
        *,
        table_name: str,
        limit: int = 500,
    ) -> MetadataRepositoryResult:
        """
        Retrieve all metadata records for an exact table name
        without requiring the caller to already know the module.

        The returned records still contain their original module.
        """

        normalized_table = self._clean_required(
            table_name,
            field_name="table_name",
        )

        operation = (
            "get_table_columns_any_module"
        )

        if not normalized_table:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "A table name is required for "
                    "metadata lookup."
                ),
                error_code=(
                    "invalid_metadata_lookup"
                ),
                diagnostics={
                    "missing_fields": [
                        "table_name"
                    ],
                },
            )

        if limit <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "The table-column lookup limit "
                    "must be greater than zero."
                ),
                error_code="invalid_limit",
                diagnostics={
                    "limit": limit,
                },
            )

        try:
            where_filter = (
                self._equal_text_filter(
                    "tableName",
                    normalized_table,
                )
            )

            response = (
                await self._execute_get_query(
                    where_filter=where_filter,
                    limit=limit,
                )
            )

            records = (
                self._records_from_response(
                    response
                )
            )

            exact_records = [
                record
                for record in records
                if self._same_identifier(
                    record.table_name,
                    normalized_table,
                )
            ]

            exact_records.sort(
                key=lambda record: (
                    record.module.casefold(),
                    record.column_name.casefold(),
                )
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(exact_records),
                operation=operation,
                records=exact_records,
                message=(
                    "Table metadata was retrieved."
                    if exact_records
                    else (
                        "No metadata was found for "
                        "the table."
                    )
                ),
                diagnostics={
                    "table_name": (
                        normalized_table
                    ),
                    "record_count": (
                        len(exact_records)
                    ),
                    "limit": limit,
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code=(
                    "weaviate_table_lookup_failed"
                ),
                diagnostics={
                    "table_name": (
                        normalized_table
                    ),
                    "limit": limit,
                },
            )


    async def search_candidate_columns_any_module(
        self,
        *,
        table_name: str,
        semantic_query: Optional[str] = None,
        query_vector: Optional[Sequence[float]] = None,
        top_k: int = 5,
        max_distance: Optional[float] = None,
    ) -> MetadataRepositoryResult:
        """
        Search semantically similar columns for an exact table name
        without requiring the caller to already know the module.

        The semantic query is sent directly to Weaviate as nearText
        (or query_vector as nearVector). The lookup is restricted to
        the exact physical table name. Returned records preserve their
        stored module metadata.
        """

        normalized_table = self._clean_required(
            table_name,
            field_name="table_name",
        )
        normalized_query = str(
            semantic_query or ""
        ).strip()

        operation = (
            "search_candidate_columns_any_module"
        )

        if not normalized_table:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "A table name is required for "
                    "candidate-column search."
                ),
                error_code="invalid_metadata_lookup",
                diagnostics={
                    "missing_fields": [
                        "table_name"
                    ],
                },
            )

        if (
            not normalized_query
            and query_vector is None
        ):
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "Either semantic_query or "
                    "query_vector must be supplied."
                ),
                error_code="missing_search_input",
            )

        if top_k <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "top_k must be greater than zero."
                ),
                error_code="invalid_top_k",
                diagnostics={
                    "top_k": top_k,
                },
            )

        try:
            where_filter = (
                self._equal_text_filter(
                    "tableName",
                    normalized_table,
                )
            )

            near_text: Optional[
                Dict[str, Any]
            ] = None
            near_vector: Optional[
                Dict[str, Any]
            ] = None

            if query_vector is not None:
                near_vector = {
                    "vector": list(
                        query_vector
                    ),
                }

                if max_distance is not None:
                    near_vector[
                        "distance"
                    ] = max_distance

                search_mode = "near_vector"

            else:
                near_text = {
                    "concepts": [
                        normalized_query
                    ],
                }

                if max_distance is not None:
                    near_text[
                        "distance"
                    ] = max_distance

                search_mode = "near_text"

            response = (
                await self._execute_get_query(
                    where_filter=where_filter,
                    limit=top_k,
                    near_text=near_text,
                    near_vector=near_vector,
                    include_distance=True,
                )
            )

            records = (
                self._records_from_response(
                    response
                )
            )

            exact_table_records = [
                record
                for record in records
                if self._same_identifier(
                    record.table_name,
                    normalized_table,
                )
            ]

            exact_table_records.sort(
                key=lambda record: (
                    record.vector_distance
                    if (
                        record.vector_distance
                        is not None
                    )
                    else float("inf")
                )
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(
                    exact_table_records
                ),
                operation=operation,
                records=(
                    exact_table_records
                ),
                message=(
                    "Candidate columns found."
                    if exact_table_records
                    else (
                        "No candidate columns "
                        "were found."
                    )
                ),
                diagnostics={
                    "table_name": (
                        normalized_table
                    ),
                    "semantic_query": (
                        normalized_query or None
                    ),
                    "search_mode": (
                        search_mode
                    ),
                    "candidate_count": (
                        len(
                            exact_table_records
                        )
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code=(
                    "weaviate_candidate_"
                    "column_search_failed"
                ),
                diagnostics={
                    "table_name": (
                        normalized_table
                    ),
                    "semantic_query": (
                        normalized_query or None
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )






    # ========================================================
    # Report-wide semantic metadata search
    # ========================================================

    async def search_metadata_by_module(
        self,
        *,
        module: str,
        semantic_query: Optional[str] = None,
        query_vector: Optional[
            Sequence[float]
        ] = None,
        top_k: int = 5,
        max_distance: Optional[
            float
        ] = None,
    ) -> MetadataRepositoryResult:
        """
        Search ColumnMetadata semantically across ALL
        tables belonging to one report/module.

        Example:

            module="Inventory Report"

            semantic_query=(
                "What does allocated quantity mean?"
            )

        Weaviate performs:

            module == "Inventory Report"
            +
            nearText(user question)

        The caller does NOT need to know the table name
        or column name beforehand.

        This is intended for:
        - metadata questions
        - field/column discovery
        - later value lookup
        - later recovery support
        """

        normalized_module = (
            self._clean_required(
                module,
                field_name="module",
            )
        )

        normalized_query = str(
            semantic_query or ""
        ).strip()

        operation = (
            "search_metadata_by_module"
        )

        # ====================================================
        # VALIDATION
        # ====================================================

        if not normalized_module:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "A module/report name is required "
                    "for metadata search."
                ),
                error_code=(
                    "invalid_metadata_lookup"
                ),
                diagnostics={
                    "missing_fields": [
                        "module"
                    ],
                },
            )

        if (
            not normalized_query
            and query_vector is None
        ):
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "Either semantic_query or "
                    "query_vector must be supplied."
                ),
                error_code=(
                    "missing_search_input"
                ),
            )

        if top_k <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "top_k must be greater "
                    "than zero."
                ),
                error_code=(
                    "invalid_top_k"
                ),
                diagnostics={
                    "top_k": top_k,
                },
            )

        # ====================================================
        # SEARCH
        # ====================================================

        try:

            # Restrict vector search to the selected report.
            where_filter = (
                self._equal_text_filter(
                    "module",
                    normalized_module,
                )
            )

            near_text: Optional[
                Dict[str, Any]
            ] = None

            near_vector: Optional[
                Dict[str, Any]
            ] = None

            # ------------------------------------------------
            # Explicit vector
            # ------------------------------------------------

            if query_vector is not None:

                near_vector = {
                    "vector": list(
                        query_vector
                    ),
                }

                if max_distance is not None:
                    near_vector[
                        "distance"
                    ] = max_distance

                search_mode = (
                    "near_vector"
                )

            # ------------------------------------------------
            # Weaviate nearText
            # ------------------------------------------------

            else:

                near_text = {
                    "concepts": [
                        normalized_query
                    ],
                }

                if max_distance is not None:
                    near_text[
                        "distance"
                    ] = max_distance

                search_mode = (
                    "near_text"
                )

            # ------------------------------------------------
            # Execute
            # ------------------------------------------------

            response = (
                await self._execute_get_query(
                    where_filter=where_filter,
                    limit=top_k,
                    near_text=near_text,
                    near_vector=near_vector,
                    include_distance=True,
                )
            )

            records = (
                self._records_from_response(
                    response
                )
            )

            # =================================================
            # EXACT MODULE CHECK
            # =================================================
            #
            # Weaviate performs the filter, but we still
            # verify the stored module ourselves so a partial
            # text match can never leak metadata from another
            # report.

            exact_module_records = [
                record
                for record in records
                if self._same_identifier(
                    record.module,
                    normalized_module,
                )
            ]

            # =================================================
            # CLOSEST VECTOR MATCH FIRST
            # =================================================

            exact_module_records.sort(
                key=lambda record: (
                    record.vector_distance
                    if (
                        record.vector_distance
                        is not None
                    )
                    else float(
                        "inf"
                    )
                )
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(
                    exact_module_records
                ),
                operation=operation,
                records=(
                    exact_module_records
                ),
                message=(
                    "Report metadata matches found."
                    if exact_module_records
                    else (
                        "No relevant metadata "
                        "was found for the report."
                    )
                ),
                diagnostics={
                    "module": (
                        normalized_module
                    ),
                    "semantic_query": (
                        normalized_query
                        or None
                    ),
                    "search_mode": (
                        search_mode
                    ),
                    "record_count": (
                        len(
                            exact_module_records
                        )
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )

        except Exception as error:

            return self._error_result(
                operation=operation,
                error=error,
                error_code=(
                    "weaviate_report_metadata_"
                    "search_failed"
                ),
                diagnostics={
                    "module": (
                        normalized_module
                    ),
                    "semantic_query": (
                        normalized_query
                        or None
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )













    # ========================================================
    # Semantic candidate-column search
    # ========================================================

    # ========================================================
    # Report-wide semantic metadata search
    # ========================================================

    async def search_metadata_by_module(
        self,
        *,
        module: str,
        semantic_query: Optional[str] = None,
        query_vector: Optional[
            Sequence[float]
        ] = None,
        top_k: int = 5,
        max_distance: Optional[
            float
        ] = None,
    ) -> MetadataRepositoryResult:
        """
        Search ColumnMetadata semantically across ALL
        tables belonging to one report/module.

        Example:

            module="Inventory Report"

            semantic_query=(
                "What does allocated quantity mean?"
            )

        Weaviate performs:

            module == "Inventory Report"
            +
            nearText(user question)

        The caller does NOT need to know the table name
        or column name beforehand.

        This is intended for:
        - metadata questions
        - field/column discovery
        - later value lookup
        - later recovery support
        """

        normalized_module = (
            self._clean_required(
                module,
                field_name="module",
            )
        )

        normalized_query = str(
            semantic_query or ""
        ).strip()

        operation = (
            "search_metadata_by_module"
        )

        # ====================================================
        # VALIDATION
        # ====================================================

        if not normalized_module:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "A module/report name is required "
                    "for metadata search."
                ),
                error_code=(
                    "invalid_metadata_lookup"
                ),
                diagnostics={
                    "missing_fields": [
                        "module"
                    ],
                },
            )

        if (
            not normalized_query
            and query_vector is None
        ):
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "Either semantic_query or "
                    "query_vector must be supplied."
                ),
                error_code=(
                    "missing_search_input"
                ),
            )

        if top_k <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "top_k must be greater "
                    "than zero."
                ),
                error_code=(
                    "invalid_top_k"
                ),
                diagnostics={
                    "top_k": top_k,
                },
            )

        # ====================================================
        # SEARCH
        # ====================================================

        try:

            # Restrict vector search to the selected report.
            where_filter = (
                self._equal_text_filter(
                    "module",
                    normalized_module,
                )
            )

            near_text: Optional[
                Dict[str, Any]
            ] = None

            near_vector: Optional[
                Dict[str, Any]
            ] = None

            # ------------------------------------------------
            # Explicit vector
            # ------------------------------------------------

            if query_vector is not None:

                near_vector = {
                    "vector": list(
                        query_vector
                    ),
                }

                if max_distance is not None:
                    near_vector[
                        "distance"
                    ] = max_distance

                search_mode = (
                    "near_vector"
                )

            # ------------------------------------------------
            # Weaviate nearText
            # ------------------------------------------------

            else:

                near_text = {
                    "concepts": [
                        normalized_query
                    ],
                }

                if max_distance is not None:
                    near_text[
                        "distance"
                    ] = max_distance

                search_mode = (
                    "near_text"
                )

            # ------------------------------------------------
            # Execute
            # ------------------------------------------------

            response = (
                await self._execute_get_query(
                    where_filter=where_filter,
                    limit=top_k,
                    near_text=near_text,
                    near_vector=near_vector,
                    include_distance=True,
                )
            )

            records = (
                self._records_from_response(
                    response
                )
            )

            # =================================================
            # EXACT MODULE CHECK
            # =================================================
            #
            # Weaviate performs the filter, but we still
            # verify the stored module ourselves so a partial
            # text match can never leak metadata from another
            # report.

            exact_module_records = [
                record
                for record in records
                if self._same_identifier(
                    record.module,
                    normalized_module,
                )
            ]

            # =================================================
            # CLOSEST VECTOR MATCH FIRST
            # =================================================

            exact_module_records.sort(
                key=lambda record: (
                    record.vector_distance
                    if (
                        record.vector_distance
                        is not None
                    )
                    else float(
                        "inf"
                    )
                )
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(
                    exact_module_records
                ),
                operation=operation,
                records=(
                    exact_module_records
                ),
                message=(
                    "Report metadata matches found."
                    if exact_module_records
                    else (
                        "No relevant metadata "
                        "was found for the report."
                    )
                ),
                diagnostics={
                    "module": (
                        normalized_module
                    ),
                    "semantic_query": (
                        normalized_query
                        or None
                    ),
                    "search_mode": (
                        search_mode
                    ),
                    "record_count": (
                        len(
                            exact_module_records
                        )
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )

        except Exception as error:

            return self._error_result(
                operation=operation,
                error=error,
                error_code=(
                    "weaviate_report_metadata_"
                    "search_failed"
                ),
                diagnostics={
                    "module": (
                        normalized_module
                    ),
                    "semantic_query": (
                        normalized_query
                        or None
                    ),
                    "top_k": top_k,
                    "max_distance": (
                        max_distance
                    ),
                },
            )

    # ========================================================
    # Governance: retrieve every stored object (Step 38)
    # ========================================================

    async def list_all_records(
        self,
        *,
        limit: int = 5000,
    ) -> MetadataRepositoryResult:
        """
        Retrieve every stored ColumnMetadata object, unfiltered.

        Read-only. Intended for deterministic governance auditing
        (duplicate custom_id / required-field / physical-reference
        checks across the whole class) - never used by ordinary
        metadata-query/value-lookup runtime paths, which stay
        report-scoped via the methods above.
        """

        operation = "list_all_records"

        if limit <= 0:
            return MetadataRepositoryResult(
                success=False,
                found=False,
                operation=operation,
                message=(
                    "The metadata list limit must "
                    "be greater than zero."
                ),
                error_code="invalid_limit",
                diagnostics={"limit": limit},
            )

        try:
            response = await asyncio.to_thread(
                self._execute_get_all_sync,
                limit=limit,
            )

            records = self._records_from_response(
                response
            )

            return MetadataRepositoryResult(
                success=True,
                found=bool(records),
                operation=operation,
                records=records,
                message=(
                    "Metadata retrieved."
                    if records
                    else "No metadata objects were found."
                ),
                diagnostics={
                    "record_count": len(records),
                    "limit": limit,
                },
            )

        except Exception as error:
            return self._error_result(
                operation=operation,
                error=error,
                error_code="weaviate_metadata_list_failed",
                diagnostics={"limit": limit},
            )

    def _execute_get_all_sync(
        self,
        *,
        limit: int,
    ) -> Dict[str, Any]:
        query = self.client.query.get(
            self.collection_name,
            DEFAULT_RETURN_PROPERTIES,
        )
        query = query.with_limit(limit)
        query = query.with_additional(["id"])

        response = query.do()

        self._raise_for_graphql_errors(response)

        if not isinstance(response, dict):
            raise TypeError(
                "Weaviate returned a non-dictionary GraphQL response."
            )

        return response

    # ========================================================
    # Weaviate v3 query helpers
    # ========================================================

    async def _execute_get_query(
        self,
        *,
        where_filter: Dict[str, Any],
        limit: int,
        near_text: Optional[Dict[str, Any]] = None,
        near_vector: Optional[Dict[str, Any]] = None,
        include_distance: bool = False,
    ) -> Dict[str, Any]:
        """Build and execute one synchronous v3 GraphQL Get query."""

        return await asyncio.to_thread(
            self._execute_get_query_sync,
            where_filter=where_filter,
            limit=limit,
            near_text=near_text,
            near_vector=near_vector,
            include_distance=include_distance,
        )

    def _execute_get_query_sync(
        self,
        *,
        where_filter: Dict[str, Any],
        limit: int,
        near_text: Optional[Dict[str, Any]] = None,
        near_vector: Optional[Dict[str, Any]] = None,
        include_distance: bool = False,
    ) -> Dict[str, Any]:
        additional_properties = ["id"]

        if include_distance:
            additional_properties.append("distance")

        query = self.client.query.get(
            self.collection_name,
            DEFAULT_RETURN_PROPERTIES,
        )
        query = query.with_where(where_filter)
        query = query.with_limit(limit)
        query = query.with_additional(
            additional_properties
        )

        if near_vector is not None:
            query = query.with_near_vector(
                near_vector
            )
        elif near_text is not None:
            query = query.with_near_text(
                near_text
            )

        response = query.do()

        self._raise_for_graphql_errors(response)

        if not isinstance(response, dict):
            raise TypeError(
                "Weaviate returned a non-dictionary GraphQL response."
            )

        return response

    @staticmethod
    def _equal_text_filter(
        property_name: str,
        value: str,
    ) -> Dict[str, Any]:
        return {
            "path": [property_name],
            "operator": "Equal",
            "valueText": value,
        }

    @staticmethod
    def _and_filter(
        *operands: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "operator": "And",
            "operands": list(operands),
        }

    @staticmethod
    def _raise_for_graphql_errors(
        response: Any,
    ) -> None:
        if not isinstance(response, dict):
            return

        errors = response.get("errors")

        if not errors:
            return

        messages: List[str] = []

        for error in errors:
            if isinstance(error, dict):
                message = error.get("message")
                messages.append(
                    str(message or error)
                )
            else:
                messages.append(str(error))

        raise RuntimeError(
            "; ".join(messages)
            or "Weaviate GraphQL query failed."
        )

    # ========================================================
    # Internal helpers
    # ========================================================

    @staticmethod
    def _clean_required(
        value: Any,
        *,
        field_name: str,
    ) -> str:
        del field_name
        return str(value or "").strip()

    @staticmethod
    def _same_identifier(
        first_value: str,
        second_value: str,
    ) -> bool:
        """Perform an exact, case-insensitive identifier comparison."""

        return (
            str(first_value or "").strip().casefold()
            == str(second_value or "").strip().casefold()
        )

    @staticmethod
    def _validate_inputs(
        *,
        operation: str,
        module: str,
        table_name: str,
        column_name: Optional[str] = None,
    ) -> Optional[MetadataRepositoryResult]:
        missing_fields: List[str] = []

        if not module:
            missing_fields.append("module")

        if not table_name:
            missing_fields.append("table_name")

        if column_name is not None and not column_name:
            missing_fields.append("column_name")

        if not missing_fields:
            return None

        return MetadataRepositoryResult(
            success=False,
            found=False,
            operation=operation,
            message=(
                "Required metadata lookup fields are "
                "missing."
            ),
            error_code="invalid_metadata_lookup",
            diagnostics={
                "missing_fields": missing_fields,
            },
        )

    def _records_from_response(
        self,
        response: Dict[str, Any],
    ) -> List[ColumnMetadataRecord]:
        data = response.get("data") or {}
        get_result = data.get("Get") or {}
        objects = get_result.get(
            self.collection_name
        ) or []

        if not isinstance(objects, list):
            return []

        records: List[ColumnMetadataRecord] = []

        for obj in objects:
            if isinstance(obj, dict):
                records.append(
                    self._record_from_object(obj)
                )

        return records

    @staticmethod
    def _record_from_object(
        obj: Dict[str, Any],
    ) -> ColumnMetadataRecord:
        properties = dict(obj or {})

        additional = properties.pop(
            "_additional",
            {},
        ) or {}

        distance = additional.get("distance")
        object_uuid = additional.get("id")

        used_value = properties.get(
            "used",
            0,
        )

        try:
            used_value = int(
                used_value or 0
            )
        except (TypeError, ValueError):
            used_value = 0

        return ColumnMetadataRecord(
            uuid=(
                str(object_uuid)
                if object_uuid is not None
                else None
            ),
            custom_id=(
                str(properties.get("custom_id"))
                if properties.get("custom_id") is not None
                else None
            ),
            module=str(
                properties.get(
                    "module",
                    "",
                )
                or ""
            ).strip(),
            table_name=str(
                properties.get(
                    "tableName",
                    "",
                )
                or ""
            ).strip(),
            column_name=str(
                properties.get(
                    "columnName",
                    "",
                )
                or ""
            ).strip(),
            data_type=(
                str(
                    properties.get("dataType")
                ).strip()
                if properties.get("dataType") is not None
                else None
            ),
            description=(
                str(
                    properties.get("description")
                ).strip()
                if properties.get("description") is not None
                else None
            ),
            text=(
                str(
                    properties.get("text")
                ).strip()
                if properties.get("text") is not None
                else None
            ),
            distinct_values=list(
                properties.get(
                    "distinctValues",
                    [],
                )
                or []
            ),
            units=list(
                properties.get(
                    "units",
                    [],
                )
                or []
            ),
            role_access=list(
                properties.get(
                    "roleAccess",
                    [],
                )
                or []
            ),
            used=used_value,
            filterable=bool(
                properties.get(
                    "filterable",
                    False,
                )
            ),
            vector_distance=distance,
        )

    @staticmethod
    def _error_result(
        *,
        operation: str,
        error: Exception,
        error_code: str,
        diagnostics: Optional[
            Dict[str, Any]
        ] = None,
    ) -> MetadataRepositoryResult:
        return MetadataRepositoryResult(
            success=False,
            found=False,
            operation=operation,
            records=[],
            message=str(error),
            error_code=error_code,
            exception_type=type(error).__name__,
            diagnostics=diagnostics or {},
        )



    