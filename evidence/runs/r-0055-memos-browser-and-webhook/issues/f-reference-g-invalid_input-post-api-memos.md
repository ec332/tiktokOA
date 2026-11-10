# G:invalid_input:POST /api/memos

**Requirement G:invalid_input:POST /api/memos** (SPEC_MISMATCH). `G:invalid_input:POST /api/memos/status`: expected a status the spec documents, got 200.

> SpecMismatch: [missing required fields:2xx] POST /api/memos accepted a body its spec forbids (missing required fields): 200

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X POST "$BASE_URL/api/memos" \
  -H "Authorization: Bearer $TOKEN"
```

Property check over the same operation:

```sh
schemathesis run proto/gen/openapi.yaml --url "$BASE_URL" -H "Authorization: Bearer $TOKEN" --include-method POST --include-path /api/memos
```

## Why an issue, not a PR

- hard rule not met: product_defect
- hard rule not met: fixed_confirmed
- hard rule not met: publishing_on

PR score 3.0 (threshold 3): wrong_value 2 × plus_planted_bug_or_property 1.5

_Drafted by an automated test platform; cases and tests were written with an AI model._
