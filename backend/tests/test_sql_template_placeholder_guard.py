"""Regression tests for preserving SQL placeholders through SQLGlot filters.

Run from the backend root:
    python -m unittest discover -s tests -p test_sql_template_placeholder_guard.py -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.sql_template_placeholders import SQLTemplatePlaceholderGuard


class SQLTemplatePlaceholderGuardTests(unittest.TestCase):
    def test_unquoted_number_survives_parse_ready_masking(self):
        original = "SELECT * FROM t WHERE PERFORMANCE_INDEX > {performance_index};"
        guard = SQLTemplatePlaceholderGuard(original)
        self.assertNotIn("{performance_index}", guard.protected_sql)
        self.assertNotIn("STRUCT", guard.protected_sql)
        self.assertTrue(guard.contains_variable(guard.protected_sql, "performance_index"))
        self.assertEqual(guard.restore(guard.protected_sql), original)

    def test_mixed_quoted_and_unquoted_placeholders_restore_exactly(self):
        original = (
            "SELECT * FROM t WHERE p BETWEEN { minimum } AND {Maximum} "
            "AND org LIKE '%{employee_org}%' AND dept = '{dept}'"
        )
        guard = SQLTemplatePlaceholderGuard(original)
        self.assertTrue(guard.contains_variable(guard.protected_sql, "minimum"))
        self.assertTrue(guard.contains_variable(guard.protected_sql, "MAXIMUM"))
        self.assertTrue(guard.contains_variable(guard.protected_sql, "employee_org"))
        self.assertEqual(guard.restore(guard.protected_sql), original)

    def test_restore_handles_oracle_uppercase_normalization(self):
        original = "SELECT * FROM t WHERE p > {Amount}"
        guard = SQLTemplatePlaceholderGuard(original)
        self.assertEqual(
            guard.restore(guard.protected_sql.lower()),
            original.replace("SELECT * FROM t WHERE p > ", "select * from t where p > "),
        )

    def test_does_not_convert_arbitrary_brace_text(self):
        original = "SELECT '{{not_a_variable}}', '{x-y}' FROM t"
        guard = SQLTemplatePlaceholderGuard(original)
        self.assertEqual(guard.protected_sql, original)
        self.assertEqual(guard.restore(guard.protected_sql), original)

    def test_twelve_placeholders_restore_without_marker_overlap(self):
        original = "SELECT " + ", ".join(f"{{value_{i}}}" for i in range(12)) + " FROM t"
        guard = SQLTemplatePlaceholderGuard(original)
        self.assertEqual(guard.restore(guard.protected_sql), original)

    def test_detects_new_placeholders_added_by_filter(self):
        guard = SQLTemplatePlaceholderGuard("SELECT * FROM t WHERE p > {p}")
        self.assertTrue(guard.contains_variable("name LIKE '%{employee_org}%'", "employee_org"))
        self.assertFalse(guard.contains_variable("name LIKE '%{employee_org}%'", "period_type"))


class SQLGlotFilterIntegrationTests(unittest.TestCase):
    """Run against real SQLGlot and the existing latest backend workbooks."""

    def setUp(self):
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            self.skipTest("sqlglot not installed (required by backend)")

        self.roles = str(BACKEND_ROOT / "Roles and options.xlsx")
        self.mapping = str(BACKEND_ROOT / "redis_mapping.xlsx")
        if not Path(self.roles).is_file() or not Path(self.mapping).is_file():
            self.skipTest("Latest backend workbooks not present")

    def test_report_option_keeps_performance_index_and_adds_options(self):
        from services.report_option_sql_filter_service import apply_report_option_sql_filters

        sql = (
            "SELECT TNO, ENAME, EMP_DEPT, EMP_ORG_NAME, PERIOD_NAME, PERFORMANCE_INDEX "
            "FROM KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP "
            "WHERE PERFORMANCE_INDEX > {performance_index} "
            "ORDER BY PERFORMANCE_INDEX DESC;"
        )
        result = apply_report_option_sql_filters(
            sql_template=sql,
            report_name="Docket Employee Report",
            variables=[
                {"name": "performance_index", "value": "10", "source": "input"},
                {"name": "employee_org", "value": "ACD - PRODUCTS", "source": "option"},
                {"name": "period_type", "value": "Year", "source": "option"},
                {"name": "period_name", "value": "1-24", "source": "option"},
            ],
            allowed_tables=["KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP"],
            roles_workbook_path=self.roles,
            mapping_workbook_path=self.mapping,
        )
        self.assertTrue(result["success"], result)
        final_sql = result["sql_template"]
        self.assertRegex(final_sql, r"(?i)PERFORMANCE_INDEX\s*>\s*\{performance_index\}")
        self.assertNotIn("STRUCT(", final_sql.upper())
        for name in ("employee_org", "period_type", "period_name"):
            self.assertIn("{" + name + "}", final_sql)
        self.assertTrue(final_sql.endswith(";"))

    def test_report_option_removes_unused_option_predicate(self):
        from services.report_option_sql_filter_service import apply_report_option_sql_filters

        sql = (
            "SELECT PERFORMANCE_INDEX FROM KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP "
            "WHERE EMP_ORG_NAME = '{employee_org}' "
            "AND PERFORMANCE_INDEX > {performance_index};"
        )
        result = apply_report_option_sql_filters(
            sql_template=sql,
            report_name="Docket Employee Report",
            variables=[{"name": "performance_index", "value": 10, "source": "input"}],
            allowed_tables=["KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP"],
            roles_workbook_path=self.roles,
            mapping_workbook_path=self.mapping,
        )
        self.assertTrue(result["success"], result)
        self.assertIn("{performance_index}", result["sql_template"])
        self.assertNotIn("{employee_org}", result["sql_template"])
        self.assertNotIn("STRUCT(", result["sql_template"].upper())

    def test_extra_sql_filter_preserves_numeric_placeholder(self):
        from services.extra_sql_filter_service import apply_extra_sql_filters

        result = apply_extra_sql_filters(
            sql_template=(
                "SELECT TNO FROM KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP "
                "WHERE PERFORMANCE_INDEX > {performance_index};"
            ),
            report_name="Docket Employee Report",
            extra_filters=[{"name": "employee_org", "value": "ACD - PRODUCTS"}],
            allowed_tables=["KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP"],
            mapping_workbook_path=self.mapping,
        )
        self.assertTrue(result["success"], result)
        self.assertIn("{performance_index}", result["sql_template"])
        self.assertIn("{employee_org}", result["sql_template"])
        self.assertNotIn("STRUCT(", result["sql_template"].upper())

    def test_between_placeholders_survive_filter(self):
        from services.report_option_sql_filter_service import apply_report_option_sql_filters

        result = apply_report_option_sql_filters(
            sql_template=(
                "SELECT TNO FROM KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP "
                "WHERE PERFORMANCE_INDEX BETWEEN {min_index} AND {max_index};"
            ),
            report_name="Docket Employee Report",
            variables=[{"name": "period_type", "value": "Year", "source": "option"}],
            allowed_tables=["KPCL_REPORTING.KPC_FULL_KIT_DOCKET_EMP"],
            roles_workbook_path=self.roles,
            mapping_workbook_path=self.mapping,
        )
        self.assertTrue(result["success"], result)
        self.assertIn("{min_index}", result["sql_template"])
        self.assertIn("{max_index}", result["sql_template"])
        self.assertNotIn("STRUCT(", result["sql_template"].upper())


if __name__ == "__main__":
    unittest.main()
