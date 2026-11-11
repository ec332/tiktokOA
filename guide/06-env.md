# Stage 6: Env (technical reference)

[← Guide](../GUIDE.md) · previous: [Gate](05-gate.md) · next: [Execute](07-execute.md)

| | |
|---|---|
| Module | `tp/stages/env/stage.py` (`EnvStage`). The work is done by the reach strategy: `strategies.py` + `service.py` (template), `blackbox.py` (discovered and live targets) |
| Version | `1.3.0` |
| Consumes | nothing (the target comes from the run's profile) |
| Produces | `env_spec` |
| Model | none |
| Person | none |
| Events | `env_up` |

## Purpose

This stage brings up the system under test, isolated, as the `reference` variant, and records a **fingerprint** of exactly what is running. Later stages bring other variants up themselves through `ctx.strategies.reach`:
- `bug:<FLAG>[+FLAG]`: seeded defects;
- `fixed:<patch>`: a fix to retest.

The stage itself is a single call: `ctx.strategies.reach.provision("reference")`.

## The reach strategies

`tp/core/strategies.py` resolves them from the profile's `reach.strategy`:

| Profile | Reach | Spec | Auth | State | Data | Mutation |
|---|---|---|---|---|---|---|
| Template (`targets/fastapi-template`) | `ContainerReach` | `ServedSpec` | `OverrideAuth` | `TransactionState` (DB from a template) | `OverrideData` (factories) | `InProcessMutation` |
| Discovered (`container`) | `blackbox.Reach` | `blackbox.Spec` | `TokenHeaderAuth` / `BasicAuth` | `NamespaceState` | `SpecDerivedData` | `NoMutation` |
| Live (`external`) | `ExternalReach` | `blackbox.Spec` | supplied token | `NamespaceState` | `SpecDerivedData` | `NoMutation` |

### Template bring-up (`service.Stack.up`)

1. Verify the checkout (`sut.verify`), write the compose override, then `down` to clear any leftovers.
2. Build the backend image once per patch set. Variants are `reference`, `bug:FLAGS` (env flags) and `fixed:<patch>`.
3. Start the DB. Run migrations and the seed. `CREATE DATABASE app_seed_<hash> TEMPLATE app`, so every test can get a fresh copy (`reset()` takes milliseconds).
4. Start the app and any extra services with `--wait`.
5. Run the probes. If any fails: `EnvError: probes failed after bring-up`.
6. Write the fingerprint to `env.lock.json`. `expect=<hash>` makes a bring-up fail if anything differs.

### Black-box bring-up (`blackbox.Stack`)

The repo is cloned at the profile's commit. Its compose file is **rewritten** (`rewrite()`) using `config/sandbox.yaml`:

| Rule | Effect |
|---|---|
| `drop` | `ports`, `restart`, `container_name`, `network_mode`, `privileged`, `cap_add`, `devices`, `pid`, `ipc`, `networks`, `deploy`, `extra_hosts`, `logging` |
| `keep` | `image`, `build`, `command`, `entrypoint`, `environment`, `depends_on`, `healthcheck`, `working_dir`, `user`, `expose`, … |
| Any other key | **Fails closed**: `unsupported keys` |
| `build` | Only `context`, `dockerfile`, `args`, `target`, `labels`. A context outside the repo fails |
| Network | Every service joins `run: {internal: true}`, which has no route out |
| Forwarder | `alpine/socat:1.8.0.3` joins `run` and `edge`, published only on `127.0.0.1::8000`. It is the one way in |
| Limits | `cpus: "2"`, `mem_limit: 2g` for each service |
| Images | Built at the commit, or retagged from a floating tag to the ref |
| Binds, volumes | Binds are moved under the run folder. Named volumes are scoped to the project |
| Secrets | Passed as `${TP_SECRET_*}` references. The value never appears in the file |

A repo with only a Dockerfile is wrapped as a one-service compose. Values the app needs but the repo doesn't give raise `MissingValues`, which discovery turns into a `var:<NAME>` question.

`up()` then runs two **probes**:
- `health`: the health path answers 200;
- `auth`: the check operation answers 401/403 anonymously and 200 with the admin principal.

A failure raises `BlackboxError: probes failed after bring-up`.

### Live targets (`ExternalStack`)

Nothing is started. `up()` first deletes whatever an earlier crashed run left behind (from the ledger), then probes and fingerprints. **Safe mode** (`SafeMode`, and `platform_rt/proxy.py` in the runner) allows reads, `POST` to collections whose items can be read and deleted (with the run's prefix), and writes or deletes only on items this run created. Paths matching a deny category in `config/safety.yaml` are blocked.

## Outside services: stand-ins

When discovery wired a dependency (a webhook or SMTP), the env value is pointed at a local **stand-in** on the internal network (`platform_rt/standin.py`; Mailpit for email). The stand-in records what it receives, and can inject faults (`dependency_failure_handled`). Results that rest on a stand-in are labelled `simulated`, and the run is **isolation 1**.

| Isolation | Meaning |
|---|---|
| 0 | Isolated: everything ran on the internal network |
| 1 | Partly isolated: outside services replaced by stand-ins |
| 2 | Live and local: a system the platform didn't start |

## Output: `env_spec`

```
{variant, project, flags[], spec_hash (sha256 of the fingerprint), fingerprint{images, services, sut{name, ref, patches}, runner, …},
 probes[{name, ok, raw}], base_url, lock_file | compose_file, reproduce: "tp env up reference --expect <hash>" | "docker compose … up -d --wait",
 cleanup_before? (live)}
```

Execute compares each variant's `spec_hash` with this one. The retest compares the fingerprints field by field.

## Running tests through env

`Stack.pytest` runs the runner image on the internal network with these mounts:
- the tests, read-only;
- the output folder;
- the spec copy, read-only.

Configuration is passed as env vars (`TP_API_BASE`, `TP_ID_FIELD`, `TP_FAMILIES`, …). Secrets are passed as env names, never in argv. Every create is logged to `ledger.jsonl` and deleted after the batch, newest first; ids are percent-encoded. Anything that can't be deleted is listed in `cleanup.json`.

## Commands

These apply to the template stack:
- `tp env up <variant> [--expect <hash>]`
- `tp env probe`
- `tp env reset`: a fresh database from the seed template
- `tp env down <variant>`

## Files and tests

Tests: `test_unit.py`, `test_blackbox.py`, `test_blackbox_discovered.py`, `test_external.py`, plus the Docker-marked `test_docker.py` and `test_blackbox_docker.py`.
