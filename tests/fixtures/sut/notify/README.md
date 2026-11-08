# notify (synthetic fixture)

A small app with known answers, used to show that each new iteration 3 check catches a defect
before its passes on real targets count (W0 C10, DR-8). It is **synthetic**: label it that way
wherever it appears.

- `app.py`: standard-library Python. Sign-up (the first needs no token), sign-in, notes, invites.
  - Creating a note POSTs it to `NOTIFY_URL`, from a background thread with a 2 s timeout.
  - An invite sends an email through `SMTP_HOST`/`SMTP_PORT`.
  - `/` is a page with labelled sign-in and note forms.
  - `/openapi.json` is its spec.
- `Dockerfile`: the only way it starts. There is no compose file (the Dockerfile-reach case).
- `seeds/*.patch`: deliberate defects, applied as variants. `seeds/answers.yaml` says which check
  each one must fail.
