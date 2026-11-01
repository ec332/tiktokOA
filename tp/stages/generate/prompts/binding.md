You write the BINDING region of one generated pytest test for an HTTP API. Everything else in the file is fixed: the case data, the setup, and a LOCKED block of assertions after your region. You never see or change the expected values.

## What your code can use (already in scope)

- `data`: the request fields **this case fixes** (`CASE_DATA[case_id]`). Send every key **exactly**, with the same value and type: as a query parameter if the route takes it as a query parameter, otherwise as a JSON body field. A value equal to `ABSENT` means: do not send that key at all. `data` is not the whole request: add any other field the route requires (see the OpenAPI excerpt) with an ordinary valid value, e.g. a short title.
- `hints`: facts about the situation that are not request data (who the caller is, which item, how many items exist, which operation). Build the situation they describe.
- `case_id`, and the setup variables already created above your region: {{SETUP}}
- `make`: test-data factories. `make.user()` → `{id, email, password, full_name, is_active, token}`; `make.superuser()` → `{email, token}`; `make.item(owner=user)` → `{id, owner_id, title, description}` (a new owner if none is given); `make.inactive_user()` → a user with `token` None. Use them for any extra users or items you need (e.g. "another user's item", "5 items in total").
- `api.get/post/put/patch/delete(path, token=None, params=None, json=None)` → an `httpx.Response`. Paths are the full OpenAPI paths (e.g. `/api/v1/items/`). Pass the caller's token (e.g. `token=user["token"]`); `token=None` sends no credentials and is only for an anonymous caller (`REQUEST[case_id]["auth"]` is false).
- `observe(name, value)`: hand a value read from a response to the locked block. {{OBSERVE}}

## Rules (a static checker rejects the file otherwise)

- Only statements for the body of the test function: no imports, no `def`, no `class`, no `assert`, no `try`, no `with`, no `return`/`raise`, no `pytest.skip`, no sleeping, randomness or clocks, no `eval`/`getattr`/dunder access, never touch `EXPECTED`, `CASE_DATA`, `api`, `expect` or `make` other than calling them.
- The request under test is the **last** request you send that goes to one of the routes in `REQUEST[case_id]["routes"]`, with a token if `REQUEST[case_id]["auth"]` is true and with `token=None` if it is false, and that carries `data`. The locked block asserts on its response, and fails the test if no request fits. Send it last (after any setup requests such as creating other items).
- `observe()` values must come from responses, never constants.
- If the case cannot be bound with these tools (e.g. it needs control over time, token expiry or something no route or factory provides), leave `binding` empty and say why in `cannot_bind`.

## The case

{{CASE}}

## The routes (OpenAPI excerpt)

{{OPENAPI}}

## The file (expected values hidden)

```python
{{SOURCE}}
```

Return the binding statements (no leading indentation) in `binding`, and `cannot_bind` as an empty string unless you can't bind it.
