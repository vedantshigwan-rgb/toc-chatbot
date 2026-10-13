"""Offline regression tests for report-wise Help and Report Terms."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from services.report_variable_help_service import ReportVariableHelpService
from services.metadata_safe_context import resolver_candidate, explanation_candidate, public_candidate
from services.metadata_response_service import MetadataResponseService

class FakeClient:
    def __init__(self):
        self.arguments = None
        self.responses = self
    async def create(self, **kwargs):
        self.arguments = kwargs
        return SimpleNamespace(output_text='The organization identifies the business unit.')

class FakePool:
    def __init__(self):
        self.client = FakeClient()
        self.semaphore = asyncio.Semaphore(1)
    async def get_client(self):
        return self.client

class ReportModeTests(unittest.TestCase):
    def test_report_scope(self):
        svc = ReportVariableHelpService()
        inventory = svc.find_matches('Inventory Report', 'What does /days mean?')
        bom = svc.find_matches('BOM Report', 'What does days mean?')
        self.assertEqual(len(inventory), 1)
        self.assertEqual(len(bom), 1)
        self.assertIn('PROCURING', inventory[0]['description'])
        self.assertIn('MANUFACTURING', bom[0]['description'])
        self.assertEqual(svc.find_matches('BOM Report', 'What is incentive_scheme?'), [])
    def test_duplicate_month_entries_preserved(self):
        self.assertEqual(len(ReportVariableHelpService().find_matches('Order Report','What does month mean?')),2)
    def test_metadata_payload_excludes_internal_fields(self):
        record = {'rank':1, 'column_name':'ORG_NAME', 'table_name':'SECRET_TABLE',
                  'description':'Organization is ACD - SPARES', 'distinct_values':['ACD - SPARES'],
                  'text':'Example values', 'data_type':'VARCHAR2', 'role_access':['R001']}
        data=json.dumps(explanation_candidate(record))
        for forbidden in ('SECRET_TABLE','ACD - SPARES','VARCHAR2','R001','distinct_values'):
            self.assertNotIn(forbidden,data)
        self.assertEqual(public_candidate(record),{'field':'ORG_NAME'})
        self.assertNotIn('distinct_values',resolver_candidate(record))
    def test_metadata_llm_only_gets_clean_fields(self):
        async def work():
            pool=FakePool()
            result=await MetadataResponseService(pool=pool).respond(
                user_input='What is ORG_NAME?',report_name='Inventory Report',
                metadata_records=[{'column_name':'ORG_NAME','table_name':'SECRET_TABLE',
                  'description':'Organization is ACD - SPARES', 'distinct_values':['ACD - SPARES'],
                  'role_access':['R001']}])
            self.assertIn('organization',result)
            body=pool.client.arguments['input']
            for forbidden in ('SECRET_TABLE','ACD - SPARES','R001','distinct_values'):
                self.assertNotIn(forbidden,body)
        asyncio.run(work())
    def test_stored_values_request_rejected_without_llm(self):
        async def work():
            pool=FakePool()
            result=await MetadataResponseService(pool=pool).respond(
                user_input='List all distinct values of ORG_NAME',report_name='Inventory Report',
                metadata_records=[{'column_name':'ORG_NAME'}])
            self.assertIn('Ask mode',result)
            self.assertIsNone(pool.client.arguments)
        asyncio.run(work())

if __name__ == '__main__':
    unittest.main()
