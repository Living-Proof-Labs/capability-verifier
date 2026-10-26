# Matrix validation and preview boundary

The matrix inputs are released catalogs v1.1.1/v1.2.0 and released verifier
executors v1.0.1/v1.1.0. Their commits are locked in the repository. No new spec
semantics or future tag is assumed. Shared guidance v4.1.0 resolves to
`e409022ccbf25419749f5c073205a3fd1afb157f` (merged guidelines PR15).

Local verification on the existing saved Docker host passed 40 tests through
`task verify`, with individual cases in `artifacts/results.xml`. The container
uses Python 3.12.12 and Task 3.54.0; host Task 3.48.0 successfully delegated the
commands. This is not a claim about fresh saved-environment provisioning.

Installed reporter integration uses an explicit temporary preview exception:
[reporter PR12](https://github.com/Living-Proof-Labs/livingproof-capability-report/pull/12)
at `2fd362c52789edf38621d71458bebf995d445949`. Its unchanged development target
built the wheel and locked dependency wheelhouse in a container; its unchanged
runtime target installed them without a source mount. The local package version
is 0.0.0, not an invented release. Runtime image identity:
`sha256:98b0ed28fbc0801db5efd3333e8029ded3c9c59bff9a3a7e09a3be1fe4fa5b5d`.

`REPORTER_IMAGE=matrix-reporter:preview-2fd362c task matrix:report` verifies both
`all-required-v1@1.0.0` and `all-required-v2@1.0.0`, four separate matrix decisions,
four separate controls, and parsed JSON/HTML/CSV/terminal outcome agreement.
All evidence hashes remain verified, including the incompatible executor cell.
Copy the complete `examples/execution-matrix` directory for interoperability.
The generated sample records its development base commit; fresh `task matrix`
output records the actual current checkout commit and a new run identifier.

An initial preview integration rejected explicit COMPATIBLE declarations. The
producer now follows PR12's documented optional-field convention: emit
`provenance.compatibility` only for INCOMPATIBLE execution. Compatible cells carry
actual released assessments and undergo the reporter's normal supported-input
checks. No incompatibility is encoded as a fabricated failed assessment.

The exception ends when the reviewed adapter and reporter are released and the
integration image is pinned to that published version. Existing released
reporter integration for `task bundle` remains separate. No merge/release is
authorized by this validation. No reporter or shared-guidance sources were edited.
