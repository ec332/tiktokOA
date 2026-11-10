# Test cases — sign-off (UNSIGNED)

Flows: FLOW-articles, FLOW-profiles, FLOW-users. 0/32 lines approved, 0 carried forward from an earlier sign-off.

## Attention

- C-1: dropped a drafted case: expected '2xx' is not traceable to C-1 (spec allows ['401', '4xx'])
- C-4: skipped minimum template boundary: Default cap is a returned-count rule with no request-side ok and fail literals; covered by the page_window acceptance case
- C-7: decision table leaves out 6 combination(s), e.g. {'caller': 'anonymous', 'article ownership': "other user's article"}
- O-1: decision table leaves out 2 combination(s), e.g. {'caller': 'anonymous', 'Authorization header': 'Token <valid jwt>'}
- O-1: skipped minimum template error_exception: No failure outcome is stated for anonymous reads; the decided outcome is success
- O-2: decision table leaves out 2 combination(s), e.g. {'caller': 'anonymous', 'Authorization header': 'Token <valid jwt>'}
- O-2: skipped minimum template error_exception: No failure outcome is stated for anonymous reads; the decided outcome is success
- O-3: decision table leaves out 2 combination(s), e.g. {'caller': 'anonymous', 'Authorization header': 'Token <valid jwt>'}
- O-3: skipped minimum template error_exception: No failure outcome is stated for anonymous reads; the decided outcome is success
- C-1: dropped a drafted case: expected '2xx' is not traceable to C-1 (spec allows ['401', '4xx'])
- C-1: preconditions ['authenticated_user_with_expired_token'] are not factories; moved to steps
- C-1: preconditions ['authenticated_user'] are not factories; moved to steps
- C-2: preconditions ['person_without_account'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-3: preconditions ['article_list_with_45_articles'] are not factories; moved to steps
- C-4: preconditions ['article_list_with_21_articles'] are not factories; moved to steps
- C-5: preconditions ['authenticated_user'] are not factories; moved to steps
- C-5: preconditions ['authenticated_user'] are not factories; moved to steps
- C-6: preconditions ['person_without_account'] are not factories; moved to steps
- C-7: preconditions ['authenticated_user'] are not factories; moved to steps
- C-7: preconditions ['authenticated_user', 'article_owned_by_user', 'other_article_with_same_title'] are not factories; moved to steps
- C-7: preconditions ['authenticated_user', 'article_owned_by_user', 'authenticated_non_owner'] are not factories; moved to steps
- C-8: preconditions ['authenticated_user', 'author_not_yet_followed'] are not factories; moved to steps
- C-8: preconditions ['authenticated_user', 'author_already_followed'] are not factories; moved to steps
- C-8: preconditions ['authenticated_user', 'author_not_followed'] are not factories; moved to steps
- C-9: preconditions ['authenticated_user', 'article_not_favourited'] are not factories; moved to steps
- C-9: preconditions ['authenticated_user', 'article_already_favourited'] are not factories; moved to steps
- C-9: preconditions ['authenticated_user', 'article_not_favourited'] are not factories; moved to steps
- C-10: preconditions ['authenticated_user', 'published_article'] are not factories; moved to steps
- O-1: preconditions ['anonymous_caller', 'published_articles_exist'] are not factories; moved to steps
- O-1: preconditions ['anonymous_caller', 'published_articles_exist'] are not factories; moved to steps
- O-1: preconditions ['anonymous_caller', 'authenticated_user'] are not factories; moved to steps
- O-2: preconditions ['anonymous_caller', 'article_with_comments'] are not factories; moved to steps
- O-2: preconditions ['anonymous_caller', 'article_with_comments'] are not factories; moved to steps
- O-2: preconditions ['anonymous_caller', 'authenticated_user', 'article_with_comments'] are not factories; moved to steps
- O-3: preconditions ['anonymous_caller', 'existing_profile'] are not factories; moved to steps
- O-3: preconditions ['anonymous_caller', 'existing_profile'] are not factories; moved to steps
- O-3: preconditions ['anonymous_caller', 'authenticated_user', 'existing_profile'] are not factories; moved to steps
- C-1: no acceptance case
- C-1: criterion is assumed, not confirmed: check its cases
- C-3: criterion is assumed, not confirmed: check its cases
- C-7: criterion is assumed, not confirmed: check its cases
- C-8: criterion is assumed, not confirmed: check its cases
- C-9: criterion is assumed, not confirmed: check its cases
- O-1: criterion is assumed, not confirmed: check its cases
- O-2: criterion is assumed, not confirmed: check its cases
- O-3: criterion is assumed, not confirmed: check its cases

## Worked examples (boundaries)

- TC-C3-3 `limit` limit 1 (inclusive): 0 → 422 | 1 → 2xx | 2 → 2xx
- TC-C3-4 `offset` limit 0 (inclusive): -1 → 422 | 0 → 2xx | 1 → 2xx

## FLOW-articles

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O1-1 | P0 | decision_table | caller × Authorization header | 2 rows | Authenticated caller refused or given a different public list than anonymous |
| TC-O2-1 | P0 | decision_table | caller × Authorization header | 2 rows | Authenticated caller refused on comments read or shown different comments than anonymous |
| TC-C7-1 | P0 | decision_table | caller × article ownership | 3 rows | Edit permitted for a caller who does not own the article |
| TC-O1-2 | P0 | acceptance | Anonymous caller gets the public article list | 200 (literal) | Anonymous request refused with 401 instead of the public list |
| TC-O2-2 | P0 | acceptance | Anonymous caller can read article comments | 200 (literal) | Anonymous comments read refused with 401 |
| TC-C10-1 | P0 | acceptance | Authenticated user can comment on an article | 2xx (literal) | Authenticated comment refused or not stored |
| TC-C7-2 | P0 | acceptance | Authenticated user can write a new article | 2xx (literal) | Authenticated user cannot create an article |
| TC-O1-3 | P0 | error_guessing | Empty token header on public list is ignored | 200 (literal) | Empty Authorization header on public list causes 401 or 500 |
| TC-O2-3 | P0 | error_guessing | Empty token header on comments read is ignored | 200 (literal) | Empty Authorization header on comments read causes 401 or 500 |
| TC-C7-3 | P1 | error_exception | Edit to a title used by another article is refused with 409 | 409 (literal) | Duplicate title on update accepted or reported as a server error |
| TC-C3-1 | P1 | acceptance | Offset and limit select the page returned | {'count': 45, 'data_len': 10} (calculator:page_window) | Offset and limit ignored so the page does not follow the parameters |
| TC-C4-1 | P1 | acceptance | Omitted limit returns at most 20 articles | {'count': 21, 'data_len': 20} (calculator:page_window) | Default limit not applied so more than 20 articles are returned |
| TC-C5-1 | P1 | acceptance | Request failing validation gets status 422 | 422 (literal) | Invalid request accepted or answered with a status other than 422 |
| TC-C9-1 | P1 | acceptance | Authenticated user can favourite an article | 2xx (literal) | Favourite of an article refused for an authenticated user |
| TC-C3-2 | P1 | error_guessing | Limit above the cap is clamped | 2xx (literal) | Limit above 100 not clamped, returning more than 100 items or erroring |
| TC-C9-2 | P1 | error_guessing | Favouriting an already favourited article is idempotent | 200 (literal) | Second favourite errors or double-counts the favourite |
| TC-C9-3 | P1 | error_guessing | Unfavouriting a non-favourited article is idempotent | 200 (literal) | Unfavourite of a non-favourited article errors |
| TC-C3-3 | P1 | boundary | limit at the 1 limit | see worked example | Limit of 0 accepted as a valid page size |
| TC-C3-4 | P1 | boundary | offset at the 0 limit | see worked example | Negative offset accepted at the boundary |
| TC-C1-1 | P1 | error_exception | Expired token is refused | 401 (literal) | Token still accepted after its 1 hour expiry |
| TC-C1-2 | P1 | error_exception | Token sent under another scheme is refused | 401 (literal) | Bearer scheme accepted in place of the Token scheme |
| TC-C3-5 | P1 | error_exception | Negative limit is refused | 422 (literal) | Negative limit causes a server error instead of a 422 |
| TC-C3-6 | P1 | error_exception | Non-numeric offset is refused | 422 (literal) | Non-numeric offset causes a server error instead of a 422 |
| TC-C5-2 | P1 | error_exception | Wrong-typed field is refused with 422 rather than a server error | 422 (literal) | Wrong-typed field causes a 500 instead of a 422 validation error |

## FLOW-profiles

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O3-1 | P0 | decision_table | caller × Authorization header | 2 rows | Authenticated caller refused on profile read or shown a different profile than anonymous |
| TC-O3-2 | P0 | acceptance | Anonymous caller can read a profile | 200 (literal) | Anonymous profile read refused with 401 |
| TC-O3-3 | P0 | error_guessing | Empty token header on profile read is ignored | 200 (literal) | Empty Authorization header on profile read causes 401 or 500 |
| TC-C8-1 | P1 | acceptance | Authenticated user can follow an author | 2xx (literal) | Follow of an author refused for an authenticated user |
| TC-C8-2 | P1 | error_guessing | Following an already followed author is idempotent | 200 (literal) | Second follow errors or creates a duplicate follow |
| TC-C8-3 | P1 | error_guessing | Unfollowing a non-followed author is idempotent | 200 (literal) | Unfollow of a non-followed author errors |

## FLOW-users

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-C2-1 | P1 | acceptance | Successful sign-up returns a JWT for the Authorization header | 2xx (literal) | Successful sign-up returns no token |
| TC-C6-1 | P1 | acceptance | New person can sign up and then log in | 2xx (literal) | Sign-up or login fails for a new person with valid details |

## Not tested

- O-4: out of scope in the spec
- O-5: out of scope in the spec
- O-6: out of scope in the spec
- O-7: out of scope in the spec
- O-8: out of scope in the spec
- O-9: out of scope in the spec
- O-10: out of scope in the spec
- O-11: out of scope in the spec
- O-12: out of scope in the spec
- O-13: out of scope in the spec
- O-14: out of scope in the spec
