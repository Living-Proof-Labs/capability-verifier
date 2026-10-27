# SPDX-License-Identifier: Apache-2.0
"""Check the documented preview path against a downloaded, extracted CI artifact."""

import functools
import http.server
import json
import re
import threading
import urllib.request
from pathlib import Path

root = Path("artifacts/preview-extracted").resolve()
guide = (root / "docs/source/docs/documentation.md").read_text()
match = re.search(r"python -m http.server 8000 --bind 127.0.0.1 --directory ([^`\s]+)", guide)
assert match, "Preview command missing from the shipped guide"
directory = match.group(1)
assert directory == "docs/html", f"Unexpected documented artifact path: {directory}"
site = root / directory
assert (site / "index.html").is_file()
assert (site / "assets").is_dir()
info = json.loads((site / "build.json").read_text())
assert ".github/PAGES.md" in info["sources"]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=10) as response:
        assert response.status == 200
        assert b"Capability verifier" in response.read()
finally:
    server.shutdown()
    thread.join()
    server.server_close()
print(f"PASS: downloaded artifact serves {directory}/index.html; source {info['commit']}")
