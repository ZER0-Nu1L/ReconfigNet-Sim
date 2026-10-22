import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const source = fileURLToPath(new URL('../', import.meta.url));

test('production build renders Markdown features and excludes draft content everywhere', { timeout: 120000 }, async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), 'reconfignet-blog-test-'));
  try {
    const root = path.join(temporary, 'blog');
    await mkdir(root);
    for (const name of ['src', 'scripts', 'astro.config.mjs', 'tsconfig.json', 'package.json']) {
      await cp(path.join(source, name), path.join(root, name), { recursive: true });
    }
    await cp(path.join(source, '../docs/assets/branding'), path.join(temporary, 'docs/assets/branding'), { recursive: true });
    await symlink(path.join(source, 'node_modules'), path.join(root, 'node_modules'), 'dir');
    const markup = '\n> [!TLDR]\n>\n> - **Summary finding.** With a [source](https://example.org/).\n\n> An ordinary quotation.\n\n## Format check\n\n### Nested section\n\n```python\nprint("hello optical world")\n```\n\n| Layer | Question |\n| --- | --- |\n| Control | What changed? |\n\nA footnote.[^one]\n\n[^one]: An example reference.\n\n![Project mark](https://example.org/mark.svg)\n\n*Figure 1: A caption with **emphasis** and a [source](https://example.org/).*\n\n![Plain image](https://example.org/plain.svg)\n\n*An ordinary italic paragraph.*\n';
    const mathMarkup = '\nInline math $T_{\\text{ready}} = C + P$ and units $200\\,\\mu\\mathrm{s}$.\n\n$$\nC / P \\le 10\\%\n$$\n';
    for (const lang of ['en', 'zh']) {
      const directory = path.join(root, 'src/content/posts', lang);
      const updated = lang === 'en' ? '2026-10-21' : '2026-10-22';
      await writeFile(path.join(directory, 'format-check.md'), `---\ntitle: Format check\nauthor: Example Author\ndescription: Markdown rendering fixture\ndate: "2026-09-15"\nupdated: "${updated}"\ndraft: false\n---\n${markup}${mathMarkup}`);
      const hello = path.join(directory, 'hello-optical-world.md');
      await writeFile(hello, (await readFile(hello, 'utf8')).replace('date: "2026-09-15"', 'date: "2026-09-15"\nupdated: "2026-09-15"'));
      await writeFile(path.join(directory, 'unpublished-check.md'), '---\ntitle: DRAFT_SENTINEL\nauthor: Example Author\ndescription: DRAFT_SENTINEL\ndate: "2026-09-15"\ndraft: true\n---\nDRAFT_SENTINEL {>>still reviewing<<}\n');
    }
    const result = spawnSync('npm', ['run', 'build'], { cwd: root, encoding: 'utf8', timeout: 100000, env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' } });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const dist = path.join(root, 'dist');
    const article = await readFile(path.join(dist, 'en/posts/format-check/index.html'), 'utf8');
    for (const pattern of [/<table>/, /<pre[^>]*class="[^"]*astro-code/, /id="format-check"/, /href="#format-check"/, /data-footnotes/, /<img[^>]*alt="Project mark"/]) assert.match(article, pattern);
    for (const pattern of [/By: <span>Example Author<\/span>/, /Published <time datetime="2026-09-15"/, /Enjoy the blog\?/, /--shiki-light:/, /--shiki-dark:/, /--shiki-light-bg:/, /--shiki-dark-bg:/]) assert.match(article, pattern);
    for (const pattern of [/<aside class="tldr" aria-label="TL;DR">/, /class="tldr-title">💡 TL;DR/, /<blockquote>\s*<p>An ordinary quotation\./, /<figure id="figure-1">/, /<figcaption>Figure 1: A caption with <strong>emphasis<\/strong>/, /class="article-outline"/, /href="#nested-section"/, /<details class="outline-branch">/, /<em>An ordinary italic paragraph\.<\/em>/]) assert.match(article, pattern);
    assert.equal((article.match(/<figure\b/g) || []).length, 1);
    assert.doesNotMatch(article, /\[!TLDR\]/);
    assert.match(article, /class="katex"/);
    assert.match(article, /class="katex-display"/);
    assert.match(article, /<math\b/);
    assert.doesNotMatch(article, /katex-error|\$T_/);
    assert.doesNotMatch(article, /class="article-citation"/);
    const citations = [];
    for (const lang of ['en', 'zh']) {
      const updated = lang === 'en' ? '2026-10-21' : '2026-10-22';
      const fixture = await readFile(path.join(dist, `${lang}/posts/format-check/index.html`), 'utf8');
      assert.ok(fixture.includes(`${lang === 'en' ? 'By: ' : '作者：'}<span>Example Author</span>`));
      assert.ok(fixture.includes(`${lang === 'en' ? 'Published' : '发布于'} <time datetime="2026-09-15"`));
      assert.ok(fixture.includes(`${lang === 'en' ? 'Updated' : '更新于'} <time datetime="${updated}"`));
      assert.match(fixture, /property="article:published_time" content="2026-09-15T00:00:00Z"/);
      assert.ok(fixture.includes(`property="article:modified_time" content="${updated}T00:00:00Z"`));
      const home = await readFile(path.join(dist, `${lang}/index.html`), 'utf8');
      assert.doesNotMatch(home, /datetime="2026-10-2[12]"/);
      assert.ok(home.indexOf('/posts/ocs-control-arch/') < home.indexOf('/posts/format-check/'));
      const rss = await readFile(path.join(dist, `${lang}/rss.xml`), 'utf8');
      const item = rss.match(/<item>\s*<title>Format check<\/title>[\s\S]*?<\/item>/)?.[0];
      assert.ok(item?.includes(`<pubDate>${new Date('2026-09-15T00:00:00Z').toUTCString()}</pubDate>`));
      const ocs = await readFile(path.join(dist, `${lang}/posts/ocs-control-arch/index.html`), 'utf8');
      const articleUpdated = '2026-10-22';
      assert.ok(ocs.includes(`property="article:modified_time" content="${articleUpdated}T00:00:00Z"`));
      assert.ok(ocs.includes(`${lang === 'en' ? 'Updated' : '更新于'} <time datetime="${articleUpdated}"`));
      const section = ocs.match(/<section class="article-citation"[\s\S]*?<\/section>/)?.[0];
      assert.ok(section, `${lang}: citation section is rendered`);
      assert.ok(section.includes(lang === 'en' ? 'Citing Us' : '引用本文'));
      assert.ok(section.includes(lang === 'en' ? 'Please cite this blog post if you found it helpful.' : '如果本文对你有所帮助，欢迎引用。'));
      assert.match(section, /href="mailto:wuchangbo@sii.edu.cn"/);
      assert.match(ocs, /href="#citing-us"/);
      assert.equal((ocs.match(/<a\b[^>]*href="#citing-us(?:-citation)*"[^>]*data-outline-link/g) || []).length, 1);
      assert.ok(ocs.indexOf(section) < ocs.indexOf('<footer class="article-footer"'));
      const code = section.match(/<code[^>]*>([\s\S]*?)<\/code>/)?.[1].replace(/<[^>]*>/g, '');
      assert.ok(code?.includes('@misc{wu2026ocscontrolarch,'));
      assert.ok(code.includes('Microsecond Optical Switching. Millisecond Reality.'));
      assert.ok(code.includes('Wu, Changbo and Yu, Zhuolong'));
      assert.ok(code.includes('https://zer0-nu1l.github.io/ReconfigNet-Sim/en/posts/ocs-control-arch/'));
      citations.push(code);
      const hello = await readFile(path.join(dist, `${lang}/posts/hello-optical-world/index.html`), 'utf8');
      assert.doesNotMatch(hello, /class="article-citation"/);
      assert.doesNotMatch(hello, /article:modified_time|>Updated |更新于/);
    }
    assert.equal(citations[0], citations[1]);
    async function inspect(directory) {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        const filename = path.join(directory, entry.name);
        assert.ok(!filename.includes('unpublished-check'), filename);
        if (entry.isDirectory()) await inspect(filename);
        else if (/\.(html|xml|json|js)$/.test(entry.name)) assert.doesNotMatch(await readFile(filename, 'utf8'), /DRAFT_SENTINEL|unpublished-check/);
      }
    }
    await inspect(dist);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
