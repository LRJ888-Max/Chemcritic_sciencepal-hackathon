"""Local UI: python -m app.chemcritic.server (loopback only)."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .rules import ValidationError, review
from .sources import load_sources
from .storage import ReviewStore


ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
MAX_BODY = 2 * 1024 * 1024


def evaluate_payload(payload):
    if not isinstance(payload, dict):
        raise ValidationError("request", "expected JSON object")
    source_text = payload.get("sciencepal_text", "")
    if not isinstance(source_text, str):
        raise ValidationError("sciencepal_text", "expected the original JSON text")
    experiment = payload.get("experiment")
    sources, retrieval = load_sources(source_text, experiment)
    result = review(experiment, sources)
    result["retrieval"].update(retrieval)
    return experiment, source_text, result


def create_server(port=8765, db_path=None):
    store = ReviewStore(db_path or ROOT / "outputs" / "chemcritic.sqlite3")

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in hosts or (origin is not None and origin != f"http://{host}"):
                # Drain a bounded submitted body before closing. Otherwise Windows
                # may reset the TCP connection and hide the intended 403 response.
                if self.command == "POST":
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if 0 < size <= MAX_BODY:
                            self.connection.settimeout(3)
                            self.rfile.read(size)
                    except (ValueError, OSError):
                        pass
                self.respond(403, {"error": "仅允许本地页面访问此服务。"})
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            if path == "/api/bootstrap":
                return self.respond(200, {
                    "experiment": json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8")),
                    "sciencepal_text": (ROOT / "cases/sciencepal/SM004_raw.json").read_text(encoding="utf-8"),
                })
            if path == "/api/history":
                return self.respond(200, store.list())
            if path == "/api/library":
                return self.respond(200, json.loads((ROOT / "cases/library/suzuki_handoff_v1.json").read_text(encoding="utf-8")))
            if path.startswith("/api/history/"):
                snapshot = store.get(path.removeprefix("/api/history/"))
                return self.respond(200, snapshot) if snapshot else self.respond(404, {"error": "记录不存在。"})
            self.respond(404, {"error": "页面不存在。"})

        def do_POST(self):
            if not self.local_request():
                return
            if self.path not in {"/api/review", "/api/reviews"}:
                return self.respond(404, {"error": "接口不存在。"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.respond(415, {"error": "请发送JSON格式请求。"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    return self.respond(413, {"error": "请求不能为空且不能超过2MB。"})
                payload = json.loads(self.rfile.read(size).decode("utf-8-sig"))
                experiment, source_text, result = evaluate_payload(payload)
                if self.path == "/api/reviews":
                    # Always recompute before saving; never trust client-supplied verdicts.
                    return self.respond(201, store.save(experiment, source_text, result))
                self.respond(200, {"result": result})
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                message = {"error": "输入校验失败", "message": str(exc)}
                if isinstance(exc, ValidationError):
                    message["field"] = exc.field
                self.respond(400, message)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ChemCritic本地交互页面")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        server = create_server(args.port)
    except OSError as exc:
        parser.exit(1, f"无法启动本地服务：{exc}\n可换端口：python -m app.chemcritic.server --port 8766\n")
    print(f"ChemCritic 已启动：http://127.0.0.1:{server.server_port}\n保持终端开启；按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
