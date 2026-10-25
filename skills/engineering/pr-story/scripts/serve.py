#!/usr/bin/env python3
"""Serve ~/.cache/pr-story with caching off, plus a small local API the story pages use to read
the PR's review comments and to put drafted comments into a pending GitHub review.

    serve.py [--port 8765] [--dry-run]

Only pages this server hands out can call the API: each page gets a per-run token, and requests
must come from this origin. A comment added on the page goes straight into the reader's pending
review on GitHub, which nobody else sees until they submit it there. `--dry-run` keeps a fake
pending review in memory instead, so the page can be tried without writing to GitHub.
"""
import argparse
import functools
import json
import re
import secrets
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github  # noqa: E402

ROOT = Path("~/.cache/pr-story").expanduser()
TOKEN = secrets.token_urlsafe(24)
API = re.compile(r"^/api/([\w.-]+)/(comments|review|comment|comment/edit|comment/delete)$")
# --dry-run's stand-in for GitHub's pending reviews: story directory -> comments.
FAKE_PENDING = {}


class Handler(SimpleHTTPRequestHandler):
    dry_run = False
    port = 8765

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.api("GET")
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path.endswith("/"):
            path += "index.html"
        if path.endswith(".html"):
            file = (ROOT / path.lstrip("/")).resolve()
            if ROOT.resolve() in file.parents and file.is_file():
                body = file.read_text().replace("__SERVE_TOKEN__", TOKEN).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.api("POST")
        self.reply(404, {"error": "not found"})

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api(self, method):
        allowed = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        if self.headers.get("X-Story-Token") != TOKEN:
            return self.reply(403, {"error": "missing or wrong token; reload the page from this server"})
        if method == "POST" and self.headers.get("Origin") not in allowed:
            return self.reply(403, {"error": "wrong origin"})
        match = API.match(self.path)
        if not match:
            return self.reply(404, {"error": "not found"})
        story_dir, action = match.groups()
        collected_file = ROOT / story_dir / "collected.json"
        if not collected_file.is_file():
            return self.reply(404, {"error": f"no collected.json for {story_dir}"})
        collected = json.loads(collected_file.read_text())
        number, repo = collected["pr"].get("number"), collected.get("repo")
        if not number or not repo:
            return self.reply(400, {"error": "this story is for a local branch, not a PR"})
        if method == "POST" and "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.reply(415, {"error": "send JSON"})
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}") if method == "POST" else {}
        fake = FAKE_PENDING.setdefault(story_dir, [])
        try:
            if action == "comments" and method == "GET":
                return self.reply(200, {"comments": github.fetch_comments(repo, number) + (fake if self.dry_run else [])})
            if action == "comment" and method == "POST":
                comment = {k: request.get(k) for k in ("path", "line", "side", "startLine", "startSide", "body")}
                if not comment["path"] or not comment["line"] or not (comment["body"] or "").strip():
                    return self.reply(400, {"error": "a comment needs a path, a line and some text"})
                if self.dry_run:
                    fake_id = 900000000 + len(fake)
                    fake.append({**comment, "id": fake_id, "nodeId": f"fake-{fake_id}", "replyTo": None, "user": "you (dry run)",
                                 "created": None, "url": None, "pending": True})
                    print("dry run, would add to pending review:", json.dumps(comment), flush=True)
                else:
                    github.add_pending_comment(repo, number, collected["head"], comment)
                return self.reply(200, {"ok": True})
            if action in ("comment/edit", "comment/delete") and method == "POST":
                node_id = request.get("nodeId")
                if not node_id:
                    return self.reply(400, {"error": "which comment?"})
                if self.dry_run:
                    match = next((c for c in fake if c["nodeId"] == node_id), None)
                    if not match:
                        return self.reply(404, {"error": "no such dry-run comment"})
                    if action == "comment/edit":
                        match["body"] = request.get("body", "")
                    else:
                        fake.remove(match)
                    print(f"dry run, would {action.split('/')[1]} {node_id}", flush=True)
                elif action == "comment/edit":
                    github.update_pending_comment(node_id, request.get("body", ""))
                else:
                    github.delete_pending_comment(repo, number, node_id)
                return self.reply(200, {"ok": True})
            if action == "review" and method == "POST":
                comments = request.get("comments") or []
                if not comments:
                    return self.reply(400, {"error": "no comments to send"})
                result = github.create_pending_review(repo, number, collected["head"], request.get("body", ""),
                                                      comments, dry_run=self.dry_run)
                if self.dry_run:
                    print("dry run, would create pending review:", json.dumps(result["payload"]), flush=True)
                return self.reply(200, result)
        except RuntimeError as error:
            return self.reply(502, {"error": str(error)})
        return self.reply(405, {"error": "method not allowed"})


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
ROOT.mkdir(parents=True, exist_ok=True)
Handler.dry_run, Handler.port = args.dry_run, args.port
handler = functools.partial(Handler, directory=str(ROOT))
mode = " (dry run: comments go to a fake pending review in memory, not GitHub)" if args.dry_run else ""
print(f"Serving {ROOT} at http://127.0.0.1:{args.port}/{mode}", flush=True)
ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
