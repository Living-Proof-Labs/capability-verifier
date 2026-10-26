# Working agreement

Read and apply https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v4.1.0/AGENTS.md
and its relevant linked procedures at that same version. Links do not load instructions.

Local additions: use `task verify` from the host, or `task -t Taskfile.runtime.yml
verify` inside the prepared runtime. Keep evidence collection, assessment and policy
separate. Preserve all provenance and negative evidence. Never claim safety certification.
Use draft PRs, then ready and merge after verification when authorized. No proprietary SDKs.

Local override: host Task delegates through Compose to a separate runtime Taskfile;
this avoids recursive wrappers and Docker-in-Docker. Python 3.12 and uv are supplied
by the Dockerfile. Project version metadata is a development placeholder; published
Git tags and source commits identify releases without generated version commits.
