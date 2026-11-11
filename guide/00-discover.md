# Stage 0: Discover (technical reference)

[← Guide](../GUIDE.md) · next: [Input](01-input.md)

| | |
|---|---|
| Module | `tp/stages/discover/stage.py` (`Discover`), steps in `discovery.py` |
| Version | `0.2.0` |
| Consumes | `run_options` (`target`, `no_conventions`, `live`, `keep_up`, `answers`) |
| Produces | `target_profile`, `discovery_probes` |
| Model | `claude-haiku-5-5`, proposals only; budget **$0.50** per run |
| Person | `setup` (at most 5 per run), `confirm_observed` (never blocks) |
| Events | `discover_reach`, `discover_dependencies`, `discover_guard`, `discover_ui` |
| Run as | `tp target add <url>[@ref]`: a run whose pipeline is only `["discover"]` |

## Purpose

Discovery writes a profile of a system the platform has never seen. The profile says how to start the system, reach it, log in, create data, and check it. Each value in it is either verified by a probe against the running system or given by a person. No target-specific code exists anywhere; `tests/test_target_literals.py` enforces that.

## Algorithm

`Discovery.run()` runs these steps in a fixed order:

| # | Step | What it settles | How |
|---|---|---|---|
| 1 | `step_reach` | `reach`: ref, compose or build, service, port, env | The ref is the latest tag matching `^v?\d+(\.\d+)+$` (or `@ref`). It tries up to `max_compose` compose files, or the root Dockerfile as a one-service compose. Secret-like env vars (`SECRET\|KEY\|PASSWORD\|TOKEN`) whose repo value is a placeholder get a generated secret. Admin env comes from conventions or AI. A floating image tag is retagged to the ref. The stack must come up |
| 2 | `step_spec` | `spec`: served path or repo file, base path; `reach.health` | First a clarify answer, then the convention paths, then OpenAPI files in the repo (≤ 5 MB), or `pack:<name>`. A served spec also fixes the health path. Otherwise the health path is the first that answers 200 from: the convention `/healthz`, `/`, then up to 5 parameterless GETs from the spec |
| 3 | `step_auth` | `auth`: check operation, login, scheme, bootstrap | It picks a secured check operation, then tries login candidates (operations matching `signin_op_pattern`) with each auth prefix (`Bearer`, `Token`). A 2xx without a token is a first-user bootstrap, after which the other candidates are retried. HTTP Basic is tried if declared. A login is verified only when the check answers 401/403 anonymously and 200 with the token |
| 4 | `step_data` | `data`: id field, id style, paging | For up to `max_resources` collections: build a body from the schema, `POST` it, find the id field, `GET` it back. A second create is used to probe paging |
| 5 | `step_secured` | `auth.secured` | Only when the spec declares no security. It reads each parameterless GET anonymously; if most are refused, the default is secured. Operations that answer anonymously are listed as `observed_open` |
| 6 | `step_spec_check` | trust in a repo or pack spec | It compares the fingerprints of real answers with the fingerprint of a garbage path. If most look like a missing route, the spec is rejected |
| 7 | `step_cleanup` | — | It `DELETE`s everything it created |
| 8 | `step_dependencies` | `checks.dependencies` | It finds outside services (webhook, SMTP) from env names and `config/dependencies.yaml`. The AI proposes the wiring; a stand-in run verifies it |
| 9 | `step_ui` | `checks.ui` | The AI proposes browser flows from the accessibility snapshot; a replay verifies each one |
| 10 | `step_resolver` | `state`, `mutation`, `safety`, `checks` | Fixed rules: `namespace` state, `none` mutation (black-box), check families from `config/discovery.yaml` |

`LiveDiscovery` (`--live`) overrides reach, auth, data and dependencies. It is read-only: it doesn't start anything, and it uses the token you supply with `--secret`.

## Provenance

`mark(concern, key, source, layer, probe)` records each value's provenance. A concern's overall `source` is its **weakest** field, ranked from weakest to strongest: `ai_proposed+verified` < `clarify` < `repo` < `resolver` < `probed`.

| Source | Meaning |
|---|---|
| `probed` | A probe against the running system confirmed it |
| `repo` | Read from the repo (compose, Dockerfile, spec file) |
| `resolver` | A fixed platform rule |
| `ai_proposed+verified` | The model proposed it and a probe verified it |
| `clarify` | A person answered a setup question |
| `override` | Hand-written in a profile |

| Layer | Meaning |
|---|---|
| `convention` | A framework standard from `config/discovery.yaml`; each entry cites its framework's docs. `--no-conventions` turns this layer off |
| `algorithm` | Worked out from the repo or the spec |
| `exploration` | Proposed by AI |
| `clarify` | Answered by a person |

## Setup questions

A step that can't verify a value raises `DiscoveryError(step, message, question, key)`. If the key hasn't been answered and fewer than `MAX_SETUP = 5` questions have been asked, a `setup` review item is created and the run stops at `awaiting_review`.

| Key | Raised when | Answer format |
|---|---|---|
| `port` | The Dockerfile has no numeric `EXPOSE` | `8080` |
| `app_env` | No compose variant came up | `NAME=value NAME2=value` (stored in the profile, so no secrets) |
| `var:<NAME>` | The compose file needs a variable the repo doesn't give | the value (kept in the run's secrets) |
| `spec` | No served spec and no repo file, or the repo spec looks wrong | `/openapi.json`, `api/openapi.yaml` or `pack:<name>` |
| `admin_env` | No login was verified and no admin env vars were found | `USER_VAR PASSWORD_VAR` |
| `login` | No login was verified | the login path |
| `body:<collection>` | No resource could be created and read back | a JSON body |

To answer: `tp review resolve <id> answer --text '…'`. An answer is a candidate like any other: it is kept only if its probe passes. `skip` fails the run with the question as the reason.

## Outputs

- **`target_profile`**, also written to `data/targets/<slug>/profile.yaml` (or `profile.no-conventions.yaml`). It holds `schema_version`, `target{name, repo, type}` and `commit_tested`. For each concern in `reach, spec, auth, data, state, mutation, safety, checks` it holds `{source, layer, evidence: "probe#a-b", value, provenance{key: {source, layer, probe}}}`.
- **`discovery_probes`**: every probe in order, sealed and with secrets redacted. It's saved as `probes.json` next to the profile together with the commit. `--replay` serves these probes instead of Docker and the network, and refuses to run if the clone's commit differs.

## Failure modes

| Message | Cause |
|---|---|
| `setup question skipped: …` | A person chose `skip` |
| `r-NNNN is using <slug>'s stack; cancel it or let it finish` | Another run holds the same compose project |
| `replay: no recorded probe log at …` / `replay: the log was recorded at <commit>` | `--replay` without a matching log |
| `no compose file with a service publishing a port, and no root Dockerfile` | The repo has no reach (not asked: there's nothing a person could answer) |

The stack is always torn down in `finally`, unless `--keep-up` is set. Before reach is settled, `Stack.raw()` raises `BlackboxError`, which `down()` suppresses.

## Configuration

`config/discovery.yaml` has two parts:
- **`conventions`**: `spec_path`, `health_path`, `admin_env_pattern`, `signin_op_pattern`, `auth_prefix` and `paging_param`, each with its provenance.
- **`platform`**: `model`, `admin_user: tpadmin`, `read_back_timeout_s: 120`, `families` defaults, `secret_name`, `placeholders`, `max_compose` and `max_resources`.

## Files and tests

- `discovery.py`: the steps.
- `reach.py`: the Dockerfile reader and stored answers.
- `spec_base.py`: the base path and pack specs.
- `ai.py`: model prompts for file picks and admin env.
- `dependencies.py`: stand-in wiring.
- `ui_flows.py`: browser flows.
- `targets.py`: `GET /targets` for the UI.
- `compare.py`: normal vs `--no-conventions`.
- Tests: `tests/test_discover.py`, `test_discover_names.py`, `test_hooks.py`.

## Limits

- Only HTTP JSON APIs with an OpenAPI spec are supported.
- Login is limited to username and password (or Basic). OAuth, SSO and 2FA are not supported.
- A container target always needs a working login.
- At most 3 compose variants are tried.
