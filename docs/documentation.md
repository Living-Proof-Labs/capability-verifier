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
within 14 days. Extract the entire artifact. From the extracted artifact root,
serve `docs/html` over loopback HTTP:

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory docs/html
```
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
and runtime redesign are outside scope. The initial manual received
[independent review](https://github.com/Living-Proof-Labs/capability-verifier/pull/12#issuecomment-6434036027).
Subsequent changes still require independent review of the delivered artifact.

Shared standards are explicitly adopted at
[collaboration-guidelines v6.0.0](https://github.com/Living-Proof-Labs/collaboration-guidelines/tree/v6.0.0),
resolved to `52a2f274576dab20e96ee1152b6c8d187cfbf377`.
See its [feature/results standard](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v6.0.0/docs/developer-interface.md#observable-behavior-and-feature-coverage)
and [independent review](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v6.0.0/docs/independent-review.md).
The existing release workflow pin stays v2.2.0 deliberately; this adoption does
not upgrade release automation or the released runtime dependencies.

<span id="publication-pending-delivery"></span>

## Published manual

Read the [Capability verifier manual](https://docs.livingprooflabs.com/capability-verifier/).
[Run 40452638380](https://github.com/Living-Proof-Labs/capability-verifier/actions/runs/40452638380)
deployed source `29b5a250bfc902cd526381dabdc33fb80999535f`. Live browser verification
on 2026-10-27 confirmed valid TLS, that exact source/run identity, Task `demo:runs`
and scenario navigation, matrix search to `matrix:report` and refresh, dark theme,
and the distinction between fixture test outcomes and capability statuses.

[Pages deployment](../.github/PAGES.md) records the main-only workflow and scoped
deployment permissions. Future deployments must repeat live checks against their
own source revision; this evidence does not establish later revisions or robot
safety. Review downloads expire after 14 days; the live manual is the durable
reading destination.

Related reading: [Living Proof manual](https://docs.livingprooflabs.com/livingproof/),
[reporter manual](https://docs.livingprooflabs.com/livingproof-capability-report/)
and [capability specifications](https://docs.livingprooflabs.com/robot-capability-specs/).
These current manuals are reading aids; executable examples keep the exact released
versions recorded in this repository. [Documentation home](https://docs.livingprooflabs.com/)
owns the full organization directory.
