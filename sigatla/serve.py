"""A small local server: the built site, plus Q&A endpoints that call Claude with your own credentials.

GET  /api/health  -> {"ok": bool, "model": str}   (ok is false without credentials; the page then offers "copy prompt")
POST /api/ask     -> {"system": str, "messages": [...]} ; streams the answer as plain text
GET  /api/qa      -> saved answers (notes/_qa.json)
POST /api/qa      -> append one saved answer

The server listens on 127.0.0.1 only, so the credentials are never exposed to other machines.
"""
from __future__ import annotations

import json
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .claude import ask_stream, has_credentials
from .project import Project, read_json, write_json

MAX_BODY = 600_000


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    project: Project = None  # set by serve()

    def log_message(self, fmt, *args):  # quieter than the default
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    # ---- helpers
    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        if data:
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

    # ---- routes
    def do_GET(self):
        if self.path.split("?")[0] == "/api/health":
            return self._json(200, {"ok": has_credentials(), "model": self.project.model})
        if self.path.split("?")[0] == "/api/qa":
            return self._json(200, read_json(self.project.qa_path, []) or [])
        return super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        payload = self._body()
        if payload is None:
            return self._json(400, {"error": "expected a JSON body"})
        if route == "/api/qa":
            item = {k: payload.get(k) for k in ("node", "link", "path", "q", "a", "source") if payload.get(k)}
            if not item.get("node") or not item.get("q") or not item.get("a"):
                return self._json(400, {"error": "node, q and a are required"})
            item["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            items = read_json(self.project.qa_path, []) or []
            items.append(item)
            write_json(self.project.qa_path, items)
            return self._json(200, item)
        if route == "/api/ask":
            system = str(payload.get("system") or "")[:200_000]
            messages = [{"role": m.get("role"), "content": str(m.get("content") or "")}
                        for m in payload.get("messages") or [] if m.get("role") in ("user", "assistant")]
            if not messages or messages[-1]["role"] != "user":
                return self._json(400, {"error": "messages must end with a user message"})
            if not has_credentials():
                return self._json(503, {"error": "no Claude API credentials on this machine"})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for text in ask_stream(self.project.model, system, messages):
                    self._chunk(text)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:  # report inside the stream; headers are already sent
                try:
                    self._chunk(f"\n\n*(Error: {e.__class__.__name__}: {e})*")
                except OSError:
                    return
            self.wfile.write(b"0\r\n\r\n")
            return
        return self._json(404, {"error": "unknown endpoint"})


def serve(project: Project, port: int = 8000, log=print) -> None:
    site = project.site_dir
    if not (site / "index.html").exists():
        from .build import build_site
        build_site(project, log=log)
    Handler.project = project
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(site)))
    mode = "answers through the Claude API" if has_credentials() else \
        "no API credentials found, so the page offers 'copy prompt' instead of answering"
    log(f"Serving {site} at http://127.0.0.1:{port}/ ({mode}). Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        httpd.server_close()
