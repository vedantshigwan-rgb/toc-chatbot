import asyncio
import hashlib
import os
import sys
from typing import Any, Dict, Optional

import oracledb
from oracledb import DatabaseError

sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
        )
    )
)

from config.settings import (
    ORACLE_PASS,
    ORACLE_USER,
    ORACLE_WALLET_PASSWORD,
    TNS_ALIAS,
    WALLET_PATH,
)
from core.errors import OracleQueryError
from utils.debugger import debug_step


def get_connection():
    """
    Create an Oracle ADW connection.

    Configuration values are validated but are never
    printed or written to application logs.
    """

    missing_settings = []

    if not ORACLE_USER:
        missing_settings.append(
            "ORACLE_USER"
        )

    if not ORACLE_PASS:
        missing_settings.append(
            "ORACLE_PASS"
        )

    if not TNS_ALIAS:
        missing_settings.append(
            "TNS_ALIAS"
        )

    if not WALLET_PATH:
        missing_settings.append(
            "WALLET_PATH"
        )

    if missing_settings:
        raise RuntimeError(
            "Required Oracle configuration is "
            "missing: "
            + ", ".join(missing_settings)
        )

    required_wallet_files = [
        "tnsnames.ora",
        "sqlnet.ora",
        "cwallet.sso",
    ]

    missing_wallet_files = [
        file_name
        for file_name in required_wallet_files
        if not os.path.isfile(
            os.path.join(
                WALLET_PATH,
                file_name,
            )
        )
    ]

    if missing_wallet_files:
        raise RuntimeError(
            "Required Oracle wallet files are "
            "missing: "
            + ", ".join(
                missing_wallet_files
            )
        )

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASS,
        dsn=TNS_ALIAS,
        config_dir=WALLET_PATH,
        wallet_location=WALLET_PATH,
        wallet_password=(
            ORACLE_WALLET_PASSWORD
        ),
    )


def add_limit_to_query(
    query: str,
    limit: int = 50,
) -> str:
    query = query.strip().rstrip(";")

    if not limit:
        return query

    query_lower = query.lower()

    if (
        "fetch first" in query_lower
        or "rownum" in query_lower
    ):
        return query

    return (
        f"{query} FETCH FIRST "
        f"{limit} ROWS ONLY"
    )


def run_sql(
    sql: str,
    limit: int = 50,
    binds: Optional[
        Dict[str, Any]
    ] = None,
):
    connection = None
    cursor = None

    try:
        query = str(
            sql or ""
        ).strip()

        if not query:
            return []

        query = add_limit_to_query(
            query,
            limit,
        )

        query_hash = hashlib.sha256(
            query.encode(
                "utf-8"
            )
        ).hexdigest()[:12]

        debug_step(
            "[run_sql] Executing query "
            f"hash={query_hash}, "
            f"length={len(query)}"
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            query,
            binds or {},
        )

        columns = [
            column[0]
            for column
            in cursor.description
        ]

        results = [
            dict(
                zip(
                    columns,
                    row,
                )
            )
            for row in cursor
        ]

        debug_step(
            "[run_sql] Fetched "
            f"{len(results)} rows"
        )

        return results

    except DatabaseError as error:
        oracle_error = (
            OracleQueryError
            .from_database_error(
                error
            )
        )

        debug_step(
            "[run_sql] Oracle DatabaseError: "
            f"code={oracle_error.code or 'unknown'}"
        )

        raise oracle_error from error

    except Exception as error:
        debug_step(
            "[run_sql] Unexpected error: "
            f"type={type(error).__name__}"
        )

        raise

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


def run_sql_paginated(
    sql: str,
    page: int = 1,
    page_size: int = 50,
    binds=None,
):
    """
    Execute one Oracle result page and count the matching rows.

    This function does not apply the existing automatic FETCH FIRST
    limit. The supplied SQL must be the original validated query,
    before add_limit_to_query() adds the default 50-row limit.
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")

    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 50
    ):
        raise ValueError("page_size must be between 1 and 50")

    clean_sql = sql.strip().rstrip(";").strip()

    if not clean_sql:
        raise ValueError("SQL query cannot be empty")

    # This initial implementation supports named dictionary binds.
    # Use a separate namespace for pagination parameters to avoid
    # collisions with application-provided bind variables.
    if binds is not None and not isinstance(binds, dict):
        raise ValueError(
            "Paginated execution currently requires named dictionary binds"
        )

    query_binds = dict(binds or {})
    
    offset = (page - 1) * page_size

    connection = get_connection()
    cursor = None

    try:
        cursor = connection.cursor()

        count_sql = (
            "SELECT COUNT(*) AS TOTAL_COUNT FROM (\n"
            + clean_sql
            + "\n) KERNEXEC_COUNT_QUERY"
        )

        query_binds = dict(binds or {})
        cursor.execute(count_sql, query_binds)

        count_row = cursor.fetchone()
        total_count = int(count_row[0]) if count_row else 0

        # page and page_size have already been validated as integers.
        # Using these validated integers avoids mixing positional and
        # named bind styles in the original SQL.
        paginated_sql = (
            clean_sql
            + f"\n OFFSET {offset} ROWS"
            + f" FETCH NEXT {page_size} ROWS ONLY"
        )

        cursor.execute(paginated_sql, query_binds)

        columns = [column[0] for column in cursor.description]
        rows = [
            dict(zip(columns, row))
            for row in cursor.fetchall()
        ]

        return {
            "results": rows,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (
                (total_count + page_size - 1) // page_size
            ),
        }

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def stream_sql_csv_rows(
    sql: str,
    binds: Optional[Dict[str, Any]] = None,
    batch_size: int = 1000,
):
    """
    Stream the complete Oracle query result in batches.

    Yields:
        First, the column names.
        Then, each batch of rows as dictionaries.

    Does not apply the default 50-row limit.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    clean_sql = str(sql or "").strip().rstrip(";").strip()

    if not clean_sql:
        raise ValueError("SQL query cannot be empty")

    if binds is not None and not isinstance(binds, dict):
        raise ValueError(
            "CSV export currently requires named dictionary binds"
        )

    connection = None
    cursor = None

    try:
        query_hash = hashlib.sha256(
            clean_sql.encode("utf-8")
        ).hexdigest()[:12]

        debug_step(
            "[stream_sql_csv_rows] Executing query "
            f"hash={query_hash}, length={len(clean_sql)}"
        )

        connection = get_connection()
        cursor = connection.cursor()
        cursor.arraysize = batch_size

        # Execute the original query directly. Do not call
        # add_limit_to_query(), because CSV must include every row.
        cursor.execute(clean_sql, binds or {})

        if cursor.description is None:
            raise ValueError(
                "The SQL query did not return a result set"
            )

        columns = [
            column[0]
            for column in cursor.description
        ]

        yield columns

        while True:
            batch = cursor.fetchmany(batch_size)

            if not batch:
                break

            yield [
                dict(zip(columns, row))
                for row in batch
            ]

    except DatabaseError as error:
        oracle_error = OracleQueryError.from_database_error(error)

        debug_step(
            "[stream_sql_csv_rows] Oracle DatabaseError: "
            f"code={oracle_error.code or 'unknown'}"
        )

        raise oracle_error from error

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


async def main():
    clean_sql = """
        SELECT DISTINCT buyer
        FROM KPCL_REPORTING.KPC_INVENTORY_BUYER_REP
    """

    results = run_sql(
        clean_sql,
        limit=50,
    )

    print(
        f"\nQuery Results "
        f"(count={len(results)}):"
    )

    for row in results:
        buyer = (
            row.get("BUYER")
            or row.get("buyer")
        )

        if buyer:
            print(
                buyer,
                "is done",
            )
        else:
            print(
                "buyer is NULL or empty"
            )


if __name__ == "__main__":
    asyncio.run(main())