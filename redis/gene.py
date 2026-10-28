import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

import pandas as pd
from dotenv import load_dotenv

from autocomplete_config import get_controller_variable, get_period_dependency

try:
    import oracledb
    from oracledb import DatabaseError
except ImportError:  # Allows --validate-only to work before oracledb is installed.
    oracledb = None

    class DatabaseError(Exception):
        pass


# -----------------------------------------------------------------------------
# Environment / Oracle configuration
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent


def load_environment() -> Optional[Path]:
    """Load Oracle settings from DOTENV_PATH, a local .env, or the old path."""

    candidates: List[Path] = []

    configured_path = os.getenv("DOTENV_PATH")
    if configured_path:
        candidates.append(Path(configured_path))

    candidates.extend(
        [
            SCRIPT_DIR / ".env",
            Path(r"D:\TOC-chatbot\TOC-chatbot\redis\.env"),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate)
            return candidate

    # Also permit values that are already present in the process environment.
    load_dotenv()
    return None


DOTENV_PATH_USED = load_environment()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASS = os.getenv("ORACLE_PASS")
ORACLE_WALLET_PASSWORD = os.getenv("ORACLE_WALLET_PASSWORD")
WALLET_PATH = os.getenv("WALLET_PATH")
TNS_ALIAS = os.getenv("TNS_ALIAS")

DEFAULT_SCHEMA = os.getenv("ORACLE_SCHEMA", "KPCL_REPORTING")


# -----------------------------------------------------------------------------
# Oracle connection and SQL execution
# -----------------------------------------------------------------------------


def get_connection():
    """Create a new Oracle ADW connection."""

    if oracledb is None:
        raise RuntimeError(
            "The 'oracledb' package is not installed. Install it with: "
            "pip install oracledb"
        )

    missing = [
        name
        for name, value in {
            "ORACLE_USER": ORACLE_USER,
            "ORACLE_PASS": ORACLE_PASS,
            "TNS_ALIAS": TNS_ALIAS,
            "WALLET_PATH": WALLET_PATH,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing Oracle environment settings: " + ", ".join(missing)
        )

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASS,
        dsn=TNS_ALIAS,
        config_dir=WALLET_PATH,
        wallet_location=WALLET_PATH,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def add_limit_to_query(query: str, limit: Optional[int] = None) -> str:
    query = query.strip().rstrip(";")

    if not limit or limit <= 0:
        return query

    query_lower = query.lower()
    if "fetch first" in query_lower or "rownum" in query_lower:
        return query

    return f"{query} FETCH FIRST {limit} ROWS ONLY"


def run_sql(query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Execute SQL and return the rows as dictionaries."""

    connection = None
    cursor = None

    try:
        query = add_limit_to_query(query, limit)
        print("\n[SQL] Executing:")
        print(query)

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(query)

        columns = [column[0] for column in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor]

        print(f"[INFO] Fetched {len(results)} rows")
        return results

    except DatabaseError as exc:
        print(f"[ERROR] Oracle DatabaseError: {exc}")
        raise

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# -----------------------------------------------------------------------------
# Excel mapping helpers
# -----------------------------------------------------------------------------

# The new workbook uses "storage_type". Header aliases allow minor future
# spelling/formatting changes without breaking the script.
HEADER_ALIASES: Mapping[str, Set[str]] = {
    "report_name": {"report name", "report", "report_name"},
    "variable_name": {
        "variables",
        "variable",
        "variable name",
        "variable_name",
    },
    "column_name": {
        "column names",
        "column name",
        "column_names",
        "column_name",
    },
    "table_name": {"table name", "table_name", "table"},
    "storage_type": {"storage type", "storage_type", "storage"},
}


def normalize_header(value: Any) -> str:
    """Normalize spaces, underscores, hyphens and capitalization in headers."""

    text = "" if value is None else str(value)
    text = text.strip().casefold()
    return re.sub(r"[\s_-]+", " ", text)


def resolve_excel_columns(columns: Iterable[Any]) -> Dict[str, Any]:
    """Map canonical field names to the workbook's actual column headers."""

    normalized_to_original: Dict[str, Any] = {}

    for original in columns:
        normalized = normalize_header(original)
        if normalized in normalized_to_original:
            raise ValueError(
                "Excel has duplicate-equivalent headers: "
                f"{normalized_to_original[normalized]!r} and {original!r}"
            )
        normalized_to_original[normalized] = original

    resolved: Dict[str, Any] = {}
    missing: List[str] = []

    for canonical_name, aliases in HEADER_ALIASES.items():
        normalized_aliases = {normalize_header(alias) for alias in aliases}
        matching_headers = [
            normalized_to_original[alias]
            for alias in normalized_aliases
            if alias in normalized_to_original
        ]

        if not matching_headers:
            missing.append(canonical_name)
        elif len(matching_headers) > 1:
            raise ValueError(
                f"Multiple Excel columns match '{canonical_name}': "
                f"{matching_headers}"
            )
        else:
            resolved[canonical_name] = matching_headers[0]

    if missing:
        raise ValueError(
            "Missing required Excel fields: "
            f"{missing}. Found headers: {list(columns)}"
        )

    return resolved


def normalize_storage_type(value: Any) -> str:
    """
    Convert workbook values to a stable category.

    Recognized examples:
      - Redis variable
      - Non-redis variable
      - Non- redis variable
      - non_redis_variable
    """

    if value is None or pd.isna(value):
        return ""

    compact = re.sub(r"[^a-z0-9]+", "", str(value).casefold())

    if compact in {"redis", "redisvariable"}:
        return "redis"

    if compact in {"nonredis", "nonredisvariable"}:
        return "non-redis"

    return "unknown"


def clean_column_name(column_name: Any) -> str:
    """Remove descriptive text in parentheses from an Oracle column name."""

    cleaned = str(column_name).strip()
    if "(" in cleaned:
        cleaned = cleaned.split("(", 1)[0].strip()
    return cleaned


def validate_oracle_identifier(identifier: str, field_name: str) -> str:
    """Validate COLUMN, TABLE, or SCHEMA.TABLE Oracle identifiers."""

    if not identifier or not isinstance(identifier, str):
        raise ValueError(f"{field_name} is missing")

    identifier = identifier.strip()
    pattern = r"^[A-Za-z][A-Za-z0-9_$#]*(\.[A-Za-z][A-Za-z0-9_$#]*)?$"

    if not re.fullmatch(pattern, identifier):
        raise ValueError(f"Invalid {field_name}: {identifier}")

    return identifier


def safe_file_name(value: Any) -> str:
    text = str(value).strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_$#_]", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def write_json_file(file_path: Path, data: List[Dict[str, Any]]) -> None:
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False, default=str)


def load_mapping(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    """Read, normalize and validate the changed Excel mapping format."""

    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=object)
    df = df.dropna(how="all").copy()

    print(f"[INFO] Excel: {excel_path}")
    print(f"[INFO] Sheet: {sheet_name}")
    print(f"[INFO] Headers found: {list(df.columns)}")

    column_map = resolve_excel_columns(df.columns)

    normalized = pd.DataFrame(
        {
            "report_name": df[column_map["report_name"]],
            "variable_name": df[column_map["variable_name"]],
            "column_name": df[column_map["column_name"]],
            "table_name": df[column_map["table_name"]],
            "storage_type_original": df[column_map["storage_type"]],
            # Excel row number includes the header row.
            "excel_row_number": df.index + 2,
        }
    )

    normalized["storage_type"] = normalized["storage_type_original"].map(
        normalize_storage_type
    )

    unknown = normalized[normalized["storage_type"] == "unknown"]
    if not unknown.empty:
        details = ", ".join(
            f"row {int(row.excel_row_number)}={row.storage_type_original!r}"
            for row in unknown.itertuples()
        )
        raise ValueError(f"Unrecognized storage_type value(s): {details}")

    missing_storage = normalized[normalized["storage_type"] == ""]
    if not missing_storage.empty:
        rows = ", ".join(
            str(int(value)) for value in missing_storage["excel_row_number"]
        )
        raise ValueError(f"storage_type is blank on Excel row(s): {rows}")

    return normalized


def validate_selected_rows(rows: pd.DataFrame) -> None:
    required_fields = ["report_name", "variable_name", "column_name", "table_name"]

    missing_messages: List[str] = []
    for row in rows.itertuples(index=False):
        for field in required_fields:
            value = getattr(row, field)
            if value is None or pd.isna(value) or not str(value).strip():
                missing_messages.append(
                    f"Excel row {int(row.excel_row_number)}: {field} is blank"
                )

    if missing_messages:
        raise ValueError("\n".join(missing_messages))

    output_names = rows.apply(
        lambda row: (
            f"{safe_file_name(row['report_name'])}_"
            f"{safe_file_name(row['variable_name'])}.json"
        ),
        axis=1,
    )

    duplicate_mask = output_names.duplicated(keep=False)
    if duplicate_mask.any():
        duplicate_rows = rows.loc[duplicate_mask].copy()
        duplicate_rows["output_file"] = output_names.loc[duplicate_mask]
        details = duplicate_rows[
            ["excel_row_number", "report_name", "variable_name", "output_file"]
        ].to_dict("records")
        raise ValueError(
            "Duplicate report/variable combinations would overwrite JSON files: "
            f"{details}"
        )




def normalize_name_for_match(value: Any) -> str:
    """Normalize report/variable names for configuration matching."""
    text = "" if value is None else str(value).strip().casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def find_mapping_row(
    mapping: pd.DataFrame,
    report_name: str,
    variable_name: str,
):
    """Find one mapping row by logical report + logical variable name."""

    report_key = normalize_name_for_match(report_name)
    variable_key = normalize_name_for_match(variable_name)

    matches = mapping[
        mapping["report_name"].map(normalize_name_for_match).eq(report_key)
        & mapping["variable_name"].map(normalize_name_for_match).eq(variable_key)
    ]

    if matches.empty:
        return None

    return next(matches.itertuples(index=False))

# -----------------------------------------------------------------------------
# Main processing
# -----------------------------------------------------------------------------


def generate_json_from_excel(
    excel_path: str,
    sheet_name: str = "Sheet1",
    output_dir: str = "json_outputs",
    limit: Optional[int] = None,
    validate_only: bool = False,
    clean_output: bool = False,
) -> None:
    """
    Generate JSON only for rows marked as Redis variables.

    NEW STORAGE MODEL
    -----------------
    A report may have ONE configured controlling autocomplete variable.

    Example:
        Inventory Report -> org_name -> ORG_NAME

    The controller itself is loaded globally.

    Every other Redis variable is generated as DISTINCT pairs:
        controller value + target variable value

    Example JSON row for buyer_name:
        {
            "REPORT_NAME": "Inventory Report",
            "VARIABLE_NAME": "buyer_name",
            "VALUE": "SASWADE, Mr. TUSHAR SHANKAR",
            "CONTROLLER_VARIABLE": "org_name",
            "CONTROLLER_VALUE": "ACD - SPARES"
        }

    Reports without a configured controller keep the old global behavior.
    """

    mapping = load_mapping(Path(excel_path), sheet_name)

    redis_rows = mapping[mapping["storage_type"] == "redis"].copy()
    non_redis_rows = mapping[mapping["storage_type"] == "non-redis"].copy()

    validate_selected_rows(redis_rows)

    # Check all declared period dependencies before contacting Oracle.
    for report_name in ("Docket Report", "Docket Employee Report"):
        dependency = get_period_dependency(report_name)
        assert dependency is not None
        required = [dependency["controller"]]
        if dependency["virtual_period_columns"]:
            required.extend(dependency["virtual_period_columns"].values())
        else:
            required.extend(("period_type", "period_name"))
        for variable in required:
            found = find_mapping_row(mapping, report_name, variable)
            if found is None or found.storage_type != "redis":
                raise ValueError(
                    f"{report_name}: Redis mapping must include Redis variable {variable!r}"
                )

    print(f"[INFO] Mapping rows: {len(mapping)}")
    print(f"[INFO] Redis rows to process: {len(redis_rows)}")
    print(f"[INFO] Non-Redis rows skipped: {len(non_redis_rows)}")

    if validate_only:
        print("[DONE] Mapping validation succeeded; Oracle was not queried.")
        return

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    if clean_output:
        removed = 0
        for old_json in destination.glob("*.json"):
            old_json.unlink()
            removed += 1
        print(f"[INFO] Removed {removed} old JSON file(s) from {destination}")

    succeeded = 0
    failed = 0
    failed_rows = []

    for row in redis_rows.itertuples(index=False):
        excel_row_number = int(row.excel_row_number)
        report_name = str(row.report_name).strip()
        variable_name = str(row.variable_name).strip()

        try:
            safe_column = validate_oracle_identifier(
                clean_column_name(row.column_name),
                "column name",
            )

            table_name = str(row.table_name).strip()
            if "." not in table_name:
                table_name = f"{DEFAULT_SCHEMA}.{table_name}"

            safe_table = validate_oracle_identifier(table_name, "table name")

            controller_variable = get_controller_variable(report_name)
            controller_row = None
            safe_controller_column = None

            if controller_variable:
                controller_row = find_mapping_row(
                    mapping,
                    report_name,
                    controller_variable,
                )

                if controller_row is None:
                    raise ValueError(
                        f"Configured autocomplete controller '{controller_variable}' "
                        f"was not found in redis_mapping.xlsx for report "
                        f"'{report_name}'."
                    )

                if str(controller_row.storage_type).strip() != "redis":
                    raise ValueError(
                        f"Configured autocomplete controller '{controller_variable}' "
                        f"must be marked as Redis variable for report '{report_name}'."
                    )

                controller_table = str(controller_row.table_name).strip()
                if "." not in controller_table:
                    controller_table = f"{DEFAULT_SCHEMA}.{controller_table}"

                safe_controller_table = validate_oracle_identifier(
                    controller_table,
                    "controller table name",
                )

                if safe_controller_table != safe_table:
                    raise ValueError(
                        f"Controller '{controller_variable}' and target variable "
                        f"'{variable_name}' must belong to the same report table. "
                        f"Controller table={safe_controller_table}; "
                        f"target table={safe_table}."
                    )

                safe_controller_column = validate_oracle_identifier(
                    clean_column_name(controller_row.column_name),
                    "controller column name",
                )

            is_controller = (
                controller_variable is not None
                and normalize_name_for_match(variable_name)
                == normalize_name_for_match(controller_variable)
            )

            dependency = get_period_dependency(report_name)
            is_physical_period_name = bool(
                dependency
                and not dependency["virtual_period_columns"]
                and normalize_name_for_match(variable_name)
                == normalize_name_for_match(dependency["period_name_variable"])
            )

            if is_physical_period_name:
                type_row = find_mapping_row(
                    mapping, report_name, dependency["period_type_variable"]
                )
                if type_row is None or type_row.storage_type != "redis":
                    raise ValueError("Period type mapping is missing")
                type_table = str(type_row.table_name).strip()
                if "." not in type_table:
                    type_table = f"{DEFAULT_SCHEMA}.{type_table}"
                if validate_oracle_identifier(type_table, "period type table") != safe_table:
                    raise ValueError("PERIOD_TYPE and PERIOD_NAME must use the same table")
                safe_type_column = validate_oracle_identifier(
                    clean_column_name(type_row.column_name), "period type column"
                )
                query = f"""
                    SELECT DISTINCT
                        {safe_controller_column} AS CONTROLLER_VALUE,
                        {safe_type_column} AS PERIOD_TYPE,
                        {safe_column} AS VALUE
                    FROM {safe_table}
                    WHERE {safe_controller_column} IS NOT NULL
                      AND {safe_type_column} IS NOT NULL
                      AND {safe_column} IS NOT NULL
                """
            elif controller_variable and not is_controller:
                query = f"""
                    SELECT DISTINCT
                        {safe_controller_column} AS CONTROLLER_VALUE,
                        {safe_column} AS VALUE
                    FROM {safe_table}
                    WHERE {safe_controller_column} IS NOT NULL
                      AND {safe_column} IS NOT NULL
                """
            else:
                query = f"""
                    SELECT DISTINCT
                        {safe_column} AS VALUE
                    FROM {safe_table}
                    WHERE {safe_column} IS NOT NULL
                """

            print(
                f"[INFO] Excel row {excel_row_number}: "
                f"{report_name} / {variable_name} -> {safe_table}.{safe_column}; "
                f"controller={controller_variable or 'none'}"
            )

            results = run_sql(query, limit=limit)

            enriched_results = []

            for result_row in results:
                value = result_row.get("VALUE")
                if value is None:
                    continue

                controller_value = ""
                if controller_variable and not is_controller:
                    raw_controller_value = result_row.get("CONTROLLER_VALUE")
                    if raw_controller_value is None:
                        continue
                    controller_value = str(raw_controller_value).strip()
                    if not controller_value:
                        continue

                enriched_row = {
                    "REPORT_NAME": report_name,
                    "VARIABLE_NAME": variable_name,
                    "VALUE": value,
                    "CONTROLLER_VARIABLE": controller_variable or "",
                    "CONTROLLER_VALUE": controller_value,
                }
                if is_physical_period_name:
                    period_type = result_row.get("PERIOD_TYPE")
                    if period_type is None or not str(period_type).strip():
                        continue
                    enriched_row["PERIOD_TYPE"] = str(period_type).strip()
                enriched_results.append(enriched_row)

            json_file_name = (
                f"{safe_file_name(report_name)}_"
                f"{safe_file_name(variable_name)}.json"
            )
            json_file_path = destination / json_file_name
            write_json_file(json_file_path, enriched_results)

            succeeded += 1
            print(
                f"[DONE] {json_file_name}: "
                f"{len(enriched_results)} record(s)"
            )

        except Exception as exc:
            failed += 1
            failed_rows.append(f"Excel row {excel_row_number}: {report_name} / {variable_name} -> {exc}")
            print(
                f"[FAILED] Excel row {excel_row_number}: "
                f"{report_name} / {variable_name}: {exc}"
            )

    # Docket Report does NOT have physical PERIOD_TYPE / PERIOD_NAME columns.
    # Generate virtual dropdown records from the three existing period columns.
    for report_name in ("Docket Report",):
        dependency = get_period_dependency(report_name)
        if not dependency or not dependency["virtual_period_columns"]:
            continue
        try:
            controller_row = find_mapping_row(
                mapping, report_name, dependency["controller"]
            )
            table = str(controller_row.table_name).strip()
            if "." not in table:
                table = f"{DEFAULT_SCHEMA}.{table}"
            safe_table = validate_oracle_identifier(table, "Docket table")
            safe_org_column = validate_oracle_identifier(
                clean_column_name(controller_row.column_name), "organization column"
            )
            type_rows: set[tuple[str, str]] = set()
            name_rows: set[tuple[str, str, str]] = set()
            for period_type, logical_column in dependency["virtual_period_columns"].items():
                period_row = find_mapping_row(mapping, report_name, logical_column)
                if period_row is None:
                    raise ValueError(f"Docket period column missing: {logical_column}")
                period_table = str(period_row.table_name).strip()
                if "." not in period_table:
                    period_table = f"{DEFAULT_SCHEMA}.{period_table}"
                if validate_oracle_identifier(period_table, "period table") != safe_table:
                    raise ValueError("Docket period columns must use one table")
                period_column = validate_oracle_identifier(
                    clean_column_name(period_row.column_name), "period column"
                )
                query = f"""
                    SELECT DISTINCT
                        {safe_org_column} AS CONTROLLER_VALUE,
                        {period_column} AS VALUE
                    FROM {safe_table}
                    WHERE {safe_org_column} IS NOT NULL
                      AND {period_column} IS NOT NULL
                """
                for result_row in run_sql(query, limit=limit):
                    org = str(result_row.get("CONTROLLER_VALUE") or "").strip()
                    period_name = str(result_row.get("VALUE") or "").strip()
                    if org and period_name:
                        type_rows.add((org, period_type))
                        name_rows.add((org, period_type, period_name))

            for variable_name, source_rows in (
                ("period_type", sorted(type_rows)),
                ("period_name", sorted(name_rows)),
            ):
                output_rows = []
                for item in source_rows:
                    org, period_type = item[:2]
                    payload = {
                        "REPORT_NAME": report_name,
                        "VARIABLE_NAME": variable_name,
                        "VALUE": period_type if variable_name == "period_type" else item[2],
                        "CONTROLLER_VARIABLE": dependency["controller"],
                        "CONTROLLER_VALUE": org,
                    }
                    if variable_name == "period_name":
                        payload["PERIOD_TYPE"] = period_type
                    output_rows.append(payload)
                file_path = destination / (
                    f"{safe_file_name(report_name)}_{variable_name}.json"
                )
                write_json_file(file_path, output_rows)
                succeeded += 1
                print(f"[DONE] {file_path.name}: {len(output_rows)} record(s)")
        except Exception as exc:
            failed += 1
            print(f"[FAILED] Generating Docket virtual period values: {exc}")

    print(
        f"\n[SUMMARY] Created: {succeeded}; Failed: {failed}; "
        f"Skipped non-Redis: {len(non_redis_rows)}"
    )

    if failed:
        failed_summary = "\n  - " + "\n  - ".join(failed_rows)
        raise RuntimeError(
            f"{failed} Redis mapping row(s) failed:\n{failed_summary}"
        )


def find_excel_file(base_dir: Path) -> Path:
    """Find redis_mapping.xlsx, redis_mapping.xlsx, or numbered copies."""

    patterns = (
        "redis_mapping*.xlsx",
        "redis mapping*.xlsx",
        "Redis_mapping*.xlsx",
        "Redis mapping*.xlsx",
    )

    candidates: Dict[Path, float] = {}
    for pattern in patterns:
        for candidate in base_dir.glob(pattern):
            if candidate.name.startswith("~$") or not candidate.is_file():
                continue
            candidates[candidate] = candidate.stat().st_mtime

    if not candidates:
        raise FileNotFoundError(
            "No Redis mapping workbook was found beside the script. "
            "Pass the workbook explicitly, for example:\n"
            'python gene_updated.py "redis mapping (2).xlsx"'
        )

    # Use the most recently modified matching workbook.
    selected = max(candidates, key=candidates.get)
    print(f"[INFO] Auto-selected workbook: {selected.name}")
    return selected


def main() -> None:
    # ================================================================
    # EDIT THESE VALUES WHEN THE INPUT OR OUTPUT LOCATION CHANGES
    # ================================================================

    # Import/input Excel filename.
    # Keep this Excel file in the same folder as this Python script.
    INPUT_EXCEL_FILE_NAME = "redis_mapping.xlsx"

    # Excel sheet containing the mapping.
    INPUT_SHEET_NAME = "Sheet1"

    # Export/output folder.
    # All generated JSON files will be created inside this folder.
    OUTPUT_JSON_FOLDER_NAME = "json_outputs"

    # None means return all distinct values from each Oracle query.
    # Change to 50, for example, to return only 50 rows per query.
    QUERY_ROW_LIMIT = None

    # True deletes existing JSON files before generating new ones.
    CLEAN_OLD_JSON_FILES = True

    # ================================================================

    input_excel_path = SCRIPT_DIR / INPUT_EXCEL_FILE_NAME
    output_json_dir = SCRIPT_DIR / OUTPUT_JSON_FOLDER_NAME

    print(f"[INFO] Running Python file: {Path(__file__).name}")
    print(f"[INFO] Import Excel file: {input_excel_path}")
    print(f"[INFO] Export JSON folder: {output_json_dir}")

    if DOTENV_PATH_USED:
        print(f"[INFO] Environment file: {DOTENV_PATH_USED}")
    else:
        print("[INFO] No .env file found; using system environment variables")

    generate_json_from_excel(
        excel_path=str(input_excel_path),
        sheet_name=INPUT_SHEET_NAME,
        output_dir=str(output_json_dir),
        limit=QUERY_ROW_LIMIT,
        validate_only=False,
        clean_output=CLEAN_OLD_JSON_FILES,
    )


if __name__ == "__main__":
    main()