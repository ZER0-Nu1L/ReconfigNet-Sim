import { expect, test } from '@playwright/test';

const base = '/ReconfigNet-Sim';

test('English entry and article-to-article language switching', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/`);
  await expect(page).toHaveURL(new RegExp(`${base}/en/$`));
  await page.getByRole('heading', { name: 'Hello Optical World' }).getByRole('link').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', `https://zer0-nu1l.github.io${base}/en/posts/hello-optical-world/`);
  await page.locator('[data-language-switch]').click();
  await expect(page).toHaveURL(new RegExp(`${base}/zh/posts/hello-optical-world/$`));
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await page.reload();
  await expect(page.getByRole('heading', { name: '为什么开设这个博客' })).toBeVisible();
  await page.getByRole('heading', { name: '为什么开设这个博客' }).getByRole('link').click();
  await expect(page).toHaveURL(/#为什么开设这个博客$|#%E4/i);
  await page.locator('[data-language-switch]').click();
  await expect(page).toHaveURL(new RegExp(`${base}/en/posts/hello-optical-world/$`));
  expect(errors).toEqual([]);
});

test('pages have working local assets, language pairs and no horizontal page overflow', async ({ page, request }) => {
  for (const lang of ['en', 'zh']) {
    for (const route of ['', 'about/', 'posts/hello-optical-world/']) {
      const response = await page.goto(`${base}/${lang}/${route}`);
      expect(response?.status()).toBe(200);
      await expect(page.locator('h1')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const targets = await page.locator('a[href], img[src], link[rel="stylesheet"]').evaluateAll(elements => elements.map(element => element.getAttribute('href') || element.getAttribute('src')).filter(Boolean));
      for (const target of new Set(targets)) {
        if (target?.startsWith('/')) {
          expect(target.startsWith(`${base}/`)).toBe(true);
          expect((await request.get(target)).ok(), target).toBe(true);
        }
      }
      expect(await page.locator('.brand img').evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    }
  }
});

test('RSS, sitemap, 404 and keyboard entry work', async ({ page, request }) => {
  for (const lang of ['en', 'zh']) {
    const response = await request.get(`${base}/${lang}/rss.xml`);
    expect(response.ok()).toBe(true);
    const xml = await response.text();
    expect(xml).toContain(`<language>${lang === 'zh' ? 'zh-CN' : 'en'}</language>`);
    expect(xml).toContain(`${base}/${lang}/posts/hello-optical-world/`);
    expect(xml).not.toContain(`${base}/${lang}/${base}`);
  }
  const sitemap = await request.get(`${base}/sitemap-0.xml`);
  expect(sitemap.ok()).toBe(true);
  expect(await sitemap.text()).toContain(`${base}/zh/posts/hello-optical-world/`);
  await page.goto(`${base}/404.html`);
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex');
  await page.goto(`${base}/en/`);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
});

test('articles show localized authors, publication dates and the repository Star link', async ({ page }) => {
  for (const [lang, author, date] of [['en', 'Changbo Wu', 'September 15, 2026'], ['zh', '吴昌博', '2026年9月15日']]) {
    await page.goto(`${base}/${lang}/posts/hello-optical-world/`);
    await expect(page.locator('.article-meta p')).toHaveText([`By: ${author}`, `Date: ${date}`]);
    await expect(page.locator('.article-meta time')).toHaveAttribute('datetime', '2026-09-15');
    await expect(page.locator('.article-footer')).toContainText('Enjoy the blog?');
    await expect(page.getByRole('link', { name: 'Give ReconfigNet-Sim a star' })).toHaveAttribute('href', 'https://github.com/ZER0-Nu1L/ReconfigNet-Sim');
  }
});

test('theme follows the system until a manual choice, then persists across pages and languages', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto(`${base}/en/posts/hello-optical-world/`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', { name: 'Switch to light theme' })).toHaveAttribute('aria-pressed', 'true');
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  const toggle = page.getByRole('button', { name: 'Switch to dark theme' });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.locator('[data-language-switch]').click();
  await expect(page.getByRole('button', { name: '切换到亮色主题' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('link', { name: '关于', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: '切换到亮色主题' }).click();
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('saved dark theme is applied before page content is parsed', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => {
    localStorage.setItem('reconfignet-sim-theme', 'dark');
    new MutationObserver((_, observer) => {
      if (document.body) {
        document.documentElement.dataset.themeAtBody = document.documentElement.dataset.theme;
        observer.disconnect();
      }
    }).observe(document, { childList: true, subtree: true });
  });
  await page.goto(`${base}/en/`);
  await expect(page.locator('html')).toHaveAttribute('data-theme-at-body', 'dark');
});

test('theme switching still works with local storage disabled', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    for (const method of ['getItem', 'setItem']) {
      Object.defineProperty(Storage.prototype, method, { value: () => { throw new DOMException('Storage disabled', 'SecurityError'); } });
    }
  });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto(`${base}/en/`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: 'Switch to light theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect(errors).toEqual([]);
});

test('both themes keep article text, branding and mobile navigation readable', async ({ page }) => {
  for (const lang of ['en', 'zh']) {
    await page.goto(`${base}/${lang}/posts/hello-optical-world/`);
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await page.locator('[data-theme-toggle]').click();
      await expect(page.locator(`.mark-${theme}`)).toBeVisible();
      await expect(page.locator(`.mark-${theme === 'light' ? 'dark' : 'light'}`)).toBeHidden();
      const colors = await page.evaluate(() => {
        const prose = getComputedStyle(document.querySelector('.prose')!);
        return { foreground: prose.color, background: getComputedStyle(document.documentElement).backgroundColor };
      });
      const luminance = (color: string) => {
        const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(value => {
          const n = Number(value) / 255;
          return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4;
        });
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      };
      const levels = [luminance(colors.foreground), luminance(colors.background)].sort((a, b) => b - a);
      expect((levels[0] + .05) / (levels[1] + .05)).toBeGreaterThanOrEqual(4.5);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
  await page.emulateMedia({ media: 'print' });
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light');
  await expect(page.locator('.mark-light')).toBeVisible();
  await expect(page.locator('.mark-dark')).toBeHidden();
});

test.describe('without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
  test('system dark theme and branding work without an unusable toggle', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(`${base}/zh/posts/hello-optical-world/`);
    await expect(page.locator('[data-theme-toggle]')).toBeHidden();
    await expect(page.locator('.mark-dark')).toBeVisible();
    await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(21, 27, 37)');
    await page.emulateMedia({ colorScheme: 'light' });
    await expect(page.locator('.mark-light')).toBeVisible();
    await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(252, 252, 250)');
  });
});
