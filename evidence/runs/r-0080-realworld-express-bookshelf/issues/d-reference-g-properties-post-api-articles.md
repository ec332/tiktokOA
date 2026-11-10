# G:properties:POST /api/articles

**Requirement G:properties:POST /api/articles** (PRODUCT_DEFECT). `property`: expected None, got POST /api/articles → 500: Server error, Undocumented status code; `property`: expected None, got POST /api/articles → 500: Server error, Undocumented status code.

## Reproduce

```sh
#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X POST "$BASE_URL/api/articles" \
  -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"article": {"": {"m\uda4e\ude98\u00b8": null}, "\b\udb2a\udd83\u00fbx\u0001\ud87b\udd27\u00c3": [false], "\u001f\u00f0\u0097\udbc0\udc98P\u0012\u00119\u009e\u0093G\ud855\udcfe\uda7e\udf96\udbb4\ude1a\udaa1\udc21\u0016\ud83f\udcc1}\u0014": [], "body": "%\u00f4\ud854\ude3b\u0017\n\u00a9", "description": "\u001b\u0085\uda90\udd66\u00f8\u00ac)", "tagList": ["\u00f7\u0094\ud8fc\ude85\u00bf0\t\u0015\u00f4\udbad\udf1a\u00ad"], "title": "\uda21\ude0e\udbad\udd7d\u0091", "~I": [], "\u0080{\u0090\uda96\udc402": [[null], {}, ["", null, null]]}, "nil": [[], [null], {}], "\ud999\udc6c\u008f\u001e\u00cc\u00e7": [null]}'
```

Property check over the same operation:

```sh
schemathesis run realworld/spec/openapi.yml --url "$BASE_URL" -H "Authorization: Token $TOKEN" --include-method POST --include-path /api/articles
```

## Why an issue, not a PR

- hard rule not met: fixed_confirmed
- hard rule not met: owned_fork
- hard rule not met: under_daily_cap
- hard rule not met: publishing_on

PR score 4.5 (threshold 3): server_error 3 × plus_planted_bug_or_property 1.5

_Drafted by an automated test platform; cases and tests were written with an AI model._
