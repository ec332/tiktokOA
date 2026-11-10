#!/bin/sh
# BASE_URL and TOKEN come from the environment
set -eu
curl -sS -i -X GET "$BASE_URL/api/correspondents/1/" \
  -H "Authorization: Token $TOKEN"
