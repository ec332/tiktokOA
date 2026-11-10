# `tp compare r-0079 r-0080`

Recomputed on 2026-11-10 from the stored, sealed records of the two runs (`runs/r-0079-…/07-execute-e534/`, `runs/r-0080-…/07-execute-e545/`). Nothing was re-run against either system. The output below is unedited.

Bound tests: `sha256:4c2a0e193733dfe2e323e475579163790bfb68ecfc32e6dccc9e5f6f5198f0ba` (the same on every run)

| Case | aspnetcore-realworld-example-app (r-0079) | express-bookshelf-realworld-example-app (r-0080) | Witness rule |
|---|---|---|---|
| `G:auth_required:DELETE /api/articles/{slug}` | PASS | PASS | AGREE |
| `G:auth_required:GET /api/articles` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `G:auth_required:GET /api/articles/{slug}` | PASS | PASS | AGREE |
| `G:auth_required:POST /api/articles` | PASS | PASS | AGREE |
| `G:auth_required:PUT /api/articles/{slug}` | PASS | PASS | AGREE |
| `G:invalid_input:POST /api/articles` | PASS | PASS | AGREE |
| `G:invalid_input:POST /api/users` | PASS | PASS | AGREE |
| `G:invalid_input:POST /api/users/login` | PASS | PASS | AGREE |
| `G:properties:GET /api/articles` | PASS | PASS | AGREE |
| `G:properties:GET /api/articles/{slug}` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-0080; witness r-0079 |
| `G:properties:POST /api/articles` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-0080; witness r-0079 |
| `G:properties:POST /api/users` | PASS | SUSPECT_ENV | INCOMPLETE |
| `G:properties:POST /api/users/login` | PASS | PASS | AGREE |
| `G:unknown_id:GET /api/articles/{slug}` | PASS | PASS | AGREE |
| `G:unknown_id:GET /api/profiles/{username}` | PASS | PASS | AGREE |
| `TC-C10-1` | PASS | PASS | AGREE |
| `TC-C2-1` | INTERMITTENT | SUSPECT_ENV | INCOMPLETE |
| `TC-C3-1` | INTERMITTENT | PASS | INCOMPLETE |
| `TC-C3-2` | PASS | PASS | AGREE |
| `TC-C3-3.a` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `TC-C3-3.b` | PASS | PASS | AGREE |
| `TC-C3-3.c` | PASS | PASS | AGREE |
| `TC-C3-4.a` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `TC-C3-4.b` | PASS | PASS | AGREE |
| `TC-C3-4.c` | PASS | PASS | AGREE |
| `TC-C3-5` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `TC-C3-6` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-0080; witness r-0079 |
| `TC-C4-1` | INTERMITTENT | PASS | INCOMPLETE |
| `TC-C5-1` | PASS | PASS | AGREE |
| `TC-C5-2` | PASS | PASS | AGREE |
| `TC-C6-1` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `TC-C7-2` | PASS | PASS | AGREE |
| `TC-C7-3` | CONSISTENT_FAIL | CONSISTENT_FAIL | TO_PERSON |
| `TC-C9-1` | PASS | PASS | AGREE |
| `TC-C9-2` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-0080; witness r-0079 |
| `TC-C9-3` | PASS | CONSISTENT_FAIL | SPEC_MISMATCH on r-0080; witness r-0079 |
| `TC-O1-1.1` | PASS | PASS | AGREE |
| `TC-O1-1.2` | PASS | PASS | AGREE |
| `TC-O1-2` | PASS | PASS | AGREE |
| `TC-O1-3` | PASS | PASS | AGREE |
| `TC-O2-1.1` | PASS | PASS | AGREE |
| `TC-O2-1.2` | PASS | PASS | AGREE |
| `TC-O2-2` | PASS | PASS | AGREE |
| `TC-O3-3` | PASS | PASS | AGREE |

Rows: AGREE 29, INCOMPLETE 4, SPEC_MISMATCH 5, TO_PERSON 6

**`G:auth_required:GET /api/articles`** (TO_PERSON): request `GET /api/articles`
- r-0079: 200 failed: SpecMismatch: [no credentials:2xx] GET /api/articles answered 200 with no credentials; the spec declares no security for it
- r-0080: 200 failed: SpecMismatch: [no credentials:2xx] GET /api/articles answered 200 with no credentials; the spec declares no security for it

**`G:properties:GET /api/articles/{slug}`** (SPEC_MISMATCH): request `GET /api/articles/þ)Ùð`
- r-0079: 404 passed
- r-0080: 404 failed: SpecMismatch: [property] GET /api/articles/{slug}: 10 example(s) disagree with the spec (Response violates schema); first /api/articles/0 → 404

**`G:properties:POST /api/articles`** (SPEC_MISMATCH): request `POST /api/articles`
- r-0079: 201 passed
- r-0080: 500 failed: ExpectationFailed: [property] POST /api/articles → 500: Server error, Undocumented status code

**`TC-C3-3.a`** (TO_PERSON): request `GET /api/articles`
- r-0079: 200 failed: ExpectationFailed: [TC-C3-3.a/status] expected '422', got 200
- r-0080: 200 failed: ExpectationFailed: [TC-C3-3.a/status] expected '422', got 200

**`TC-C3-4.a`** (TO_PERSON): request `GET /api/articles`
- r-0079: 200 failed: ExpectationFailed: [TC-C3-4.a/status] expected '422', got 200
- r-0080: 500 failed: ExpectationFailed: [TC-C3-4.a/status] expected '422', got 500

**`TC-C3-5`** (TO_PERSON): request `GET /api/articles`
- r-0079: 200 failed: ExpectationFailed: [TC-C3-5/status] expected '422', got 200
- r-0080: 500 failed: ExpectationFailed: [TC-C3-5/status] expected '422', got 500

**`TC-C3-6`** (SPEC_MISMATCH): request `GET /api/articles`
- r-0079: 422 passed
- r-0080: 200 failed: ExpectationFailed: [TC-C3-6/status] expected '422', got 200

**`TC-C6-1`** (TO_PERSON): request `POST /api/users/login`
- r-0079: 401 failed: ExpectationFailed: [TC-C6-1/status] expected '2xx', got 401
- r-0080: 422 failed: ExpectationFailed: [TC-C6-1/status] expected '2xx', got 422

**`TC-C7-3`** (TO_PERSON): request `PUT /api/articles/tp-x534referencea1-11024f-owned`
- r-0079: 200 failed: ExpectationFailed: [TC-C7-3/status] expected '409', got 200
- r-0080: 200 failed: ExpectationFailed: [TC-C7-3/status] expected '409', got 200

**`TC-C9-2`** (SPEC_MISMATCH): request `POST /api/articles/tp-x534referencea1-081e39-article/favorite`
- r-0079: 200 passed
- r-0080: 422 failed: ExpectationFailed: [TC-C9-2/status] expected '200', got 422

**`TC-C9-3`** (SPEC_MISMATCH): request `DELETE /api/articles/tp-x534referencea1-a7e9cc-fav/favorite`
- r-0079: 200 passed
- r-0080: 404 failed: ExpectationFailed: [TC-C9-3/status] expected '200', got 404

