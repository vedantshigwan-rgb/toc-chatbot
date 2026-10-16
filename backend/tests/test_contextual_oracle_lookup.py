"""Oracle-only contextual lookup tests. Does not connect to Oracle."""
import sys
import types
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Prevent Oracle wallet import in offline tests.
connection_module=types.ModuleType("agents.query_executor")
connection_module.get_connection=lambda: None
sys.modules["agents.query_executor"]=connection_module
from services import contextual_autocomplete_service as backend_service

class BackendQueryTests(unittest.TestCase):
    def test_inventory_filters_share_table(self):
        sql,binds,_=backend_service.build_query('Inventory Report','buyer',{'org_name':['ACD - CENTRI'],'item':['ITEM001']},'',40)
        self.assertIn('t.BUYER_NAME',sql)
        self.assertIn('t.CHILD_ITEM',sql)
        self.assertIn('t.ORG_NAME',sql)
        self.assertIn('ITEM001',binds.values())
        self.assertNotIn('ITEM001',sql)

    def test_docket_employee_uses_real_period_columns(self):
        sql,_,_=backend_service.build_query('Docket Employee Report','employee',{'employee_org':['ACD - PRODUCTS'],'period_type':['Year'],'period_name':['1-24']},'',40)
        self.assertIn('PERIOD_TYPE',sql)
        self.assertIn('PERIOD_NAME',sql)

    def test_docket_virtual_month(self):
        sql,_,_=backend_service.build_query('Docket Report','period_name',{'org_name':['X'],'period_type':['Month'],'resource_code':['R1']},'',40)
        self.assertIn('MONTH_PERIOD',sql)
        self.assertNotIn('t.PERIOD_TYPE',sql)

    def test_injected_value_is_bind_parameter(self):
        payload="X' OR 1=1 --"
        sql,binds,_=backend_service.build_query('Inventory Report','buyer',{'org_name':['X'],'item':[payload]},'',40)
        self.assertNotIn(payload,sql)
        self.assertIn(payload,binds.values())



if __name__=="__main__": unittest.main()
