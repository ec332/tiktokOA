#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X GET "$BASE_URL/api/custom_fields/?page=5197553" \
  -H "Authorization: Token $TOKEN"
