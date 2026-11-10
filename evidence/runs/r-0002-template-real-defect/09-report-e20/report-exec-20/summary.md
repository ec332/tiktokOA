# Test report — summary

Labels: real

## P0 — defects
- **O-1** Create item with price and discount returns rounded final_price (real): POST /api/v1/items/ → 200; top suspect `backend/app/api/routes/items.py:62`
- **O-3** Create item rejects discount_percent of 100 or more and negatives (real): POST /api/v1/items/ → 422; top suspect `backend/app/api/routes/items.py:62`
- **O-5** Final price rounds half-even at the cent (real): POST /api/v1/items/ → 200; top suspect `backend/app/api/routes/items.py:62`
- **O-6** Update discount only keeps stored price and recomputes final_price (real): PUT /api/v1/items/6ff70fda-5b8a-4c50-9b4b-4154a25e6dab → 200; top suspect `backend/app/api/routes/items.py:76`
- **O-8** Update accepts a negative price (real): PUT /api/v1/items/0388b5fa-a20e-4ec6-8f10-57393ae989f1 → 500; top suspect `backend/app/api/routes/items.py:75`
- **O-9** Update with explicit null discount_percent fails with a server error (real): PUT /api/v1/items/2d00b7c1-cb28-4447-b36d-e0de4629f1bc → 500; top suspect `backend/app/api/routes/items.py:94`

## P1 — untested or weak
- weak test (cannot fail on any mutant): TC-C1-1 → a row of C-1 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O10-1.1 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O10-1.2 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O10-1.3 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O10-1.4 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O10-2 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-2 → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-3.a → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-3.c → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-4.a → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-4.c → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O3-5 → a row of O-3 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O4-2.a → a row of O-4 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O4-3 → a row of O-4 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O8-2 → a row of O-8 whose request reaches the item rules (the anonymous row stops at auth)
- signed case not bound to a test: TC-O3-1 → bind TC-O3-1 (needs the clock/decode patches)
- signed case not bound to a test: TC-O5-1 → bind TC-O5-1 (needs the clock/decode patches)
- criterion with no admitted test: C-1 → TC-C1-1
- criterion with no admitted test: O-1 → TC-O1-1
- criterion with no admitted test: O-10 → TC-O10-1, TC-O10-2
- criterion with no admitted test: O-5 → TC-O5-1, TC-O5-2
- criterion with no admitted test: O-6 → TC-O6-1
- criterion with no admitted test: O-8 → TC-O8-1, TC-O8-2, TC-O8-3
- criterion with no admitted test: O-9 → TC-O9-1, TC-O9-2, TC-O9-3

## P2 — test health
- redundant 8/36 (22%), mutation strength 13/31 (42%), health findings 0, quarantined 0

## P3 — spec and code drift

## Scorecard
- seeded detected (authored) 0/0; blind not run (M7)
- real defects 6/6 (100%)
- triage correct on seeded rows 0/0
- localisation top1 0/0
- localisation top3 0/0
- localisation top5 0/0
- fail before / pass after: not run (M5)
