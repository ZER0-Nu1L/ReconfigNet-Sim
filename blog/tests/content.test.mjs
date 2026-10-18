import assert from 'node:assert/strict';
import test from 'node:test';
import { validatePosts } from '../scripts/check-content.mjs';

const makePost = (id, changes = {}, raw = 'A research note.') => ({
  id, data: { title: 'Hello Optical World', description: 'A research note.', author: 'Changbo Wu', date: '2026-09-15', draft: false, ...changes }, raw,
});
const pair = () => [makePost('en/hello-optical-world'), makePost('zh/hello-optical-world')];

test('accepts a published bilingual pair', () => assert.deepEqual(validatePosts(pair()), []));
test('allows localized author names and requires an author even on drafts', () => {
  assert.deepEqual(validatePosts([pair()[0], makePost('zh/hello-optical-world', { author: '吴昌博' })]), []);
  for (const author of [undefined, null, '', '   ', 42]) {
    for (const draft of [true, false]) {
      assert.match(validatePosts([makePost('en/hello-optical-world', { author, draft }), pair()[1]]).join('\n'), /author is required/);
    }
  }
});
test('requires a published translation, not just a draft', () => {
  assert.match(validatePosts([pair()[0]]).join('\n'), /translation is missing/);
  assert.match(validatePosts([pair()[0], makePost('zh/hello-optical-world', { draft: true })]).join('\n'), /translation is missing/);
});
test('requires matching publication dates', () => {
  assert.match(validatePosts([pair()[0], makePost('zh/hello-optical-world', { date: '2026-09-16' })]).join('\n'), /dates differ/);
});
test('rejects every review marker and endmatter on published content', () => {
  for (const raw of ['{>>review<<}', '{++insert++}', '{--delete--}', '{~~old~>new~~}', '{==highlight==}', '{#c1}', '{#s2}', '---\ncomments:\n  c1: {}', '---\nsuggestions:\n  s1: {}', '{>>legacy<<}{by="AI"}']) {
    assert.match(validatePosts([makePost('en/hello-optical-world', {}, raw), pair()[1]]).join('\n'), /review markup/, raw);
  }
});
test('allows an unpaired draft with review feedback', () => {
  assert.deepEqual(validatePosts([makePost('en/work-in-progress', { draft: true }, '{>>review<<}')]), []);
});
test('rejects invalid dates, ambiguous draft values, empty metadata and invalid paths', () => {
  for (const date of ['2026-02-30', '2026-13-01', 'yesterday', new Date()]) {
    assert.match(validatePosts([makePost('en/example', { date, draft: true })]).join('\n'), /valid YYYY-MM-DD/);
  }
  assert.match(validatePosts([makePost('en/example', { draft: 'false' })]).join('\n'), /draft must/);
  assert.match(validatePosts([makePost('en/example', { title: '', description: '', draft: true })]).join('\n'), /title is required/);
  assert.match(validatePosts([makePost('fr/Not Valid', { draft: true })]).join('\n'), /language\/lowercase/);
});
