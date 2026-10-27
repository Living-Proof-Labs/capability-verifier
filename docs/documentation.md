# Build and review the manual

The manual renders the canonical README and project Markdown, with generated Task
commands, released Gherkin scenarios and individual pytest outcomes. It uses
Zensical 0.0.73, locked with its dependencies in `uv.lock`. There is no application
CLI to invent or duplicate: Task is the supported developer interface.

From a checkout with Docker and Task 3.54.0:

```sh
task verify
task docs
```

Inside the existing Docker/development container, use `task -t Taskfile.runtime.yml`
for the same commands. `docs` requires the current verification report; it refuses
missing or stale results. Tests may fail and still be documented as failures.
Run `task docs:test:setup` then `task test:docs` for Chromium navigation, search,
theme, mobile viewport and local page/asset/anchor checks. Browser setup explicitly
downloads the browser pinned by Playwright 1.64.1; no host settings change.

Download the **documentation-<commit>** artifact from
[Verify and release](https://github.com/Living-Proof-Labs/capability-verifier/actions/workflows/verify.yml)
within 14 days. Extract the entire artifact and serve its `html` directory over
loopback HTTP, for example `python -m http.server 8000 --bind 127.0.0.1 --directory html`.
Open `http://127.0.0.1:8000/`. Direct Android content-provider opening is not verified.
The artifact includes source hashes, build version/commit, test outcomes and JUnit.

## Bounded adoption and gaps

Existing coverage includes five released Gherkin examples, four released executor
coordinates, negative/missing evidence controls, bundle integrity and archive
regression. Generated results preserve each test identity and failed/skipped
status. Scenario coverage is not line coverage; no line-coverage percentage is
measured. These fixtures do not establish robot behavior or safety certification.
Installed reporter archive consumption remains the existing separate native
AMD64/ARM64 CI integration, not a claim made by source pytest results.

Before this change there was no browsable manual or generated Task/scenario
inventory. The initial manual covers existing fixture inputs, execution, manifests,
version provenance and report bundles. Full API documentation, new simulator work,
and runtime redesign are outside scope. Independent installed-artifact review is
still required; the author cannot supply independent review evidence.

Shared standards are explicitly adopted at
[collaboration-guidelines v5.0.0](https://github.com/Living-Proof-Labs/collaboration-guidelines/tree/v5.0.0),
resolved to `dd4b0391a9a66fa7944f6655aac57c67d3f8c002`.
See its [feature/results standard](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v5.0.0/docs/developer-interface.md#observable-behavior-and-feature-coverage)
and [independent review](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v5.0.0/docs/independent-review.md).
The existing release workflow pin stays v2.2.0 deliberately; this adoption does
not upgrade release automation or the released runtime dependencies.

## Publication pending delivery

The intended URL is `https://docs.livingprooflabs.com/capability-verifier/`.
It is not a verified live manual. [Pages preparation](../.github/PAGES.md) names
exact settings and permissions needed. Scoped approval permits main-only deployment, with Pages-write and identity-token
permissions confined to the deployment job. The parent coordinates repository
settings. No domain or DNS change is included.
After activation and independent review, verify the deployed revision, navigation,
search results, themes, related manuals and assets under the project path. Only
then add the prominent live README link and documentation-directory entry.

Related reading: [Living Proof manual](https://docs.livingprooflabs.com/livingproof/).
For reporter and specification contracts use the released source links in the
README until their own rendered manuals have been verified.
