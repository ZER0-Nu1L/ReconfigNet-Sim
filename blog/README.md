# ReconfigNet-Sim Blog

A static, bilingual research blog built with Astro. Articles are ordinary Markdown.

## Local development

From this directory, use the Node version in `.nvmrc`:

```sh
nvm use
npm ci
npm run dev
```

Open `http://localhost:4321/ReconfigNet-Sim/en/` or the corresponding `/zh/` address. Run `npm run check`, `npm run build`, then `npm run preview` to validate and inspect the production output. `npm run test:build` also checks Markdown rendering and draft exclusion in an isolated temporary build. Browser checks use `npx playwright install chromium` followed by `npm run test:browser` after a build. If your operating system is unsupported by the browser installer, set `CHROMIUM_EXECUTABLE_PATH` to an existing compatible Chromium executable for local checks; CI uses the bundled browser.

## Writing workspace

Keep early writing in a local workspace, organized by article. Use the same lowercase, hyphenated slug for the workspace directory and the eventual article files; the title can change without changing this identifier.

```text
blog/
├── writing/                         # Local only; ignored by Git
│   └── hello-optical-world/
│       ├── outline.md               # Audience, central claim, and outline
│       ├── draft-zh.md              # Current Chinese working draft
│       ├── draft-en.md              # Current English working draft
│       ├── sources.md               # References, evidence, and open questions
│       └── assets/                  # Figure sources and candidate images
├── src/content/posts/               # Site content tracked in Git
│   ├── zh/hello-optical-world.md
│   └── en/hello-optical-world.md
└── public/images/
    └── hello-optical-world/         # Final images tracked in Git
```

`/writing/` is ignored by this directory's `.gitignore`. These files are neither committed nor published; back them up separately. Site-specific data and private testbed artifacts still belong outside this repository, including its workspace.

Keep one current working draft per language. Save additional versions only for meaningful milestones or alternative approaches, using descriptive names rather than sequences such as `final-v2-final`. Start in the language most comfortable for the author, stabilize the argument, then prepare the other version.

Use this workflow for each article:

1. Create `writing/<slug>/` and record the intended reader, central claim, and outline in `outline.md`. Collect references and unresolved factual questions in `sources.md`.

2. Write and review the working drafts with Roughdraft as described below. Keep editable figure sources and candidate images in the workspace's `assets/`.

3. Once reviewed, copy the clean English and Chinese text into the matching `src/content/posts/` files with the frontmatter below and `draft: true`. Copy the final public images into `public/images/<slug>/` and update image links.

4. Check both versions, resolve review markup, set the same publication date, and change both files to `draft: false`. Run `npm run check`, `npm run build`, and inspect the site with `npm run preview` before submitting for publication.


After this handoff, the files in `src/content/posts/` are the authoritative text. Make subsequent corrections there and use Git history to track them. Retain the workspace as a writing record; avoid editing both copies in parallel.

## Bilingual site content

Create matching files at `src/content/posts/en/<slug>.md` and `src/content/posts/zh/<slug>.md`. Use lowercase, hyphenated slugs; the filename is the stable article identifier and URL. Each file begins with:

```yaml
---
title: Hello Optical World
author: Changbo Wu
description: A short, language-specific summary.
date: "2026-09-15"
draft: true
---
```

Both files must have the same publication date and `draft: false` to publish. An unpaired draft may be saved while writing. Drafts are excluded from article routes, lists, RSS, and the sitemap. Keep translations equivalent in claims, figures, and references; they need not be literal sentence-by-sentence copies.

Every article, including drafts, requires a nonempty `author` string. Use the author's name in the article's language: `Changbo Wu` in English and `吴昌博` in Chinese for Hello Optical World. The article template displays `By:` and `Date:` below the title and adds the GitHub Star link after the body; do not duplicate these in the Markdown text.

The navigation includes a light/dark theme switch. First visits follow the system preference; a manual choice is saved in local storage under `reconfignet-sim-theme` and shared across pages and languages. Without JavaScript, the site follows the system theme. Print output uses the light palette.

Markdown supports fenced code, tables, images, footnotes, and linked headings. Store final article images in `public/images/<slug>/` so both languages can share them. Reference these public images with the site base path, for example `![Topology diagram](/ReconfigNet-Sim/images/hello-optical-world/topology.svg)`. For links to another article, use its full site path including `/ReconfigNet-Sim/` and the reader's language. Do not use workstation paths or private testbed links.

## Review and publication boundaries

Review one Markdown file at a time with `roughdraft open "/absolute/path.md"`. Keep the command running until Done Reviewing, then read the file back and resolve the feedback. Review English, then Chinese. Remove resolved CriticMarkup and its review metadata before publishing; the build rejects remaining review markup in published articles. Review comments in drafts are allowed.

Site-specific addresses, MACs, device ports, raw acceptance results, and private artifacts remain outside this repository. Public articles may describe generalized, de-identified lessons, with public supporting material where necessary. Distinguish packet-switch observations from optical-hardware conclusions.

## Deployment and recovery

See [deployment.md](deployment.md) for the full `gh` CLI setup and publication
commands, custom-domain troubleshooting, verification and rollback.

The site URL is `https://zer0-nu1l.github.io/ReconfigNet-Sim/`. Its origin and base path are configured in `astro.config.mjs`; navigation and metadata use the same base. English is the default; language links preserve the current article or page.

The Blog workflow checks and builds relevant pull requests. On `main`, relevant pushes and manual runs also deploy `blog/dist` through GitHub Pages. In the repository settings, Pages must first be enabled with **GitHub Actions** as the source; an authorized maintainer performs this one-time setup and any initial push or merge. No `gh-pages` branch or custom domain is required.

After the first deployment, check the public English and Chinese pages, article switching, assets, and RSS. Recover a previous version by reverting the relevant blog commit through the normal review process and deploying again. Changes confined to `blog/` run only the Blog workflow on PRs and pushes. Changes that also touch files outside `blog/` retain the P4App and Tofino checks; manual target runs remain available.
