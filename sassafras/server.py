"""Local HTTP server for the sassafras browser GUI (standard library only)."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .backend import Session, UserError, supported_extensions

STATIC = Path(__file__).with_name("static")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
MAX_JSON = 256 * 1024
MAX_UPLOAD = 512 * 1024 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' blob: data:; frame-ancestors 'none'; base-uri 'none'")


def versions() -> dict:
    out = {}
    for name in ("sasmodels", "sasdata", "numpy"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "?"
    try:
        out["sassafras"] = importlib.metadata.version("sassafras")
    except importlib.metadata.PackageNotFoundError:
        from . import __version__
        out["sassafras"] = __version__
    return out


def make_handler(session: Session, token: str, startup: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, content, mime):
            if isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, status, payload):
            self.send(status, json.dumps(payload, allow_nan=False), "application/json; charset=utf-8")

        # ---------------------------------------------------------------- GET
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                html = (STATIC / "index.html").read_text("utf-8").replace("__TOKEN__", token)
                self.send(200, html, "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if f.parent != STATIC.resolve() or f.suffix not in STATIC_TYPES or not f.is_file():
                    self.send(404, "Not found", "text/plain")
                    return
                self.send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
            elif path == "/api/state":
                self.send_json(200, {
                    "versions": versions(),
                    "catalogue": session.catalogue.listing(),
                    "datasets": [d.summary() for d in session.datasets.values()],
                    "extensions": supported_extensions(),
                    "startup": startup,
                })
            else:
                self.send(404, "Not found", "text/plain")

        # --------------------------------------------------------------- POST
        def do_POST(self):
            if self.headers.get("X-GUI-Token") != token:
                self.send_json(403, {"error": "Invalid local GUI token; reload the page."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/upload":
                    if not 0 < size <= MAX_UPLOAD:
                        raise UserError("Upload is empty or larger than 512 MB.")
                    name = unquote(self.headers.get("X-Filename", "upload.dat"))
                    added, msgs = session.load_bytes(name, self.rfile.read(size))
                    self.send_json(200, {"added": [d.summary() for d in added], "messages": msgs})
                    return
                if not 0 <= size <= MAX_JSON:
                    raise UserError("Request too large.")
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict):
                    raise UserError("Expected a JSON object.")
                if path == "/api/stop":
                    self.send_json(200, {"stopped": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                elif path == "/api/load_path":
                    added, msgs = session.load_path(str(body.get("path", "")).strip())
                    self.send_json(200, {"added": [d.summary() for d in added], "messages": msgs})
                elif path == "/api/reload":
                    ds, msgs = session.reload(body.get("id"))
                    self.send_json(200, {"dataset": ds.summary(), "messages": msgs})
                elif path == "/api/remove":
                    session.remove(body.get("id"))
                    self.send_json(200, {"removed": body.get("id")})
                elif path == "/api/describe":
                    self.send_json(200, session.describe(body.get("model", ""),
                                                         body.get("structure_factor") or None,
                                                         body.get("controls") or {}))
                elif path == "/api/evaluate":
                    self.send_json(200, session.evaluate(body))
                elif path == "/api/match":
                    self.send_json(200, session.match_scale(body))
                else:
                    self.send_json(404, {"error": "Not found"})
            except UserError as exc:
                self.send_json(400, {"error": str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                self.send_json(400, {"error": f"Bad request: {exc}"})
            except Exception as exc:  # keep the server alive, report the cause
                self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sassafras",
        description="Rapid SANS/SAXS data viewer with live sasmodels overlays (chi-by-eye).")
    parser.add_argument("files", nargs="*", help="data files or folders to load at start-up")
    parser.add_argument("-m", "--model", default=None,
                        help="initial form factor (default: last used in this browser, else sphere)")
    parser.add_argument("-s", "--sq", default="", help="initial structure factor, e.g. hardsphere")
    parser.add_argument("--port", type=int, default=0, help="local port (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    parser.add_argument("--check", action="store_true",
                        help="load files, compile and evaluate the initial model, print a summary and exit")
    args = parser.parse_args(argv)

    try:
        session = Session()
    except ImportError as exc:
        raise SystemExit(f"Missing dependency: {exc}\nInstall with: pip install sasmodels sasdata numpy")

    messages = []
    for f in args.files:
        try:
            added, msgs = session.load_path(f)
            messages += msgs
            print(f"Loaded {f}: {len(added)} dataset(s)")
        except UserError as exc:
            messages.append(str(exc))
            print(f"Could not load {f}: {exc}", file=sys.stderr)
    startup = {"model": args.model, "structure_factor": args.sq, "messages": messages}

    if args.check:
        model = args.model or "sphere"
        try:
            desc = session.describe(model, args.sq or None)
        except UserError as exc:
            raise SystemExit(str(exc))
        params = {p["name"]: p["default"] for p in desc["parameters"]}
        ds = next(iter(session.datasets), None)
        out = session.evaluate({"model": model, "structure_factor": args.sq or None,
                                "params": params, "dataset": ds})
        print(json.dumps({"model": out["model"], "compile_ms": round(desc["compile_ms"], 1),
                          "evaluate_ms": round(out["elapsed_ms"], 2), "points": len(out["q"]),
                          "dataset": ds and session.datasets[ds].label,
                          "chisq_per_point_at_defaults": out.get("chisq"),
                          "warnings": out["warnings"], "messages": messages,
                          "versions": versions()}, indent=2))
        return

    token = secrets.token_hex(24)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(session, token, startup))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"\nsassafras is running at {url}")
    print("Everything runs locally. Stop with Ctrl+C or the Stop button in the page.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\nsassafras stopped.")


if __name__ == "__main__":
    main()
