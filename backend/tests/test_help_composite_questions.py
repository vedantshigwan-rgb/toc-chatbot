"""Focused offline regression tests for Help composite questions.

Run: python -m unittest discover -s tests -p test_help_composite_questions.py -v
"""
import asyncio
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace

# Local test runners without the production faiss wheel can still test the
# orchestration logic. The actual backend requires its normal faiss package.
try:
    import faiss  # noqa: F401
except ModuleNotFoundError:
    sys.modules['faiss'] = types.ModuleType('faiss')

from services.help_service import HelpService
from services.report_variable_help_service import ReportVariableHelpService


class FakePool:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(1)
        self.calls = []
        self.responses = SimpleNamespace(create=self.create)

    async def get_client(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text='The application has three modes. **quantity** is one variable.')


class FakeRetriever:
    def __init__(self):
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get('source_type') == 'general':
            return [{'id': 'help_002', 'title': 'Ask mode', 'content': 'Ask retrieves report data.'}]
        return [{
            'id': 'report_variable', 'title': 'quantity in Inventory Report',
            'content': 'Tracks bulk purchase allocations and order limits.',
            'report_name': kwargs.get('report_name'), 'variable_name': 'quantity'
        }]


def run(coro):
    return asyncio.run(coro)


class HelpOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.pool = FakePool()
        self.retriever = FakeRetriever()
        self.service = HelpService(pool=self.pool, retrieval_service=self.retriever)

    def test_combined_question_includes_both_knowledge_sources(self):
        text, matches = run(self.service.respond(
            report_name='Inventory Report',
            user_input='how application works what are the variables here',
        ))
        payload = json.loads(self.pool.calls[-1]['input'])
        self.assertGreaterEqual(len(payload['approved_application_guide']), 6)
        rows = self.service.variable_guide.for_report('Inventory Report')
        self.assertEqual(len(payload['approved_report_variables']), len(rows))
        self.assertEqual(len(rows), 11)
        self.assertTrue(payload['request_types']['full_variable_catalog_requested'])
        self.assertEqual(len(matches), len(rows) + len(self.service.app_guide))
        self.assertNotIn('**', text)
        # Model output in this test omits the list entirely. The backend's
        # completeness fallback must still show every approved variable.
        for row in rows:
            self.assertIn(f"- {row['variable_name']}:", text)

    def test_all_variables_in_bom_instead_of_top_four(self):
        run(self.service.respond(report_name='BOM Report', user_input='List all variables in this report'))
        payload = json.loads(self.pool.calls[-1]['input'])
        self.assertEqual(len(payload['approved_report_variables']), 29)
        self.assertEqual(payload['approved_application_guide'], [])
        self.assertFalse(self.retriever.calls)

    def test_exact_days_is_not_mixed_across_reports(self):
        run(self.service.respond(report_name='BOM Report', user_input='What does days mean?'))
        bom = json.loads(self.pool.calls[-1]['input'])
        run(self.service.respond(report_name='Inventory Report', user_input='What does days mean?'))
        inventory = json.loads(self.pool.calls[-1]['input'])
        self.assertEqual(len(bom['approved_report_variables']), 1)
        self.assertEqual(len(inventory['approved_report_variables']), 1)
        self.assertIn('MANUFACTURING', bom['approved_report_variables'][0]['description'])
        self.assertIn('PROCURING', inventory['approved_report_variables'][0]['description'])

    def test_application_question_keeps_general_guide(self):
        run(self.service.respond(report_name='Inventory Report', user_input='How does this application work?'))
        payload = json.loads(self.pool.calls[-1]['input'])
        self.assertGreaterEqual(len(payload['approved_application_guide']), 6)
        self.assertEqual(payload['approved_report_variables'], [])

    def test_top_k_is_not_used_to_truncate_catalog(self):
        guide = ReportVariableHelpService()
        self.assertTrue(guide.wants_catalog('What are the variables here?'))
        self.assertTrue(guide.wants_catalog('Which variables can I use?'))
        self.assertFalse(guide.wants_catalog('What does /quantity mean?'))
        self.assertEqual(len(guide.for_report('Inventory Report')), 11)

    def test_faiss_lanes_keep_app_and_report_specific_matches(self):
        import numpy as np
        from services.help_retrieval_service import HelpRetrievalService

        class RankedIndex:
            ntotal = 4
            def search(self, vector, limit):
                return (np.array([[0.91, 0.85, 0.8, 0.7]], dtype='float32'),
                        np.array([[0, 1, 2, 3]], dtype='int64'))

        retriever = object.__new__(HelpRetrievalService)
        retriever.index = RankedIndex()
        retriever.documents = [
            {'id':'a','report_name':'BOM Report','content':'BOM days'},
            {'id':'b','report_name':'Inventory Report','content':'Inventory quantity'},
            {'id':'c','title':'App overview','content':'Select report'},
            {'id':'d','report_name':'Inventory Report','content':'Inventory days'},
        ]
        retriever.top_k = 1
        retriever.min_score = 0.25

        async def fake_embed(query):
            return np.array([[1.0]], dtype='float32')
        retriever._embed_query = fake_embed

        general = run(retriever.search(query='How does app work?',report_name='Inventory Report',source_type='general'))
        variable = run(retriever.search(query='What is days?',report_name='Inventory Report',source_type='variable'))
        self.assertEqual([x.get('id') for x in general], ['c'])
        self.assertEqual([x.get('id') for x in variable], ['b'])

    def test_default_help_temperature(self):
        run(self.service.respond(report_name='Inventory Report', user_input='What does days mean?'))
        if self.service.model.startswith(('gpt-4.1','gpt-4o')):
            self.assertAlmostEqual(self.pool.calls[-1]['temperature'], float(os.getenv('HELP_RESPONSE_TEMPERATURE','0.65')))


if __name__ == '__main__':
    unittest.main()
