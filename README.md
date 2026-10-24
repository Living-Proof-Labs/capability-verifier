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
