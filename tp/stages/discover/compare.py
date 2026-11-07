"""Field-by-field comparison of a discovered profile with a hand-written one (discover/001 D14,
002 P6). Sources, layers and evidence are not compared: the reference is all `override`."""


def _secret(v) -> bool:
    return isinstance(v, dict) and "secret" in v


def _same_env(a: dict, b: dict) -> bool:
    """Same keys; secret refs match secret refs (names may differ), literals match literals
    (user names may differ)."""
    return set(a) == set(b) and all(_secret(a[k]) == _secret(b[k]) for k in a)


def _spec_path(p: str) -> str:
    return p.replace("?format=json", "")  # YAML and JSON specs count as the same


def diff(found: dict, ref: dict) -> list[str]:
    out = []

    def eq(label, a, b):
        if a != b:
            out.append(f"{label}: discovered {a!r}, reference {b!r}")

    for k in ("name", "repo"):
        eq(f"target.{k}", found["target"].get(k), ref["target"].get(k))
    eq("commit_tested", found["commit_tested"], ref["commit_tested"])
    fr, rr = found["reach"]["value"], ref["reach"]["value"]
    for k in sorted((set(fr) | set(rr)) - {"env"}):
        eq(f"reach.{k}", fr.get(k), rr.get(k))
    if not _same_env(fr.get("env") or {}, rr.get("env") or {}):
        out.append(f"reach.env: discovered {sorted(fr.get('env') or {})}, "
                   f"reference {sorted(rr.get('env') or {})}")  # fmt: skip
    fs, rs = found["spec"]["value"], ref["spec"]["value"]
    eq("spec.strategy", fs["strategy"], rs["strategy"])
    eq("spec.base", fs.get("base"), rs.get("base"))
    eq("spec.openapi", _spec_path(fs.get("openapi", "")), _spec_path(rs.get("openapi", "")))
    fa, ra = found["auth"]["value"], ref["auth"]["value"]
    for k in sorted((set(fa) | set(ra)) - {"login", "check", "user"}):
        eq(f"auth.{k}", fa.get(k), ra.get(k))
    fl, rl = fa.get("login") or {}, ra.get("login") or {}
    for k in sorted((set(fl) | set(rl)) - {"body"}):
        eq(f"auth.login.{k}", fl.get(k), rl.get(k))
    if not _same_env(fl.get("body") or {}, rl.get("body") or {}):
        eq("auth.login.body keys", sorted(fl.get("body") or {}), sorted(rl.get("body") or {}))
    # auth.check: any secured parameterless GET (P6c); it was verified by the auth probe
    for c in ("data", "state", "mutation", "safety", "checks"):
        a = {k: v for k, v in found[c]["value"].items() if k != "reason"}
        b = {k: v for k, v in ref[c]["value"].items() if k != "reason"}
        for k in sorted(set(a) | set(b)):
            eq(f"{c}.{k}", a.get(k), b.get(k))
    return out
