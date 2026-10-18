import { getCollection } from 'astro:content';

export const languages = ['en', 'zh'] as const;
export type Language = typeof languages[number];
export const repository = 'https://github.com/ZER0-Nu1L/ReconfigNet-Sim';
export const siteName = 'ReconfigNet-Sim Blog';
export const text = {
  en: {
    blog: 'Blog', about: 'About', language: '中文', skip: 'Skip to content',
    subtitle: 'Notes on the systems around reconfigurable optical networks.',
    description: 'Research notes on reconfigurable networks: integration, control, and the work of making systems reproducible.',
    latest: 'Latest writing', read: 'Read article', back: 'All articles',
    footer: 'Research infrastructure for reconfigurable optical networks.',
    switchToLight: 'Switch to light theme', switchToDark: 'Switch to dark theme',
  },
  zh: {
    blog: '博客', about: '关于', language: 'English', skip: '跳至正文',
    subtitle: '记录可重构光网络周边系统的研究与实践。',
    description: '关于可重构网络的研究笔记：系统集成、控制，以及让实验能够复现的工程实践。',
    latest: '最新文章', read: '阅读全文', back: '全部文章',
    footer: '面向可重构光网络的研究基础设施。',
    switchToLight: '切换到亮色主题', switchToDark: '切换到暗色主题',
  },
};

export const href = (route = '') => `${import.meta.env.BASE_URL.replace(/\/$/, '')}/${route.replace(/^\//, '')}`;
export const otherLanguage = (lang: Language): Language => lang === 'en' ? 'zh' : 'en';
export const postSlug = (id: string) => id.split('/')[1];
export const formatDate = (date: string, lang: Language) => new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en', {
  dateStyle: 'long', timeZone: 'UTC',
}).format(new Date(`${date}T00:00:00Z`));

export async function publishedPosts(lang?: Language) {
  const posts = await getCollection('posts', ({ data, id }) => !data.draft && (!lang || id.startsWith(`${lang}/`)));
  return posts.sort((a, b) => b.data.date.localeCompare(a.data.date) || a.id.localeCompare(b.id));
}
