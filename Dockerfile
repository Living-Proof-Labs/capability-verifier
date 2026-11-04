# SPDX-License-Identifier: Apache-2.0
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12.12-slim-bookworm AS runtime
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/*
# Official, pinned installer verifies the released binary checksum. No Node/npm needed.
ADD --checksum=sha256:e360c4d49273347e8ceb740f98d81d0b8b753de7d1b3c3452bdae7ec54289b99 https://raw.githubusercontent.com/go-task/task/v3.54.0/install-task.sh /tmp/install-task.sh
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export CURL_CA_BUNDLE=/run/secrets/ca; fi; \
    sh /tmp/install-task.sh -b /usr/local/bin v3.54.0 && rm /tmp/install-task.sh
ARG SOURCE_COMMIT
ENV SOURCE_COMMIT=${SOURCE_COMMIT}
ARG LIVINGPROOF_VERIFIER_VERSION
ENV LIVINGPROOF_VERIFIER_VERSION=${LIVINGPROOF_VERIFIER_VERSION}
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never PYTHONPATH=/app/src
COPY . .
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca GIT_SSL_CAINFO=/run/secrets/ca; fi; \
    task -t Taskfile.runtime.yml setup
ENTRYPOINT ["task", "-t", "Taskfile.runtime.yml"]
CMD ["demo"]

# Browser tooling is isolated from the existing fixture runtime.
FROM runtime AS documentation
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN uv run --locked --no-sync playwright install --with-deps chromium

# Hallway demo: one image for `docker run` locally, the devcontainer and walk-hallway.yml's
# evidence, verify and render jobs. The *_COMMIT defaults are the refs that
# livingproof-verification's verification lock names (robot, verifier, reporter); their uv.lock
# files are synced here so a run only links from the warm cache. Bump them with that lock.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv-hallway
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS hallway
COPY --from=uv-hallway /uv /uvx /usr/local/bin/
# APT_MIRROR replaces deb.debian.org (e.g. https://cloudfront.debian.net behind a proxy that
# breaks deb.debian.org). Failed index downloads stop the build.
ARG APT_MIRROR=https://deb.debian.org
RUN set -eu; \
    sed -i -E "s#^URIs: https?://deb\.debian\.org/#URIs: ${APT_MIRROR%/}/#" /etc/apt/sources.list.d/debian.sources; \
    apt-get -o APT::Update::Error-Mode=any update; \
    apt-get install -y --no-install-recommends ca-certificates git jq libosmesa6; \
    rm -rf /var/lib/apt/lists/*
ARG LIVINGPROOF_REF=v1.4.9
ARG ROBOT_REPOSITORY=Living-Proof-Labs/livingproof-unitree-g1
ARG ROBOT_COMMIT=4de6de66fbfdfdcd438f2f5bf77431125dcbce54
ARG VERIFIER_COMMIT=58388d3c5f970e11deae940c26d8558abb36314e
ARG REPORTER_COMMIT=f084f252de3e5af91a8099eacfd805dec291e9ec
# The build fails unless these defaults are what the G1 hallway lock at
# livingproof-verification VERIFICATION_LOCK_COMMIT names (robot.commit and
# releases.verifier/reporter.commit): relock there, then bump all of them here together.
ARG VERIFICATION_LOCK_COMMIT=1d5e9c2d872b9701ea2c80829cd8a45436996381
ARG VERIFICATION_LOCK_URL=https://raw.githubusercontent.com/Living-Proof-Labs/livingproof-verification/${VERIFICATION_LOCK_COMMIT}/verifications/unitree-g1-stock-mujoco-hallway.lock.json
ENV UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/opt/uv-cache UV_LINK_MODE=copy \
    MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \
    PATH=/opt/lpl/bin:$PATH
COPY pyproject.toml uv.lock /tmp/cv/
COPY src /tmp/cv/src
RUN set -eu; \
    uv venv -q /opt/lpl; \
    VIRTUAL_ENV=/opt/lpl uv pip install --no-cache -q \
      "livingproof @ git+https://github.com/Living-Proof-Labs/livingproof@${LIVINGPROOF_REF}" /tmp/cv; \
    rm -rf /tmp/cv; \
    python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=60).read())' \
      "$VERIFICATION_LOCK_URL" > /tmp/lock.json; \
    jq -e --arg robot "$ROBOT_REPOSITORY" --arg rc "$ROBOT_COMMIT" --arg vc "$VERIFIER_COMMIT" --arg pc "$REPORTER_COMMIT" \
      '.robot.repository == $robot and .robot.commit == $rc and .releases.verifier.commit == $vc and .releases.reporter.commit == $pc' \
      /tmp/lock.json >/dev/null \
      || { echo "pre-synced refs differ from $VERIFICATION_LOCK_URL:" >&2; \
           jq '{robot: .robot.commit, verifier: .releases.verifier.commit, reporter: .releases.reporter.commit}' /tmp/lock.json >&2; exit 1; }; \
    rm /tmp/lock.json; \
    presync() { \
      d="$(mktemp -d)"; git -C "$d" init -q; \
      git -C "$d" fetch -q --depth 1 "https://github.com/$1.git" "$2"; \
      git -C "$d" checkout -q FETCH_HEAD; \
      (cd "$d" && uv sync --locked -q); rm -rf "$d"; \
    }; \
    presync "$ROBOT_REPOSITORY" "$ROBOT_COMMIT"; \
    presync Living-Proof-Labs/capability-verifier "$VERIFIER_COMMIT"; \
    presync Living-Proof-Labs/livingproof-capability-report "$REPORTER_COMMIT"; \
    uv run --no-project -q --with packaging==26.3 -- true; \
    chmod -R a+rwX /opt/uv-cache; \
    useradd --uid 1000 --create-home --shell /bin/bash lpl; \
    mkdir -p /work && chown lpl:lpl /work
# A `-u $(id -u):$(id -g)` run may have no passwd entry; git needs a writable HOME and
# must trust the bind-mounted checkout whoever owns it.
ENV HOME=/tmp GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=*
USER lpl
WORKDIR /work
ENTRYPOINT ["livingproof"]
CMD ["--help"]

# Plain docker build keeps the original runtime as its default output.
FROM runtime AS default
