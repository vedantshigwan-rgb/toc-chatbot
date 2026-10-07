from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import faiss
import numpy as np


class HelpRetrievalError(
    RuntimeError
):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Optional[
            dict[str, Any]
        ] = None,
    ) -> None:

        super().__init__(
            message
        )

        self.code = code

        self.details = (
            details or {}
        )


class HelpRetrievalService:
    """
    Retrieves application user-guide content
    from a dedicated FAISS index.

    This service performs retrieval only.
    It does not generate the final answer.
    """

    def __init__(
        self,
        *,
        pool: Any,
        index_path: Optional[
            str
        ] = None,
        documents_path: Optional[
            str
        ] = None,
        config_path: Optional[
            str
        ] = None,
    ) -> None:

        base_dir = (
            Path(__file__)
            .resolve()
            .parent
            .parent
        )

        help_dir = (
            base_dir
            / "data"
            / "help_faiss"
        )

        self.index_path = Path(
            index_path
            or (
                help_dir
                / "index.faiss"
            )
        )

        self.documents_path = Path(
            documents_path
            or (
                help_dir
                / "documents.json"
            )
        )

        self.config_path = Path(
            config_path
            or (
                help_dir
                / "config.json"
            )
        )

        self.pool = pool

        self.top_k = int(
            os.getenv(
                "HELP_TOP_K",
                "4",
            )
        )

        self.min_score = float(
            os.getenv(
                "HELP_MIN_SCORE",
                "0.25",
            )
        )

        self.index = None

        self.documents: list[
            dict[str, Any]
        ] = []

        self.embedding_model = ""

        self._load()

    def _load(
        self,
    ) -> None:

        missing = [
            str(path)
            for path in (
                self.index_path,
                self.documents_path,
                self.config_path,
            )
            if not path.exists()
        ]

        if missing:
            raise HelpRetrievalError(
                (
                    "Help FAISS resources "
                    "are missing."
                ),
                code=(
                    "help_index_missing"
                ),
                details={
                    "missing": missing,
                },
            )

        try:

            self.index = (
                faiss.read_index(
                    str(
                        self.index_path
                    )
                )
            )

            with (
                self.documents_path.open(
                    "r",
                    encoding="utf-8",
                )
            ) as file:

                self.documents = (
                    json.load(file)
                )

            with (
                self.config_path.open(
                    "r",
                    encoding="utf-8",
                )
            ) as file:

                config = (
                    json.load(file)
                )

        except Exception as error:
            raise HelpRetrievalError(
                (
                    "Help FAISS resources "
                    "could not be loaded."
                ),
                code=(
                    "help_index_load_failed"
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

        if not isinstance(
            self.documents,
            list,
        ):
            raise HelpRetrievalError(
                (
                    "Help document metadata "
                    "is invalid."
                ),
                code=(
                    "invalid_help_documents"
                ),
            )

        self.embedding_model = str(
            config.get(
                "embedding_model"
            )
            or ""
        ).strip()

        if not self.embedding_model:
            raise HelpRetrievalError(
                (
                    "Help embedding model "
                    "configuration is missing."
                ),
                code=(
                    "help_embedding_model_missing"
                ),
            )

        if (
            self.index.ntotal
            != len(
                self.documents
            )
        ):
            raise HelpRetrievalError(
                (
                    "Help FAISS index and "
                    "document metadata are "
                    "out of sync."
                ),
                code=(
                    "help_index_document_mismatch"
                ),
                details={
                    "index_count": (
                        self.index.ntotal
                    ),
                    "document_count": (
                        len(
                            self.documents
                        )
                    ),
                },
            )

    async def _embed_query(
        self,
        query: str,
    ) -> np.ndarray:

        try:

            client = (
                await self.pool.get_client()
            )

            async with (
                self.pool.semaphore
            ):

                response = (
                    await client.embeddings.create(
                        model=(
                            self.embedding_model
                        ),
                        input=[
                            query
                        ],
                    )
                )

        except Exception as error:
            raise HelpRetrievalError(
                (
                    "Help query embedding "
                    "failed."
                ),
                code=(
                    "help_embedding_failed"
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

        vector = np.asarray(
            [
                response
                .data[0]
                .embedding
            ],
            dtype="float32",
        )

        faiss.normalize_L2(
            vector
        )

        return vector

    async def search(
        self,
        *,
        query: str,
        top_k: Optional[int] = None,
    ) -> list[dict[str, Any]]:

        cleaned_query = str(
            query or ""
        ).strip()

        if not cleaned_query:
            raise HelpRetrievalError(
                (
                    "Help search query "
                    "cannot be empty."
                ),
                code=(
                    "empty_help_query"
                ),
            )

        if self.index is None:
            raise HelpRetrievalError(
                (
                    "Help FAISS index "
                    "is not loaded."
                ),
                code=(
                    "help_index_not_loaded"
                ),
            )

        limit = (
            int(top_k)
            if top_k is not None
            else self.top_k
        )

        limit = max(
            1,
            min(
                limit,
                len(
                    self.documents
                ),
            ),
        )

        vector = (
            await self._embed_query(
                cleaned_query
            )
        )

        scores, indexes = (
            self.index.search(
                vector,
                limit,
            )
        )

        matches: list[
            dict[str, Any]
        ] = []

        for score, index_position in zip(
            scores[0],
            indexes[0],
        ):

            position = int(
                index_position
            )

            if position < 0:
                continue

            numeric_score = float(
                score
            )

            if (
                numeric_score
                < self.min_score
            ):
                continue

            if position >= len(
                self.documents
            ):
                continue

            document = dict(
                self.documents[
                    position
                ]
            )

            document[
                "score"
            ] = numeric_score

            matches.append(
                document
            )

        return matches