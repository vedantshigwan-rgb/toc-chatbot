from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from openai import OpenAI


DOTENV_PATH = Path(
    r"D:\chatbot_w_L\backend_v3\config\.env"
)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_API_KEY_VARIABLE = "OPENAI_API_KEY_MODEL"

# OpenAI permits large batches, but a smaller batch is safer for
# request-size and rate-limit handling.
DEFAULT_BATCH_SIZE = 100


class OpenAIEmbedder(Embeddings):
    """
    LangChain-compatible OpenAI embedding provider.

    Only this class handles:
    - Loading the .env file
    - Reading the OpenAI API key
    - Creating the OpenAI client
    - Calling the OpenAI embeddings API

    The index builder and searcher receive this object through
    dependency injection.
    """

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        api_key_variable: str = DEFAULT_API_KEY_VARIABLE,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero.")

        self.model = model
        self.api_key_variable = api_key_variable
        self.batch_size = batch_size

        self._load_environment()

        api_key = os.getenv(self.api_key_variable)

        if not api_key:
            raise ValueError(
                f"{self.api_key_variable} is not set in "
                f"the environment or in: {DOTENV_PATH}"
            )

        self.client = OpenAI(api_key=api_key)

    @staticmethod
    def _load_environment() -> None:
        """
        Load environment variables from the configured .env file.
        """

        if not DOTENV_PATH.exists():
            raise FileNotFoundError(
                f".env file was not found: {DOTENV_PATH}"
            )

        load_dotenv(
            dotenv_path=DOTENV_PATH,
            override=False,
        )

    def embed_query(self, text: str) -> List[float]:
        """
        Generate an embedding for one search query.

        LangChain FAISS calls this during similarity search.
        """

        cleaned_text = self._clean_text(text)

        if not cleaned_text:
            raise ValueError("Query text cannot be empty.")

        embeddings = self._create_embeddings(
            [cleaned_text]
        )

        return embeddings[0]

    def embed_documents(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple documents.

        LangChain FAISS calls this while creating an index.
        The returned list preserves the input order.
        """

        if not texts:
            return []

        cleaned_texts = [
            self._clean_text(text)
            for text in texts
        ]

        empty_positions = [
            index
            for index, text in enumerate(cleaned_texts)
            if not text
        ]

        if empty_positions:
            raise ValueError(
                "Empty document text found at positions: "
                f"{empty_positions}"
            )

        all_embeddings: List[List[float]] = []

        for start in range(
            0,
            len(cleaned_texts),
            self.batch_size,
        ):
            batch = cleaned_texts[
                start:start + self.batch_size
            ]

            batch_embeddings = self._create_embeddings(
                batch
            )

            all_embeddings.extend(
                batch_embeddings
            )

        return all_embeddings

    def _create_embeddings(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        """
        Call the OpenAI embeddings API.
        """

        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
        )

        # Explicit sorting guarantees the same order as the input.
        ordered_items = sorted(
            response.data,
            key=lambda item: item.index,
        )

        return [
            item.embedding
            for item in ordered_items
        ]

    @staticmethod
    def _clean_text(value: object) -> str:
        """
        Convert text to a clean, single-line string.
        """

        if value is None:
            return ""

        return " ".join(
            str(value).strip().split()
        )