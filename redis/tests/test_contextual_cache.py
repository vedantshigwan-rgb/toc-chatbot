"""Redis contextual cache tests. Run from Redis root with unittest discover."""
import os
import unittest
from unittest.mock import patch
import contextual_cache_service as cache_service


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttl = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        self.ttl[key] = ex
        return True

    def eval(self, script, count, key, lock_token):
        if self.data.get(key) == lock_token:
            self.data.pop(key, None)
            return 1
        return 0


class CachingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TOC_AUTOCOMPLETE_INTERNAL_KEY':'test-private-secret', 'CONTEXT_AUTOCOMPLETE_TTL_SECONDS':'86400'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.redis = FakeRedis()
        self.payload = {
            'report_name':'Inventory Report', 'target_variable':'buyer',
            'filters': {'org_name': ['ACD - CENTRI'], 'item': ['ITEM001']},
            'search_text':'', 'limit':40,
        }

    def test_first_miss_then_hit_oracle_only_once(self):
        calls=[]
        def fake_backend(path, auth, *, body=None):
            calls.append(path)
            if path == '/auth/me':
                return {'user': {'email':'user@example.com'}}
            self.assertEqual(path, '/internal/autocomplete/oracle')
            self.assertEqual(body['filters']['item'], ['ITEM001'])
            return {'values':['Rahul','Amit'], 'has_more':False}
        with patch.object(cache_service,'_backend_json',side_effect=fake_backend):
            first=cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='Bearer access-token',mapping_version=10)
            reverse={**self.payload,'filters':dict(reversed(list(self.payload['filters'].items())))}
            second=cache_service.query_contextual_cached(cache=self.redis,payload=reverse,authorization='Bearer access-token',mapping_version=10)
        self.assertEqual(first['source'],'oracle')
        self.assertEqual(second['source'],'redis_context_cache')
        self.assertEqual(calls.count('/internal/autocomplete/oracle'),1)
        self.assertEqual(calls.count('/auth/me'),2)
        self.assertEqual(first['values'],['Rahul','Amit'])
        self.assertIn(86400,self.redis.ttl.values())

    def test_empty_results_cached(self):
        calls=[]
        def fake(path,auth,*,body=None):
            calls.append(path)
            return {'user': {'email':'a@example.com'}} if path == '/auth/me' else {'values':[], 'has_more':False}
        with patch.object(cache_service,'_backend_json',side_effect=fake):
            a=cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='Bearer token',mapping_version=10)
            b=cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='Bearer token',mapping_version=10)
        self.assertEqual(a['values'],[])
        self.assertEqual(b['values'],[])
        self.assertEqual(calls.count('/internal/autocomplete/oracle'),1)

    def test_login_checked_even_for_cache_hit(self):
        state={'valid':True}
        def fake(path, auth, *, body=None):
            if path == '/auth/me' and not state['valid']:
                raise cache_service.ContextualLookupError(401,'Please sign in again.')
            return {'user': {'email':'a@example.com'}} if path == '/auth/me' else {'values':['X'], 'has_more':False}
        with patch.object(cache_service,'_backend_json',side_effect=fake):
            cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='Bearer token',mapping_version=10)
            state['valid']=False
            with self.assertRaises(cache_service.ContextualLookupError) as cm:
                cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='Bearer token',mapping_version=10)
        self.assertEqual(cm.exception.status_code,401)

    def test_user_scoping(self):
        oracle_calls=[]
        def fake(path,auth,*,body=None):
            if path == '/auth/me': return {'user': {'email':auth+'@example.com'}}
            oracle_calls.append(auth)
            return {'values':['X'], 'has_more':False}
        with patch.object(cache_service,'_backend_json',side_effect=fake):
            for user in ('Bearer A','Bearer B','Bearer A'):
                cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization=user,mapping_version=10)
        self.assertEqual(oracle_calls,['Bearer A','Bearer B'])

    def test_normalized_filter_order(self):
        a=cache_service._canonical_filters({'item':['B','A'],'org_name':['C']},'buyer')
        b=cache_service._canonical_filters({'org_name':['C'],'item':['A','B']},'buyer')
        self.assertEqual(a,b)

    def test_missing_auth_rejected(self):
        with self.assertRaises(cache_service.ContextualLookupError) as cm:
            cache_service.query_contextual_cached(cache=self.redis,payload=self.payload,authorization='',mapping_version=10)
        self.assertEqual(cm.exception.status_code,401)



if __name__=="__main__": unittest.main()
