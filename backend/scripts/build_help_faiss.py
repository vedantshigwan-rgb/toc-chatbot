from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from utils.openai_pool import OpenAIPool


BASE_DIR = Path(__file__).resolve().parent.parent

HELP_DATA_PATH = (
    BASE_DIR
    / "data"
    / "help"
    / "help_knowledge.json"
)

HELP_INDEX_DIR = (
    BASE_DIR
    / "data"
    / "help_faiss"
)

INDEX_PATH = (
    HELP_INDEX_DIR
    / "index.faiss"
)

DOCUMENTS_PATH = (
    HELP_INDEX_DIR
    / "documents.json"
)

CONFIG_PATH = (
    HELP_INDEX_DIR
    / "config.json"
)


EMBEDDING_MODEL = os.getenv(
    "HELP_EMBEDDING_MODEL",
    "text-embedding-3-small",
)


def load_help_documents() -> list[dict[str, Any]]:

    if not HELP_DATA_PATH.exists():
        raise FileNotFoundError(
            f"Help knowledge file not found: "
            f"{HELP_DATA_PATH}"
        )

    with HELP_DATA_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "help_knowledge.json must contain a JSON array."
        )

    documents: list[dict[str, Any]] = []

    seen_ids: set[str] = set()

    for position, item in enumerate(data):

        if not isinstance(item, dict):
            raise ValueError(
                f"Help item at position {position} "
                "must be an object."
            )

        document_id = str(
            item.get("id") or ""
        ).strip()

        title = str(
            item.get("title") or ""
        ).strip()

        content = str(
            item.get("content") or ""
        ).strip()

        tags = item.get("tags") or []

        if not document_id:
            raise ValueError(
                f"Help item at position {position} "
                "has no id."
            )

        if document_id in seen_ids:
            raise ValueError(
                f"Duplicate help id: {document_id}"
            )

        seen_ids.add(document_id)

        if not title:
            raise ValueError(
                f"Help item {document_id} has no title."
            )

        if not content:
            raise ValueError(
                f"Help item {document_id} has no content."
            )

        if not isinstance(tags, list):
            tags = []

        normalized_tags = [
            str(tag).strip()
            for tag in tags
            if str(tag).strip()
        ]

        retrieval_text = "\n".join(
            [
                f"Title: {title}",
                f"Content: {content}",
                (
                    "Tags: "
                    + ", ".join(normalized_tags)
                    if normalized_tags
                    else ""
                ),
            ]
        ).strip()

        documents.append(
            {
                "id": document_id,
                "title": title,
                "content": content,
                "tags": normalized_tags,
                "retrieval_text": retrieval_text,
            }
        )

    if not documents:
        raise ValueError(
            "No help documents were found."
        )

    return documents


async def create_embeddings(
    *,
    pool: OpenAIPool,
    texts: list[str],
) -> np.ndarray:

    client = await pool.get_client()

    async with pool.semaphore:

        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )

    vectors = np.asarray(
        [
            item.embedding
            for item in response.data
        ],
        dtype="float32",
    )

    if vectors.ndim != 2:
        raise ValueError(
            "Embedding response has invalid dimensions."
        )

    # Normalize so IndexFlatIP behaves like
    # cosine-similarity search.
    faiss.normalize_L2(vectors)

    return vectors


async def main() -> None:

    documents = load_help_documents()

    print(
        f"[HELP] Loaded "
        f"{len(documents)} documents."
    )

    pool = OpenAIPool()

    try:

        vectors = await create_embeddings(
            pool=pool,
            texts=[
                document["retrieval_text"]
                for document in documents
            ],
        )

        dimension = int(
            vectors.shape[1]
        )

        index = faiss.IndexFlatIP(
            dimension
        )

        index.add(vectors)

        HELP_INDEX_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        faiss.write_index(
            index,
            str(INDEX_PATH),
        )

        with DOCUMENTS_PATH.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                documents,
                file,
                indent=2,
                ensure_ascii=False,
            )

        with CONFIG_PATH.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                {
                    "embedding_model": (
                        EMBEDDING_MODEL
                    ),
                    "dimension": dimension,
                    "document_count": (
                        len(documents)
                    ),
                },
                file,
                indent=2,
            )

        print(
            "[OK] Help FAISS index created."
        )

        print(
            f"[OK] Documents: "
            f"{len(documents)}"
        )

        print(
            f"[OK] Dimension: {dimension}"
        )

        print(
            f"[OK] Index: {INDEX_PATH}"
        )

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())