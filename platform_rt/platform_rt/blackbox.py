"""Black-box test support (blackbox D1, D8): `make` for targets reached only over HTTP, and
`eventually` for read-backs of asynchronously processed writes. Loaded with `-p platform_rt.blackbox`
in place of a target's own in-process harness."""

import json
import os
import time

import httpx
import pytest

from platform_rt import record
from platform_rt.api import api, route_matches, token


class Make:
    def admin(self) -> dict:
        """The admin principal the platform provisioned: `{token}` (sent as $TP_AUTH_SCHEME)."""
        return {"token": token()}

    def name(self, label: str = "x") -> str:
        return record.current().name(label)

    def pdf(self, text: str = "tp test document") -> bytes:
        """A valid one-page PDF showing `text` (for upload endpoints)."""
        return pdf(text)


def pdf(text: str) -> bytes:
    safe = text.replace("\\", "").replace("(", "").replace(")", "")
    stream = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
             b"/Resources << /Font << /F1 5 0 R >> >> >>"),
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]  # fmt: skip
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@pytest.fixture
def make():
    return Make()


def delete_path(spec: dict, entry: dict) -> str | None:
    """The DELETE for a created resource: its Location, or `<create path><id>[/]`, if the spec
    has a DELETE operation of that shape."""
    paths = [p for p, ops in spec.get("paths", {}).items() if "delete" in ops]
    cands = [httpx.URL(entry["location"]).path] if entry.get("location") else []
    if entry.get("id") is not None:
        stem = entry["path"].rstrip("/") + f"/{str(entry['id']).split('/')[-1]}"  # names: last part
        cands += [stem + "/", stem]
    return next((c for c in cands for p in paths if route_matches(f"DELETE {p}", "DELETE", c)),
                None)  # fmt: skip


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item):
    """Delete what this test created, newest first (blackbox/006): names fixed by case data
    can't be made unique, so the next test must not find them. Unrecorded, best effort; the
    platform's cleanup after the batch catches anything left."""
    rec = record.current()
    if not rec.created or not os.environ.get("TP_SECRET_TOKEN"):
        return
    from platform_rt.plugin import _openapi

    spec = _openapi() or {}
    auth = {"Authorization": f"{os.environ.get('TP_AUTH_SCHEME', 'Bearer')} {token()}"}
    done = []
    for e in reversed(rec.created):
        if target := delete_path(spec, e):
            try:
                status = api.client.delete(target, headers=auth).status_code
            except httpx.HTTPError:
                continue
            if status in (200, 202, 204, 404):
                done.append(e)
    if (path := os.environ.get("TP_LEDGER")) and done:
        with open(path, "a") as f:
            f.writelines(json.dumps(e | {"deleted_by_test": True}) + "\n" for e in done)


def eventually(fn, until=bool, timeout: float | None = None):
    """Call `fn` until `until(result)` holds or `timeout` (default $TP_READ_BACK_TIMEOUT, 5 s)
    passes; returns the last result. Retries are recorded so the gate can tell eventual
    consistency from flakiness."""
    timeout = float(os.environ.get("TP_READ_BACK_TIMEOUT", "5")) if timeout is None else timeout
    t0, wait, rec = time.monotonic(), 0.1, record.current()
    while True:
        result = fn()
        try:
            done = until(result)
        except Exception:  # noqa: BLE001 — a body not yet in its final shape is "not yet"
            done = False
        if done or time.monotonic() - t0 + wait > timeout:
            rec.retries["waited_s"] = round(rec.retries["waited_s"] + time.monotonic() - t0, 2)
            return result
        rec.retries["attempts"] += 1
        time.sleep(wait)
        wait = min(wait * 2, 1.0)
