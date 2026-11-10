# Test report — summary

Labels: real, unsigned
Network: at run time none: an internal network, reached inbound through a forwarder; build: unrestricted outbound while building app.
**it3-i1-notify · container · isolation 1 (partly isolated (stand-ins)) · Stable, property-checked · ran 28 of 29; blocked ▸ signed case not bound to a test 1/29 (3%) · rests on: real 26/28 (93%), simulated 2/28 (7%)**
Strongest claim: Stable, property-checked.

## P0 — defects
- **G:dependency_called:POST /api/invite** G:dependency_called:POST /api/invite
- **U:create_notes** None
- **G:dependency_failure_handled:POST /api/notes** G:dependency_failure_handled:POST /api/notes
- **G:dependency_called:POST /api/notes** G:dependency_called:POST /api/notes

## P1 — untested or weak
- signed case not bound to a test: TC-O6-1 → bind TC-O6-1: The case needs 150 notes to exist for the admin, but the only route in the OpenAPI excerpt is GET /api/notes and the tools provide no notes factory. Creating the notes would require a POST route that is not in the excerpt, so the precondition cannot be set up and the request would test an unknown data state.
- criterion with no admitted test: O-4 → TC-O4-1
- criterion with no admitted test: O-6 → TC-O6-1

## P2 — test health
- redundant 0/28 (0%), mutation strength not measured, health findings 0, quarantined 0

## P3 — spec and code drift
- spec gap: TC-O4-1 — the served spec and the server agree on this status: the drafted assumption is wrong
- assumed criterion (never confirmed by a person): C-1 — People can post short notes
- assumed criterion (never confirmed by a person): C-2 — Posted notes notify a webhook
- assumed criterion (never confirmed by a person): C-3 — People can invite others by email
- assumed criterion (never confirmed by a person): O-4 — Note text of any length is accepted
- assumed criterion (never confirmed by a person): O-6 — Notes list has no bound on item count
- assumed criterion (never confirmed by a person): O-10 — Invitation with malformed email has no declared response
- assumed criterion (never confirmed by a person): O-11 — Invitation refused without token

## Generic checks
- properties: 5 cases — ADMITTED_UNMEASURED 4/5 (80%), SPEC_MISMATCH 1/5 (20%)
- auth_required: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- unknown_id: 1 cases — ADMITTED_UNMEASURED 1/1 (100%)
- invalid_input: 4 cases — ADMITTED_UNMEASURED 4/4 (100%)
- read_back: 1 cases — ADMITTED_UNMEASURED 1/1 (100%)
- delete_final: 1 cases — ADMITTED_UNMEASURED 1/1 (100%)
- dependency_called: 2 cases — ADMITTED_UNMEASURED 2/2 (100%)
- dependency_failure_handled: 2 cases — ADMITTED_UNMEASURED 2/2 (100%)

## Server and spec disagree (1 operations)
- `G:properties:POST /api/login`: POST /api/login → 400 (Undocumented status code)

## Outbound dependencies
- http (http): tested, simulated on tp-standin-http: G:dependency_called:POST /api/notes passed, G:dependency_failure_handled:POST /api/notes passed; received 1
- mail (smtp): tested, simulated on tp-standin-mail: G:dependency_called:POST /api/invite passed, G:dependency_failure_handled:POST /api/invite passed; received 1

## Scorecard
- seeded detected (authored) 4/4 (100%); blind not run (M7)
- real defects 0/0
- triage correct on seeded rows 0/0
- localisation top1 0/4 (0%)
- localisation top3 0/4 (0%)
- localisation top5 0/4 (0%)
- fail before / pass after: not run (M5)
