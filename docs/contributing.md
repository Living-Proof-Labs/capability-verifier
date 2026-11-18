# Contribute and get help

Use the [issue tracker](https://github.com/Living-Proof-Labs/capability-verifier/issues)
for reproducible bugs, questions and proposed changes. Include the source revision,
command, expected result and observed result. This is the existing project help
route, not a support-service guarantee.

Read the explicitly adopted
[collaboration-guidelines v6.0.0](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v6.0.0/AGENTS.md)
and this repository's [local additions](../AGENTS.md). The released
[documentation recipe](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v6.0.0/docs/documentation-recipe.md)
organizes this manual; shared guidance is linked rather than copied. Current manual
and directory links are navigation exceptions, not moving dependency pins.

Use `task setup`, `task check`, `task test` and `task verify` from the host. Inside
the prepared runtime use `task -t Taskfile.runtime.yml` with the same task name.
[Generated Task reference](../tasks.md) records actual commands and execution contexts.
Open a focused pull request with verification evidence, and follow the released
[independent review procedure](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v6.0.0/docs/independent-review.md).

Keep evidence collection, assessment and grading policy separate. Preserve negative
and insufficient evidence, provenance, exact fixture/spec/executor pins and the
existing runtime behavior. Passing expectation tests is not robot certification.
See [verification and preview reproduction](documentation.md) and
[license scopes](license.md).

## Pins Renovate does not update on its own

`renovate.json` disables two pins with `enabled: false`. Each rule names both the
file path and the package name, so the same package in another file, and other
packages in these files, still receive updates. The shared organization preset
is unchanged.

`livingproof-sim-mujoco` in `tests/fixtures/repin/pyproject.toml` is a test
fixture coupled to `tests/fixtures/repin/verification.lock.json`. It is not a
runtime dependency. Updating the tag alone fails the repin tests. Renovate
resolves that git source to package name `Living-Proof-Labs/livingproof-sim-mujoco`
and keeps the dependency name `livingproof-sim-mujoco`, so the rule matches
both, with the fixture file.

`ghcr.io/living-proof-labs/livingproof-capability-report` at 1.6.0 in
`.github/workflows/verify.yml` is the legacy demonstration image.
`tools/check_demo.py` requires reporter 1.6.0. Workflows do not reference that
image at another version: the hallway workflow builds its reporter image from a
verification lock. The rule therefore matches this file and package only.

Move either pin later in a deliberate migration pull request that updates the
fixture lock, or the `check_demo` guard, together with the pin.

## Development-container regression check

From a host with Docker, Node and Task, run `task devcontainer:verify`. This uses
pinned Development Containers tooling to build `.devcontainer/devcontainer.json`,
run its post-create setup and execute the runtime verification tasks inside the
container. The same check gates releases. It tests the container configuration,
not GitHub Codespaces account provisioning or the hosted editor connection.

The Task installer is downloaded with curl and its pinned SHA-256 is checked
before execution. This avoids depending on a builder's support for remote
`ADD --checksum`; a download or checksum failure stops the image build.

The development target adds a non-root `developer` user to the same runtime.
Development Containers matches that user's identifier to the host workspace owner,
so Git can read its history without a trust exception. Development runs explicitly
identify their producer as `dev`, never as a released verifier version.

### Development self-image refreshes

The hallway development container pins this repository's own released image by
version and digest. Releasing another product version solely because that pin
changed would create a release feedback loop. As a narrow local exception to
maintenance releases, `task release:classify` examines every first-parent change
since the highest reachable stable release tag. Only changes to the `image`
value in `.devcontainer/hallway/devcontainer.json`, retaining the same image
repository and explicit version plus SHA-256 digest, suppress release creation.
Other development-container changes, other maintenance work, and mixed runtime
fixes or features retain the shared semantic-release policy. Missing or shallow
release history never suppresses a release. Git history-query errors fail the
classification step rather than silently skipping a release. An unreadable or
invalid self-image configuration conservatively retains normal release policy.

These tooling refreshes remain recorded in their pull requests and Git history;
tags and old evidence are never rewritten. Normal verification still runs. The
classification only gates product release creation and its dependent image
publication, so a self-pin refresh cannot alone create the next image or a new
downstream release to adopt. The next substantive release naturally includes the
tooling refresh in its history. The classifier needs Git and Python 3; CI uses its
full-history checkout and the hosted runner's Python 3. Tests use disposable Git
histories, including mixed changes and a runtime change reverted before the pin.
