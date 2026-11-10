# Test report — summary

Labels: real, unsigned
Network: at run time runner on an internal network, out only through the safe-mode proxy; build: none: the system was already running.
Strongest claim: Seen on the live system, stable 3/3.

## P0 — defects
- none

## P1 — untested or weak

## P2 — test health
- redundant 0/35 (0%), mutation strength not measured, health findings 0, quarantined 0

## P3 — spec and code drift
- assumed criterion (never confirmed by a person): C-1 — Admin creates a tag with a name
- assumed criterion (never confirmed by a person): C-3 — Tags can be listed with paging
- assumed criterion (never confirmed by a person): C-4 — Tag can be deleted
- assumed criterion (never confirmed by a person): O-6 — Tag list page_size has no upper bound

## Generic checks
- properties: 5 cases — EXCLUDED 2/5 (40%), SPEC_MISMATCH 3/5 (60%)
- auth_required: 5 cases — ADMITTED_UNMEASURED 2/5 (40%), EXCLUDED 3/5 (60%)
- unknown_id: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- invalid_input: 5 cases — EXCLUDED 5/5 (100%)
- read_back: 5 cases — ADMITTED_UNMEASURED 3/5 (60%), EXCLUDED 2/5 (40%)
- delete_final: 5 cases — ADMITTED_UNMEASURED 3/5 (60%), EXCLUDED 2/5 (40%)
- paging: 5 cases — ADMITTED_UNMEASURED 5/5 (100%)
- excluded (NOT_RUN): 14/35 (40%)

## Server and spec disagree (3 operations)
- `G:properties:GET /api/correspondents/`: GET /api/correspondents/ → 404 (Undocumented status code)
- `G:properties:GET /api/correspondents/{id}/`: GET /api/correspondents/1/ → 404 (Undocumented status code)
- `G:properties:GET /api/custom_fields/`: GET /api/custom_fields/ → 404 (Undocumented status code)

## Scorecard
- seeded detected (authored) 0/0; blind not run (M7)
- real defects 0/0
- triage correct on seeded rows 0/0
- localisation top1 0/0
- localisation top3 0/0
- localisation top5 0/0
- fail before / pass after: not run (M5)
