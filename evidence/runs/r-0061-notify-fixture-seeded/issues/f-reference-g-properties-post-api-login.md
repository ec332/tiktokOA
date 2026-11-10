# G:properties:POST /api/login

**Requirement G:properties:POST /api/login** (SPEC_MISMATCH). `G:properties:POST /api/login/status`: expected a status the spec documents, got 400.

> SpecMismatch: [property] POST /api/login: 3 example(s) disagree with the spec (Undocumented status code); first /api/login → 400

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X POST "$BASE_URL/api/login" \
  -H "Authorization: Bearer $TOKEN"
```

Property check over the same operation:

```sh
schemathesis run "$BASE_URL/openapi.json" --url "$BASE_URL" -H "Authorization: Bearer $TOKEN" --include-method POST --include-path /api/login
```

## Why an issue, not a PR

- hard rule not met: product_defect
- hard rule not met: fixed_confirmed
- hard rule not met: publishing_on

PR score 3.0 (threshold 3): wrong_value 2 × plus_planted_bug_or_property 1.5

_Drafted by an automated test platform; cases and tests were written with an AI model._
