#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X POST "$BASE_URL/api/login" \
  -H "Authorization: Bearer $TOKEN"
