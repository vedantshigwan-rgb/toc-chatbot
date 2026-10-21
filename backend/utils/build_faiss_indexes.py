from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
from langchain_community.vectorstores import FAISS

from openai_embedder import OpenAIEmbedder


REQUIRED_COLUMNS = [
    "Display Questions",
    "Questions",
    "Normalized Questions",
    "SQL Queries",
    "Variables Used",
]

OPTIONAL_COLUMNS = [
    "Normalized Entities",
]


def clean_text(value: object) -> str:
    """
    Convert an Excel cell into a clean string.
    """

    if value is None or pd.isna(value):
        return ""

    return " ".join(
        str(value).strip().split()
    )


def sanitize_folder_name(value: object) -> str:
    """
    Convert a report name or variables key into a safe folder name.
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
    Create an exact, order-independent key from variable names.

    Examples:
        ["item", "buyer_name"]
            -> "buyer_name,item"

        ["buyer_name", "item", "item"]
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


def parse_variables_cell(value: object) -> List[str]:
    """
    Convert an Excel Variables Used cell into a list.

    Expected Excel format:
        buyer_name,item
    """

    text = clean_text(value)

    if not text:
        return []

    return [
        variable.strip().lower()
        for variable in text.split(",")
        if variable.strip()
    ]


def validate_sheet_columns(
    dataframe: pd.DataFrame,
    sheet_name: str,
) -> None:
    """
    Validate the required FAQ columns.
    """

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Sheet '{sheet_name}' is missing required columns: "
            f"{missing_columns}"
        )


def prepare_sheet_dataframe(
    dataframe: pd.DataFrame,
    sheet_name: str,
) -> pd.DataFrame:
    """
    Clean and standardize one FAQ sheet.
    """

    validate_sheet_columns(
        dataframe=dataframe,
        sheet_name=sheet_name,
    )

    prepared_rows: List[Dict[str, object]] = []

    for excel_row_number, row in dataframe.iterrows():
        display_question = clean_text(
            row.get("Display Questions", "")
        )

        question = clean_text(
            row.get("Questions", "")
        )

        normalized_question = clean_text(
            row.get("Normalized Questions", "")
        )

        sql_query = clean_text(
            row.get("SQL Queries", "")
        )

        normalized_entities = clean_text(
            row.get("Normalized Entities", "")
        )

        variables = parse_variables_cell(
            row.get("Variables Used", "")
        )

        variables_key = make_variables_key(
            variables
        )

        # A normalized question is required because it is the text
        # that will be embedded and searched.
        if not normalized_question:
            continue

        prepared_rows.append(
            {
                "display_question": display_question,
                "question": question,
                "normalized_question": normalized_question,
                "sql": sql_query,
                "normalized_entities": normalized_entities,
                "variables": variables,
                "variables_key": variables_key,
                "report_name": sheet_name,
                # Add 2 because Excel row 1 contains headers.
                "excel_row_number": int(excel_row_number) + 2,
            }
        )

    prepared_dataframe = pd.DataFrame(
        prepared_rows
    )

    if prepared_dataframe.empty:
        raise ValueError(
            f"Sheet '{sheet_name}' has no valid normalized questions."
        )

    return prepared_dataframe


def create_vectorstore(
    dataframe: pd.DataFrame,
    embedder: OpenAIEmbedder,
) -> FAISS:
    """
    Create a LangChain FAISS vector store from a prepared dataframe.
    """

    texts: List[str] = []
    metadatas: List[Dict[str, object]] = []

    for _, row in dataframe.iterrows():
        normalized_question = clean_text(
            row["normalized_question"]
        )

        texts.append(
            normalized_question
        )

        metadatas.append(
            {
                "display_question": clean_text(
                    row["display_question"]
                ),
                "question": clean_text(
                    row["question"]
                ),
                "normalized_question": normalized_question,
                "sql": clean_text(
                    row["sql"]
                ),
                "normalized_entities": clean_text(
                    row["normalized_entities"]
                ),
                "variables": list(
                    row["variables"]
                ),
                "variables_key": clean_text(
                    row["variables_key"]
                ),
                "report_name": clean_text(
                    row["report_name"]
                ),
                "excel_row_number": int(
                    row["excel_row_number"]
                ),
            }
        )

    return FAISS.from_texts(
        texts=texts,
        embedding=embedder,
        metadatas=metadatas,
    )


def save_vectorstore(
    vectorstore: FAISS,
    destination: Path,
    rebuild: bool,
) -> None:
    """
    Save one FAISS index to disk.
    """

    if destination.exists():
        if not rebuild:
            raise FileExistsError(
                f"Index already exists: {destination}"
            )

        shutil.rmtree(destination)

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    vectorstore.save_local(
        str(destination)
    )


def build_report_indexes(
    dataframe: pd.DataFrame,
    report_name: str,
    base_path: Path,
    embedder: OpenAIEmbedder,
    rebuild: bool = True,
) -> Dict[str, object]:
    """
    Build:
    1. One complete index for the report.
    2. One separate index for each exact variable combination.
    """

    report_folder_name = sanitize_folder_name(
        report_name
    )

    report_folder = (
        base_path / report_folder_name
    )

    report_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    all_index_path = (
        report_folder / "all"
    )

    all_vectorstore = create_vectorstore(
        dataframe=dataframe,
        embedder=embedder,
    )

    save_vectorstore(
        vectorstore=all_vectorstore,
        destination=all_index_path,
        rebuild=rebuild,
    )

    variable_index_details: Dict[str, Dict[str, object]] = {}

    grouped_data = dataframe.groupby(
        "variables_key",
        sort=True,
        dropna=False,
    )

    for variables_key, variable_dataframe in grouped_data:
        variables_key = clean_text(
            variables_key
        ) or "no_variables"

        variable_folder_name = sanitize_folder_name(
            variables_key
        )

        variable_index_path = (
            report_folder
            / "variables"
            / variable_folder_name
        )

        variable_vectorstore = create_vectorstore(
            dataframe=variable_dataframe,
            embedder=embedder,
        )

        save_vectorstore(
            vectorstore=variable_vectorstore,
            destination=variable_index_path,
            rebuild=rebuild,
        )

        variable_index_details[variables_key] = {
            "folder": str(
                Path("variables")
                / variable_folder_name
            ),
            "row_count": int(
                len(variable_dataframe)
            ),
        }

    manifest = {
        "report_name": report_name,
        "report_folder": report_folder_name,
        "created_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "total_rows": int(len(dataframe)),
        "all_index_folder": "all",
        "variable_indexes": variable_index_details,
    }

    manifest_path = (
        report_folder / "manifest.json"
    )

    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as manifest_file:
        json.dump(
            manifest,
            manifest_file,
            indent=2,
            ensure_ascii=False,
        )

    return manifest


def build_all_indexes_from_excel(
    excel_path: str,
    base_path: str,
    embedder: OpenAIEmbedder,
    allowed_sheets: Optional[List[str]] = None,
    rebuild: bool = True,
) -> Dict[str, object]:
    """
    Build FAISS indexes for every selected Excel sheet.

    Each sheet is treated as one report.

    Parameters
    ----------
    excel_path:
        FAQ Excel workbook path.

    base_path:
        Root directory where all indexes will be stored.

    embedder:
        Existing OpenAIEmbedder instance.

    allowed_sheets:
        Optional list of sheets to build.
        None means build all sheets.

    rebuild:
        True deletes and recreates existing indexes.
    """

    excel_file = Path(excel_path)

    if not excel_file.exists():
        raise FileNotFoundError(
            f"Excel file was not found: {excel_file}"
        )

    index_base_path = Path(base_path)

    index_base_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    workbook = pd.ExcelFile(
        excel_file
    )

    available_sheets = workbook.sheet_names

    if allowed_sheets:
        unknown_sheets = [
            sheet
            for sheet in allowed_sheets
            if sheet not in available_sheets
        ]

        if unknown_sheets:
            raise ValueError(
                f"These sheets were not found in the workbook: "
                f"{unknown_sheets}. "
                f"Available sheets: {available_sheets}"
            )

        selected_sheets = allowed_sheets
    else:
        selected_sheets = available_sheets

    build_summary: Dict[str, object] = {
        "excel_path": str(excel_file),
        "base_path": str(index_base_path),
        "successful_reports": {},
        "failed_reports": {},
    }

    for sheet_name in selected_sheets:
        try:
            source_dataframe = pd.read_excel(
                excel_file,
                sheet_name=sheet_name,
            )

            prepared_dataframe = prepare_sheet_dataframe(
                dataframe=source_dataframe,
                sheet_name=sheet_name,
            )

            manifest = build_report_indexes(
                dataframe=prepared_dataframe,
                report_name=sheet_name,
                base_path=index_base_path,
                embedder=embedder,
                rebuild=rebuild,
            )

            build_summary[
                "successful_reports"
            ][sheet_name] = manifest

            print(
                f"Built indexes for '{sheet_name}': "
                f"{manifest['total_rows']} rows"
            )

        except Exception as error:
            build_summary[
                "failed_reports"
            ][sheet_name] = str(error)

            print(
                f"Failed to build '{sheet_name}': {error}"
            )

    return build_summary


if __name__ == "__main__":
    FAQ_EXCEL_PATH = (
        r"D:\TOC-chatbot\TOC-chatbot\backend\data\FAQ.xlsx"
    )

    INDEX_BASE_PATH = (
        r"D:\TOC-chatbot\TOC-chatbot\backend\utils\indexes"
    )

    # None means all sheets.
    # Example:
    # ALLOWED_SHEETS = ["Inventory report", "BOM Report"]
    ALLOWED_SHEETS = None

    embedder = OpenAIEmbedder()

    summary = build_all_indexes_from_excel(
        excel_path=FAQ_EXCEL_PATH,
        base_path=INDEX_BASE_PATH,
        embedder=embedder,
        allowed_sheets=ALLOWED_SHEETS,
        rebuild=True,
    )

    print(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        )
    )