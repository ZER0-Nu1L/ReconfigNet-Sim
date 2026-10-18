import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import slug from 'rehype-slug';
import autolink from 'rehype-autolink-headings';
import { unified } from '@astrojs/markdown-remark';
import articleFormatting from './src/plugins/remark-article.mjs';
import math from 'remark-math';
import katex from 'rehype-katex';

export default defineConfig({
  site: 'https://zer0-nu1l.github.io',
  base: '/ReconfigNet-Sim',
  trailingSlash: 'always',
  output: 'static',
  integrations: [sitemap({ filter: (page) => !page.endsWith('/404/') })],
  markdown: {
    shikiConfig: { themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false },
    processor: unified({ remarkPlugins: [articleFormatting, math], rehypePlugins: [katex, slug, [autolink, { behavior: 'wrap' }]] }),
  },
});
