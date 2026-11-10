# Test report — summary

Labels: real, unsigned
Network: at run time none: an internal network, reached inbound through a forwarder; build: no build: images pulled at the tested tag.
**memos · container · isolation 1 (partly isolated (stand-ins)) · Stable, property-checked · ran 37 of 42; blocked ▸ excluded: no_valid_body 4/42 (10%); signed case not bound to a test 1/42 (2%) · rests on: real 36/37 (97%), simulated 1/37 (3%)**
Strongest claim: Stable, property-checked.

## P0 — defects
- none

## P1 — untested or weak
- signed case not bound to a test: TC-C3-1 → bind TC-C3-1: The only route provided is DELETE /api/memos/{memo}. No route or factory creates a note or person, so there is no way to obtain a memo id owned by the admin caller to delete. Deleting an unknown id would not test the creator-deletes-own-note behaviour.
- criterion with no admitted test: C-3 → TC-C3-1

## P2 — test health
- redundant 0/41 (0%), mutation strength not measured, health findings 0, quarantined 0

## P3 — spec and code drift
- assumed criterion (never confirmed by a person): C-1 — People can write short notes
- assumed criterion (never confirmed by a person): C-2 — People can edit notes they have written
- assumed criterion (never confirmed by a person): O-8 — Edit can rewrite creation and update timestamps

## Generic checks
- properties: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- auth_required: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- unknown_id: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- invalid_input: 5 cases — ADMITTED_UNMEASURED 4/5 (80%), SPEC_MISMATCH 1/5 (20%)
- read_back: 5 cases — ADMITTED_UNMEASURED 3/5 (60%), EXCLUDED 2/5 (40%)
- delete_final: 5 cases — ADMITTED_UNMEASURED 3/5 (60%), EXCLUDED 2/5 (40%)
- paging: 4 cases — ADMITTED_UNMEASURED 4/4 (100%)
- dependency_called: 1 cases — ADMITTED_UNMEASURED 1/1 (100%)
- dependency_failure_handled: 1 cases — ADMITTED_UNMEASURED 1/1 (100%)
- excluded (no_valid_body): 4/41 (10%)

## Server and spec disagree (1 operations)
- `G:invalid_input:POST /api/memos`: POST /api/memos → 200

## Outbound dependencies
- http (http): tested, simulated on tp-standin-http: G:dependency_called:POST /api/memos passed, G:dependency_failure_handled:POST /api/memos passed; received 1; reached through MEMOS_WEBHOOK_PRIVATE_NETWORK_ALLOWLIST

## Scorecard
- seeded detected (authored) 0/0; blind not run (M7)
- real defects 0/0
- triage correct on seeded rows 0/0
- localisation top1 0/0
- localisation top3 0/0
- localisation top5 0/0
- fail before / pass after: not run (M5)
