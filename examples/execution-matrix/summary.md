| Spec | Executor | Fixture | Compatibility | Capability outcome |
| --- | --- | --- | --- | --- |
| v1.1.1 | v1.0.1 | fixtures/pass.json | COMPATIBLE | PASS |
| v1.1.1 | v1.1.0 | fixtures/slow.json | COMPATIBLE | PASS |
| v1.2.0 | v1.0.1 | fixtures/pass.json | INCOMPATIBLE | INCOMPATIBLE |
| v1.2.0 | v1.1.0 | fixtures/slow.json | COMPATIBLE | FAIL |
| v1.1.1 | v1.1.0 | fixtures/fail.json | COMPATIBLE | FAIL |
| v1.1.1 | v1.1.0 | fixtures/insufficient.json | COMPATIBLE | INSUFFICIENT_EVIDENCE |
| v1.2.0 | v1.1.0 | fixtures/fail.json | COMPATIBLE | FAIL |
| v1.2.0 | v1.1.0 | fixtures/insufficient.json | COMPATIBLE | INSUFFICIENT_EVIDENCE |
