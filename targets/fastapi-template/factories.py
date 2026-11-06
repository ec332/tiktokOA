"""Deterministic test-data factories over the SUT's HTTP API (plan 3.2 FixtureRegistry, E5).

Same tc_id on a freshly reset DB -> the same data (ids are server-generated, so they differ).
Every entity is read back after creation. Standard library only: the in-process harness loads
this file inside the runner image (env design D15), where `tp` is not installed.
"""

import hashlib
import time
from collections.abc import Callable

READ_BACK_WAIT = 1.0  # seconds; covers BUG_FLAKE_RACE's late commit (0.3s max)


class FactoryError(Exception):
    pass


# (method, path under the API base, token=, json_body=, form=) -> (status, body)
Http = Callable[..., tuple[int, object]]


class Factories:
    NAMES = ("user", "superuser", "item", "inactive_user")  # the registry (cases design D6)

    def __init__(self, http: Http, superuser: tuple[str, str], tc_id: str):
        self.http, self.su = http, superuser
        self.seed = hashlib.sha256(tc_id.encode()).hexdigest()[:10]
        self.n = 0
        self._su_token: str | None = None

    def _key(self) -> str:
        self.n += 1
        return f"{self.seed}-{self.n}"

    def _login(self, email: str, password: str) -> str:
        status, body = self.http(
            "POST", "/login/access-token", form={"username": email, "password": password}
        )
        if status != 200:
            raise FactoryError(f"login as {email} failed: {status} {body}")
        return body["access_token"]

    def _read_back(self, path: str, token: str, expect: dict) -> dict:
        deadline = time.monotonic() + READ_BACK_WAIT
        while True:
            status, body = self.http("GET", path, token=token)
            if status == 200 and all(body.get(k) == v for k, v in expect.items()):
                return body
            if time.monotonic() > deadline:
                raise FactoryError(f"read-back of {path} failed: {status} {body}")
            time.sleep(0.1)

    # ---- factories -----------------------------------------------------------

    def superuser(self) -> dict:
        if self._su_token is None:
            self._su_token = self._login(*self.su)
        return {"email": self.su[0], "token": self._su_token}

    def user(self, active: bool = True) -> dict:
        k = self._key()
        email = f"u-{k}@example.com"
        password = "pw-" + hashlib.sha256(k.encode()).hexdigest()[:20]
        full_name = f"User {k}"
        assert 8 <= len(password) <= 128 and len(email) <= 255  # the SUT's own rules
        status, body = self.http(
            "POST", "/private/users/",
            json_body={"email": email, "password": password, "full_name": full_name},
        )  # fmt: skip
        if status != 200:
            raise FactoryError(f"create user failed: {status} {body}")
        su = self.superuser()["token"]
        uid = body["id"]
        if not active:
            status, body = self.http(
                "PATCH", f"/users/{uid}", token=su, json_body={"is_active": False}
            )
            if status != 200:
                raise FactoryError(f"deactivate user failed: {status} {body}")
        self._read_back(
            f"/users/{uid}", su, {"email": email, "full_name": full_name, "is_active": active}
        )
        user = {"id": uid, "email": email, "password": password, "full_name": full_name}
        user["is_active"] = active
        user["token"] = self._login(email, password) if active else None
        return user

    def inactive_user(self) -> dict:
        return self.user(active=False)

    def item(self, owner: dict | None = None) -> dict:
        owner = owner or self.user()
        k = self._key()
        data = {"title": f"Item {k}", "description": f"Description {k}"}
        status, body = self.http("POST", "/items/", token=owner["token"], json_body=data)
        if status != 200:
            raise FactoryError(f"create item failed: {status} {body}")
        self._read_back(f"/items/{body['id']}", owner["token"], data)
        return {"id": body["id"], "owner_id": body["owner_id"], **data}
