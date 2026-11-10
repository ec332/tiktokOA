# Spec v3 — it3-i1-notify @ cb7e637

Requirements: brief `sha256:3c1e7059ca2d`. Code index: 7 routes in 8 files.

## Criteria

| id | status | title | given / when / then | expected | sources | flags |
|---|---|---|---|---|---|---|
| C-1 | assumed | People can post short notes |  / a person posts a short note / the short note is posted | human judgement | L1-L1 | no_observable_outcome, missing_actor, missing_error_path, review needed |
| C-2 | assumed | Posted notes notify a webhook | a note has been posted / the note is posted / a webhook is notified | 201 | L1-L1 | no_observable_outcome, missing_actor, missing_error_path, review needed |
| C-3 | assumed | People can invite others by email |  / a person invites others by email / the invitation is sent to those people by email | 409 | L1-L1 | no_observable_outcome, missing_actor, missing_error_path, review needed |
| O-1 | out_of_scope | Valid note is created | a valid bearer token and text of at least one character / POST /api/notes with a JSON body containing text / the server responds 201 with a Note containing id, text and owner | 201 | openapi/api/notes/POST.yaml:10, openapi/api/notes/POST.yaml:14 | missing_error_path |
| O-2 | out_of_scope | Empty note text is rejected | a valid bearer token / POST /api/notes with text of empty string / the server responds 400 with an Error body | 400 | openapi/api/notes/POST.yaml:16, openapi/components.yaml:42, openapi/components.yaml:44 |  |
| O-3 | out_of_scope | Unauthenticated note creation is refused | no bearer token / POST /api/notes / the server responds 401 with an Error body | 401 | openapi/api/notes/POST.yaml:2, openapi/api/notes/POST.yaml:22 |  |
| O-4 | assumed | Note text of any length is accepted | a valid bearer token / POST /api/notes with text of very large length / The server rejects text above a documented maximum length with 400. | 400 | openapi/components.yaml:42, openapi/components.yaml:44 | vague_quantifier, missing_error_path, SUSPECTED DEFECT, review needed |
| O-5 | out_of_scope | Notes list returns all notes in one response | a valid bearer token / GET /api/notes / the server responds 200 with an items array of Note objects, with no paging | 200 | openapi/api/notes/GET.yaml:5, openapi/components.yaml:48 | missing_error_path |
| O-6 | assumed | Notes list has no bound on item count | many notes exist / GET /api/notes / The list is paged with a documented default and maximum page size. |  | openapi/api/notes/GET.yaml:9, openapi/components.yaml:45, openapi/components.yaml:48 | vague_quantifier, missing_value, missing_error_path, SUSPECTED DEFECT, review needed |
| O-7 | out_of_scope | Notes list refused without token | no bearer token / GET /api/notes / the server responds 401 with an Error body | 401 | openapi/api/notes/GET.yaml:2, openapi/api/notes/GET.yaml:15 |  |
| O-8 | out_of_scope | Valid invitation is accepted for sending | a valid bearer token and a well-formed email address / POST /api/invite with an email / the server responds 202 with Sent.sent | 202 | openapi/api/invite/POST.yaml:10, openapi/components.yaml:25, openapi/components.yaml:27 | missing_error_path |
| O-9 | out_of_scope | Invitation delivery failure returns 502 | a valid bearer token and a well-formed email address / POST /api/invite and the email upstream fails / the server responds 502 with an Error body | 502 | openapi/api/invite/POST.yaml:16, openapi/api/invite/POST.yaml:21 |  |
| O-10 | assumed | Invitation with malformed email has no declared response | a valid bearer token and an email that is not well-formed / POST /api/invite / The server responds 400 with an Error body. | 400 | openapi/api/invite/POST.yaml:9, openapi/components.yaml:27 | SUSPECTED DEFECT, review needed |
| O-11 | assumed | Invitation refused without token | no bearer token / POST /api/invite / The server responds 401 with an Error body, as the security requirement implies, but the spec declares no 401 response. | 401 | openapi/api/invite/POST.yaml:2, openapi/api/invite/POST.yaml:9 | SUSPECTED DEFECT, review needed |
| O-12 | out_of_scope | Account creation returns session | a username and password each of at least one character / POST /api/users / the server responds 201 with a Session containing username and token | 201 | openapi/api/users/POST.yaml:8, openapi/api/users/POST.yaml:12 | missing_error_path |
| O-13 | out_of_scope | Duplicate account creation is refused | a username that already exists / POST /api/users / the server responds 409 with an Error body | 409 | openapi/api/users/POST.yaml:20, openapi/api/users/POST.yaml:24 |  |
| O-14 | out_of_scope | Login with bad credentials is refused | credentials that do not match an account / POST /api/login / the server responds 401 with an Error body | 401 | openapi/api/login/POST.yaml:14, openapi/api/login/POST.yaml:18 |  |

## Findings

- **suspected_defect**: NoteIn.text has minLength 1 but no maxLength, so the note text has no upper bound on size. (openapi/components.yaml:42, openapi/components.yaml:44, openapi/api/notes/POST.yaml:8)
- **suspected_defect**: GET /api/notes returns the full NoteList with no pagination, limit, or ordering parameter, so one request can return every note. (openapi/api/notes/GET.yaml:9, openapi/components.yaml:45, openapi/components.yaml:48)
- **missing_error_path**: POST /api/invite declares no 400 response, although Invite.email has format email; the response to a malformed address is not specified. (openapi/api/invite/POST.yaml:9, openapi/components.yaml:25, openapi/components.yaml:27)
- **missing_error_path**: POST /api/invite is secured with bearer auth but declares no 401 response. (openapi/api/invite/POST.yaml:2, openapi/api/invite/POST.yaml:9)
- **rule_interaction**: Note creation (201) is specified with a webhook notification, but the spec gives no outcome when the webhook call fails after the note is stored. (openapi/api/notes/POST.yaml:10, openapi/api/notes/POST.yaml:15)
- **no_requirement**: No route or schema in the index or source implements the webhook notification; the spec only shows a note-creation response. (openapi/api/notes/POST.yaml:1, openapi/api/notes/POST.yaml:27)
- **no_requirement**: Account creation, login, note listing, reading and deletion routes exist but the brief never mentions them. (openapi/api/users/POST.yaml:1, openapi/api/login/POST.yaml:1, openapi/api/notes/GET.yaml:1, openapi/api/notes/{id}/GET.yaml:1, openapi/api/notes/{id}/DELETE.yaml:1)
- **missing_error_path**: Note read and delete routes take a Note with an owner field but declare no 403 response and no ownership rule, so whether a user may act on another user's note is unspecified. (openapi/api/notes/{id}/GET.yaml:16, openapi/api/notes/{id}/DELETE.yaml:12, openapi/components.yaml:35)
- **missing_error_path**: Credentials.username and password have minLength 1 but no maxLength, and POST /api/users declares 409 for duplicates without stating which field is unique. (openapi/components.yaml:9, openapi/components.yaml:12, openapi/api/users/POST.yaml:20)
- **missing_error_path**: Session has no expiry field, so token lifetime and behaviour after expiry are not specified. (openapi/components.yaml:57, openapi/api/login/POST.yaml:8)

## Behaviour with no requirement (routes no criterion covers)

- DELETE /api/notes/{id}  (openapi/api/notes/{id}/DELETE.yaml:1)
- GET /api/notes/{id}  (openapi/api/notes/{id}/GET.yaml:1)

## Decisions

- intended behaviour: The server rejects text above a documented maximum length with 400. (assumed)
- intended behaviour: The list is paged with a documented default and maximum page size. (assumed)
- intended behaviour: The server responds 400 with an Error body. (assumed)
- intended behaviour: The server responds 401 with an Error body, as the security requirement implies, but the spec declares no 401 response. (assumed)
- note_max_length: A limit such as 280 characters rejected with 400. (assumed)
- notes_list_limit: a default page of 20 with a maximum of 100 (assumed)
- note_visibility: only the caller's own notes (assumed)
- webhook_failure: return 201 and retry asynchronously (assumed)
- invite_sent_false: sent may be false with 202 when delivery is refused (assumed)
- invite_duplicate: refuse with 409 (assumed)
