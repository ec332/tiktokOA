# Test report — summary

Labels: real, unsigned
Network: at run time none: an internal network, reached inbound through a forwarder; build: unrestricted outbound while building node.
**express-bookshelf-realworld-example-app · container · isolation 0 (isolated) · Stable, property-checked · ran 42 of 55; blocked ▸ excluded: no_valid_body 2/55 (4%); not run: SUSPECT_ENV 2/55 (4%); signed case not bound to a test 9/55 (16%) · rests on: real 42/42 (100%)**
Strongest claim: Stable, property-checked.

## P0 — defects
- **G:properties:POST /api/articles** G:properties:POST /api/articles

## P1 — untested or weak
- signed case not bound to a test: TC-C1-1 → bind TC-C1-1: This case needs a JWT that is already one hour old. The only tools available are the admin token from make.admin(), name and PDF factories, and API calls. None of them can mint a token with a past issue time or wait out an hour, and sleeping and clocks are forbidden, so no request can be sent with an expired token.
- signed case not bound to a test: TC-C1-2 → bind TC-C1-2: The case needs the request to carry `Authorization: Bearer <valid jwt>`, but the available `api` methods only accept a `token` argument, which sends the admin's credentials in the platform's own scheme. No parameter sets a custom Authorization header or chooses the scheme, so the wrong-scheme request cannot be built with these tools. Passing `token=None` would send no credentials at all, which is a different case.
- signed case not bound to a test: TC-C7-1 → bind TC-C7-1: The case needs a second, authenticated non-owner user, but the only identity make provides is the admin, and no route in the excerpt creates users. Row TC-C7-1.2 therefore cannot be set up. Making the article owned by someone else would need a user-creation tool, so the binding cannot be written for this case.
- signed case not bound to a test: TC-C8-1 → bind TC-C8-1: The case needs an existing author whose username the test can address, but the tools provide no way to learn that username or to create a user. The only factory is make.admin(), which returns just a token, and the OpenAPI excerpt has no users or profiles listing route. Without a username, the POST /api/profiles/{username}/follow request cannot be built against the 'existing author' hint.
- signed case not bound to a test: TC-C8-2 → bind TC-C8-2: The hints say only 'existing author' and give no username. The tools offer no way to look up or create a user, and make.admin() returns only a token, so the username to follow cannot be obtained. Following the admin's own account is not a valid substitute for an existing author, so the case cannot be bound.
- signed case not bound to a test: TC-C8-3 → bind TC-C8-3: The case needs a real existing author that the admin does not follow, but the tools provide no way to obtain one. The only provided user is the admin, there is no user factory, and no profile lookup route is in REQUEST routes to discover an existing username. Using a made-up name would test a 404 for an unknown profile, not an idempotent unfollow of an existing author.
- signed case not bound to a test: TC-O2-3 → bind TC-O2-3: The case needs a request carrying an Authorization header whose token is empty ("Token "). The api helper only takes a token argument and has no headers parameter. The request rules require token=None when REQUEST auth is false, and token=None sends no Authorization header at all, so the request would test anonymous access rather than an empty token header. No available tool sends the empty header, so the case cannot be bound.
- signed case not bound to a test: TC-O3-1 → bind TC-O3-1: The case needs an existing profile to request, but the tools provide only the admin token, with no way to learn the admin's username or to create a user or profile. The request path requires a username, and none is available in the hints, the case data, or any factory.
- signed case not bound to a test: TC-O3-2 → bind TC-O3-2: The case needs an existing profile to read anonymously, but the only provided user is the admin, and make.admin() returns only a token with no username. No route in the excerpt creates users or profiles, so there is no way to obtain a known existing username to request without authentication.
- criterion with no admitted test: C-1 → TC-C1-1, TC-C1-2
- criterion with no admitted test: C-2 → TC-C2-1
- criterion with no admitted test: C-6 → TC-C6-1
- criterion with no admitted test: C-8 → TC-C8-1, TC-C8-2, TC-C8-3

## P2 — test health
- redundant 0/46 (0%), mutation strength not measured, health findings 2, quarantined 0

## P3 — spec and code drift
- spec gap: TC-C3-3.a — the served spec and the server agree on this status: the drafted assumption is wrong
- spec gap: TC-C3-6 — the served spec and the server agree on this status: the drafted assumption is wrong
- spec gap: TC-C6-1 — the served spec and the server agree on this status: the drafted assumption is wrong
- spec gap: TC-C7-3 — the served spec and the server agree on this status: the drafted assumption is wrong
- spec gap: TC-C9-2 — the served spec and the server agree on this status: the drafted assumption is wrong
- spec gap: TC-C9-3 — the served spec and the server agree on this status: the drafted assumption is wrong
- assumed criterion (never confirmed by a person): C-1 — Protected operations require the Authorization Token header
- assumed criterion (never confirmed by a person): C-3 — Article lists are paged with offset and limit
- assumed criterion (never confirmed by a person): C-7 — Users can write and edit articles
- assumed criterion (never confirmed by a person): C-8 — Users can follow authors
- assumed criterion (never confirmed by a person): C-9 — Users can favourite articles
- assumed criterion (never confirmed by a person): O-1 — Anonymous article list returns 401 per spec despite optional auth
- assumed criterion (never confirmed by a person): O-2 — Anonymous read of article comments returns 401 per spec
- assumed criterion (never confirmed by a person): O-3 — Anonymous read of a profile returns 401 per spec

## Generic checks
- properties: 5 cases — ADMITTED_UNMEASURED 1/5 (20%), CANDIDATE_DEFECT 1/5 (20%), FLAKY 1/5 (20%), SPEC_MISMATCH 2/5 (40%)
- auth_required: 5 cases — ADMITTED_UNMEASURED 4/5 (80%), SPEC_MISMATCH 1/5 (20%)
- unknown_id: 2 cases — ADMITTED_UNMEASURED 2/2 (100%)
- invalid_input: 3 cases — ADMITTED_UNMEASURED 3/3 (100%)
- read_back: 1 cases — EXCLUDED 1/1 (100%)
- delete_final: 1 cases — EXCLUDED 1/1 (100%)
- excluded (no_valid_body): 2/46 (4%)

## Server and spec disagree (3 operations)
- `C-3`: GET /api/articles → 500
- `G:auth_required:GET /api/articles`: GET /api/articles → 200
- `G:properties:GET /api/articles/{slug}`: GET /api/articles/0 → 404 (Response violates schema)

## Scorecard
- seeded detected (authored) 0/0; blind not run (M7)
- real defects 1/1 (100%)
- triage correct on seeded rows 0/0
- localisation top1 0/0
- localisation top3 0/0
- localisation top5 0/0
- fail before / pass after: not run (M5)
