import assert from 'node:assert/strict';
import { buildAutocompleteFilters } from './autocompleteContext.js';
const variables = ['org_name','item','buyer','period_type','period_name'].map(
  variable_name => ({variable_name,autocomplete_enabled:true})
);
const make = (text, targetVariable, optionValues={org_name:'ACD - CENTRI'}) =>
  buildAutocompleteFilters({text,targetVariable,optionValues,reportVariables:variables,reportName:'Inventory Report'});
assert.deepEqual(make('show /item {ITEM001} /buyer {', 'buyer'), {
  item:['ITEM001'],org_name:['ACD - CENTRI']
});
assert.deepEqual(make('show /item {ITEM001} /item {ITEM002} /buyer {','buyer'), {
  item:['ITEM001','ITEM002'],org_name:['ACD - CENTRI']
});
assert.deepEqual(make('show /buyer {Alle, Mr. Sachin Yuvaraj}', 'item'), {
  buyer:['Alle, Mr. Sachin Yuvaraj'],org_name:['ACD - CENTRI']
});
assert.deepEqual(make('show /item {ITEM001}', 'item'), {
  org_name:['ACD - CENTRI']
});
assert.deepEqual(make('/org_name {OTHER} /item {ITEM001}', 'buyer'), {
  item:['ITEM001'],org_name:['OTHER']
});
console.log('Autocomplete context JS tests passed');
