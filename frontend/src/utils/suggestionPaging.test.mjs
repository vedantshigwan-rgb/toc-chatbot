import assert from 'node:assert/strict';
import { normaliseSuggestionPage } from './suggestionPaging.js';

const first = normaliseSuggestionPage({ values: ['A', 'B'], total_matches: 5 }, 0);
assert.deepEqual(first, { values: ['A', 'B'], hasMore: true, nextOffset: 2 });
const next = normaliseSuggestionPage({ values: ['C', 'D'], total_matches: 5 }, 2);
assert.equal(next.nextOffset, 4);
const last = normaliseSuggestionPage({ values: ['E'], total_matches: 5 }, 4);
assert.deepEqual(last, { values: ['E'], hasMore: false, nextOffset: null });
assert.equal(normaliseSuggestionPage({ values: ['A'], has_more: true, next_offset: 1 }, 0).nextOffset, 1);
assert.deepEqual(normaliseSuggestionPage({ values: [], has_more: false }, 10), { values: [], hasMore: false, nextOffset: null });
console.log('Suggestion paging tests passed.');
