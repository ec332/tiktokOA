# Article lists are paged with offset and limit

**Requirement C-3** (SPEC_MISMATCH). `TC-C3-5/status`: expected a status the spec documents, got 500.

> ExpectationFailed: [TC-C3-5/status] expected '422', got 500

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X GET "$BASE_URL/api/articles" \
  -H "Authorization: Token $TOKEN"
```

Property check over the same operation:

```sh
schemathesis run realworld/spec/openapi.yml --url "$BASE_URL" -H "Authorization: Token $TOKEN" --include-method GET --include-path /api/articles
```

## Why an issue, not a PR

- hard rule not met: product_defect
- hard rule not met: fixed_confirmed
- hard rule not met: owned_fork
- hard rule not met: under_daily_cap
- hard rule not met: publishing_on

PR score 3.0 (threshold 3): server_error 3 × stable 1.0

_Drafted by an automated test platform; cases and tests were written with an AI model._
