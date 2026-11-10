# G:properties:GET /api/articles/{slug}

**Requirement G:properties:GET /api/articles/{slug}** (SPEC_MISMATCH). `G:properties:GET /api/articles/{slug}/status`: expected a status the spec documents, got 404.

> SpecMismatch: [property] GET /api/articles/{slug}: 10 example(s) disagree with the spec (Response violates schema); first /api/articles/0 → 404

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X GET "$BASE_URL/api/articles/0" \
  -H "Authorization: Token $TOKEN"
```

Property check over the same operation:

```sh
schemathesis run realworld/spec/openapi.yml --url "$BASE_URL" -H "Authorization: Token $TOKEN" --include-method GET --include-path /api/articles/0
```

## Why an issue, not a PR

- hard rule not met: product_defect
- hard rule not met: fixed_confirmed
- hard rule not met: owned_fork
- hard rule not met: under_daily_cap
- hard rule not met: publishing_on

PR score 3.0 (threshold 3): wrong_value 2 × plus_planted_bug_or_property 1.5

_Drafted by an automated test platform; cases and tests were written with an AI model._
