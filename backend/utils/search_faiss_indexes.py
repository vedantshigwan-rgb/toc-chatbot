from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from utils.openai_embedder import OpenAIEmbedder
# from utils.search_faiss_indexes import FAQIndexSearcher



def clean_text(value: object) -> str:
    """
    Convert a value into a clean string.
    """

    if value is None:
        return ""

    return " ".join(
        str(value).strip().split()
    )


def sanitize_folder_name(value: object) -> str:
    """
    Convert report names and variable keys into safe folder names.
    """

    cleaned_value = clean_text(value)

    if not cleaned_value:
        return "unnamed"

    cleaned_value = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        cleaned_value,
    )

    cleaned_value = cleaned_value.rstrip(". ")

    return cleaned_value or "unnamed"


def make_variables_key(
    variable_names: Iterable[str],
) -> str:
    """
    Create an exact, order-independent variable key.

    Examples:
        ["item", "buyer_name"]
            -> "buyer_name,item"

        []
            -> "no_variables"
    """

    normalized_variables = {
        clean_text(variable).lower()
        for variable in variable_names
        if clean_text(variable)
    }

    if not normalized_variables:
        return "no_variables"

    return ",".join(
        sorted(normalized_variables)
    )


class FAQIndexSearcher:
    """
    Search persisted report-specific FAQ FAISS indexes.

    The embedder is supplied from outside so this class does not:
    - Load a .env file.
    - Create an OpenAI client.
    - Know how the API key is configured.
    """

    def __init__(
        self,
        base_path: str,
        embedder: OpenAIEmbedder,
    ) -> None:
        self.base_path = Path(base_path)
        self.embedder = embedder

        self._vectorstore_cache: Dict[
            str,
            FAISS,
        ] = {}

        if not self.base_path.exists():
            raise FileNotFoundError(
                f"FAISS base path was not found: "
                f"{self.base_path}"
            )

    def _get_report_folder(
        self,
        report_name: str,
    ) -> Path:
        report_folder = (
            self.base_path
            / sanitize_folder_name(report_name)
        )

        if not report_folder.exists():
            available_reports = self.list_reports()

            raise FileNotFoundError(
                f"No stored index was found for report "
                f"'{report_name}'. "
                f"Available reports: {available_reports}"
            )

        return report_folder

    def _read_manifest(
        self,
        report_name: str,
    ) -> Dict[str, object]:
        report_folder = self._get_report_folder(
            report_name
        )

        manifest_path = (
            report_folder / "manifest.json"
        )

        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Manifest file was not found: "
                f"{manifest_path}"
            )

        with manifest_path.open(
            "r",
            encoding="utf-8",
        ) as manifest_file:
            return json.load(manifest_file)

    def _resolve_index_path(
        self,
        report_name: str,
        variable_names: Optional[List[str]],
        search_all_variables: bool,
    ) -> Tuple[Path, Optional[str]]:
        """
        Resolve the correct persisted index.

        search_all_variables=True:
            Search the report's complete index.

        search_all_variables=False:
            Search only the exact variable combination index.
        """

        report_folder = self._get_report_folder(
            report_name
        )

        if search_all_variables:
            return (
                report_folder / "all",
                None,
            )

        variables_key = make_variables_key(
            variable_names or []
        )

        manifest = self._read_manifest(
            report_name
        )

        variable_indexes = manifest.get(
            "variable_indexes",
            {},
        )

        variable_details = variable_indexes.get(
            variables_key
        )

        if not variable_details:
            available_variable_keys = sorted(
                variable_indexes.keys()
            )

            raise ValueError(
                f"No exact variable index exists for "
                f"'{variables_key}' in report '{report_name}'. "
                f"Available variable combinations: "
                f"{available_variable_keys}"
            )

        relative_folder = variable_details[
            "folder"
        ]

        return (
            report_folder / relative_folder,
            variables_key,
        )

    def _load_vectorstore(
        self,
        index_path: Path,
    ) -> FAISS:
        """
        Load and cache one FAISS vector store.
        """

        cache_key = str(
            index_path.resolve()
        )

        cached_vectorstore = (
            self._vectorstore_cache.get(
                cache_key
            )
        )

        if cached_vectorstore is not None:
            return cached_vectorstore

        faiss_file = (
            index_path / "index.faiss"
        )

        metadata_file = (
            index_path / "index.pkl"
        )

        if not faiss_file.exists():
            raise FileNotFoundError(
                f"FAISS index file was not found: "
                f"{faiss_file}"
            )

        if not metadata_file.exists():
            raise FileNotFoundError(
                f"FAISS metadata file was not found: "
                f"{metadata_file}"
            )

        vectorstore = FAISS.load_local(
            folder_path=str(index_path),
            embeddings=self.embedder,
            allow_dangerous_deserialization=True,
        )

        self._vectorstore_cache[
            cache_key
        ] = vectorstore

        return vectorstore

    @staticmethod
    def _format_result(
        document: Document,
        score: float,
        report_name: str,
        searched_variables_key: Optional[str],
    ) -> Dict[str, object]:
        metadata = document.metadata or {}

        stored_variables = metadata.get(
            "variables",
            [],
        )

        if not isinstance(
            stored_variables,
            list,
        ):
            stored_variables = [
                variable.strip()
                for variable in clean_text(
                    stored_variables
                ).split(",")
                if variable.strip()
            ]

        distance = float(score)

        # This is only a convenient transformed score.
        # The FAISS distance remains the main threshold value.
        similarity_score = 1 / (1 + distance)

        return {
            "display_question": clean_text(
                metadata.get(
                    "display_question",
                    "",
                )
            ),
            "matched_question": clean_text(
                metadata.get(
                    "question",
                    "",
                )
            ),
            "normalized_question": clean_text(
                metadata.get(
                    "normalized_question",
                    document.page_content,
                )
            ),
            "sql": clean_text(
                metadata.get(
                    "sql",
                    "",
                )
            ),
            "normalized_entities": clean_text(
                metadata.get(
                    "normalized_entities",
                    "",
                )
            ),
            "variables": stored_variables,
            "variables_key": clean_text(
                metadata.get(
                    "variables_key",
                    "",
                )
            ),
            "searched_variables_key": (
                searched_variables_key
            ),
            "report_name": clean_text(
                metadata.get(
                    "report_name",
                    report_name,
                )
            ),
            "excel_row_number": metadata.get(
                "excel_row_number"
            ),

            # Lower means a closer FAQ match.
            "distance": distance,

            # Higher means a closer FAQ match.
            "similarity_score": round(
                similarity_score,
                4,
            ),

            "similarity_percent": round(
                similarity_score * 100,
                2,
            ),
        }


    def search(
        self,
        report_name: str,
        query_text: str,
        variable_names: Optional[List[str]] = None,
        top_k: int = 5,
        search_all_variables: bool = False,
        max_distance: Optional[float] = None,
        min_similarity_score: Optional[float] = None,
    ) -> Dict[str, object]:
        """
        Search one report index.

        max_distance:
            Keep matches whose FAISS distance is less than or
            equal to this value. Lower distance is better.

        min_similarity_score:
            Keep matches whose transformed similarity score is
            greater than or equal to this value.
        """

        normalized_query = clean_text(
            query_text
        )

        if not normalized_query:
            return {
                "status": "error",
                "message": "query_text cannot be empty.",
                "results": [],
            }

        if top_k <= 0:
            return {
                "status": "error",
                "message": "top_k must be greater than zero.",
                "results": [],
            }

        if (
            max_distance is not None
            and max_distance < 0
        ):
            return {
                "status": "error",
                "message": (
                    "max_distance cannot be negative."
                ),
                "results": [],
            }

        if (
            min_similarity_score is not None
            and not 0 <= min_similarity_score <= 1
        ):
            return {
                "status": "error",
                "message": (
                    "min_similarity_score must be "
                    "between 0 and 1."
                ),
                "results": [],
            }

        try:
            index_path, variables_key = (
                self._resolve_index_path(
                    report_name=report_name,
                    variable_names=variable_names,
                    search_all_variables=(
                        search_all_variables
                    ),
                )
            )

            vectorstore = self._load_vectorstore(
                index_path=index_path
            )

            raw_results = (
                vectorstore.similarity_search_with_score(
                    query=normalized_query,
                    k=top_k,
                )
            )

            results = [
                self._format_result(
                    document=document,
                    score=score,
                    report_name=report_name,
                    searched_variables_key=variables_key,
                )
                for document, score in raw_results
            ]

            if max_distance is not None:
                results = [
                    result
                    for result in results
                    if result["distance"]
                    <= max_distance
                ]

            if min_similarity_score is not None:
                results = [
                    result
                    for result in results
                    if result["similarity_score"]
                    >= min_similarity_score
                ]

            return {
                "status": "success",
                "report_name": report_name,
                "query_text": normalized_query,
                "variables_key": variables_key,
                "search_all_variables": (
                    search_all_variables
                ),
                "index_path": str(index_path),
                "max_distance": max_distance,
                "min_similarity_score": (
                    min_similarity_score
                ),
                "result_count": len(results),
                "results": results,
            }

        except Exception as error:
            return {
                "status": "error",
                "report_name": report_name,
                "query_text": normalized_query,
                "message": str(error),
                "results": [],
            }

    def list_reports(self) -> List[str]:
        """
        List report names stored under the base index path.
        """

        reports: List[str] = []

        if not self.base_path.exists():
            return reports

        for folder in self.base_path.iterdir():
            if not folder.is_dir():
                continue

            manifest_path = (
                folder / "manifest.json"
            )

            if manifest_path.exists():
                try:
                    with manifest_path.open(
                        "r",
                        encoding="utf-8",
                    ) as manifest_file:
                        manifest = json.load(
                            manifest_file
                        )

                    report_name = clean_text(
                        manifest.get(
                            "report_name",
                            folder.name,
                        )
                    )

                except (
                    OSError,
                    json.JSONDecodeError,
                ):
                    report_name = folder.name

                reports.append(report_name)

        return sorted(reports)

    def list_variable_combinations(
        self,
        report_name: str,
    ) -> List[str]:
        """
        List all stored exact variable combinations for a report.
        """

        manifest = self._read_manifest(
            report_name
        )

        variable_indexes = manifest.get(
            "variable_indexes",
            {},
        )

        return sorted(
            variable_indexes.keys()
        )

    def clear_cache(self) -> None:
        """
        Remove loaded vector stores from memory.
        """

        self._vectorstore_cache.clear()


if __name__ == "__main__":
    INDEX_BASE_PATH = (
        r"D:\TOC-chatbot\TOC-chatbot\backend\utils\indexes"
    )

    embedder = OpenAIEmbedder()

    searcher = FAQIndexSearcher(
        base_path=INDEX_BASE_PATH,
        embedder=embedder,
    )

    search_result = searcher.search(
        report_name="Inventory report",
        query_text=(
            "what required allocation third month "
            "stock buyer_name item"
        ),
        variable_names=[
            "buyer_name",
            "item",
        ],
        top_k=3,
        search_all_variables=False,
    )

    print(
        json.dumps(
            search_result,
            indent=2,
            ensure_ascii=False,
        )
    )