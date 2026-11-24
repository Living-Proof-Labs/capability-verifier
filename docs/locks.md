<span id="verification-locks"></span>

# Verification locks

A verification lock records the commits, release tags and hashes a walk-hallway
run uses. Passing `lock --check` compares those records with the files and tags
they name. That comparison is a pin check. It is not a capability result, and it
is not robot certification.

Two files carry locks, and they use different format strings. The version numbers
are the format of each file.

| Name | Format string | File |
| --- | --- | --- |
| Robot verification lock | `livingproof-verification-lock/1.0.0` | `verification.lock.json` in the robot repository |
| Caller verification lock | `livingproof-verification-lock/0.1.0` | `<definition-stem>.lock.json` beside the definition. `lock` writes this format. |
| Caller verification lock | `livingproof-verification-lock/0.2.0` | The path passed as `verification-lock`. A run copies that file to `caller-verification.lock.json`. `lock` in this repository writes a `0.1.0` file. |

The robot lock is the document the runner already reads. `0.1.0` records the
sha256 of that file and copies its `releases`. `0.2.0` carries the evidence
fields itself, plus the scenario-evaluator pin. A caller run projects those
evidence fields into a `1.0.0` document so the runner can read them, and records
the replaced robot file's sha256 on that document.

Hash fields, commits and image digests are written by the command that computes
them. [Recorded hashes](#recorded-hashes) names each command and the check that
rejects a value typed in by hand.

## Spellings

| Value | Spelling | Example of the shape |
| --- | --- | --- |
| Commit | 40 lowercase hexadecimal characters | `0123456789abcdef0123456789abcdef01234567` |
| File sha256 | 64 lowercase hexadecimal characters, with no prefix | `definition.sha256`, `robot.lock.sha256`, `configurations[].sha256`, `world.sha256`, `overridden_robot_lock.sha256` |
| Image digest | `sha256:` and then 64 lowercase hexadecimal characters | `releases.reporter.digest` |
| Release tag | `v` and then `X.Y.Z` of digits | `v1.2.3` |
| Repository | `OWNER/NAME` | `Living-Proof-Labs/capability-verifier` |
| Scenario id | `name@version` | `walk-hallway@0.1.0` |
| Configuration version | `X.Y.Z` of digits | `1.1.0` |

An uppercase hash or commit fails the pattern check that applies to that field.
A tag without the leading `v` fails where the tag pattern applies. `0.1.0`
`read_lock` requires the verifier tag and commit keys to exist, and the pattern
check for those two keys is the comparison of the whole `releases` object with
the robot file, together with `repin --check` on that file. `0.2.0` `read_lock`
applies the tag, commit, repository and digest patterns itself.

## Robot verification lock

`livingproof-sim-mujoco` reads `verification.lock.json` from the robot root.
This repository's evidence writer builds that document from a `0.2.0` caller
lock. The document's top-level keys are `format`, `scenario`, `releases`,
`world`, `configurations` and `plan`, in that order. `format` is written as
`livingproof-verification-lock/1.0.0`.

`$comment` may sit on a robot file. It is left out of the evidence document.

When a caller run replaces a robot file that was already there, the evidence
document gains `overridden_robot_lock.path` (`verification.lock.json`) and
`overridden_robot_lock.sha256` (the sha256 of the file before replacement).
When the robot commit has no lock file, the log line is `robot commit has no verification.lock.json`
and the document has no `overridden_robot_lock` object.

The two robot files kept in this repository are
`tests/fixtures/g1-862cf979-verification.lock.json` and
`tests/fixtures/repin/verification.lock.json`. Together their leaves are:

| Field | What the files hold |
| --- | --- |
| `format` | `livingproof-verification-lock/1.0.0` |
| `scenario` | `walk-hallway@0.1.0` in both files |
| `$comment` | A note. The evidence writer omits it. |
| `releases.specs.repository` | `OWNER/NAME` |
| `releases.specs.tag` | A `vX.Y.Z` tag |
| `releases.specs.commit` | The commit that tag names |
| `releases.runner.repository` | `OWNER/NAME` |
| `releases.runner.tag` | A `vX.Y.Z` tag |
| `releases.runner.commit` | The commit that tag names |
| `releases.verifier.repository` | `OWNER/NAME` |
| `releases.verifier.tag` | A `vX.Y.Z` tag |
| `releases.verifier.commit` | The commit that tag names |
| `releases.verifier.image` | Present on the G1 file: `ghcr.io/living-proof-labs/capability-verifier-hallway` |
| `releases.verifier.digest` | Present on the G1 file, beside that image |
| `releases.reporter.repository` | `OWNER/NAME` |
| `releases.reporter.tag` | A `vX.Y.Z` tag |
| `releases.reporter.commit` | The commit that tag names |
| `releases.reporter.image` | `ghcr.io/living-proof-labs/livingproof-capability-report` in both files |
| `releases.reporter.digest` | The registry digest beside that image |
| `world.id` | `hallway-open` in both files |
| `world.version` | `0.1.0` in both files |
| `world.repository` | `OWNER/NAME` in both files |
| `world.tag` | A tag in both files |
| `world.commit` | A commit in both files |
| `configurations[].name` | Configuration name |
| `configurations[].version` | `X.Y.Z` |
| `configurations[].path` | Path of the configuration file inside the robot repository |
| `configurations[].sha256` | sha256 of that file |
| `plan.command_speed` | A number in both files |
| `plan.trials` | An integer in the repin fixture |
| `plan.trials.count` | The trial count in the G1 fixture, where `plan.trials` is an object |
| `plan.seeds[]` | The seed list in the repin fixture |
| `plan.required_passes` | An integer in the repin fixture |

`repin` also reads `releases.<name>.expected` while an entry is still
`PENDING_RELEASE`. Filling that entry removes `expected`. The message is
`releases.{name} is PENDING_RELEASE (expected {expected})`, and `{expected}`
falls back to the words `a release tag` when the key is absent.

The walk-hallway plan step reads these paths from `verification.lock.json`:
`configurations[].name`, `scenario`, `world.id`, `releases.verifier.repository`,
`releases.verifier.commit`, `releases.verifier.tag`, `releases.verifier.image`,
`releases.verifier.digest`, `releases.reporter.image`, `releases.reporter.tag`
and `releases.reporter.digest`. The evaluator pin is read from the selected pin
file: `caller-verification.lock.json` when `EVIDENCE_SOURCE` is `caller`, and
`verification.lock.json` otherwise. The paths are
`evaluators.livingproof-evaluator-scenario.repository`,
`evaluators.livingproof-evaluator-scenario.commit` and
`evaluators.livingproof-evaluator-scenario.tag`. An empty result leaves the
evaluator outputs empty. The robot fixtures in this repository have no
`evaluators` object. A `0.2.0` caller lock has one, and the evidence document
does not: the projection copies `scenario`, `releases`, `world`,
`configurations` and `plan`, and the copied caller file keeps `evaluators`.

<span id="resolved-pins"></span>

## Resolved pin inputs

`walk-hallway.yml` and `scenario.yml` take four optional string inputs. Each
defaults to empty. An empty input leaves the lock field as the value the plan
step already writes. A caller that has resolved a release tag passes the commit
or image ref it checked, and the run uses that value.

| Input | Lock field | Accepted value |
| --- | --- | --- |
| `verifier-commit` | `releases.verifier.commit` | 40 lowercase hexadecimal characters |
| `verifier-image` | `releases.verifier.image`, `releases.verifier.tag` and `releases.verifier.digest`, joined as `image:tag@digest` | `ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex>` |
| `reporter-image` | `releases.reporter.image`, `releases.reporter.tag` and `releases.reporter.digest`, joined the same way | `ghcr.io/living-proof-labs/livingproof-capability-report:<semver>@sha256:<64 hex>` |
| `evaluator-commit` | `evaluators.livingproof-evaluator-scenario.commit` on the selected pin file | 40 lowercase hexadecimal characters |

`<semver>` is `MAJOR.MINOR.PATCH`: three numeric parts, no leading zeros, and no
pre-release suffix. The image tag has no leading `v`. That is the ref the plan
step builds from a lock (`ltrimstr("v")` on the tag, then `image:tag@digest`).
Digests and commits are lowercase. Any other spelling fails before the value is
written as a job output. The media and bundle gate still reads
`releases.verifier.tag` and `releases.reporter.tag` from the lock file.

The plan step passes each input through an environment variable
(`RESOLVED_VERIFIER_COMMIT`, `RESOLVED_VERIFIER_IMAGE`, `RESOLVED_REPORTER_IMAGE`,
`RESOLVED_EVALUATOR_COMMIT`). It does not interpolate the inputs into the shell
script.

`jq -r` prints `null` for a missing verifier commit and `:@` when the reporter
image, tag and digest are all missing. A verifier image with no `image` key is
empty. An evaluator commit that is absent is empty. Those results count as an
absent lock field, so a set input fills them. When the lock field is present
and equal to the input, the run uses the input. When the lock field is present
and different, the step exits 1 before it writes the plan outputs. The message
names the input, the input value, the lock field and the lock value:

```text
verifier-commit <input> does not match releases.verifier.commit (<lock>); the verification lock is stale
```

The same sentence is used for `verifier-image` with `releases.verifier image ref`,
for `reporter-image` with `releases.reporter image ref`, and for
`evaluator-commit` with `evaluators.livingproof-evaluator-scenario.commit`.
A value that fails the spelling check is not compared with the lock. The
messages are:

```text
verifier-commit must be a 40-character lowercase hexadecimal SHA (got <value>)
evaluator-commit must be a 40-character lowercase hexadecimal SHA (got <value>)
verifier-image must be ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex> (got <value>)
reporter-image must be ghcr.io/living-proof-labs/livingproof-capability-report image, tag <semver>, digest sha256:<64 hex> (got <value>)
```

`got` prints the rejected value with shell quoting. The plan job records the
source that supplied each field: `verifier-commit-source`,
`verifier-image-source`, `reporter-image-source` and `evaluator-commit-source`
are `input` or `lock`.

## Caller verification lock 0.1.0

`python3 -m capability_verifier.definition lock DEFINITION.toml` fetches the
definition's robot ref, reads `verification.lock.json` at that commit, and
writes `livingproof-verification-lock/0.1.0` to
`<definition-stem>.lock.json`. The same entry point is
`livingproof capability verify lock DEFINITION.toml`.

| Field | Role |
| --- | --- |
| `format` | `livingproof-verification-lock/0.1.0` |
| `definition.sha256` | sha256 of the definition file bytes |
| `definition.path` | The definition file name. `lock` writes it. `lock --check` does not read it. |
| `procedure` | `walk-hallway` or `scenario` |
| `robot.repository` | `OWNER/NAME` from the definition |
| `robot.ref` | The branch, tag or commit that was resolved. `lock` writes it. `lock --check` does not read it. |
| `robot.commit` | The fetched commit. `lock --check` requires 40 lowercase hexadecimal characters. |
| `robot.configurations` | The names from the definition. Each name must appear in the robot file's configuration list at lock time. |
| `robot.lock.path` | `verification.lock.json` |
| `robot.lock.sha256` | sha256 of that robot file's bytes |
| `releases.verifier.repository` | Copied from the robot file. The key is required. |
| `releases.verifier.tag` | Copied from the robot file. The key is required. |
| `releases.verifier.commit` | Copied from the robot file. The key is required. |

`releases` is the robot file's `releases` object, copied whole. Further release
entries travel with it. `lock --check` requires `releases.verifier` and then
requires the whole object to equal the robot file at `robot.commit`.

`0.1.0` remains accepted by `lock --check`. A `0.1.0` file is not an evidence
source: the evidence writer requires `0.2.0` for `plan`, `world` and
configuration sha256s.

## Caller verification lock 0.2.0

`lock --check` and the workflow input `verification-lock` accept
`livingproof-verification-lock/0.2.0`. This repository's `lock` command writes
`0.1.0`. The required leaves below are what `read_lock` demands.

| Field | Rule |
| --- | --- |
| `format` | `livingproof-verification-lock/0.2.0` |
| `definition.sha256` | sha256 of the definition file. `lock --check` compares it with the file bytes. |
| `definition.path` | Accepted. `lock --check` does not read it. |
| `procedure` | `walk-hallway` or `scenario` |
| `scenario` | `name@version`, for example `walk-hallway@0.1.0` |
| `robot.repository` | `OWNER/NAME`. `lock --check` requires it to equal the definition. |
| `robot.ref` | Accepted. `lock --check` does not read it. |
| `robot.commit` | Required as a key. `lock --check` requires 40 lowercase hexadecimal characters, then checks out that commit. |
| `robot.configurations` | A non-empty list of names. It must equal the definition. Every name must appear in `configurations`. |
| `robot.adapter` | The non-empty `[tool.livingproof]` adapter string. `lock --check` compares it with `pyproject.toml` at the locked commit. |
| `releases.specs.repository` | `OWNER/NAME` |
| `releases.specs.tag` | `vX.Y.Z` |
| `releases.specs.commit` | 40 lowercase hexadecimal characters |
| `releases.runner.repository` | `OWNER/NAME` |
| `releases.runner.tag` | `vX.Y.Z` |
| `releases.runner.commit` | 40 lowercase hexadecimal characters |
| `releases.verifier.repository` | `OWNER/NAME` |
| `releases.verifier.tag` | `vX.Y.Z` |
| `releases.verifier.commit` | 40 lowercase hexadecimal characters. `lock --check` checks out this commit and compares the evaluator pin with that tree. |
| `releases.reporter.repository` | `OWNER/NAME` |
| `releases.reporter.tag` | `vX.Y.Z` |
| `releases.reporter.commit` | 40 lowercase hexadecimal characters |
| `releases.reporter.image` | Required string. The run uses `image:tag@digest`. |
| `releases.reporter.digest` | Required `sha256:` digest |
| `world.id` | Non-empty string |
| `world.version` | Non-empty string |
| `world.sha256` | Optional. When present, 64 lowercase hexadecimal characters. `lock --check` checks that spelling and does not open a world file for it. |
| `configurations[].name` | Non-empty string. Names are unique. |
| `configurations[].version` | `X.Y.Z` |
| `configurations[].path` | A relative path inside the robot repository. An absolute path, or a path that contains `..`, fails before any fetch. |
| `configurations[].sha256` | 64 lowercase hexadecimal characters. `lock --check` compares it with the file at the locked commit. |
| `plan.command_speed` | A JSON number. A boolean fails. Required when `scenario` is `walk-hallway@…`. Another scenario may leave it out (walk-corner takes its speed from its parameter file); when present it is still a JSON number. |
| `evaluators.livingproof-evaluator-scenario.repository` | `OWNER/NAME` |
| `evaluators.livingproof-evaluator-scenario.tag` | `vX.Y.Z`. The tag must resolve to the locked commit. |
| `evaluators.livingproof-evaluator-scenario.commit` | 40 lowercase hexadecimal characters |

`evaluators.livingproof-evaluator-run` is optional. When present it is a
tag-only pin: `repository` (`OWNER/NAME`) and `tag` (`vX.Y.Z`). There is
no commit field. When the lock omits the pin, the walk-hallway
run-integrity job defaults to `Living-Proof-Labs/livingproof-evaluator-run`
at `v1.5.0`. An explicit lock pin still takes precedence. This default supports
recording integrity via `--media`; applicability remains determined by the run’s
declared recording limits, not by the evaluator version alone.

The assessing jobs fetch the specs commit the evidence lock declares with
`tools/fetch_hallway_specs.py --from evidence/manifest.json`. Every file in
`releases.specs.files` must match its declared sha256, `description.json` as well as
`parameters.json`, or the step fails. The run-integrity job adds
`--record evidence/specs-files.json`, which writes `livingproof-specs-files/0.1` beside
`manifest.json`: `run_id`, the specs `repository` and `tag`, the `commit` the checkout
resolved to, and `files`, the sha256 of each declared file as read from the checkout (not the
lock's values). A failed check writes no record and removes an old one. The job keeps a copy
with the integrity result as `specs-files.json`.

`releases.specs.digest`, `releases.runner.digest`, `releases.verifier.digest`
and `evaluators.livingproof-evaluator-scenario.digest` are optional. A `digest`
must match the `sha256:` spelling. `releases.specs.image`, `releases.runner.image`,
`releases.verifier.image` and `evaluators.livingproof-evaluator-scenario.image`
require the matching digest: an image alone fails with
`{label}.digest must be a sha256 digest`. A digest alone, with a matching
spelling, passes `read_lock`. `repin --check` compares a digest with the
registry when the entry has `image`.

The lock's `releases.verifier.image` and `digest` are what the jobs are asked to run, so
a digest read from the lock only repeats the lock. When the lock names a verifier image, the
`evidence` job also runs `.github/scripts/record_verifier_image.py` from the workflow's own
commit. It asks the container engine for the image under the job's container (the runner's
`job.container.id`) and the registry digests that image was pulled by, and writes
`verifier-image.json` (`livingproof-verifier-image/0.1`) into the evidence artifact, beside
`manifest.json`, where Run Integrity already reads. Fields: `image` and `locked_digest` (the
lock), `digest` (the engine's digest of that image), `matches` (`true` when the locked digest is
one the engine reports, `false` when the engine reports another), `image_id`, `repo_digests`
and `container`. When the engine cannot be reached, does not know the container, or reports no
digest of that image, `digest` and `matches` are `null` and `unavailable` says why. A difference or
a missing answer is recorded with a workflow warning and does not fail the job; the Run
Integrity `pins` check reports it. The record comes from the `evidence` job only: the `verify`,
`scenario-evaluator` and `run-integrity` jobs run in the same reference but are not recorded.
The reporter image is not recorded, because the `report` job runs after Run Integrity.

`robot.json` configuration names must equal the `configurations` names in the
same order. That list is the full `configurations` array, including entries the
definition did not select. `robot.configurations` is the selected subset.

A `0.2.0` `world` or `plan` object may carry further keys. The evidence
projection copies the object whole. The published robot files carry
`world.repository`, `world.tag`, `world.commit`, `plan.trials`,
`plan.trials.count`, `plan.seeds[]` and `plan.required_passes`. `read_lock`
accepts those keys and does not require them. `read_lock` requires a `plan`
object, `world.id` and `world.version`, and `plan.command_speed` for a
`walk-hallway` scenario.

A walk-corner lock (`scenario` `walk-corner@0.1.0`, `world.id` `hallway-corner`)
names the world livingproof-sim-mujoco v3.2.1 vendors: `world.tag` `v1.3.0` of
livingproof-worlds, not a later worlds tag with the same bytes, which that sim
refuses. It must carry `world.sha256`: the grader refuses walk-corner evidence
whose `lock.content.world.sha256` is missing or differs from the world it grades.

<span id="lock-fields"></span>

## Field inventory

The tables above are this inventory. The test
`tests/test_lock_format_docs.py` rebuilds it from `read_lock`, from `lock`, and
from the two robot fixtures.

<!-- lock-field-inventory -->

```json
{
  "caller_0_1_0": {
    "format": "livingproof-verification-lock/0.1.0",
    "required": [
      "definition.sha256",
      "format",
      "procedure",
      "releases.verifier.commit",
      "releases.verifier.repository",
      "releases.verifier.tag",
      "robot.commit",
      "robot.configurations",
      "robot.lock.path",
      "robot.lock.sha256",
      "robot.repository"
    ],
    "written_unchecked": [
      "definition.path",
      "robot.ref"
    ]
  },
  "caller_0_2_0": {
    "format": "livingproof-verification-lock/0.2.0",
    "required": [
      "configurations[].name",
      "configurations[].path",
      "configurations[].sha256",
      "configurations[].version",
      "definition.sha256",
      "evaluators.livingproof-evaluator-scenario.commit",
      "evaluators.livingproof-evaluator-scenario.repository",
      "evaluators.livingproof-evaluator-scenario.tag",
      "format",
      "plan.command_speed",
      "procedure",
      "releases.reporter.commit",
      "releases.reporter.digest",
      "releases.reporter.image",
      "releases.reporter.repository",
      "releases.reporter.tag",
      "releases.runner.commit",
      "releases.runner.repository",
      "releases.runner.tag",
      "releases.specs.commit",
      "releases.specs.repository",
      "releases.specs.tag",
      "releases.verifier.commit",
      "releases.verifier.repository",
      "releases.verifier.tag",
      "robot.adapter",
      "robot.commit",
      "robot.configurations",
      "robot.repository",
      "scenario",
      "world.id",
      "world.version"
    ],
    "optional_digest": [
      "evaluators.livingproof-evaluator-scenario.digest",
      "releases.runner.digest",
      "releases.specs.digest",
      "releases.verifier.digest"
    ],
    "optional_image_requires_digest": [
      "evaluators.livingproof-evaluator-scenario.image",
      "releases.runner.image",
      "releases.specs.image",
      "releases.verifier.image"
    ],
    "optional_file_sha256": [
      "world.sha256"
    ],
    "accepted_unchecked": [
      "definition.path",
      "robot.ref"
    ]
  },
  "robot_1_0_0": {
    "format": "livingproof-verification-lock/1.0.0",
    "evidence_fields": [
      "format",
      "scenario",
      "releases",
      "world",
      "configurations",
      "plan"
    ],
    "not_copied": [
      "$comment"
    ],
    "caller_run_adds": [
      "overridden_robot_lock.path",
      "overridden_robot_lock.sha256"
    ],
    "repin_optional": [
      "releases.<name>.expected"
    ],
    "fixture_fields": [
      "$comment",
      "configurations[].name",
      "configurations[].path",
      "configurations[].sha256",
      "configurations[].version",
      "format",
      "plan.command_speed",
      "plan.required_passes",
      "plan.seeds[]",
      "plan.trials",
      "plan.trials.count",
      "releases.reporter.commit",
      "releases.reporter.digest",
      "releases.reporter.image",
      "releases.reporter.repository",
      "releases.reporter.tag",
      "releases.runner.commit",
      "releases.runner.repository",
      "releases.runner.tag",
      "releases.specs.commit",
      "releases.specs.repository",
      "releases.specs.tag",
      "releases.verifier.commit",
      "releases.verifier.digest",
      "releases.verifier.image",
      "releases.verifier.repository",
      "releases.verifier.tag",
      "scenario",
      "world.commit",
      "world.id",
      "world.repository",
      "world.tag",
      "world.version"
    ]
  }
}
```

## Writing a lock

The definition file is `livingproof-verification-definition/0.1.0`. `lock` and
`lock --check` read it first. It needs `format`, `id` (lowercase letters, digits
and dashes), `procedure` (`walk-hallway` or `scenario`), `robot.repository`, `robot.ref` and
a non-empty `robot.configurations` list.

`lock` resolves `robot.ref` with `git fetch` of that ref and records
`robot.commit`. It refuses a definition configuration that the robot file does
not list: `{repository}@{commit12} has no configuration {names}`, where
`{commit12}` is the first 12 characters of the commit. On success it prints
`wrote {path}; review it before committing`.

`repin` fills `PENDING_RELEASE` release entries on a robot lock, or on the
caller lock when the workflow points `--lock` at `caller-verification.lock.json`:

```sh
python3 -m capability_verifier.repin --root ROBOT runner=vX.Y.Z verifier=vX.Y.Z reporter=vX.Y.Z
python3 -m capability_verifier.repin --root ROBOT --check
python3 -m capability_verifier.repin --root ROBOT --check --offline
```

Each `entry=vX.Y.Z` is resolved with `git ls-remote`. An annotated tag is peeled.
A tag that does not exist is an error: `{repository} has no tag {tag}; release it first (tags are never invented)`.
An entry with `image` also records `digest` from the registry manifest for
`image:X.Y.Z` (the tag with the leading `v` removed). The digest lookup accepts
`ghcr.io` images. Any other host fails with `{image}: only ghcr.io images are supported`.
Pinning `runner` rewrites the `livingproof-sim-mujoco` source in `pyproject.toml`
to that tag and runs `uv lock`. The change log ends with `uv.lock: regenerated by uv lock`.
`--dry-run` prints the changes and leaves the files in place. With no
`entry=vX.Y.Z` and no `--check`, the command exits with
`name at least one entry=vX.Y.Z, or pass --check`.

`repin --check` prints `NOT RELEASED: {problem}` for each problem and
`{lock}: every ref is a released tag` when there are none. `--offline` skips the
tag-to-commit lookup and the registry digest lookup. The problems it can report:

- `releases.{name} is PENDING_RELEASE (expected {expected})`
- `releases.{name} needs a vX.Y.Z tag and a full commit`
- `releases.{name} has an image but no sha256 digest`
- `releases.verifier.image is {image}, not ghcr.io/living-proof-labs/capability-verifier-hallway`
- `pyproject.toml has a path-pinned dependency`
- `pyproject.toml livingproof-sim-mujoco source is not tag {tag}: {body}`
- `releases.{name}: cannot resolve {tag}: {error}`
- `releases.{name}: {repository} tag {tag} is {resolved}, not the locked {recorded}`
- `releases.{name}: cannot read {image}:{tag}: {error}`
- `releases.{name}: {image}:{version} is {published}, not the locked {recorded}`
- `{image}:{tag} is not published ({code}); release it first`
- `{image}:{tag}: registry returned no sha256 digest`
- `{name}={tag}: expected vX.Y.Z`
- `unknown entry {name}; one of {names}`
- `pyproject.toml has no [tool.uv.sources] livingproof-sim-mujoco line`

The tag comparison treats a moved tag, a deleted tag, and a hand-edited commit
as the same failure: the tag resolves to a commit other than the one in the lock.

<span id="lock-check"></span>

## What `lock --check` enforces

```sh
python3 -m capability_verifier.definition lock DEFINITION.toml --check
python3 -m capability_verifier.definition lock DEFINITION.toml --check --workflow FILE
python3 -m capability_verifier.definition lock DEFINITION.toml --check --work DIR
```

`--work` defaults to `artifacts/.lock-work`. `--check` re-resolves nothing. It
uses the commit written in the lock, including when the definition's ref has
moved on since locking. Success prints
`{lockname}: robot {repository}@{commit12}, verifier {tag}: ok`, with the first
12 characters of the commit.

The checkout of a reused `--work` directory is `git checkout -f --detach FETCH_HEAD`,
then `git clean -fdx -e .venv`. Tracked edits are discarded. Untracked files
other than `.venv` are removed. The hash comparison reads the files at the
pinned commit.

Shared checks, for `0.1.0` and `0.2.0`:

- The lock file is missing: `{path} is missing; run `lock {definition}` and review it`.
- The lock is not a JSON object, or its `format` is neither `0.1.0` nor `0.2.0`.
- A required key is missing, or a required object lacks a required child. Several `read_lock` problems are joined with `; ` and the line ends with `; re-run lock and review`.
- `definition.sha256` differs from the definition file: `{name} changed since it was locked; re-run lock and review`.
- `robot.repository` or `robot.configurations` differs from the definition: `{lockname}: robot.{key} {recorded} differs from {definition} ({current}); re-run lock and review`.
- `robot.repository` fails `OWNER/NAME`: `{lockname}: robot.repository {value} is not OWNER/REPO`.
- `robot.repository` matches the definition and `robot.commit` is not 40 lowercase hexadecimal characters: `robot.commit is not a full commit sha`.
- `robot.repository` differs from the definition: the checker reports that edit and skips the checkout.

When `robot.repository` matches the definition, `0.1.0` fetches `robot.lock.path`
at `robot.commit`:

- The file's sha256 differs: `{repository}@{commit12} lock differs from {lockname}`.
- The sha256 matches and `releases` differs: `releases in {lockname} differ from the robot lock`.

When `robot.repository` matches the definition, `0.2.0` fetches the robot tree
and the verifier tree. That comparison reads the configuration files,
`pyproject.toml` and `robot.json`. The robot `verification.lock.json` stays out
of it. On the robot tree:

- `{repository}@{commit}: {path} is missing`
- `{repository}@{commit}: {path} sha256 is {actual}, not the locked {recorded}`
- `{repository}@{commit}: pyproject.toml is missing`
- `{repository}@{commit}: pyproject.toml has no [tool.livingproof] adapter string`
- `{repository}@{commit}: [tool.livingproof] adapter is {actual}, not the locked {recorded}`
- `{repository}@{commit}: robot.json is missing`
- `{repository}@{commit}: robot.json configurations must be a list of objects with name`
- `{repository}@{commit}: robot.json configuration names are {actual}, not the locked {recorded}`

`0.2.0` shape failures from `read_lock`, before that fetch:

- `scenario must be name@version (for example walk-hallway@0.1.0), got {value}`
- `robot.adapter must be the [tool.livingproof] adapter string`
- `robot.configurations must be a non-empty list of names`
- `configurations must be a non-empty list`
- `configurations[{index}] must be an object`
- `configurations[{index}].name must be a string`
- `configurations[{index}].version must be X.Y.Z`
- `configurations[{index}].path must stay inside the robot repository`
- `configurations[{index}].sha256 must be 64 hex characters`
- `configuration names must be unique`
- `robot.configurations {names} are not in configurations`
- `plan must be an object`
- `plan.command_speed must be a number`
- `world.{id|version} must be a string`
- `world.sha256 must be 64 hex characters`
- `{label} must be an object`
- `{label}.repository must be OWNER/NAME`
- `{label}.tag must be a vX.Y.Z release tag (got {value})`
- `{label}.commit must be a full commit sha`
- `{label}.digest must be a sha256 digest`
- `{label}.image is required` for `releases.reporter` when `image` is missing or is not a string
- `robot.lock must have path, sha256` on a `0.1.0` lock
- `releases.verifier must have repository, tag, commit` on a `0.1.0` lock
- `missing {key}`
- `{key} must have {children}`, including `definition must have sha256`, `robot must have repository, commit, configurations, lock` (`0.1.0`), `robot must have repository, commit, configurations, adapter` (`0.2.0`), `releases must have verifier` (`0.1.0`), `releases must have specs, runner, verifier, reporter` (`0.2.0`), `world must have id, version`, and `evaluators must have livingproof-evaluator-scenario`
- `procedure must be one of ['scenario', 'walk-hallway']`
- `format is not livingproof-verification-lock/0.1.0 or livingproof-verification-lock/0.2.0`
- `{path}: not valid JSON ({error}); re-run lock and review`
- `{path}: must be a JSON object; re-run lock and review`
- `{path}: not valid TOML ({error})`
- `{path}: no such definition`
- `format must be 'livingproof-verification-definition/0.1.0'`
- `id must be lower-case letters, digits and dashes`
- `robot.repository must be OWNER/NAME`
- `robot.ref is required (a branch, tag or commit)`

`check()` reports its own problems on separate lines. `read_lock` joins its problems with `; ` and ends with `; re-run lock and review`.

With `--workflow`, the job whose id equals the definition id must call
`{verifier repository}/.github/workflows/walk-hallway.yml@{verifier commit}` for
procedure `walk-hallway`, or `.github/workflows/scenario.yml` at that commit for
procedure `scenario`. `scenario.yml` runs the scenario and world the lock names
(walk-corner, for example); `walk-hallway.yml` is the thin wrapper around it.
Its evidence step passes the evidence document it wrote (`verification.lock.json`
in the robot checkout) to livingproof-sim-mujoco with `--lock` when the robot's
`uv.lock` has sim 3.0 or later, which requires it. A robot on sim v2.x gets the
same command as before, without `--lock`.
Owner and repository compare case-insensitively. The commit compares exactly.
The job must pass the locked robot repository, the locked robot commit and the
locked configurations. A commented-out pin does not count. Another job that
calls the procedure fails unless its id is another definition beside this one.
The messages:

- `{workflow}: cannot be read as YAML ({error})`
- `{workflow}: has no jobs`
- `{workflow}: no job calls {target} for {repository} (job {id}); must call {pinned}`
- `{path}: job {name}: calls the procedure, but job id {name} is no known definition; every procedure-calling job's id must be a definition`
- `{path}: job {name}: must call {pinned} (calls {uses})`
- `{path}: job {name}: must pass the locked robot repository {repository}`
- `{path}: job {name}: must pass the locked robot commit {commit}`
- `{path}: job {name}: must pass the locked configurations '{json}'`

<span id="evaluator-tag"></span>

## Evaluator tag

On a `0.2.0` lock, `lock --check` checks out `releases.verifier` and compares
`evaluators.livingproof-evaluator-scenario` with that tree. Three comparisons
all have to hold:

1. `pyproject.toml` requests `livingproof-evaluator-scenario` from the locked repository at the locked tag. The dependency text is `livingproof-evaluator-scenario @ git+https://github.com/{repository}[.git]@{tag}`.
2. `uv.lock` resolves that package to the same repository, tag and commit. The source text is `https://github.com/{repository}[.git]?rev={tag}#{commit}`.
3. The tag resolves, through `git ls-remote` with annotated tags peeled, to the locked commit.

The messages, with the label `evaluators.livingproof-evaluator-scenario`:

- `{label}: tag is required`
- `{label}: the verifier pyproject.toml does not request livingproof-evaluator-scenario`
- `{label}: the verifier requests {repository}@{tag}, not the locked {repository}@{tag}`
- `{label}: the verifier uv.lock does not resolve livingproof-evaluator-scenario`
- `{label}: the verifier resolves {repository}@{tag}#{commit}, not the locked {repository}@{tag}#{commit}`
- `{label}: cannot resolve tag {tag} of {repository}: {error}`
- `{label}: tag {tag} is {resolved}, not the locked commit {recorded}`

`lock --check` reaches the tag-resolution line only after `read_lock` has
accepted a `vX.Y.Z` tag. An empty tag fails earlier with
`{label}.tag must be a vX.Y.Z release tag (got {value})`. The workflow can still
pass an empty tag into the same comparison: the plan output `evaluator-tag` is
the pin's tag, or empty when the selected file has no tag.

The plan step `Every ref is a released tag` runs that comparison when
`evaluator-commit` is non-empty. It imports `capability_verifier.evaluator_pin`
from this workflow's source (`workflow-src/src`) when that checkout is present,
and from the locked verifier checkout otherwise. The scenario-evaluator job
receives `evaluator-tag` and repeats the comparison in the verifier checkout.
It then runs `uv sync --locked` in the evaluator checkout, puts `evaluator/src`
and that checkout's site-packages on `PYTHONPATH` ahead of the verifier tree,
and imports `livingproof_evaluator_scenario`. The import has to land under
`evaluator/src`. Otherwise the job exits 1 with
`livingproof_evaluator_scenario imported from {path}, not evaluator/src`.

When `evaluator-commit` is empty, that job skips the pin comparison and imports
through `PYTHONPATH=src` from the verifier checkout. A robot lock with no
`evaluators` object takes that path.

<span id="caller-lock-on-the-robot-path"></span>

## A caller lock committed in the robot repository

`caller-verification.lock.json` is the copy a caller run writes for
`repin --check` and for the evaluator pin. A copy already committed in the
robot repository is a different thing. The plan step `Refs from verification.lock.json`
treats it as follows.

When `EVIDENCE_SOURCE` is `caller`, that file is the pin. The workflow sets
`caller` by passing `verification-lock`. The plan step sets `COPY_CALLER_LOCK=1`,
and the evidence writer then projects the `1.0.0` document and writes the
caller file to `caller-verification.lock.json`.

When `EVIDENCE_SOURCE` is anything else, including empty and `robot`, and
`caller-verification.lock.json` is in the robot tree, the step exits 1 with:

```text
caller-verification.lock.json is committed in the robot repository; pass verification-lock or remove that file
```

That refusal is the first action in the step, before `jq`. The verifier runtime
that replays the step does not install `jq`. A caller lock passed in
`verification-lock` remains the pin, because that run's source is `caller`.

The evidence script refuses a bad `verification-lock` path on its own:

- An empty input, and no robot `verification.lock.json`: `the robot commit has no verification.lock.json; pass verification-lock (a path in the caller repository)`. The local `run` command's wording is `the robot commit has no verification.lock.json; pass a livingproof-verification-lock/0.2.0 as verification-lock`.
- A path that starts with `/` or contains `..`: `verification-lock must be a relative path inside the caller repository`.
- A missing file: `caller has no verification lock at {path}`.
- A file whose format is not `0.2.0`: `evidence inputs are written from a livingproof-verification-lock/0.2.0 (got {found}). A 0.1.0 lock does not own plan, world or configuration sha256s.`
- A `0.2.0` file missing an evidence field: `verification lock is missing {fields}`.
- A replacement digest that is not 64 hexadecimal characters: `overridden robot lock sha256 must be 64 hex characters, got {digest}`.
- Evidence input that is not JSON: `{path}: not valid JSON ({error})`.

On success the script prints `source=caller` or `source=robot`. A caller write
logs `EVIDENCE_SOURCE: caller` and either
`overridden robot verification.lock.json sha256: {digest}` or
`robot commit has no verification.lock.json`. A `0.1.0` local run leaves an
existing robot `verification.lock.json` byte for byte.

<span id="recorded-hashes"></span>

## Recordings in the Run Integrity job

The `run-integrity` job also needs the `media` job. Whenever that job was not skipped it attempts
this configuration's artifact, `<prefix>mcap-<configuration>` (`media.json` and `mcap/`), into
`media/`. `needs.media.result` is the matrix aggregate, so one failed configuration must not drop
another's already-uploaded recordings. If the aggregate succeeded and this artifact is missing, the
download fails: the job does not silently grade without recordings. If the aggregate is not success
and the artifact is missing, the job continues without `--media`. When `media.json` is present, the
job passes the directory to the evaluator as `--media "$GITHUB_WORKSPACE/media"`. The flag arrived
in livingproof-evaluator-run v1.4.0 with the `recording` check. The job passes it only when the
pinned evaluator lists `--media` as a whole flag in its `--help` (not as a prefix of a longer
option such as `--media-dir`), so a lock that pins an earlier tag, or none (the default is
v1.0.0), runs exactly as before.

With media off, not media-capable, skipped because the evidence step failed, or with no uploaded
artifact for this configuration, there is no usable `media/media.json` and the job runs without
`--media`: it stays green, and v1.4.0 and later report `recording` as not applicable. The gap
limit is `plan.recording.max_gap_seconds`, read by the evaluator from the evidence manifest and
then the lock. Neither this repository nor the evaluator has a default for it, so a run that
declares none is not judged, with or without media.

A failed or refused Run Integrity is shown, not omitted. The `report` job does not require
`run-integrity` to succeed, and passes its result to the collector. With no integrity result
collected (the job failed, the evaluator refused the run with exit 2, or it was cancelled or
skipped, or it succeeded and left nothing) the stored assessment gets `integrity_not_run` (the job
result and the reason: the evaluator's refusal message, else the job's own `no-result.txt` line, else the job result),
every assessment run is `INCONCLUSIVE`, and the reports show a
`Run Integrity: REFUSED` or `Run Integrity: NOT RUN (...)` line. The robot grade is stored as before and is not changed. The
job result is the matrix aggregate, so one failed configuration is reported for each. The reason is
the first line of the run-integrity job's own `no-result.txt`; a note in any other evaluator's
artifact is never used.

The integrity line sits with the verdict in every format, never after the footer: in `report.md`
and `report.txt` right after the verdict line (the reporter words it `...; overall PASS. 100% required.`
with trial counts, or `...: overall PASS.` without; both are matched), and in `report.html` right after each
"Stored overall status" line, with the same weight. It uses one of four labels, which change only
how the result is shown and never what is stored:

| Label | When | Stored as |
| --- | --- | --- |
| `PASS` | the evaluator ran and the check passed | `test_system_status` `COMPLETED` |
| `FAILED` | the evaluator ran and the check failed | `ERROR` (or `success: false`) |
| `REFUSED: <message>` | the evaluator refused the run (exit 2, no result); the reason is its own message | `assessment.integrity_not_run` with `refused: true` |
| `NOT RUN (<job result>)` | the check never ran: cancelled, skipped, or the job ended without a result and without a refusal | `assessment.integrity_not_run` |

Any other stored status (`INCONCLUSIVE`, or one a later evaluator adds) is shown as it is. A
`PASS` line reads `Run Integrity: PASS`. The run-integrity step knows the evaluator's exit status:
on exit 2 it keeps the evaluator's last message line in `refused.txt` in the same artifact (still
failing the job), and the collector reads it from the run-integrity artifact only. Any exit 2 is `REFUSED`, whatever the
cause (a media refusal, a malformed request, or a tool's own exit 2), and the reason is the
message itself, so they read differently. The message is capped at 500 characters, ends in a full
stop before "The run is INCONCLUSIVE", and is backslash-escaped in `report.md` and HTML-escaped in
`report.html`; `report.txt` shows it as it is. Markdown escaping covers backslash, backtick,
asterisk, underscore, tilde, ampersand, brackets, and pipe, so emphasis, a labelled link, and an
HTML entity in a refusal stay plain text. Angle brackets are written `&lt;` and `&gt;`, not with a
backslash, so a tag cannot open even when it is placed straight after an address. Only the span
GitHub's Markdown will actually auto-link is left as written: `http`, `https`, `ftp`, or `www`,
in any letter case for the scheme. `_`, `&` and `~` stay in that link and in the visible address,
including a query string and a fragment. Trailing punctuation cmark removes from that link
(`?!.,:*_~'"`, an unbalanced `)`, a trailing `&name;`) is not left raw when it contains `*`, `_`
or `~`. The address is then written as an angle autolink, `<https://e.com/a>`, and the trimmed
mark is backslash-escaped (`\~`, `\*`, `\_`), so the href is the address, the character is the
ASCII mark, and it cannot strike a word such as PASSED. A `www.` address has no scheme, so it
is not wrapped; the dot is escaped and any `://` in that escaped text is written `:\/\/`, so
cmark cannot link it again. Those brackets are the only raw `<` and `>` written for a refusal,
and only around an address with no whitespace or control character. Inside them, `&` is `&amp;`.
Whitespace that ends an address is the ASCII set cmark uses (space, tab, line feed, carriage
return), not a non-breaking space. An `https://` run that is not a real link, such as
`https://*bold*`, is still escaped. `report.html` HTML-escapes the original line once, so `&` is
`&amp;` and is not escaped again. Only an artifact named
`[<prefix>-]evaluator-results-livingproof-run-integrity-<configuration>` (or its inner
`livingproof-run-integrity` directory) counts, not a lookalike name. A
`no-result.txt` note is the reason for `NOT RUN`, as before. The reporter has no integrity input,
so the workflow places the lines after rendering
(`collect_evaluator_results.py --place-integrity reports`) and before it writes `SHA256SUMS`, so
the sums cover the edited files. The reporter's own grade line is unchanged.

Run Integrity starts after `verify` and `media` (the `media` job needs `verify`), where it used to
start after `evidence`. `report` already waited for all of them, so the run's total time does not
change, and only the time the integrity result is ready moves later.

## Recordings in the scenario media job

The `scenario-media` job runs the scenario evaluator on each trial's MCAP, read through that trial's
binding file (media 2.1.0 and 2.2.0; the 2.2.0 `robot_mcap` recording is not read here, only Run Integrity
reads it). It needs the `media` job, runs only when the lock pins the scenario
evaluator by commit, and never holds up the report. The evaluator resolves every limit from the run's
`manifest.json`; the job passes none, and a limit the manifest cannot give leaves its step
`NOT_EVALUATED` (`missing_threshold`). Each result names the evaluator (id, version, and the pinned
commit with a digest of its source). Its files go to the artifact `evaluator-results-livingproof-scenario-media-<configuration>`,
which the report collects like the other evaluator results, so wall-contact steps and any
`NOT_EVALUATED` step appear on the capability report.

The job builds the evaluator at the lock's release, as `scenario-evaluator` does. `actions/checkout`
fetches the locked commit shallow and without tags, which hatch-vcs builds as `0.1.dev1`, so
`evaluator_pin --checkout evaluator` first fetches the locked tag into that checkout, after checking
it names the locked commit, and only then runs `uv sync`. Before grading, the job refuses two things
(exit 1, no results): an evaluator imported from anywhere but `evaluator/src` (a stale copy in the
verifier environment), and a build whose `importlib.metadata` version is not the release the pin step
printed ("reports ... not the locked ..."). So each result names the release, not `0.1.dev1`. The
evaluator pin is `v1.5.3` (commit `ca9d337`), which is also the verifier's own pinned dependency
(`pyproject.toml` and `uv.lock`); the media runner arrived in `v1.5.0` (commit `3fab127`),
and a pin older than that leaves a `NO-MEDIA-RESULTS.txt` note instead. A run without a world
document leaves one too. The job stops after 30 minutes. The collector leaves any result file that is not valid JSON (truncated, invalid or not
UTF-8), from any evaluator, and any media result whose `media/thresholds.json` is missing, off the
report. It names each one, with its reason, on stdout ("skipped (unusable result: ...)") and
stderr, and in `evaluator-result-problems.txt` in the report directory, where each line names the
evaluator (id and version, or "unknown" when the file cannot be read). It then carries on. A
missing citation outside `media/`, and any altered or substituted one, still fails the collection.

## Recorded hashes

Record a hash, a commit or an image digest by running the command that computes
it. Change the file, the tag or the definition, then run that command again. A
value typed by hand fails the check that compares it with the file, the tag or
the registry. `repin --check` describes a commit that is not the commit its tag
resolves to as a moved or deleted tag, or a hand-edited commit.

| Record | Written by | Mismatch |
| --- | --- | --- |
| `definition.sha256` | `lock`, from the definition file bytes | `{name} changed since it was locked; re-run lock and review` |
| `robot.lock.sha256` | `lock`, from the robot `verification.lock.json` bytes | `{repository}@{commit} lock differs from {lockname}` |
| `robot.commit` | `lock`, from the fetched ref | `robot.commit is not a full commit sha`, and the checkout uses the recorded commit |
| `releases.{name}.commit` | `repin`, from `git ls-remote`. Tags are never invented. | `releases.{name}: {repository} tag {tag} is {resolved}, not the locked {recorded}` |
| `releases.{name}.digest` | `repin`, from the registry `Docker-Content-Digest` | `releases.{name}: {image}:{version} is {published}, not the locked {recorded}` |
| `configurations[].sha256` | Compared with the file at the locked commit. This repository's `lock` command writes `0.1.0`, which has no configuration sha256 of its own. | `{repository}@{commit}: {path} sha256 is {actual}, not the locked {recorded}` |
| `overridden_robot_lock.sha256` | The evidence writer, from the robot file before replacement | `overridden robot lock sha256 must be 64 hex characters, got {digest}` |
| `world.sha256` | Optional on a `0.2.0` lock. The checker tests the spelling. | `world.sha256 must be 64 hex characters` |

File sha256 fields are 64 lowercase hexadecimal characters. Image digests add
the `sha256:` prefix. The two spellings are not interchangeable:
`configurations[].sha256` set to a `sha256:` digest fails
`.sha256 must be 64 hex characters`, and `releases.reporter.digest` set to a
bare 64-character hash fails `.digest must be a sha256 digest`.

## How the evidence step ended

The `evidence` job writes `evidence-ending.json` (`livingproof-evidence-ending/0.1`) into the
evidence artifact, beside `manifest.json`, whether the step succeeded, failed, was cancelled or
timed out. The Run Integrity job downloads that artifact, so it can tell these cases apart
even when the runner left no `manifest.json`. The JSON Schema is
`src/capability_verifier/livingproof-evidence-ending-0.1.schema.json`, and the tests check every
record the workflow writes against it. Fields:

| Field | Meaning |
| --- | --- |
| `configuration` | The configuration name of this matrix leg |
| `ended` | `exited` (the runner command returned an exit code), `signalled` (the exit code was 128+N, how a shell reports a process killed by signal N) or `unfinished` (the step's own shell never reached the end of the command) |
| `exit_code` | The runner command's exit code as the step's shell saw it, or `null` when `unfinished` |
| `signal` | The signal name for `signalled` (`KILL` for 137, `TERM` for 143), otherwise `null` |
| `step_outcome` | The workflow's outcome for the evidence step: `success`, `failure`, `cancelled` or `skipped` |
| `manifest_present` | Whether `manifest.json` was in the uploaded directory |
| `planned_trials` | `plan.trials.count` from the run's lock, or `null` when the lock has no whole-number count of zero or more |

How to read it: `exited` with `exit_code` 0 is a clean run. Exit 4 is trials that disagreed (the
manifest carries the test-system finding and the step continues). Any other non-zero exit is a
runner failure. `signalled` is a runner killed from outside, for example by the kernel's
out-of-memory killer. `unfinished` with `step_outcome` `cancelled` is a job timeout or a
cancellation, and `unfinished` with `failure` is a step whose shell died. A program that itself
exits with a code above 128 is recorded as `signalled`. A run on a verifier release before this
file has no `evidence-ending.json`, and neither does a job whose runner or virtual machine
disappears, because no later step runs.

Hosted-runner behaviour, checked with throwaway probe workflows (branch `scratch/155-probe`,
since deleted) in a container job on `ubuntu-24.04`: when the job timeout cancels the step, or
the run is cancelled, or the step's shell is killed with SIGKILL, the next `if: always()` step
still runs, sees the evidence step's outcome as `cancelled` or `failure`, and the artifact upload
after it succeeds. The probes did not run this workflow and did not write `evidence-ending.json`;
they show the `always()` and upload pattern only. The Python that writes the record is covered by
the tests, not by a hosted run.

A temp record from the evidence step that is not a complete `exited` or `signalled` object (a
missing key, a wrong type) is not used: the record says `unfinished` with a `null` exit code and
signal, and the job log shows what was there.

The `run-integrity` job runs when the evidence job failed. With no `manifest.json`, it does not
fail on the missing file: when `planned_trials` is a whole number above zero, it writes a stand-in
manifest, `no-evidence/stand-in-manifest.json`, that carries `plan.trials.count` from
`planned_trials`, no trial results, and the ending record, and runs the Run Integrity evaluator
on it. The evaluator then reports each planned trial as missing in its own words, and the
result's input is named as the stand-in.

When there is no usable planned trial count, the job has no result to write and **fails** with
an `::error::` that names the cause. It also writes
`evaluator-results/livingproof-run-integrity/no-result.txt` with the same sentence, so the
artifact upload that follows has a file to keep and the job's only failure is the named one.
`no-result.txt` is not a result document, and the report ignores it. The cases:

| Case | Message says |
| --- | --- |
| No `evidence-ending.json` in the artifact | `evidence-ending.json is missing from the evidence artifact` |
| The file cannot be read or is not JSON | `evidence-ending.json cannot be read` |
| The file is not a JSON object | `evidence-ending.json is not a JSON object` |
| No `planned_trials` key | `planned_trials is missing from evidence-ending.json` |
| `planned_trials` is `null` (the lock has no `plan.trials.count`) | `planned_trials is null` |
| `planned_trials` is not a whole number (a string, a boolean, a float) | `planned_trials is not a whole number` |
| `planned_trials` is 0 or negative | `planned_trials is 0` (or the negative value): the evaluator does not take a plan with no trials |

No stand-in is evaluated for these. The report job runs only when `verify` succeeded, which needs
the evidence job, so a run in this state is already failed and nothing downstream reads a missing
result.
