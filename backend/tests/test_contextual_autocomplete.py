"""Offline tests. Run from the backend root: python -m unittest discover -s tests -p test_contextual_autocomplete.py -v"""
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Offline tests do not import the Oracle wallet/connection code.
connection_module = types.ModuleType("agents.query_executor")
connection_module.get_connection = lambda: None
sys.modules["agents"] = types.ModuleType("agents")
sys.modules["agents.query_executor"] = connection_module

from services import contextual_autocomplete_service as svc


class FakeCache:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def eval(self, _script, _num_keys, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, binds):
        self.connection.calls += 1
        self.connection.sql = sql
        self.connection.binds = binds

    def fetchmany(self, count):
        return self.connection.rows[:count]

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.sql = ""
        self.binds = {}
        self.call_timeout = None

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class ContextualTests(unittest.TestCase):
    def test_inventory_combines_item_and_org(self):
        sql, binds, _ = svc.build_query("Inventory Report", "buyer", {
            "item": ["ITEM001"], "org_name": ["ACD - CENTRI"]
        }, "", 40)
        self.assertIn("t.BUYER_NAME", sql)
        self.assertIn("t.CHILD_ITEM = :", sql)
        self.assertIn("t.ORG_NAME = :", sql)
        self.assertEqual(set(binds.values()), {"ITEM001", "ACD - CENTRI"})

    def test_values_are_bound_not_interpolated(self):
        injected = "ABC' OR 1=1 --"
        sql, binds, _ = svc.build_query("Inventory Report", "buyer", {
            "item": [injected], "org_name": ["ORG-X"]
        }, "", 40)
        self.assertNotIn(injected, sql)
        self.assertIn(injected, binds.values())

    def test_period_employee_actual_columns(self):
        sql, _, _ = svc.build_query("Docket Employee Report", "employee", {
            "employee_org": ["ACD-PRODUCTS"], "period_type": ["Year"],
            "period_name": ["1-24"]
        }, "", 40)
        self.assertIn("PERIOD_TYPE", sql)
        self.assertIn("PERIOD_NAME", sql)
        self.assertIn("EMP_ORG_NAME", sql)

    def test_docket_virtual_period_name(self):
        sql, _, _ = svc.build_query("Docket Report", "period_name", {
            "period_type": ["Month"], "org_name": ["ORG-X"], "resource_code": ["R1"]
        }, "", 40)
        self.assertIn("t.MONTH_PERIOD", sql)
        self.assertIn("t.RESOURCE_CODE", sql)
        self.assertNotIn("t.PERIOD_TYPE", sql)

    def test_docket_virtual_period_type(self):
        sql, _, _ = svc.build_query("Docket Report", "period_type", {
            "org_name": ["ORG-X"], "resource_code": ["R1"]
        }, "", 40)
        self.assertIn("WEEK_PERIOD", sql)
        self.assertIn("MONTH_PERIOD", sql)
        self.assertIn("YEAR_PERIOD", sql)

    def test_ambiguous_nonredis_target_rejected(self):
        with self.assertRaises(svc.ContextualAutocompleteError):
            svc.build_query("Inventory Report", "quantity", {"org_name": ["ORG-X"]}, "", 40)

    def test_search_wildcard_escaped(self):
        sql, binds, _ = svc.build_query("Inventory Report", "buyer", {"org_name": ["A"]}, "A_%", 40)
        self.assertIn("ESCAPE '!'", sql)
        self.assertIn("%a!_!%%", binds.values())

    def test_cache_hit_avoids_second_oracle_query(self):
        cache, db = FakeCache(), FakeConnection([("Rahul",), ("Amit",)])
        with patch.object(svc, "_get_redis", return_value=cache), patch.object(svc, "get_connection", return_value=db):
            first = svc.query_contextual_values(
                report="Inventory Report", target="buyer", filters={
                    "org_name": ["ACD - CENTRI"], "item": ["ITEM001"]
                }, search="", limit=40, user_email="test@company.example"
            )
            second = svc.query_contextual_values(
                report="Inventory Report", target="buyer", filters={
                    "item": ["ITEM001"], "org_name": ["ACD - CENTRI"]
                }, search="", limit=40, user_email="test@company.example"
            )
        self.assertEqual(first["source"], "oracle")
        self.assertEqual(second["source"], "redis_context_cache")
        self.assertEqual(db.calls, 1)
        self.assertIn(86400, cache.ttls.values())

    def test_empty_cached_results_are_not_requeried(self):
        cache, db = FakeCache(), FakeConnection([])
        with patch.object(svc, "_get_redis", return_value=cache), patch.object(svc, "get_connection", return_value=db):
            arguments = dict(report="Inventory Report", target="buyer", filters={"item": ["UNKNOWN"], "org_name": ["ORG-X"]}, search="", limit=40, user_email="test@company.example")
            svc.query_contextual_values(**arguments)
            answer = svc.query_contextual_values(**arguments)
        self.assertEqual(answer["values"], [])
        self.assertEqual(db.calls, 1)

    def test_controller_cannot_be_bypassed(self):
        with self.assertRaises(svc.ContextualAutocompleteError):
            svc.build_query("Inventory Report", "buyer", {"item": ["ITEM001"]}, "", 40)

    def test_cache_per_user_scope(self):
        cache = FakeCache()
        connections = []
        def make_connection():
            connection = FakeConnection([("Example",)])
            connections.append(connection)
            return connection
        with patch.object(svc, "_get_redis", return_value=cache), patch.object(svc, "get_connection", side_effect=make_connection):
            args = dict(report="Inventory Report", target="buyer", filters={"item": ["ITEM001"], "org_name": ["ORG-X"]}, search="", limit=40)
            svc.query_contextual_values(**args, user_email="a@example.com")
            svc.query_contextual_values(**args, user_email="b@example.com")
        self.assertEqual(len(connections), 2)


if __name__ == "__main__":
    unittest.main()
