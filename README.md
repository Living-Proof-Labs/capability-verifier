# Capability verifier

**[Start here: documentation](https://docs.livingprooflabs.com/capability-verifier/)**

A small Python reference for Living Proof Labs: collect evidence, assess it with
multiple evaluators, then apply an explicit grading policy. It is
**not robot safety certification**. The fixture demo below is deterministic and needs no
robot, simulator, AI service, dashboard or server; the [Quickstart](https://docs.livingprooflabs.com/quickstart.html)
runs the verifier on a MuJoCo simulation.

## Quickstart

To verify a real controller in simulation, start with the
[Quickstart](https://docs.livingprooflabs.com/quickstart.html). The fixture demo
below runs without a simulator.

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

<span id="documentation-preview"></span>

## Documentation

Read the [published manual](https://docs.livingprooflabs.com/capability-verifier/)
or [build and review it locally](docs/documentation.md). Tested HTML previews
remain available in CI for 14 days. The manual records its source revision and
verification results.

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
[collaboration-guidelines v2.2.0](https://github.com/Living-Proof-Labs/collaboration-guidelines/tree/v2.2.0).
GitHub release notes are generated; no generated changelog or version bumps are
committed. The Python project version is a development placeholder; release tags
identify published source.

### When a release fails after a push

Published tags, including `capability-verifier-hallway:<version>`, are never moved
or retagged. If a release step fails after something was pushed, the tag stays up,
that version is marked unverified (it is not pinned anywhere, and the next release
says so in `.github/release-notes/<next version>.md`), and the fix ships as the next
patch. The GitHub release is published only after every hallway image job passes.
Example: the 1.15.0 image index was pushed before its platform check failed, so
1.15.0 stays published but unverified, and 1.15.1 carries the fix and that note.

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
[Living Proof Labs capability report v1.6.0](https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/tag/v1.6.0)
as its versioned job-container image. It downloads the portable bundle, invokes
the installed reporting command, and attaches offline HTML, lossless JSON and
flattened CSV. The run summary shows each capability decision and an artifact
link; JUnit above continues to show expectation-test results separately. The job
uses Task for repository test orchestration, with no reporter source checkout,
pip installation or Docker-in-Docker.
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

The matrix adapter was published in [verifier v1.2.0](https://github.com/Living-Proof-Labs/capability-verifier/releases/tag/v1.2.0).
Its producer commit identifies the source used for each execution; its source
dependencies are also pinned to published releases.
The original `task bundle` remains available. Both installed reporter integrations
now consume released reporter v1.6.0, which supports execution compatibility.
No reporter code is vendored or changed here.

Run `task matrix:report` to consume
`ghcr.io/living-proof-labs/livingproof-capability-report:1.6.0`. This uses only its
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

## Walk-hallway evaluators

`capability_verifier.hallway` assesses each `livingproof-hallway-observation/1.0.0` or `/2.0.0` trial with
four deterministic evaluators: `hallway-exit-reached`, `hallway-lateral-limit`,
`hallway-wall-contact` and `hallway-fall`. It judges each trial with `all-required-v1`, then
aggregates k of N with a Wilson 95% interval. Each rationale names the check, the time and the
limit, and the time (2 decimals). Evidence 1.0.0 also names the seed, for example
`lateral 0.62 m > 0.49 m at 10.26 s (seed 7)` or `wall contact depth 0.03 m at 4.50 s (seed 2)`.
Evidence 2.0.0 names the trial index in that sentence, for example `(trial 2)`. Lengths gain
decimals until observed and limit differ. Each failed check is also structured as
`{check, observed, limit, unit, t, seed}` (plus `wall` for contacts and `via` for exits outside
the segment or a `left_hallway` end), listed per assessment (`details.failures`), per trial and
for the whole run (`failures`). For evidence 2.0.0 the integer in `seed` is the trial index:
the failure schema and the pinned reporter still use that field, and they have no index field.

## Accepted hallway evidence and media

Two evidence formats are accepted. Anything else is a `ManifestError` that names the format;
it is not a `KeyError`.

- `livingproof-hallway-evidence/1.0.0` (legacy). `plan.trials` is an integer, `plan.seeds` is
  the seed list, and `plan.required_passes` is the k-of-N threshold. Trial files are keyed by
  seed. This stays accepted so a robot that still records it (livingproof-sim-mujoco v1.8.3
  and the locks pinned to that release) keeps verifying after it takes this verifier, until
  it repins the runner.
- `livingproof-hallway-evidence/2.0.0`. `plan.trials` and the manifest's `trials` are objects,
  `{"count": N, ...}`. There are no `plan.seeds` and no `plan.required_passes`; a manifest
  that still carries either is refused. Trial files are `trial-NN.json`, keyed by `index`
  (1 through N). `trials.reproducibility` is `compared`, `identical` and `finding`. When the
  identical trials disagree, `finding` is `{kind: "test-system", name: "reproducibility", ...}`.

The same k-of-N aggregator grades both. Format 2.0.0 has no required-pass count, and the
trials are identical repeats of the nominal pose, so the threshold used for that format is
N: every trial has to pass. That is the existing aggregator with one input filled in, not a
new grading policy. The per-trial evaluators are unchanged. A reproducibility finding is
copied onto the verification result (`findings`) and into each bundle result's provenance.
It is a test-system finding. It is not a robot FAIL, and it does not change the grade.
`livingproof-sim-mujoco` v2.0.0 exits 4 after writing that finding. The walk-hallway
evidence step captures the exit code and branches on it: 4 continues into verify, and
any other non-zero exit fails the evidence job before verify. Verify prints
`test-system reproducibility: ...` and then exits 4, so the verify job fails after the
finding is on the record. A robot `PASS` or `FAIL` with no finding still exits 0. A
missing or unreadable observation is still `INSUFFICIENT_EVIDENCE` for that trial. If none
of the planned trials complete, k-of-N with threshold N grades the run
`INSUFFICIENT_EVIDENCE`. Reclassifying those trials as a test-system `ERROR` status, and
dropping them from N, belongs to the later grading-policy change. The reporter bundle schema
only allows `PASS`, `FAIL` and `INSUFFICIENT_EVIDENCE` as assessment statuses.

Result ids stay `{configuration}-seed-NN`. For format 2.0.0, NN is the trial index. The
pinned reporter matches a video link by a result id ending in `-seed-NN` and rejects any
other link field, so the walk-hallway workflow still writes that integer under `seed`.

Media manifests follow the same split. `livingproof-hallway-media/1.0.0` trial entries carry
`seed`. `livingproof-hallway-media/2.0.0` entries carry `index` and the files are named
`trial-NN`. Any other media format fails the workflow step with a message that names the
format, before it reads `seed` or `index`.

The trial plan (command speed, and for 1.0.0 the seeds and required passes) is read from the
evidence manifest. The manifest echoes the robot repository's committed
`verification.lock.json`, so nothing is hard-coded here. Thresholds come from the robot
profile, the configuration profile and the world. Every assessment records `assessor.type`
(`deterministic`, `human` or `agent`). Human and agent conclusions enter through `recorded=`
and are judged by the same policy.

`hallway-exit-reached` passes only a `crossed_exit` trial whose base crossed the exit line
inside the exit segment (|y| <= half the world's `clear_width`, read from the trace sample at
`exit_time`). A crossing outside the segment fails (`via: exit-outside-segment`), as does a 2.0.0
`left_hallway` end (the rationale names the wall it left through). An exit whose crossing point
cannot be read, or an end the format does not define, is INSUFFICIENT. Any other observation
format is refused: every evaluator reports INSUFFICIENT. Any wall contact still fails.

`hallway-specs.lock.json` pins the catalog. While it says `"tag": "UNRELEASED"`,
`HALLWAY_SPECS_SOURCE` must name a local checkout that holds the locked commit. A release
changes only the `"tag"` line (and `"commit"` if the release commit differs).

    PYTHONPATH=src python tools/verify_hallway.py <evidence>/manifest.json <out> [--prototype] [--bundle-version 1.1.0]

`--bundle-version 1.1.0` adds the verifier's own grading to the bundle
(`capability_verifier.evaluate`): each result carries a `decision` (status, reason,
structured failures, assessor types, policy) and the bundle carries an `assessment`
in the `livingproof-run-assessment/0.1` format from robot-capability-specs, with every
decision and, per run, k of N, the Wilson 95% interval, the grade and its rationale.
The rules are the ones capability report v1.10.0 applies: INCOMPATIBLE results make the
run INCOMPATIBLE, and without a usable plan N is the recorded count and the grade is
INSUFFICIENT_EVIDENCE. The default stays 1.0.0 because capability report 1.x accepts
only that version. `tools/compare_assessment.py <bundle> <report.json>` checks a 1.1.0
bundle against a report computed from the same evidence.

Each 1.1.0 `decision` also names one `primary_failure` (a copy of one entry of `failures`, or
`null` when nothing failed) and keeps every other failure in `secondary_failures`, so a reader
renders one main reason per trial without ranking anything itself (cv #36). These keys are
additive: `failures` keeps its recorded order and the bundle stays 1.1.0, and status, reason
and grade do not depend on them. The primary is chosen by a fixed category precedence,
`hallway-fall`, `hallway-lateral-limit`, `hallway-wall-contact`, `hallway-exit-reached`
(`evaluate.FAILURE_PRECEDENCE`); within a category the earliest `t` comes first (untimed
last), then recorded order; checks not listed (recorded reviews) follow, by name. It is a
label, not a root cause: the lateral `t` is when the base first passed the limit, while a
contact can come earlier from a shoulder or foot (Go1 seed 1: contact 10.30 s, lateral
10.60 s), and maxima alone cannot establish cause. A fall ranks first because later checks then describe a robot that is down; drift
ranks before contact, since a contact means the lateral clearance was already exceeded;
not reaching the exit ranks last, as the outcome the other failures explain.

Each 1.1.0 assessment `run` also carries, additively (not in the reporter's summary shape, so
reporter v3.0.3's exact `1.1.0` check is unaffected and the bundle is not bumped):

- `failure_summary`: `failed_trials` (each FAIL result counted once) and `by_primary`, one
  entry per primary check in precedence order with `count`, `seeds` and `result_ids`. A FAIL
  with no structured failure counts under `check: null`.
- `worst`: one entry per deterministic check, copied from the stored assessments with its
  `seed`, `t` and `result_id`: the largest lateral (`t` is the peak, from the new
  `details.peak_time`), the deepest wall contact (and its `wall`; `observed` null when no
  trial touched a wall), the lowest base height, and the latest exit time with `missing`, the
  trials that have none. Ties keep the earlier result; INSUFFICIENT assessments are skipped.

Mission, world and resolved limits (robot-capability-specs #30), also additive with no
bundle bump; no grading decision reads them:

- Each result's `provenance.limits` names every limit with the inputs it was resolved from,
  its formula and the value graded against, unrounded beyond the grading value:
  `lateral_limit` (`clear_width / 2 - robot_width / 2`, with the robot profile's
  `dimensions.width`), `exit_segment` (`|y| <= clear_width / 2`), `time_limit`,
  `fall_height` and `fits_width` (robot class and its wall clearance, 0.05 m humanoid,
  0.10 m quadruped). For example G1 0.52 m gives 0.49 m, Go1 0.282 m gives 0.609 m and
  T1 0.541 m gives 0.4795 m.
- The bundle's top-level `description` is the locked catalog's
  `specs/scenarios/walk-hallway/description.json` (`livingproof-scenario-description/0.1`),
  copied unchanged with its `source` path, sha256 and catalog commit, once per bundle. It
  is written only when the catalog has the file, and refused if it names another scenario
  revision or world; catalogs up to v1.8.0 have none; the lock pins v1.9.0, the first that does.

## Reusable walk-hallway workflow (robot repositories)

A robot repository's whole CI chain is one call; every ref comes from its
`verification.lock.json`:

```yaml
jobs:
  walk-hallway:
    uses: Living-Proof-Labs/capability-verifier/.github/workflows/walk-hallway.yml@<release commit>
    permissions: {contents: read, pull-requests: write}
```

The robot repository provides the lock, `pins.json`, `tests/` and a `pyproject.toml` with
the runner source and `[tool.livingproof] adapter = "<module>:ROBOT"`. The workflow runs
lint, profile validation and the robot's tests (`check`, disable with `check: false`) and
the evidence through `livingproof_sim_mujoco.evidence`; no robot Taskfile targets are used. Inputs `repository`, `ref`, `configurations` and `artifact-prefix`
let a cross-robot workflow call it once per robot. `python3 -m capability_verifier.repin
--root ROBOT_REPO --check` (or `runner=vX.Y.Z verifier=… reporter=…`) replaces the robot
repositories' `tools/repin.py`.

The `media` job (on by default, `media: false` to skip) runs `livingproof_sim_mujoco.media`:
an MCAP file for every trial and a video for the first PASS and the first FAIL trial, each
re-run checked against its evidence sha256. Media 1.0.0 selects that trial by `seed`. Media
2.0.0 selects it by `index`. It uploads `<prefix>mcap-<configuration>` and
`<prefix>video-<configuration>-seed-NN-{pass,fail}` artifacts and writes their
`artifact-url`s and the run URL to `<prefix>links-<configuration>`, which the report job
passes to `--evidence-links` (of `grade` or `render`, below); the reporter renders them in report.md, report.json and
the PR comment. The link field is still `seed`, because that is the field the pinned reporter
accepts; for media 2.0.0 the integer is the trial index. It runs only when the robot's locks
can use it (`uv.lock` has livingproof-sim-mujoco >= 1.6.0 with the `media` extra, and the
reporter tag is >= 1.11.0);
robots still on older pins skip it instead of failing. A skipped or failed media job never
blocks the report or the PR comment; the report is then rendered without links. Release
uploads use `--clobber`, so re-running a partly uploaded release still publishes it.

Which bundle the chain writes follows the robot's lock. With verifier >= 1.9.0 and
reporter >= 2.0.0, `verify` writes bundle 1.1.0 (the verifier's own decisions and run
assessments) and the report job calls the reporter's `render` on it, which grades
nothing. Any older pair keeps bundle 1.0.0 and the reporter's `grade`, so robots pinned to
reporter 1.x keep working unchanged. Both paths produce the same report.md and PR comment.
