#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X POST "$BASE_URL/api/memos" \
  -H "Authorization: Bearer $TOKEN"
