"""Offline paging tests; run from the Redis application root."""
import os
import unittest
from unittest.mock import patch

from suggestion_paging import slice_page
import contextual_cache_service as contextual


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def eval(self, script, keys, key, token):
        if self.store.get(key) == token:
            self.store.pop(key)
        return 1


class PagedAutocompleteTests(unittest.TestCase):
    def test_default_redis_lists_page_through_all_values(self):
        values = [f"Value {i:03d}" for i in range(67)]
        pages = [slice_page(values, limit=30, offset=o) for o in (0, 30, 60)]
        self.assertEqual([len(p['values']) for p in pages], [30, 30, 7])
        self.assertEqual([p['next_offset'] for p in pages], [30, 60, None])
        self.assertEqual(sum((p['values'] for p in pages), []), values)

    def test_empty_page_has_no_next(self):
        self.assertEqual(slice_page(['A'], limit=30, offset=30)['next_offset'], None)

    def test_context_cache_keys_include_page_offset(self):
        cache = FakeCache()
        calls = []
        payload = {
            'report_name': 'Inventory Report',
            'target_variable': 'buyer',
            'filters': {'item': ['ITEM001'], 'org_name': ['ACD - CENTRI']},
            'limit': 2,
            'search_text': '',
        }
        all_values = ['A', 'B', 'C', 'D', 'E']

        def backend(path, authorization, *, body=None):
            if path == '/auth/me':
                return {'user': {'email': 'operator@example.com'}}
            calls.append(body['offset'])
            offset = body['offset']
            page = all_values[offset:offset + body['limit']]
            has_more = offset + len(page) < len(all_values)
            return {'values': page, 'has_more': has_more,
                    'next_offset': offset + len(page) if has_more else None}

        with patch.dict(os.environ, {'TOC_AUTOCOMPLETE_INTERNAL_KEY': 'test-key'}), \
             patch.object(contextual, '_backend_json', side_effect=backend):
            first = contextual.query_contextual_cached(
                cache=cache, payload={**payload, 'offset': 0},
                authorization='Bearer test', mapping_version=3)
            next_page = contextual.query_contextual_cached(
                cache=cache, payload={**payload, 'offset': first['next_offset']},
                authorization='Bearer test', mapping_version=3)
            again = contextual.query_contextual_cached(
                cache=cache, payload={**payload, 'offset': 0},
                authorization='Bearer test', mapping_version=3)
            third = contextual.query_contextual_cached(
                cache=cache, payload={**payload, 'offset': next_page['next_offset']},
                authorization='Bearer test', mapping_version=3)
        self.assertEqual(calls, [0, 2, 4])
        self.assertEqual(first['values'], ['A', 'B'])
        self.assertEqual(next_page['values'], ['C', 'D'])
        self.assertEqual(again['source'], 'redis_context_cache')
        self.assertEqual(third['values'], ['E'])
        self.assertFalse(third['has_more'])
        self.assertIsNone(third['next_offset'])

    def test_negative_offset_rejected(self):
        with patch.dict(os.environ, {'TOC_AUTOCOMPLETE_INTERNAL_KEY': 'test-key'}), \
             patch.object(contextual, '_backend_json', return_value={'user': {'email': 'e@example.com'}}):
            with self.assertRaises(contextual.ContextualLookupError) as ctx:
                contextual.query_contextual_cached(
                    cache=FakeCache(),
                    payload={'report_name': 'Inventory Report',
                             'target_variable': 'buyer', 'offset': -1},
                    authorization='Bearer test', mapping_version=3)
            self.assertEqual(ctx.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()
