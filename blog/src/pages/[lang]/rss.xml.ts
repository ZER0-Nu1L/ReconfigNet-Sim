import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { languages, publishedPosts, postSlug, href, siteName, text, type Language } from '../../lib/site';

export function getStaticPaths() {
  return languages.map(lang => ({ params: { lang } }));
}

export async function GET(context: APIContext) {
  const lang = context.params.lang as Language;
  return rss({
    title: `${siteName} (${lang === 'en' ? 'English' : '中文'})`,
    description: text[lang].description,
    site: new URL(href(`${lang}/`), context.site!).href,
    items: (await publishedPosts(lang)).map(post => ({
      title: post.data.title,
      description: post.data.description,
      pubDate: new Date(`${post.data.date}T00:00:00Z`),
      link: href(`${lang}/posts/${postSlug(post.id)}/`),
    })),
    customData: `<language>${lang === 'zh' ? 'zh-CN' : 'en'}</language>`,
  });
}
