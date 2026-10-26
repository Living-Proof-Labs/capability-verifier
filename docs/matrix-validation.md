# Released matrix integration validation

The matrix inputs are released catalogs v1.1.1/v1.2.0 and released verifier
executors v1.0.1/v1.1.0. Their commits are locked in the repository. No new spec
semantics or future tag is assumed. Shared guidance v4.1.0 resolves to
`e409022ccbf25419749f5c073205a3fd1afb157f` (merged guidelines PR15).

Local verification on the existing saved Docker host passed 40 tests through
`task verify`, with individual cases in `artifacts/results.xml`. The container
uses Python 3.12.12 and Task 3.54.0; host Task 3.48.0 successfully delegated the
commands. This is not a claim about fresh saved-environment provisioning.

`task matrix:report` consumes the installed image
`ghcr.io/living-proof-labs/livingproof-capability-report:1.5.0` from published
[reporter v1.5.0](https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/tag/v1.5.0).
There is no reporter preview dependency or reporter source checkout in the runtime.
Both the matrix integration task and the existing native AMD64/ARM64 reporting
jobs use this released version. CI retains matrix JSON, HTML, CSV and terminal
reports for 14 days, alongside the input bundle and individual test results.

The integration verifies `all-required-v1@1.0.0` and `all-required-v2@1.0.0`, four
separate matrix decisions, four separate controls, and parsed
JSON/HTML/CSV/terminal outcome agreement. All evidence hashes remain verified,
including the incompatible executor cell. Copy the complete
`examples/execution-matrix` directory for interoperability. The sample records its
development base commit; fresh `task matrix` output records the actual current
checkout commit and a new run identifier.

The optional `provenance.compatibility` declaration is emitted only for
INCOMPATIBLE execution, as supported by reporter v1.5.0. Compatible cells carry
actual released assessments and undergo normal reporter supported-input checks.
No incompatibility is encoded as a fabricated failed assessment.

Only this adapter's publication remains pending reviewer-owned merge/release.
The catalog explanatory reference will adopt its actual published tag afterward;
no future verifier release number is assumed. No reporter/shared-guidance sources
were edited. Hosted intentional-failure probing remains unapproved and was not
retried during release adoption.

Released image verification resolved the registry manifest digest to
`sha256:bb69c56bf2588b1ff79c9b64211b456b0c628a39c50e12ce309f5850c3aa039f`;
the local AMD64 image ID is
`sha256:c05fa81878229373b16b01ef795cf3ba21bd3022e9641ddcbb3f02b7a12a8dc4`.
The installed command reported version 1.5.0 and all matrix integration assertions
passed. Dependencies remain referenced by the published version tag; the digests
are verification evidence.
