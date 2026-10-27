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
