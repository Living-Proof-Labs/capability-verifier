# Pages deployment and settings

The owner approved this repository’s main-only Pages deployment on 2026-10-27.
The parent browser worker handles settings; this implementation changes no settings.

Required settings: Pages build source **GitHub Actions**; `github-pages`
environment deployment branch policy restricted to **main**. Preserve the
organization domain configuration. Do not add a project CNAME or change DNS.
Published URL: `https://docs.livingprooflabs.com/capability-verifier/`.

Required new permissions, exclusively on the deployment job: `pages: write` and
`id-token: write`. Build remains `contents: read`. The deploy job runs only after
the successful same-run build on trusted main, has no checkout, and uses the
`github-pages` environment. No PR code executes with deployment permissions.
Serialize deployment and reject a superseded commit inside the deployment lock.

The workflow implements the approved jobs. Its Docker/Task build must pass both
verification and documentation browser checks before upload.
[Initial publication and live verification](../docs/documentation.md#published-manual)
records the deployed revision and checks. For each subsequent deployment, use the
actual `page_url`, compare `build.json` to the run/head, and verify real-browser
navigation, search, themes, deep links and assets.
