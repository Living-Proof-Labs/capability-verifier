# Capability verifier

A small Python reference for Living Proof Labs: collect evidence, assess it with
multiple evaluators, then apply an explicit grading policy. This is a deterministic
fixture demonstration, **not robot safety certification**. No robot, simulator,
AI service, dashboard or server is required.

Install [Git](https://git-scm.com/downloads),
[Docker with Compose v2](https://docs.docker.com/compose/install/), and
[Task 3.54.0](https://taskfile.dev/docs/installation), then start Docker.
Host Python, uv and Node.js are not needed; the Dockerfile supplies the runtime.

To run the known-good released demo from a fresh directory:

```sh
git clone --branch v1.0.1 https://github.com/Living-Proof-Labs/capability-verifier.git
cd capability-verifier
task demo
```

If you already have a checkout:

```sh
task demo
```

The host Task command builds the Dockerfile runtime and invokes Compose. Inside
the container a separate Taskfile runs pytest-bdd against the Gherkin scenarios
from [robot-capability-specs v1.1.0](https://github.com/Living-Proof-Labs/robot-capability-specs/tree/v1.1.0/specs/v1).
The fetch verifies the released tag's commit and content hashes in
`specs.lock.json`. First use requires network access for images, dependencies and
the public catalog; repeat builds use Docker's cache.

| Evidence fixture | Verification result | Expected test result |
| --- | --- | --- |
| Valid pause in 120 ms | PASS | Passed |
| Motion inhibition false; other evaluators favorable | FAIL | Passed (failure correctly detected) |
| Elapsed time absent | INSUFFICIENT_EVIDENCE | Passed (no silent pass) |

All five Gherkin examples must pass for the demo command to succeed. An incorrect
verification result fails pytest and CI. `artifacts/demo.xml` retains each full
report as a JUnit property, alongside separate expected/actual capability statuses; console output includes reports and individual tests.
`task verify` runs static checks plus the complete test suite and writes
`artifacts/results.xml`. Hosted CI uses these same commands and uploads JUnit;
open [**Verify and release**](https://github.com/Living-Proof-Labs/capability-verifier/actions/workflows/verify.yml), choose a run, and open its **Summary** for the expanded test results and
fixture verdict table, or the **Verification expectation tests** check for individual
cases and failure annotations. The full suite includes the five fixture expectation
tests; the demo reruns those same five, rather than adding five independent tests.
Raw XML remains downloadable as an artifact. Reporting also runs after test failure,
and never changes a failing test exit into success. Fork PRs receive the run summary
but cannot create the additional check with their read-only token. No separate UI
service is required.

For a result you can view immediately, the
[v1.0.1 test report](https://github.com/Living-Proof-Labs/capability-verifier/runs/120847135184)
shows **26 passed, 0 failed, 0 skipped**. Its
[run summary](https://github.com/Living-Proof-Labs/capability-verifier/actions/runs/40085341763)
contains the fixture expected/actual verdict table; GitHub may require sign-in for
run summaries and downloadable artifacts. The test-report page is publicly readable.

The runner only collects JSON and hashes the evidence. A mandatory deterministic
pause evaluator checks observations. The mock AI evaluator reads a clearly
labeled scripted fixture; it does not call a model. The human evaluator reads a
recorded fixture; it is not a live approval. `all-required-v1` preserves every
assessment and rationale. Any failure wins; missing, invalid or duplicate
membership is insufficient; only all required passes yield PASS. No averaging.

Reports include the catalog tag/commit and capability revision, fixture system
version, evidence digest, runner/evaluator/policy versions, and null simulator
provenance because no simulator is involved. Pause does not imply an emergency
stop. Emergency stop is not universally equivalent to removing power.

## Development

Use `task setup`, `task check`, `task test`, `task verify` or `task demo` on the
host. Task inside the container uses `Taskfile.runtime.yml`; its `specs:fetch`
target illustrates the colon hierarchy. There is no Docker socket in the runtime,
Docker-in-Docker, or recursive host wrapper.

VS Code, Cursor and Codespaces can open `.devcontainer/devcontainer.json`, which
builds the same Dockerfile. Inside that prepared runtime, humans and agents run:

```sh
task -t Taskfile.runtime.yml setup
task -t Taskfile.runtime.yml verify
task -t Taskfile.runtime.yml demo
```

The reference targets Linux containers; other host platforms and editor launch
flows require verification before claiming support. `uv.lock` pins dependencies;
Python 3.12 and uv come from the Dockerfile. Generated `.specs` content is excluded
from Git. Do not edit it; update the immutable version lock deliberately by PR.
No proprietary code or data is included.

## Contribution and licensing

Open a focused draft PR with a Conventional Commit title. Ready and merge only
after validation and authorization; release automation uses
[collaboration-guidelines v2.0.3](https://github.com/Living-Proof-Labs/collaboration-guidelines/tree/v2.0.3).
GitHub release notes are generated; no generated changelog or version bumps are
committed. The Python project version is a development placeholder; release tags
identify published source.

Original code, fixtures, tooling and documentation here are Apache-2.0; see
[LICENSE](LICENSE). Downloaded spec content under `.specs/specs/` is CC BY 4.0,
copyright Living Proof Labs contributors, from the immutable catalog version
linked above, unmodified. See that catalog's license scope and
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This attribution does not
imply endorsement or certification.

## Portable reporting input

`task bundle` creates `artifacts/bundle/manifest.json` and relative evidence files.
Copy the complete directory to a reporter; consumers do not need this repository.
The version 1.0.0 manifest preserves the original verifier result, assessments,
evidence hashes and provenance. Mock-agent and recorded human fixtures are labeled;
no model or live human review is implied. Existing output is never overwritten.

The compatibility matrix uses catalog [v1.1.1](https://github.com/Living-Proof-Labs/robot-capability-specs/tree/v1.1.1)
(pause revision 1.0.0, 500 ms) and [v1.2.0](https://github.com/Living-Proof-Labs/robot-capability-specs/tree/v1.2.0)
(pause revision 2.0.0, 250 ms). `reporting-specs.lock.json` records their commits.
Fixture set 1.0.0 retains 120 ms pass, mandatory failure and missing-time examples.
Fixture set 2.0.0 adds a genuinely different 300 ms observation: PASS under the old
contract, FAIL under the strict contract. Environments and revisions remain explicit.
JUnit still reports whether tests matched expectations, not a capability grade.

The dedicated `report` job consumes
[Living Proof Labs capability report v1.5.0](https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/tag/v1.5.0)
as its versioned job-container image. It downloads the portable bundle, invokes
the installed reporting command, and attaches offline HTML, lossless JSON and
flattened CSV. The run summary shows each capability decision and an artifact
link; JUnit above continues to show expectation-test results separately. The job
has no reporter source checkout, pip installation, Task or Docker-in-Docker.
GitHub Actions manages the job container startup and runs steps through a shell;
normal Docker usage keeps the reporting command as the convenience entrypoint.

On a published producer release, the same verified report files and their SHA-256
checksums are attached as public release assets. This provides stable, versioned
downloads in addition to the workflow artifacts.

The report job runs the same released multi-architecture image on native AMD64
and ARM64 GitHub runners. It checks the changed slow-fixture verdict across the
two released specification revisions and preserves insufficient evidence. Each
job uploads its own reports; producer releases attach the verified AMD64 outputs,
including a plain terminal table, before publishing the immutable release. This
uses the optional draft assembly in collaboration-guidelines v2.2.0.

## Released execution compatibility matrix (preview)

`task matrix` writes `artifacts/matrix/manifest.json`, relative evidence files,
`controls.json` and `summary.md`. Copy the whole directory to a reporter. The main
manifest contains exactly four coordinates; controls are a separate bundle.
`task verify` runs each coordinate as an individually reported expectation test.

| Catalog / pause revision | Executor v1.0.1, fixture 1.0.0 (120 ms) | Executor v1.1.0, fixture 2.0.0 (300 ms) |
| --- | --- | --- |
| v1.1.1 / 1.0.0, 500 ms | PASS | PASS |
| v1.2.0 / 2.0.0, 250 ms | INCOMPATIBLE execution | FAIL |

This is **execution compatibility**, not an evidence-schema change. Both released
contracts use the same observations. Verifier v1.0.1 implements only the 500 ms
contract; calling it for revision 2 would silently apply the wrong limit. The
adapter rejects that pairing before assessment, preserving the valid 120 ms
observation and digest. Verifier v1.1.0 implements both revisions and supplies the
300 ms fixture. Its released exporter/evaluator code actually runs in a separate
Python process; the matrix does not reimplement its threshold decisions.

`matrix.lock.json` pins released executors and supported revisions;
`reporting-specs.lock.json` pins released catalogs. Setup fetches each exact tag,
verifies its commit, and execution rejects modified tracked source. The generated
source cache is not edited or committed. Python runtime version, released executor,
fixture identity/version/source, spec, run identifier and evidence hashes remain
explicit. There is no robot, simulator, or live AI/human assessment.

The existing portable schema stays at 1.0.0. Additive result provenance includes
`run_id`, `fixture`, `executor`, `runtime`, `role`, `evidence_state`, and
`compatibility` (`status`, `scope`, `reason`). A verified evidence digest means the
bytes are intact, not that observations are complete. Missing elapsed time still
produces INSUFFICIENT_EVIDENCE in the separate controls; mandatory failure remains
FAIL. Incompatible execution has no fabricated assessments. Never combine these
coordinates into a single grade.

The adapter itself is an **unreleased feature-branch preview**, identified by the
producer commit. The exception ends when its reviewed release is published; no
future tag is claimed. Its source dependencies are already published releases.
The original `task bundle` remains available. Both installed reporter integrations
now consume released reporter v1.5.0, which supports execution compatibility.
No reporter code is vendored or changed here.

Run `task matrix:report` to consume
`ghcr.io/living-proof-labs/livingproof-capability-report:1.5.0`. This uses only its
installed command, validates and renders the matrix under both policies, and
asserts all four expected decisions plus the controls across JSON, HTML, CSV and
terminal output. CI runs this same task and uploads the rendered outputs.
Output is retained under `artifacts/matrix-report`; existing output is not replaced.

Builds behind an existing provider trust bundle can set `HOST_BUILD_CA_FILE` to
that existing PEM bundle. It is a temporary BuildKit secret, never copied into the
image. TLS verification stays enabled; no host trust or network setting changes.

Contract alignment with reporter PR12: the optional `provenance.compatibility`
declaration is emitted only for `INCOMPATIBLE` execution. Compatible records omit
it and carry actual assessments; the reporter performs its normal supported-input
checks. The matrix summary displays COMPATIBLE only after the adapter executes the
released supported pair. Presence of valid evidence alone never asserts support.

## Download and reproduce demonstration runs

Run `task demo:runs` from a fresh checkout to produce `artifacts/demo-runs/`.
The generated `CATALOG.md` maps five named ZIPs to their input manifests and
explains the differences: baseline pass, strict timing failure, mandatory failure,
missing measurement and the existing four-coordinate compatibility matrix.
All observations, mock-agent conclusions and recorded human assessments are
fixtures. No robot, simulator, model call or live human review is implied.

Each ZIP is independently portable: `manifest.json`, its `evidence/` files,
`report.json`, `report.html`, `report.csv`, `report.txt`, `provenance.json`,
`SHA256SUMS` and a regeneration README. Extract each into a separate directory.
Use installed **livingproof-capability-report 1.6.0**, or its versioned container,
to validate the manifest, grade it and render the saved report. Evidence paths
are relative to the manifest. HTML works offline; JSON retains the entire
manifest and decisions; CSV and text are presentation formats. These are the
reporter's existing schema 1.0.0 inputs and outputs, not a new interchange format.
Your CI supplies its own manifests, observations and assessments to the same
reporter commands; this verifier supplies illustrative recorded fixtures.

Names follow `capability-verifier-<version>-<demo>-<bundle UUID>-<architecture>-<CI run>-<attempt>.zip`.
Local/PR builds use an observed Git tag/distance/commit (and dirty marker when
applicable), not an invented release number. New release attachments use the
actual semantic-release tag; provenance retains the tested source identity and
commit. Historical spec, executor, fixture and adapter versions in the manifest
are unchanged. The matrix adapter's producer version identifies its export
contract, not the version of the release distributing it.

`collection_id` identifies one demo execution batch; `bundle_run_id` identifies
one selected demonstration, and each result's `execution_id` identifies its
capability execution. A shared result has the same execution ID across related
manifests. Repeating execution creates new UUIDs. Repackaging preserves those
UUIDs; CI run and attempt are separate provenance and filename components.
The catalog does not average different spec/software versions into a grade.

New releases attach five grouped ZIPs, `CATALOG.md` and `BUNDLE-SHA256SUMS`.
Generic `report.*` and `SHA256SUMS` remain deprecated compatibility assets for
existing consumers; they describe the older combined fixture export, not the
new demo catalog. Published releases, including v1.2.0, are not rewritten.

`task verify` tests identities, archive path safety and integrity. `task demo:runs`
tests extraction and byte-identical report regeneration with the released image.
CI repeats consumption on native AMD64 and ARM64 and publishes individual JUnit
cases in run summaries; the verification check includes archive tests. Raw reports
and bundles are retained for 14 days. Failed expectation tests fail CI even when
the expected capability outcome is FAIL or INSUFFICIENT_EVIDENCE. Output directories
must be new; move prior artifacts aside before repeating a local export.
