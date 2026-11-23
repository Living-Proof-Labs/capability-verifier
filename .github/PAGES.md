# Pages deployment and settings

The owner approved this repository’s main-only Pages deployment on 2026-10-27.
The parent browser worker handles settings; this implementation changes no settings.

Required settings: Pages build source **GitHub Actions**; `github-pages`
environment deployment branch policy restricted to **main**. Preserve the
organization domain configuration. Do not add a project CNAME or change DNS.
Published URL: `https://docs.livingprooflabs.com/capability-verifier/`.

Required permissions on the deployment job: `contents: read`, `pages: write`, and
`id-token: write`. The main-freshness read sends the workflow token;
`contents: read` is enough, and the job does not get `contents: write`. Build
remains `contents: read`. The deploy job runs only after
the successful same-run build of the exact immutable published release on trusted
main, has no checkout, and uses the
`github-pages` environment. No PR code executes with deployment permissions.
Serialize deployment and reject a superseded commit inside the deployment lock.

The **Documentation** job remains the verification and downloadable preview gate.
It never supplies production Pages bytes. After the original release report and
image workflow finalizes the immutable release, the read-only delivery builder
checks that its actual tag points to current source and runs the same Docker/Task
verification and browser checks. Pages only deploys its sealed, verified archive.
The **Replay released verifier documentation** workflow can rebuild from an
existing immutable stable tag at current main. It cannot create releases, update
release report bundles, or alter published images.
[Initial publication and live verification](../docs/documentation.md#published-manual)
records the deployed revision and checks. For each subsequent deployment, use the
actual `page_url`, compare `build.json` to the run/head, and verify real-browser
navigation, search, themes, deep links and assets.
