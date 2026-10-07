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