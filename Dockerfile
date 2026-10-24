# SPDX-License-Identifier: Apache-2.0
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/*
# Official, pinned installer verifies the released binary checksum. No Node/npm needed.
ADD --checksum=sha256:e360c4d49273347e8ceb740f98d81d0b8b753de7d1b3c3452bdae7ec54289b99 https://raw.githubusercontent.com/go-task/task/v3.54.0/install-task.sh /tmp/install-task.sh
RUN sh /tmp/install-task.sh -b /usr/local/bin v3.54.0 && rm /tmp/install-task.sh
ARG SOURCE_COMMIT
ENV SOURCE_COMMIT=${SOURCE_COMMIT}
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never PYTHONPATH=/app/src
COPY . .
RUN task -t Taskfile.runtime.yml setup
ENTRYPOINT ["task", "-t", "Taskfile.runtime.yml"]
CMD ["demo"]
