import assert from 'node:assert/strict';
import test from 'node:test';
import { validatePosts } from '../scripts/check-content.mjs';
import { formatCitation } from '../src/lib/citation.mjs';

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

test('update dates are optional, may match publication, and may differ between translations', () => {
  assert.deepEqual(validatePosts([makePost('en/hello-optical-world', { updated: '2026-09-15' }), pair()[1]]), []);
  assert.deepEqual(validatePosts([makePost('en/hello-optical-world', { updated: '2026-10-21' }), makePost('zh/hello-optical-world', { updated: '2026-10-22' })]), []);
});

test('update dates must be valid quoted dates no earlier than publication', () => {
  for (const updated of ['', null, 20261021, new Date(), '2026-02-30', '2026-13-01', '2026-2-01', 'today', '2026-10-21T00:00:00Z']) {
    assert.match(validatePosts([makePost('en/hello-optical-world', { updated }), pair()[1]]).join('\n'), /updated must be a quoted valid YYYY-MM-DD date/);
  }
  assert.match(validatePosts([makePost('en/hello-optical-world', { updated: '2026-09-14' }), pair()[1]]).join('\n'), /updated must not precede publication date/);
});

test('later updates leave the BibTeX publication date and citation key intact', () => {
  const post = makePost('en/example').data;
  assert.equal(formatCitation({ ...post, updated: '2027-01-01' }, 'example', 'https://example.org/'), formatCitation(post, 'example', 'https://example.org/'));
});
test('rejects every review marker and endmatter on published content', () => {
  for (const raw of ['{>>review<<}', '{++insert++}', '{--delete--}', '{~~old~>new~~}', '{==highlight==}', '{#c1}', '{#s2}', '---\ncomments:\n  c1: {}', '---\nsuggestions:\n  s1: {}', '{>>legacy<<}{by="AI"}']) {
    assert.match(validatePosts([makePost('en/hello-optical-world', {}, raw), pair()[1]]).join('\n'), /review markup/, raw);
  }
});
test('allows an unpaired draft with review feedback', () => {
  assert.deepEqual(validatePosts([makePost('en/work-in-progress', { draft: true }, '{>>review<<}')]), []);
});

test('citation opt-in requires a boolean and a suitable English counterpart', () => {
  assert.deepEqual(validatePosts(pair().map(post => ({ ...post, data: { ...post.data, citation: true } }))), []);
  assert.match(validatePosts([makePost('zh/example', { draft: true, citation: true })]).join('\n'), /citation requires a corresponding English article/);
  assert.deepEqual(validatePosts([makePost('en/example', { draft: true }), makePost('zh/example', { draft: true, citation: true })]), []);
  assert.match(validatePosts([makePost('en/example', { draft: true }), makePost('zh/example', { citation: true })]).join('\n'), /citation requires a corresponding English article/);
  assert.match(validatePosts([makePost('en/example', { draft: true, citation: 'true' })]).join('\n'), /citation must be true or false/);
});

test('BibTeX preserves title capitalization and formats both English authors', () => {
  assert.equal(formatCitation({
    title: 'Microsecond Optical Switching. Millisecond Reality.',
    author: 'Changbo Wu, Zhuolong Yu', date: '2026-10-19',
  }, 'ocs-control-arch', 'https://zer0-nu1l.github.io/ReconfigNet-Sim/en/posts/ocs-control-arch/'), `@misc{wu2026ocscontrolarch,
  title = {{Microsecond Optical Switching. Millisecond Reality.}},
  author = {Wu, Changbo and Yu, Zhuolong},
  year = {2026},
  month = oct,
  howpublished = {ReconfigNet-Sim Blog},
  url = {https://zer0-nu1l.github.io/ReconfigNet-Sim/en/posts/ocs-control-arch/}
}`);
});

test('BibTeX escapes TeX punctuation without escaping its generated commands twice', () => {
  const citation = formatCitation({ title: 'OCS & {P4}: 50% #1_cost $ ~ ^ \\', author: 'A B&C', date: '2026-01-01' }, 'example', 'https://example.org/');
  assert.ok(citation.includes(String.raw`title = {{OCS \& \{P4\}: 50\% \#1\_cost \$ \textasciitilde{} \textasciicircum{} \textbackslash{}}}`));
  assert.ok(citation.includes(String.raw`author = {B\&C, A}`));
  assert.match(citation, /month = jan,/);
});
test('rejects invalid dates, ambiguous draft values, empty metadata and invalid paths', () => {
  for (const date of ['2026-02-30', '2026-13-01', 'yesterday', new Date()]) {
    assert.match(validatePosts([makePost('en/example', { date, draft: true })]).join('\n'), /valid YYYY-MM-DD/);
  }
  assert.match(validatePosts([makePost('en/example', { draft: 'false' })]).join('\n'), /draft must/);
  assert.match(validatePosts([makePost('en/example', { title: '', description: '', draft: true })]).join('\n'), /title is required/);
  assert.match(validatePosts([makePost('fr/Not Valid', { draft: true })]).join('\n'), /language\/lowercase/);
});
