import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import slug from 'rehype-slug';
import autolink from 'rehype-autolink-headings';
import { unified } from '@astrojs/markdown-remark';
import articleFormatting from './src/plugins/remark-article.mjs';
import math from 'remark-math';
import katex from 'rehype-katex';

const site = 'https://zer0-nu1l.github.io';
const base = '/ReconfigNet-Sim';

// `/` is a client-side redirect to `/en/`, so Search Console reports it as
// "Page with redirect" and never indexes it. Keep it out of the sitemap.
const isIndexablePage = (page) => {
  const { pathname } = new URL(page);
  return !pathname.endsWith('/404/') && pathname !== `${base}/`;
};

export default defineConfig({
  site,
  base,
  trailingSlash: 'always',
  output: 'static',
  integrations: [sitemap({ filter: isIndexablePage })],
  markdown: {
    shikiConfig: { themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false },
    processor: unified({ remarkPlugins: [articleFormatting, math], rehypePlugins: [katex, slug, [autolink, { behavior: 'wrap' }]] }),
  },
});
