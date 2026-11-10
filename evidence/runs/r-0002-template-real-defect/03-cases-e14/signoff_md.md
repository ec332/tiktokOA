# Test cases — sign-off (signed e02ed73bdb46)

Flows: FLOW-items-crud. 27/27 lines approved, 0 carried forward from an earlier sign-off.

## Attention

- C-2: dropped a drafted case: expected '2xx' is not traceable to C-2 (spec allows [])
- O-10: decision table leaves out 4 combination(s), e.g. {'caller': 'ordinary user', 'item owner': 'other user', 'item has price': 'without price'}
- O-11: decision table leaves out 3 combination(s), e.g. {'reader': 'non-owner ordinary user', 'item owner': 'self'}
- C-2: dropped a drafted case: expected 'null' is not traceable to C-2 (spec allows [])
- C-2: no acceptance case

## Worked examples (boundaries)

- TC-O3-3 `discount_percent` limit 100 (inclusive): 99 → 200 | 100 → 200 | 101 → 422
- TC-O3-4 `discount_percent` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200
- TC-O4-2 `price` limit 0 (inclusive): -1 → 422 | 0 → 200 | 1 → 200

## FLOW-items-crud

| id | prio | technique | purpose | expected | catches |
|---|---|---|---|---|---|
| TC-O3-1 | P0 | acceptance | Verify discount_percent 100 is accepted and gives a final_price of 0.00 | 0.00 (calculator:percent_off) | Full discount of 100 is rejected or gives a final_price other than 0.00 |
| TC-O5-1 | P0 | acceptance | Verify GET returns final_price 0.03 for a half-cent discounted price | 0.03 (calculator:percent_off) | GET route shows a final_price for a half-cent value that differs from round-half-up |
| TC-O8-1 | P0 | acceptance | Verify PUT with a negative price is refused with 422 | 422 (literal) | Update schema lacks the price ge 0 bound, so a negative price is accepted |
| TC-O9-1 | P0 | acceptance | Verify PUT with explicit null discount_percent is refused with 422 and not a server error | 422 (literal) | Explicit null discount_percent causes a 5xx instead of a 422 |
| TC-O3-2 | P0 | error_guessing | Verify a very large discount_percent is refused with 422 and not a server error | 422 (literal) | Huge discount_percent is accepted or causes a 5xx instead of 422 |
| TC-O5-2 | P0 | error_guessing | Verify creating an item with a half-cent discounted price gives final_price 0.03 | 0.03 (calculator:percent_off) | Half-cent value rounds half-even to 0.02 on create |
| TC-O8-2 | P0 | error_guessing | Verify a very large negative price on PUT is refused with 422 and not a server error | 422 (literal) | Very large negative price on update causes overflow or a 5xx |
| TC-O9-2 | P0 | error_guessing | Verify null discount_percent on an item with no price is refused with 422 | 422 (literal) | Null discount_percent crashes the update when price is also null |
| TC-O3-3 | P0 | boundary | discount_percent at the 100 limit | see worked example | Upper bound off by one: discount_percent above 100 is accepted |
| TC-O3-4 | P0 | boundary | discount_percent at the 0 limit | see worked example | Lower bound off by one: discount_percent 0 is rejected |
| TC-O3-5 | P0 | error_exception | Verify a negative discount_percent is refused with 422 | 422 (literal) | Negative discount_percent is accepted and stored |
| TC-O8-3 | P0 | error_exception | Verify a refused negative-price PUT leaves the stored item unchanged | 422 (literal) | Failed update partially writes fields before rejecting the price |
| TC-O9-3 | P0 | error_exception | Verify a refused null-discount PUT leaves the stored discount and final_price unchanged | 422 (literal) | Failed null-discount update partially changes the stored discount or final_price |
| TC-C1-1 | P0 | acceptance | Verify a response shows the discounted price for an item with a price and discount percentage | 16.99 (calculator:percent_off) | Discounted price shown in responses is not price x (100 - discount) / 100 rounded half-up to 2 decimals |
| TC-O1-1 | P1 | acceptance | Verify creating an item with price and discount returns the computed, rounded final_price | 16.99 (calculator:percent_off) | Created item response has a final_price that is not price x (100 - discount) / 100 rounded half-up to 2 places |
| TC-O2-1 | P1 | acceptance | Verify creating an item without price returns null final_price and default discount 0 | 200 (literal) | Created item without price has a non-zero default discount or a non-null final_price |
| TC-O4-1 | P1 | acceptance | Verify a price with more than 2 decimals is refused with 422 and no item is created | 422 (literal) | Price with 3 decimals is accepted and stored |
| TC-O6-1 | P1 | acceptance | Verify a PUT sending only discount_percent keeps the stored price and recomputes final_price | 40.00 (calculator:percent_off) | Update that sends only discount_percent clears the stored price or leaves final_price stale |
| TC-O7-1 | P1 | acceptance | Verify PUT with explicit price null clears the price and final_price | 200 (literal) | Explicit null price is ignored, so the stored price stays 50.00 |
| TC-O4-2 | P1 | boundary | price at the 0 limit | see worked example | Price 0 is wrongly rejected |
| TC-O4-3 | P1 | error_exception | Verify a negative price is refused with 422 and no item is created | 422 (literal) | Negative price is accepted on create |
| TC-O4-4 | P1 | error_exception | Verify a price above 10 digits is refused with 422 and no item is created | 422 (literal) | Price with more than 10 digits is accepted on create |
| TC-O10-1 | P2 | decision_table | caller × item owner × item has price | 4 rows | List shows other users' items to an ordinary user, or hides items from a superuser |
| TC-O11-1 | P2 | decision_table | reader × item owner | 3 rows | Non-owner ordinary user can read another user's item, or is refused with a status other than 403 |
| TC-O10-2 | P2 | acceptance | Verify GET list returns final_price for the caller's items | 200 (literal) | List response omits the final_price field |
| TC-O11-2 | P2 | acceptance | Verify the owner can read an item with its final_price | 2xx (literal) | Owner read of an item omits final_price |
| TC-O11-3 | P2 | error_exception | Verify a non-owner read is refused with 403 and returns no item fields or final_price | 403 (literal) | 403 response body includes item fields or final_price |

## Not tested

