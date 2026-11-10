# Test cases — sign-off (signed 0dd73b30f9b5)

Flows: FLOW-items-crud. 42/42 lines approved, 0 carried forward from an earlier sign-off.

## Attention

- O-3: dropped a drafted case: expected 'null' is not traceable to O-3 (spec allows ['200', '2xx'])
- O-4: skipped minimum template boundary: Rounding tie is not a numeric threshold, so there is no limit to set
- O-6: dropped a drafted case: expected '4xx' is not traceable to O-6 (spec allows [])
- O-6: skipped minimum template decision_table: Resolution left to human judgement, so no row has a decided outcome
- O-7: dropped a drafted case: expected '4xx' is not traceable to O-7 (spec allows [])
- O-7: dropped a drafted case: expected '4xx' is not traceable to O-7 (spec allows [])
- O-8: decision table leaves out 1 combination(s), e.g. {'caller': 'anonymous', 'discount_percent': 'null'}
- O-9: decision table leaves out 2 combination(s), e.g. {'caller': 'anonymous', 'price': '20.00'}
- O-10: dropped a drafted case: expected '2xx' is not traceable to O-10 (spec allows ['422', '4xx'])
- O-10: decision table leaves out 1 combination(s), e.g. {'caller': 'anonymous', 'title': 'null'}
- O-11: dropped a drafted case: expected 'null' is not traceable to O-11 (spec allows ['200', '2xx'])
- O-12: dropped a drafted case: expected '2xx' is not traceable to O-12 (spec allows [])
- O-12: skipped minimum template decision_table: Outcome left to human judgement with no single decided row; only one caller and input
- O-14: dropped a drafted case: expected '4xx' is not traceable to O-14 (spec allows [])
- O-14: dropped a drafted case: expected '4xx' is not traceable to O-14 (spec allows [])
- O-15: decision table leaves out 1 combination(s), e.g. {'caller': 'superuser', 'method': 'GET'}
- O-17: dropped a drafted table: expected 'own items only, newest first' is not traceable to O-17 (spec allows ['200', '2xx', '422', '4xx'])
- O-18: dropped a drafted table: expected 'all items, count is total' is not traceable to O-18 (spec allows ['200', '2xx', '422', '4xx'])
- O-20: skipped minimum template boundary: Spec gives no maximum above 1000, so the outcome for 1001 is undecided and no fail point can be set
- O-3: dropped a drafted case: expected 'null' is not traceable to O-3 (spec allows ['200', '2xx'])
- O-11: dropped a drafted case: expected 'null' is not traceable to O-11 (spec allows ['200', '2xx'])
- O-14: dropped a drafted case: expected '' is not traceable to O-14 (spec allows [])
- O-3: no acceptance case
- O-6: minimum template error_guessing neither used nor skipped with a reason
- O-7: minimum template error_guessing neither used nor skipped with a reason
- O-11: no acceptance case
- O-12: minimum template error_guessing neither used nor skipped with a reason
- O-14: no acceptance case
- O-14: minimum template error_guessing neither used nor skipped with a reason
- O-18: minimum template decision_table neither used nor skipped with a reason

## Worked examples (boundaries)

- TC-O9-4 `price` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200
- TC-O19-3 `limit` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200
- TC-O19-4 `skip` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200
- TC-O5-2 `discount_percent` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200

## FLOW-items-crud

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O8-1 | P0 | decision_table | caller × discount_percent | 3 rows | validation rejects valid discount on update |
| TC-O9-1 | P0 | decision_table | caller × price | 4 rows | non-owner PUT reaches price validation before the 403 |
| TC-O2-1 | P0 | acceptance | GET item by id returns exact discounted final_price | 8.54 (calculator:percent_off) | final_price missing or undiscounted on GET by id |
| TC-O6-1 | P0 | acceptance | Owner PUT discount is applied to exact final_price | 5.00 (calculator:percent_off) | PUT discount not applied to final_price |
| TC-O7-1 | P0 | acceptance | Valid two-decimal price gives exact final_price | 9.22 (calculator:percent_off) | valid two-decimal price rejected or mis-rounded |
| TC-O8-2 | P0 | acceptance | Valid PUT discount updates exact final_price | 5.00 (calculator:percent_off) | final_price stale after PUT discount change |
| TC-O9-2 | P0 | acceptance | Valid PUT price is stored and final_price is exact | 18.00 (calculator:percent_off) | valid PUT price not stored |
| TC-O2-2 | P0 | error_guessing | List endpoint returns undiscounted price; resolution undecided so expectation follows the title | 8.54 (calculator:percent_off) | list endpoint returns undiscounted final_price |
| TC-O8-3 | P0 | error_guessing | Null discount on PUT refused and item left unchanged | 422 (literal) | null discount on PUT partly applied despite refusal |
| TC-O9-3 | P0 | error_guessing | PUT with price -5 refused and original price kept | 422 (literal) | PUT with price -5 stored despite 422 |
| TC-O9-4 | P0 | boundary | price at the 0 limit | see worked example | price exactly 0 wrongly refused on PUT |
| TC-O8-4 | P0 | error_exception | Null discount on PUT refused with 422 | 422 (literal) | null discount on PUT returns 500 |
| TC-O9-5 | P0 | error_exception | PUT with negative price refused with 422 | 422 (literal) | PUT with price -5 returns 500 instead of 422 |
| TC-O10-1 | P0 | decision_table | caller × title | 3 rows | title validation skipped on PUT for owner |
| TC-O10-2 | P0 | acceptance | PUT with title null by the owner is rejected with a 422 validation error | 422 (literal) | null title is accepted and stored, returning 200 instead of rejecting it with 422 |
| TC-O12-1 | P0 | acceptance | Empty PUT leaves price and discount unchanged, checked via exact final_price | 8.50 (calculator:percent_off) | empty PUT wipes price or discount |
| TC-O19-1 | P0 | acceptance | Negative limit refused with 422 | 422 (literal) | negative limit returns 500 instead of 422 |
| TC-O10-3 | P0 | error_guessing | Null title refused and stored title kept | 422 (literal) | null title overwrites stored title despite refusal |
| TC-O19-2 | P0 | error_guessing | Combined negative paging parameters refused with 422 | 422 (literal) | combined negative skip and limit crashes paging |
| TC-O1-1 | P0 | decision_table | caller | 2 rows | anonymous caller can create an item |
| TC-O11-1 | P0 | decision_table | caller × price | 2 rows | anonymous PUT clears an item price |
| TC-O19-3 | P0 | boundary | limit at the 0 limit | see worked example | limit 0 wrongly rejected |
| TC-O19-4 | P0 | boundary | skip at the 0 limit | see worked example | skip 0 wrongly rejected |
| TC-O10-4 | P0 | error_exception | Null title refused with 422 | 422 (literal) | PUT with null title returns 500 instead of 422 |
| TC-O19-5 | P0 | error_exception | Negative skip refused with 422 | 422 (literal) | negative skip accepted and returns items |
| TC-O1-2 | P1 | acceptance | Create item and check final_price is the exact discounted value | 8.54 (calculator:percent_off) | final_price not computed on create |
| TC-O4-1 | P1 | acceptance | Tie value rounds half to even to the exact stated price | 0.02 (calculator:percent_off) | tie rounds half-up instead of half-even |
| TC-O5-1 | P1 | acceptance | Discount 99 accepted and final_price is exact | 0.10 (calculator:percent_off) | discount 99 rejected by over-strict upper check |
| TC-O5-2 | P1 | boundary | discount_percent at the 0 limit | see worked example | lower edge discount 0 wrongly rejected |
| TC-O5-3 | P1 | error_exception | Discount 100 refused with 422 and no item created | 422 (literal) | discount 100 accepted on create |
| TC-O5-4 | P1 | error_exception | Negative discount refused with 422 | 422 (literal) | negative discount accepted on create |
| TC-O13-1 | P2 | decision_table | caller | 4 rows | owner or superuser blocked by the ownership check on GET |
| TC-O15-1 | P2 | decision_table | caller × method | 3 rows | superuser write blocked by the ownership check |
| TC-O16-1 | P2 | decision_table | caller | 3 rows | owner PUT refused or non-owner PUT allowed |
| TC-O13-2 | P2 | acceptance | Non-owner GET of another user's item is refused | 403 (literal) | non-owner can read another user's item |
| TC-O15-2 | P2 | acceptance | Superuser reads an item owned by another user | 200 (literal) | superuser blocked from reading another user's item |
| TC-O16-2 | P2 | acceptance | Non-owner PUT refused with 403 | 403 (literal) | non-owner PUT not refused with 403 |
| TC-O17-1 | P2 | acceptance | Own items listed newest first by created_at | [3, 2, 1] (calculator:newest_first) | list not ordered newest first for ordinary user |
| TC-O18-1 | P2 | acceptance | Superuser list shows all items newest first | [4, 3, 2, 1] (calculator:newest_first) | superuser list not ordered newest first |
| TC-O20-1 | P2 | acceptance | Limit 1000 returns all items up to 1000 in one response | {'count': 150, 'data_len': 150} (calculator:page_window) | list capped at 100 items regardless of limit |
| TC-O13-3 | P2 | error_exception | 403 response carries no item details | 403 (literal) | 403 response body includes item details |
| TC-O16-3 | P2 | error_exception | Item unchanged after refused non-owner PUT, checked via exact final_price | 9.00 (calculator:percent_off) | non-owner PUT persists change before refusing |

## Not tested

