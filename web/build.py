"""Package the Python modules the browser app runs into web/rockform.zip, then optionally serve web/.

    python web/build.py            # write web/rockform.zip
    python web/build.py --serve    # and serve http://localhost:8000

Only the hold generator and the small parts of Rockform it imports are included; the scan app's modules
(and their native dependencies) are not needed in the browser.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import zipfile

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
MODULES = ["rockform/__init__.py", "rockform/model.py"] + [
    f"rockform/holdgen/{name}.py" for name in ("__init__", "shapes", "holding", "generate", "drill", "route", "webapi")]


def build():
    target = WEB / "rockform.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in MODULES:
            archive.write(ROOT / name, name)
    print(f"wrote {target} ({target.stat().st_size // 1024} KB, {len(MODULES)} modules)")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    build()
    if args.serve:
        # Module workers need the right MIME type for .js / .mjs.
        handler = partial(SimpleHTTPRequestHandler, directory=str(WEB))
        handler.func.extensions_map.update({".js": "text/javascript", ".mjs": "text/javascript"})
        print(f"serving {WEB} at http://localhost:{args.port}")
        ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
