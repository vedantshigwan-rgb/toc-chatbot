
from agents.query_executor import run_sql_paginated

result = run_sql_paginated(
    sql="""
        SELECT *
        FROM KPCL_REPORTING.KPC_INV_BUYER_REPORT
        ORDER BY ORG_NAME
    """,
    page=1,
    page_size=50,
)

print("Page:", result["page"])
print("Rows returned:", len(result["results"]))
print("Total matching rows:", result["total_count"])
print("Total pages:", result["total_pages"])

if result["results"]:
    print("First row:", result["results"][0])
