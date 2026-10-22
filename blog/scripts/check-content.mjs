import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import matter from 'gray-matter';

const reviewMarkup = /\{(?:>>|\+\+|--|~~|==)|\{#[cs]\d+\}|^\s*(?:comments|suggestions):\s*$/m;

function validDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 10) === value;
}

export function validatePosts(posts) {
  const errors = [];
  const published = posts.filter(({ data }) => data.draft === false);
  for (const post of posts) {
    const { id, data } = post;
    if (!/^(en|zh)\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) errors.push(`${id}: use language/lowercase-hyphenated-slug`);
    for (const key of ['title', 'description', 'author']) {
      if (typeof data[key] !== 'string' || !data[key].trim()) errors.push(`${id}: ${key} is required`);
    }
    if (typeof data.draft !== 'boolean') errors.push(`${id}: draft must be explicitly true or false`);
    if (data.citation !== undefined && typeof data.citation !== 'boolean') errors.push(`${id}: citation must be true or false`);
    if (data.citation === true) {
      const english = posts.find(entry => entry.id === `en/${id.split('/')[1]}`);
      if (!english || (data.draft === false && english.data.draft !== false)) {
        errors.push(`${id}: citation requires a corresponding English article (published for published posts)`);
      }
    }
    if (!validDate(data.date)) errors.push(`${id}: use a quoted valid YYYY-MM-DD date`);
    if (data.updated !== undefined) {
      if (!validDate(data.updated)) errors.push(`${id}: updated must be a quoted valid YYYY-MM-DD date`);
      else if (validDate(data.date) && data.updated < data.date) errors.push(`${id}: updated must not precede publication date`);
    }
  }
  for (const post of published) {
    const [lang, slug] = post.id.split('/');
    const counterpart = published.find(({ id }) => id === `${lang === 'en' ? 'zh' : 'en'}/${slug}`);
    if (!counterpart) errors.push(`${post.id}: published translation is missing`);
    else if (counterpart.data.date !== post.data.date) errors.push(`${post.id}: translation dates differ`);
    if (reviewMarkup.test(post.raw)) errors.push(`${post.id}: resolve Roughdraft review markup before publishing`);
  }
  return errors;
}

export async function readPosts(root) {
  const posts = [];
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(filename);
      else if (entry.name.endsWith('.md')) {
        const raw = await readFile(filename, 'utf8');
        posts.push({ id: path.relative(root, filename).replaceAll(path.sep, '/').slice(0, -3), data: matter(raw).data, raw });
      }
    }
  }
  await walk(root);
  return posts;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const posts = await readPosts(fileURLToPath(new URL('../src/content/posts/', import.meta.url)));
  const errors = validatePosts(posts);
  if (errors.length) {
    console.error(errors.join('\n'));
    process.exitCode = 1;
  } else console.log(`Content checked: ${posts.length} Markdown files.`);
}
