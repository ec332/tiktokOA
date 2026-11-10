# Test cases — sign-off (UNSIGNED)

Flows: FLOW-invite, FLOW-notes. 0/7 lines approved, 0 carried forward from an earlier sign-off.

## Attention

- C-1: the drafter returned no cases
- C-2: the drafter returned no cases
- C-3: the drafter returned no cases
- O-4: the drafter returned no cases
- O-6: the drafter returned no cases
- O-10: the drafter returned no cases
- O-11: the drafter returned no cases
- C-1: preconditions ['authenticated_user'] are not factories; moved to steps
- C-2: preconditions ['authenticated_user'] are not factories; moved to steps
- C-3: preconditions ['authenticated_user'] are not factories; moved to steps
- O-4: preconditions ['authenticated_user'] are not factories; moved to steps
- O-6: preconditions ['user_with_150_notes'] are not factories; moved to steps
- O-10: preconditions ['authenticated_user'] are not factories; moved to steps
- O-11: preconditions ['anonymous_caller'] are not factories; moved to steps
- C-1: minimum template error_exception neither used nor skipped with a reason
- C-1: error path with no exception case
- C-3: minimum template error_exception neither used nor skipped with a reason
- C-3: error path with no exception case
- O-4: minimum template boundary neither used nor skipped with a reason
- O-4: minimum template error_exception neither used nor skipped with a reason
- O-4: minimum template error_guessing neither used nor skipped with a reason
- O-4: error path with no exception case
- O-6: minimum template boundary neither used nor skipped with a reason
- O-6: minimum template error_guessing neither used nor skipped with a reason
- O-10: minimum template error_exception neither used nor skipped with a reason
- O-10: minimum template error_guessing neither used nor skipped with a reason
- O-10: error path with no exception case
- O-11: minimum template error_exception neither used nor skipped with a reason
- O-11: minimum template error_guessing neither used nor skipped with a reason
- O-11: error path with no exception case
- C-1: criterion is assumed, not confirmed: check its cases
- C-2: criterion is assumed, not confirmed: check its cases
- C-3: criterion is assumed, not confirmed: check its cases
- O-4: criterion is assumed, not confirmed: check its cases
- O-6: criterion is assumed, not confirmed: check its cases
- O-10: criterion is assumed, not confirmed: check its cases
- O-11: criterion is assumed, not confirmed: check its cases

## Worked examples (boundaries)


## FLOW-invite

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O10-1 | P0 | acceptance | An invitation with a malformed email is rejected with 400 and an Error body. | 400 (literal) | POST /api/invite accepts or fails differently from 400 when the email is not well-formed. |
| TC-O11-1 | P0 | acceptance | An invitation without a bearer token is refused with 401 and an Error body. | 401 (literal) | POST /api/invite without a token is not refused with 401. |
| TC-C3-1 | P1 | acceptance | A person inviting others by valid email addresses gets the invitation sent. | 2xx (literal) | A valid invitation request by a person is refused instead of being sent by email. |

## FLOW-notes

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O4-1 | P0 | acceptance | Note text above the documented maximum length is rejected with 400. | 400 (literal) | Note text far above the documented maximum length is stored instead of rejected. |
| TC-O6-1 | P0 | acceptance | The notes list without paging parameters returns one default page and reports the total. | {'count': 150, 'data_len': 20} (calculator:page_window) | GET /api/notes returns all notes at once instead of the documented default page size. |
| TC-C2-1 | P0 | acceptance | Posting a note returns 201 while the webhook notification proceeds. | 201 (literal) | Posting a note returns a status other than 201 when the webhook notification is pending or retried. |
| TC-C1-1 | P1 | acceptance | A person posting a short note gets the note posted. | 2xx (literal) | A valid short note from a person is refused or not accepted by POST /api/notes. |

## Not tested

- O-1: out of scope in the spec
- O-2: out of scope in the spec
- O-3: out of scope in the spec
- O-5: out of scope in the spec
- O-7: out of scope in the spec
- O-8: out of scope in the spec
- O-9: out of scope in the spec
- O-12: out of scope in the spec
- O-13: out of scope in the spec
- O-14: out of scope in the spec
