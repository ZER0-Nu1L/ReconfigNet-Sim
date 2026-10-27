# Deploying the blog to GitHub Pages

The public site is <https://zer0-nu1l.github.io/ReconfigNet-Sim/>. English and
Chinese articles live under `/en/` and `/zh/`. Astro's `site` and `base` in
`astro.config.mjs` must match that origin and project path.

## One-time setup

An administrator enables Pages with **Settings → Pages → Build and deployment
→ Source → GitHub Actions**. The equivalent CLI commands for this existing
Pages site are:

```sh
gh auth status
gh api repos/ZER0-Nu1L/ReconfigNet-Sim/pages
gh api --method PUT repos/ZER0-Nu1L/ReconfigNet-Sim/pages \
  -f build_type=workflow
```

If the GET returns 404 because Pages has never been enabled, create the site
with `gh api --method POST repos/ZER0-Nu1L/ReconfigNet-Sim/pages -f build_type=workflow`.
The `github-pages` environment must allow deployments from `main`; retain any
configured environment approval rules.
No additional deployment token is needed: the workflow uses `GITHUB_TOKEN`
with `pages: write` and `id-token: write` in the deploy job.

Use HTTPS for the public URL. If the Pages API already reports
`https_enforced: true`, no extra setting is needed. Otherwise, after the first
successful deployment, enable it with:

```sh
gh api --method PUT repos/ZER0-Nu1L/ReconfigNet-Sim/pages -F https_enforced=true
```

If GitHub reports "The certificate does not exist yet", allow certificate
provisioning to finish, recheck the Pages API, and retry only if HTTPS is still
not enforced.

## Publish an update

From `blog/`, use the pinned Node version and run:

```sh
nvm use
npm ci
npm run check
npm run build
npm run test:build
npx playwright install chromium
npm run test:browser
```

For local machines with an existing compatible Chromium, set
`CHROMIUM_EXECUTABLE_PATH` as described in `README.md`. CI installs its own
browser and operating-system dependencies.

Prepare the change on a feature branch. Inspect the staged diff, scan for
secrets and private deployment material, and confirm that generated files,
`writing/` and local artifacts are excluded. Commit the intended files only.
After publication has been authorized, run from the repository root:

```sh
git push -u origin HEAD
gh pr create --base main --fill
gh pr checks --watch
```

Review the diff and check results, then merge the specific PR with
`gh pr merge PR_NUMBER --squash`. The Blog workflow runs checks on relevant
PRs. After a relevant change reaches `main`, it builds the static site,
uploads `blog/dist` as the Pages artifact, and deploys it. Changes confined to
`blog/` skip automatic P4App and Tofino runs on both PRs and pushes. Mixed
changes that include files outside `blog/`, and manual target runs, retain
the target checks.

Monitor the deployment with:

```sh
gh run list --workflow blog.yml --branch main --limit 5
gh run watch RUN_ID --exit-status
```

To redeploy the current `main` without changing content:

```sh
gh workflow run blog.yml --ref main
```

Manual runs on other branches perform checks only; the deploy job requires
`refs/heads/main`.

## Google Search Console

Use a URL-prefix property for `https://zer0-nu1l.github.io/ReconfigNet-Sim/`.
The HTML-file ownership check uses `public/google2939badf67338db7.html`;
Astro copies it unchanged to the output root, so the deployed URL is
<https://zer0-nu1l.github.io/ReconfigNet-Sim/google2939badf67338db7.html>.
Keep this file in future deployments, including after ownership is verified.

After deployment, confirm that the URL returns HTTP 200 with the exact
verification text and no redirect, then click **Verify** in Search Console.
The repository deploys the file; it does not complete the Google-account step.
Once verified, submit
<https://zer0-nu1l.github.io/ReconfigNet-Sim/sitemap-index.xml>
under **Sitemaps**, then use **URL inspection** to request indexing for the
English and Chinese article URLs. Google decides whether and when to index them.

### Domain-root discovery

The crawler file at <https://zer0-nu1l.github.io/robots.txt> belongs to the
separate `ZER0-Nu1L/ZER0-Nu1L.github.io` repository. GitHub Pages publishes that
repository's `master` branch from `/`; this project's `blog/public/` cannot
provide a file at the domain root. Keep the root file in future deployments
and in the source of any future site generator, even before a personal
homepage is built:

```text
User-agent: *
Allow: /

Sitemap: https://zer0-nu1l.github.io/sitemap.xml
Sitemap: https://zer0-nu1l.github.io/ReconfigNet-Sim/sitemap-index.xml
```

The first sitemap retains discovery of the existing user-site pages; the
second points to this blog. A missing `robots.txt` returning 404 does not block
crawling. Adding these declarations provides another discovery path; it does
not guarantee indexing or establish the cause of a Search Console fetch error.
Do not add a project-path `robots.txt` as a substitute for the domain-root file.

After publishing a change to the user-site repository, verify that the root
file returns HTTP 200 with the expected plain text and no redirect. Check that
both sitemap URLs return valid XML and that the blog's child sitemap includes
both language versions of the published articles. Record the PR, merge commit,
Pages run and HTTP verification outside the public repositories. To roll back,
revert the relevant commit through the repository's normal PR process.

Then resubmit the blog sitemap once in Search Console and request indexing for
the English and Chinese blog homepages. These Google-account actions are manual;
repository deployment does not submit them. Observe sitemap status and formal
crawl timestamps rather than repeatedly requesting the same article. If the
error persists, inspect the specific fetch error and use a live URL inspection
of the sitemap to distinguish access problems from report-processing problems.

When the planned personal homepage is built, add visible HTML links to the blog
and its articles. Homepage construction and navigation changes are deferred.

## Diagnose redirects to an old domain

A custom domain on the user-site repository can redirect every project site
under that account, even when the project's own `cname` is null. Inspect both:

```sh
gh api repos/ZER0-Nu1L/ZER0-Nu1L.github.io/pages \
  --jq '{cname,html_url,https_enforced}'
gh api repos/ZER0-Nu1L/ReconfigNet-Sim/pages \
  --jq '{cname,html_url,build_type,https_enforced}'
curl -I https://zer0-nu1l.github.io/ReconfigNet-Sim/
```

On 2026-10-18 the user site had an obsolete custom domain,
`www.zer0-nu1l.com`, which caused the project redirect. The intended setup uses
the default `github.io` host for both sites, with no custom domain.

When the account owner explicitly requests removing such a binding, save the
old Pages settings and any `CNAME` file outside the public repository, then
clear the user site's domain:

```sh
gh api --method PUT repos/ZER0-Nu1L/ZER0-Nu1L.github.io/pages -F cname=null
```

GitHub can remove the tracked `CNAME` during this update. Inspect the user
site's publishing branch afterward to confirm that it is gone. If it
still exists, remove that file in a commit so a later publication cannot
restore the old domain. Keep the existing site content. Do not add a project
`CNAME` as a workaround. If the old site's source generator lives elsewhere,
remove its custom-domain setting there before the next regeneration.

Recheck both Pages APIs and HTTP headers after propagation. A browser may
retain an old permanent redirect; try a private window or clear the cached
redirect after the server stops returning it.

## Verify and recover

After a successful deployment, confirm that the root enters the English
site and these URLs return the expected content without an old-domain redirect:

- <https://zer0-nu1l.github.io/ReconfigNet-Sim/en/>
- <https://zer0-nu1l.github.io/ReconfigNet-Sim/zh/>
- <https://zer0-nu1l.github.io/ReconfigNet-Sim/en/posts/hello-optical-world/>
- <https://zer0-nu1l.github.io/ReconfigNet-Sim/zh/posts/hello-optical-world/>
- Both language-specific `rss.xml` feeds and `sitemap-index.xml`.

Check styles, images, language switching, the theme switch and mobile layout.
If a run fails, inspect `gh run view RUN_ID --log-failed`. A failed build does
not publish a new artifact. Recover bad published content by reverting the
relevant commit through the normal PR process and letting `main` deploy again.
