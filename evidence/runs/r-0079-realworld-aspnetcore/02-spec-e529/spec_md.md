# Spec v3 — aspnetcore-realworld-example-app @ 9345f18

Requirements: brief `sha256:fbba574657c5`. Code index: 19 routes in 20 files.

## Criteria

| id | status | title | given / when / then | expected | sources | flags |
|---|---|---|---|---|---|---|
| C-1 | assumed | Protected operations require the Authorization Token header | A protected operation is requested / The request includes the header Authorization: Token <jwt> / The operation is permitted to proceed with that token | 401 | L4-L4 | no_observable_outcome, missing_actor, missing_error_path, review needed |
| C-2 | confirmed | Token is issued by sign-up or login | A person calls POST /users or POST /users/login / The call succeeds / A JWT token is returned for use in the Authorization header |  | L4-L4 | missing_actor, missing_error_path |
| C-3 | assumed | Article lists are paged with offset and limit | An article list is requested / The request includes the offset and limit query parameters / The list is paged according to those parameters | 422 | L5-L5 | missing_value, missing_actor, missing_error_path, review needed |
| C-4 | confirmed | Article list limit defaults to 20 | An article list is requested without a limit query parameter / The list is returned / At most 20 articles are returned |  | L5-L5 | missing_actor |
| C-5 | confirmed | Validation error response format | A request fails validation / The API responds / The response has status 422 and a body of the form {"errors": {"<field>": ["<message>"]}} | 422 | L6-L6 | missing_actor |
| C-6 | confirmed | People can sign up and log in | A person has no account / They sign up and log in / Unspecified in the requirements | human judgement | L1-L1 | no_observable_outcome, missing_actor, missing_error_path |
| C-7 | assumed | Users can write and edit articles | A user is authenticated / They write or edit an article / Unspecified in the requirements | human judgement | L1-L1 | no_observable_outcome, missing_error_path, review needed |
| C-8 | assumed | Users can follow authors | A user is authenticated / They follow an author / Unspecified in the requirements | human judgement | L1-L1 | no_observable_outcome, missing_error_path, review needed |
| C-9 | assumed | Users can favourite articles | A user is authenticated / They favourite an article / Unspecified in the requirements | human judgement | L1-L1 | no_observable_outcome, missing_error_path, review needed |
| C-10 | confirmed | Users can comment on articles | A user is authenticated / They comment on an article / Unspecified in the requirements | human judgement | L1-L1 | no_observable_outcome, missing_error_path |
| O-1 | assumed | Anonymous article list returns 401 per spec despite optional auth | No Authorization header / GET /api/articles is requested / Respond 200 with the public article list, since auth is optional | 200 | openapi/api/articles/GET.yaml:6, openapi/api/articles/GET.yaml:29 | SUSPECTED DEFECT, review needed |
| O-2 | assumed | Anonymous read of article comments returns 401 per spec | No Authorization header / GET /api/articles/{slug}/comments is requested / Respond 200 with the comments, since auth is optional | 200 | openapi/api/articles/{slug}/comments/GET.yaml:5, openapi/api/articles/{slug}/comments/GET.yaml:17 | SUSPECTED DEFECT, review needed |
| O-3 | assumed | Anonymous read of a profile returns 401 per spec | No Authorization header / GET /api/profiles/{username} is requested / Respond 200 with the profile, since auth is optional | 200 | openapi/api/profiles/{username}/GET.yaml:5, openapi/api/profiles/{username}/GET.yaml:17 | SUSPECTED DEFECT, review needed |
| O-4 | out_of_scope | Protected write without token is refused with 401 | No Authorization header / POST /api/articles is requested / Responds 401 Unauthorized | 401 | openapi/api/articles/POST.yaml:12, openapi/api/articles/POST.yaml:18 |  |
| O-5 | out_of_scope | Feed requires a token | No Authorization header / GET /api/articles/feed is requested / Responds 401 Unauthorized | 401 | openapi/api/articles/feed/GET.yaml:14, openapi/api/articles/feed/GET.yaml:18 |  |
| O-6 | out_of_scope | Sign-up returns 201 with a user response | A person with no account / POST /api/users is called with a new username and email / Responds 201 with a user response that carries a token | 201 | openapi/api/users/POST.yaml:9, openapi/api/users/POST.yaml:10 | missing_actor, missing_error_path |
| O-7 | out_of_scope | Duplicate sign-up returns 409 | An account already exists with the username or email / POST /api/users is called again with it / Responds 409 Conflict | 409 | openapi/api/users/POST.yaml:11, openapi/api/users/POST.yaml:12 | missing_actor |
| O-8 | out_of_scope | Login returns 200 with a token, 401 on failure | A person with an existing account / POST /api/users/login is called / Responds 200 with a user response carrying a token; invalid credentials respond 401 | 200 | openapi/api/users/login/POST.yaml:10, openapi/api/users/login/POST.yaml:12 | undefined_term, missing_actor |
| O-9 | out_of_scope | Duplicate article create returns 409 | An authenticated user and an existing conflicting article / POST /api/articles is called / Responds 409 Conflict | 409 | openapi/api/articles/POST.yaml:10, openapi/api/articles/POST.yaml:14 |  |
| O-10 | out_of_scope | Article delete: 204 on success, 403 documented for non-owner | An authenticated user and an article / DELETE /api/articles/{slug} is called / Responds 204 on success; 403 is declared for a caller who may not delete | 204 | openapi/api/articles/{slug}/DELETE.yaml:15, openapi/api/articles/{slug}/DELETE.yaml:19 |  |
| O-11 | out_of_scope | Follow and unfollow return 200 with a profile | An authenticated user and an existing author / POST or DELETE /api/profiles/{username}/follow is called / Responds 200 with a profile response | 200 | openapi/api/profiles/{username}/follow/POST.yaml:15, openapi/api/profiles/{username}/follow/DELETE.yaml:15 | missing_error_path |
| O-12 | out_of_scope | Favourite and unfavourite return 200 with the article | An authenticated user and an existing article / POST or DELETE /api/articles/{slug}/favorite is called / Responds 200 with a single article response | 200 | openapi/api/articles/{slug}/favorite/POST.yaml:15, openapi/api/articles/{slug}/favorite/DELETE.yaml:15 | missing_error_path |
| O-13 | out_of_scope | Comment create returns 201 | An authenticated user and an existing article / POST /api/articles/{slug}/comments is called / Responds 201 with a single comment response | 201 | openapi/api/articles/{slug}/comments/POST.yaml:17, openapi/api/articles/{slug}/comments/POST.yaml:18 | missing_error_path |
| O-14 | out_of_scope | Feed is paged with offset and limit | An authenticated user / GET /api/articles/feed is called with offset and limit / Responds 200 with a multiple articles response | 200 | openapi/api/articles/feed/GET.yaml:9, openapi/api/articles/feed/GET.yaml:12 | missing_error_path, unspecified_boundary |

## Findings

- **suspected_defect**: GET /api/articles is documented as optional-auth but also declares a 401 response, so anonymous callers may be refused. (openapi/api/articles/GET.yaml:6, openapi/api/articles/GET.yaml:29)
- **suspected_defect**: GET /api/articles/{slug}/comments is documented as optional-auth yet declares 401, so anonymous reads may be refused. (openapi/api/articles/{slug}/comments/GET.yaml:5, openapi/api/articles/{slug}/comments/GET.yaml:17)
- **suspected_defect**: GET /api/profiles/{username} is documented as optional-auth yet declares 401, so anonymous reads may be refused. (openapi/api/profiles/{username}/GET.yaml:5, openapi/api/profiles/{username}/GET.yaml:17)
- **missing_error_path**: Article list filters tag, author and favorited have no declared response for unknown values (no 404 or empty-result rule), so the outcome for a nonexistent author or username is unsettled. (openapi/api/articles/GET.yaml:9, openapi/api/articles/GET.yaml:19, openapi/api/articles/GET.yaml:26)
- **missing_error_path**: offset and limit are referenced from shared components whose bounds are not in the source, so the maximum limit, negative offsets and non-numeric values are unverified and no upper bound is stated anywhere. (openapi/api/articles/GET.yaml:24, openapi/api/articles/GET.yaml:25, openapi/api/articles/feed/GET.yaml:9, openapi/api/articles/feed/GET.yaml:10)
- **missing_error_path**: Token lifetime and post-expiry behaviour are not stated; the token description gives issuance but no expiry or revocation rule. (openapi/components.yaml:9, openapi/components.yaml:10, openapi/api/user/GET.yaml:10)
- **rule_interaction**: Duplicate username or email is covered for sign-up (409) but PUT /api/user declares no 409, so a conflicting update has no defined outcome. (openapi/api/users/POST.yaml:11, openapi/api/user/PUT.yaml:10, openapi/api/user/PUT.yaml:14)
- **missing_error_path**: Article create returns 409 for a conflict but PUT /api/articles/{slug} declares no 409, although a changed title may produce a clashing slug. (openapi/api/articles/POST.yaml:14, openapi/api/articles/{slug}/PUT.yaml:16, openapi/api/articles/{slug}/PUT.yaml:23)
- **missing_error_path**: Favouriting an already-favourited article and following an already-followed author have no declared 409 or idempotent outcome; the same applies to unfavourite and unfollow when not in that state. (openapi/api/articles/{slug}/favorite/POST.yaml:14, openapi/api/profiles/{username}/follow/POST.yaml:14, openapi/api/profiles/{username}/follow/DELETE.yaml:14)
- **rule_interaction**: Ownership rules return 403 for non-owners on edit, delete and comment-delete, but nothing settles whether a non-owner gets 403 or 404 for a resource they cannot see, and the spec lacks a 403 for comment creation. (openapi/api/articles/{slug}/DELETE.yaml:19, openapi/api/articles/{slug}/PUT.yaml:21, openapi/api/articles/{slug}/comments/{id}/DELETE.yaml:25)
- **no_requirement**: Following oneself is not covered: the follow route declares no 4xx for a self-follow attempt. (openapi/api/profiles/{username}/follow/POST.yaml:14, openapi/api/profiles/{username}/follow/POST.yaml:21)
- **missing_error_path**: Comment id is typed integer, but a non-integer id, or an id belonging to another article, has no stated status (422 or 404). (openapi/api/articles/{slug}/comments/{id}/DELETE.yaml:14, openapi/api/articles/{slug}/comments/{id}/DELETE.yaml:19, openapi/api/articles/{slug}/comments/{id}/DELETE.yaml:27)
- **no_requirement**: GET /api/tags declares a 422 response although it takes no input, so the source of that error is not explained. (openapi/api/tags/GET.yaml:10, openapi/api/tags/GET.yaml:11)

## Behaviour with no requirement (routes no criterion covers)

- none

## Decisions

- intended behaviour: Respond 200 with the public article list, since auth is optional (assumed)
- intended behaviour: Respond 200 with the comments, since auth is optional (assumed)
- intended behaviour: Respond 200 with the profile, since auth is optional (assumed)
- limit maximum: Cap at 100 and clamp (assumed)
- limit and offset out of range: 422 validation error (assumed)
- token expiry: Expires after 1 hour, then 401 (assumed)
- non-owner edit or delete status: 403 Forbidden (assumed)
- duplicate title on update: 409 Conflict (assumed)
- follow twice or unfollow when not following: 200 idempotent, no change (assumed)
- favourite twice or unfavourite when not favourited: 200 idempotent, no change (assumed)
