# G:auth_required:GET /api/articles

**Requirement G:auth_required:GET /api/articles** (SPEC_MISMATCH). `G:auth_required:GET /api/articles/status`: expected a status the spec documents, got 200.

> SpecMismatch: [no credentials:2xx] GET /api/articles answered 200 with no credentials; the spec declares no security for it

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X GET "$BASE_URL/api/articles" \
  -H "Authorization: Bearer $TOKEN"
```

Property check over the same operation:

```sh
schemathesis run realworld/spec/openapi.yml --url "$BASE_URL" -H "Authorization: Bearer $TOKEN" --include-method GET --include-path /api/articles
```

## Why an issue, not a PR

- hard rule not met: product_defect
- hard rule not met: fixed_confirmed
- hard rule not met: publishing_on

PR score 3.0 (threshold 3): wrong_value 2 × plus_planted_bug_or_property 1.5

_Drafted by an automated test platform; cases and tests were written with an AI model._
