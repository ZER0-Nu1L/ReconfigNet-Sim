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

With `npm run dev`, drafts are accessible directly at `/ReconfigNet-Sim/<lang>/posts/<slug>/`, with a draft notice and `noindex`. They stay out of article lists and feeds. Production builds and `npm run preview` exclude draft article routes. Draft dates are provisional until publication.

When both draft translations exist, the language switch opens the corresponding article; an unpaired draft links to the other language's home. The published OCS article lives at `/ReconfigNet-Sim/en/posts/ocs-control-arch/` and `/ReconfigNet-Sim/zh/posts/ocs-control-arch/`. Its Chinese figures live in `public/images/ocs-control-arch/`, and English figures in its `en/` subdirectory.

Every article, including drafts, requires a nonempty `author` string. Use the author's name in the article's language: `Changbo Wu` in English and `吴昌博` in Chinese for Hello Optical World. The article template displays `By:` in English or `作者：` in Chinese and a localized publication date below the title, and adds the Star prompt and a linked Shields.io GitHub stars badge after the body; do not duplicate these in the Markdown text. The badge loads its current count from Shields.io when the reader reaches the footer, following the presentation in UCCL's articles.

Keep `date` as the original publication date. After a substantive revision, optionally add `updated: "2026-10-21"` to that language's frontmatter. The article page then shows `Published … · Updated …` or `发布于 … · 更新于 …`, and includes `article:modified_time` metadata. Dates must be quoted, valid `YYYY-MM-DD` values, with `updated` no earlier than `date`; an update on the publication day shows only the publication date. English and Chinese may have different update dates. Omit `updated` until the article has actually been revised; file timestamps, Git commits and builds do not set it automatically. Home-page dates, ordering, RSS publication dates and BibTeX citations continue to use the original `date`.

Set `citation: true` in a post's frontmatter to add a localized citation section after the body and before the Star prompt, with a link in the outline. It is enabled only for the OCS control architecture article in both languages. Both versions cite the matching English article's title, authors, publication date and production URL using BibTeX `@misc`; English author names use given-name surname order and commas between authors, for example `Changbo Wu, Zhuolong Yu`. A published post with citations requires a published English counterpart. The discussion contact is `wuchangbo@sii.edu.cn`. Omit `citation` or set it to `false` to leave the section out.

The navigation includes a light/dark theme switch. First visits follow the system preference; a manual choice is saved in local storage under `reconfignet-sim-theme` and shared across pages and languages. Without JavaScript, the site follows the system theme. Print output uses the light palette.

### Optional article hero

Omit `hero` for articles without a header image. To add one below the subtitle, store the final assets in `public/images/<slug>/` and use paths relative to `public/`, without the site base prefix:

```yaml
hero:
  light: images/ocs-control-arch/hero-light.webp
  dark: images/ocs-control-arch/hero-dark.webp
  alt: "Conceptual illustration of optical switching and its surrounding control layers."
```

`dark` is optional; without it, the light image appears in both themes. Both variants use the same localized `alt` text and dimensions. `width` and `height` default to 1536 and 640; set them to the actual dimensions for other sizes. The existing theme button switches the visible image immediately through the root `data-theme` attribute. Saved preferences, system changes and the JavaScript-free system fallback follow the same rules as the page; print always uses the light image. The OCS article uses C for light and the corrected D for dark, shared by both languages.

For this 760px article column, a **2.4:1 banner (1536 × 640)** worked better than 16:9: its displayed height is about 317px instead of 428px, leaving more of the opening paragraph visible. On a 357px mobile column it is about 149px tall. Treat this as a composition starting point, not a required crop. Recompose the subject for the wide frame, preserve safe margins, and inspect it below the actual title and subtitle with the outline visible, in both languages, themes and screen sizes. Check connectors, beam counts and unintended duplicate strokes at full resolution as well as article size; each port in D should have one beam, with two independent paths throughout the input chain. Keep only the selected assets after review, export WebP for the site, and remove rejected candidates, intermediate renders, copied preview sites and generation environments.

Markdown supports fenced code, tables, images, footnotes, and linked headings. Store final article images in `public/images/<slug>/` so both languages can share them. Reference these public images with the site base path, for example `![Topology diagram](/ReconfigNet-Sim/images/hello-optical-world/topology.svg)`. For links to another article, use its full site path including `/ReconfigNet-Sim/` and the reader's language. Do not use workstation paths or private testbed links.

Math uses KaTeX: `$T_{\text{ready}} = C + P$` for inline formulas, or `$$` on separate lines around a display equation. Math also works inside table cells. In prose, use `200 μs` and `5 ms`; in formulas, use `$200\,\mu\mathrm{s}$` and `$5\,\mathrm{ms}$` for spaced, upright units. KaTeX styles and fonts are bundled locally, and rendered MathML is available to assistive readers.

## Article template, summaries, figures and outline

Start a Chinese article by copying [templates/post-zh.md](templates/post-zh.md) to `src/content/posts/zh/<slug>.md`. Update its title, description, date and image paths; retain `draft: true` while previewing locally. For English, use the same structure with localized metadata and captions. Templates live outside the content collection and are not published.

Write a TL;DR as a blockquote beginning with `[!TLDR]`. Blank lines inside the block also need `>`. The renderer turns it into an accessible summary card with a lightbulb title and theme-aware background; normal blockquotes keep their usual appearance. Paragraphs and lists both work:

```markdown
> [!TLDR]
>
> - **Main finding.** What the reader should remember.
> - **Evidence and limits.** Where the finding applies.
```

For a captioned figure, write a standalone Markdown image followed immediately by an italic caption paragraph, separated by one blank line. Begin the caption with `图 N：` or `Figure N:`. The renderer combines them into `<figure id="figure-N">` with a `<figcaption>`; links, emphasis and inline code inside the caption are preserved. Plain images without this convention remain plain images.

```markdown
![Alternative text describing the important visual information](/ReconfigNet-Sim/images/your-slug/overview.svg)

*Figure 1: What is compared, how to read it, and the relevant conditions. Source: [reference](https://example.org/).*

See [Figure 1](#figure-1).
```

Use alt text to describe the image for readers who cannot see it. Use the visible caption to explain the figure, its conditions, and any source or adaptation credit. Keep figure numbers unique and ordered within each article; numbering is explicit, not automatic. Caption text is smaller and centered below the image, with colors following the selected theme. Existing SVG/PNG colors are preserved; a dark image variant is not generated automatically.

The TL;DR styling takes inspiration from [UCCL's MRC article](https://uccl-project.github.io/posts/openai-mrc/). That article and [rdmatop](https://uccl-project.github.io/posts/rdma-monitoring/) use centered italic paragraphs for captions; our renderer provides a similar presentation with semantic figure/caption markup.

Every article automatically gets a hierarchical outline from its `##` through `######` headings. Child sections start collapsed. Click the triangle beside a parent to expand or collapse its children; click the title to navigate. Rows use indentation, bold top-level titles, and single-line truncation; hover over a title to read its full text. Navigation finishes with a quick 240ms scroll and respects the system's reduced-motion preference. Scrolling highlights the current section, including its parent when the child is collapsed.

At widths of 1280px and above, the outline sticks to the left within the article bounds and stops before the site footer. Drag its right divider to change its width and the space available to the article. The divider also accepts Left/Right arrow keys (Shift for larger steps), Home (minimum) and End (maximum). Width is constrained to preserve readable article space and saved in local storage under `reconfignet-sim-outline-width`; resizing still works if storage is disabled. On smaller screens the outline becomes a collapsible directory above the article and the divider is hidden. Native anchor links and collapsible branches also work without JavaScript. Articles without section headings have no outline; the outline is hidden in print.

## Review and publication boundaries

Review one Markdown file at a time with `roughdraft open "/absolute/path.md"`. Keep the command running until Done Reviewing, then read the file back and resolve the feedback. Review English, then Chinese. Remove resolved CriticMarkup and its review metadata before publishing; the build rejects remaining review markup in published articles. Review comments in drafts are allowed.

Site-specific addresses, MACs, device ports, raw acceptance results, and private artifacts remain outside this repository. Public articles may describe generalized, de-identified lessons, with public supporting material where necessary. Distinguish packet-switch observations from optical-hardware conclusions.

## Deployment and recovery

See [deployment.md](deployment.md) for the full `gh` CLI setup and publication
commands, custom-domain troubleshooting, verification and rollback.

The site URL is `https://zer0-nu1l.github.io/ReconfigNet-Sim/`. Its origin and base path are configured in `astro.config.mjs`; navigation and metadata use the same base. English is the default; language links preserve the current article or page.

The Blog workflow checks and builds relevant pull requests. On `main`, relevant pushes and manual runs also deploy `blog/dist` through GitHub Pages. In the repository settings, Pages must first be enabled with **GitHub Actions** as the source; an authorized maintainer performs this one-time setup and any initial push or merge. No `gh-pages` branch or custom domain is required.

After the first deployment, check the public English and Chinese pages, article switching, assets, and RSS. Recover a previous version by reverting the relevant blog commit through the normal review process and deploying again. Changes confined to `blog/` run only the Blog workflow on PRs and pushes. Changes that also touch files outside `blog/` retain the P4App and Tofino checks; manual target runs remain available.
