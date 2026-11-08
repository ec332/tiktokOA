"""notify: a SYNTHETIC known-answer app for iteration 3's new checks (W0 C10, DR-8). Not a real
target: it exists so each new check family can be shown to catch a seeded defect (seeds/) before
its passes on real targets count. Standard library only.

A user signs up (the first sign-up needs no token) and keeps notes. Two outbound dependencies:
creating a note POSTs it to NOTIFY_URL from a background thread, and an invite sends an email
through SMTP_HOST:SMTP_PORT. `/` is a page with labelled sign-in and note forms."""

import json
import os
import re
import secrets
import smtplib
import sys
import threading
import urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERS: dict[str, str] = {}  # username -> password
TOKENS: dict[str, str] = {}  # token -> username
NOTES: dict[int, dict] = {}
LOCK = threading.Lock()
NEXT = [1]

BEARER = {"bearer": []}
JSON = "application/json"


def _body(schema: str) -> dict:
    return {"content": {JSON: {"schema": {"$ref": f"#/components/schemas/{schema}"}}}}


def _ok(code: str, schema: str | None = None) -> dict:
    return {code: _body(schema) | {"description": code} if schema else {"description": code}}


SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "notify (synthetic fixture)", "version": "1.0.0"},
    "paths": {
        "/api/users": {"post": {"requestBody": _body("Credentials"), "responses": {
            **_ok("201", "Session"), **_ok("400", "Error"), **_ok("409", "Error")}}},
        "/api/login": {"post": {"requestBody": _body("Credentials"), "responses": {
            **_ok("200", "Session"), **_ok("401", "Error")}}},
        "/api/notes": {
            "get": {"security": [BEARER], "responses": {**_ok("200", "NoteList"),
                                                        **_ok("401", "Error")}},
            "post": {"security": [BEARER], "requestBody": _body("NoteIn"), "responses": {
                **_ok("201", "Note"), **_ok("400", "Error"), **_ok("401", "Error")}}},
        "/api/notes/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "integer"}}],
            "get": {"security": [BEARER], "responses": {**_ok("200", "Note"),
                                                        **_ok("404", "Error")}},
            "delete": {"security": [BEARER], "responses": {**_ok("204"), **_ok("404", "Error")}}},
        "/api/invite": {"post": {"security": [BEARER], "requestBody": _body("Invite"),
                                 "responses": {**_ok("202", "Sent"), **_ok("502", "Error"),
                                               **_ok("503", "Error")}}},
    },
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Credentials": {"type": "object", "required": ["username", "password"],
                            "properties": {"username": {"type": "string", "minLength": 1},
                                           "password": {"type": "string", "minLength": 1}}},
            "Session": {"type": "object", "properties": {"username": {"type": "string"},
                                                         "token": {"type": "string"}}},
            "NoteIn": {"type": "object", "required": ["text"],
                       "properties": {"text": {"type": "string", "minLength": 1}}},
            "Note": {"type": "object", "properties": {"id": {"type": "integer"},
                                                      "text": {"type": "string"},
                                                      "owner": {"type": "string"}}},
            "NoteList": {"type": "object", "properties": {
                "items": {"type": "array", "items": {"$ref": "#/components/schemas/Note"}}}},
            "Invite": {"type": "object", "required": ["email"],
                       "properties": {"email": {"type": "string", "format": "email"}}},
            "Sent": {"type": "object", "properties": {"sent": {"type": "boolean"}}},
            "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
        },
    },
}  # fmt: skip

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>notify</title></head>
<body><main>
<h1>notify</h1>
<form id="signin"><label for="username">Username</label><input id="username" name="username">
<label for="password">Password</label><input id="password" name="password" type="password">
<button type="submit">Sign in</button></form>
<form id="compose" hidden><label for="text">Note</label><textarea id="text" name="text"></textarea>
<button type="submit">Save note</button></form>
<ul id="notes" aria-label="Notes"></ul>
<p id="status" role="status"></p>
</main><script>
let token = null;
const $ = (id) => document.getElementById(id);
async function call(method, path, body) {
  const r = await fetch(path, {method, headers: {"Content-Type": "application/json",
    ...(token ? {Authorization: "Bearer " + token} : {})}, body: body && JSON.stringify(body)});
  return [r.status, r.status === 204 ? null : await r.json()];
}
async function refresh() {
  const [, list] = await call("GET", "/api/notes");
  $("notes").replaceChildren(...list.items.map((n) => Object.assign(
    document.createElement("li"), {textContent: n.text})));
}
$("signin").addEventListener("submit", async (e) => {
  e.preventDefault();
  const [s, b] = await call("POST", "/api/login", {username: $("username").value,
                                                   password: $("password").value});
  if (s !== 200) { $("status").textContent = "Sign-in failed"; return; }
  token = b.token; $("signin").hidden = true; $("compose").hidden = false;
  $("status").textContent = "Signed in as " + b.username; await refresh();
});
$("compose").addEventListener("submit", async (e) => {
  e.preventDefault();
  const [s] = await call("POST", "/api/notes", {text: $("text").value});
  $("status").textContent = s === 201 ? "Saved" : "Not saved";
  $("text").value = ""; await refresh();
});
</script></body></html>"""


def notify(note: dict) -> None:
    """Tell NOTIFY_URL about a new note. In the background, with a timeout: a slow or failing
    receiver never holds up the request that created the note."""
    if not (url := os.environ.get("NOTIFY_URL")):
        return
    payload = {"event": "note.created", "note": note}

    def send():
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": JSON})  # fmt: skip
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except OSError as e:
            print(f"notify: {url}: {e}", file=sys.stderr, flush=True)

    threading.Thread(target=send, daemon=True).start()


def invite(sender: str, email: str) -> tuple[int, dict]:
    """Email an invitation through SMTP_HOST. A refusing or unreachable mail server is a 502,
    not a crash."""
    if not (host := os.environ.get("SMTP_HOST")):
        return 503, {"error": "email is not configured"}
    msg = EmailMessage()
    msg["From"], msg["To"] = f"{sender}@notify.invalid", email
    msg["Subject"] = f"{sender} invited you to notify"
    msg.set_content(f"{sender} invited you to keep notes on notify.")
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "25")), timeout=5) as smtp:
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        return 502, {"error": f"the mail server refused: {e}"}
    return 202, {"sent": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet; the container log keeps errors only
        pass

    def reply(self, status: int, body=None, ctype: str = JSON) -> None:
        data = b"" if body is None else (body if isinstance(body, bytes) else
                                         json.dumps(body).encode())  # fmt: skip
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def json_body(self) -> dict | None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def user(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return TOKENS.get(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None

    def do_GET(self):
        if self.path == "/":
            return self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/openapi.json":
            return self.reply(200, SPEC)
        if not (who := self.user()):
            return self.reply(401, {"error": "sign in first"})
        if self.path.split("?")[0] == "/api/notes":
            with LOCK:
                mine = [n for n in NOTES.values() if n["owner"] == who]
            return self.reply(200, {"items": mine})
        if m := re.fullmatch(r"/api/notes/(\d+)", self.path):
            note = NOTES.get(int(m[1]))
            if note and note["owner"] == who:
                return self.reply(200, note)
            return self.reply(404, {"error": "no such note"})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        body = self.json_body()
        if self.path in ("/api/users", "/api/login"):
            name, pw = (body or {}).get("username"), (body or {}).get("password")
            if not (isinstance(name, str) and name and isinstance(pw, str) and pw):
                return self.reply(400, {"error": "username and password are required"})
            with LOCK:
                if self.path == "/api/users":
                    if name in USERS:
                        return self.reply(409, {"error": "that username is taken"})
                    USERS[name] = pw
                elif USERS.get(name) != pw:
                    return self.reply(401, {"error": "wrong username or password"})
                token = secrets.token_hex(16)
                TOKENS[token] = name
            return self.reply(201 if self.path == "/api/users" else 200,
                              {"username": name, "token": token})  # fmt: skip
        if not (who := self.user()):
            return self.reply(401, {"error": "sign in first"})
        if self.path == "/api/notes":
            text = (body or {}).get("text")
            if not (isinstance(text, str) and text.strip()):
                return self.reply(400, {"error": "text is required"})
            with LOCK:
                note = {"id": NEXT[0], "text": text, "owner": who}
                NOTES[NEXT[0]] = note
                NEXT[0] += 1
            notify(note)
            return self.reply(201, note)
        if self.path == "/api/invite":
            email = (body or {}).get("email")
            if not (isinstance(email, str) and "@" in email):
                return self.reply(400, {"error": "a valid email is required"})
            return self.reply(*invite(who, email))
        return self.reply(404, {"error": "not found"})

    def do_DELETE(self):
        if not (who := self.user()):
            return self.reply(401, {"error": "sign in first"})
        if m := re.fullmatch(r"/api/notes/(\d+)", self.path):
            with LOCK:
                note = NOTES.get(int(m[1]))
                if note and note["owner"] == who:
                    del NOTES[int(m[1])]
                    return self.reply(204)
            return self.reply(404, {"error": "no such note"})
        return self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"notify (synthetic fixture) on :{port}", file=sys.stderr, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
