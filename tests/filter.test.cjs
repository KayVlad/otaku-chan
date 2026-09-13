const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, '../src/templates/library.html'), 'utf8');

test('filter rows use stable identities when one row is removed', () => {
  assert.match(template, /filters:\s+[^,]+\.map\(\(f, i\) => \(\{ \.\.\.f, id: 'filter-' \+ i \}\)\)/);
  assert.match(template, /<template x-for="f in filters" :key="f\.id">/);
  assert.match(template, /removeFilter\(f\.id\)/);
  assert.match(template, /<button type="button" class="btn filter-rm-btn"/);
});
