# Test report — summary

Labels: real

## P0 — defects
- none

## P1 — untested or weak
- weak test (cannot fail on any mutant): TC-O10-1.3 → a row of O-10 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O11-1.2 → a row of O-11 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O13-1.4 → a row of O-13 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O15-1.2 → a row of O-15 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O15-1.3 → a row of O-15 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O16-1.3 → a row of O-16 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O19-3.b → a row of O-19 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O19-3.c → a row of O-19 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O19-4.b → a row of O-19 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O19-4.c → a row of O-19 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O1-1.1 → a row of O-1 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O1-1.2 → a row of O-1 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O1-2 → a row of O-1 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O5-2.a → a row of O-5 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O5-2.c → a row of O-5 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O5-4 → a row of O-5 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O7-1 → a row of O-7 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O8-1.3 → a row of O-8 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O8-2 → a row of O-8 whose request reaches the item rules (the anonymous row stops at auth)
- weak test (cannot fail on any mutant): TC-O9-1.4 → a row of O-9 whose request reaches the item rules (the anonymous row stops at auth)
- signed case not bound to a test: TC-O12-1 → bind TC-O12-1 (needs the clock/decode patches)
- signed case not bound to a test: TC-O16-3 → bind TC-O16-3 (needs the clock/decode patches)
- signed case not bound to a test: TC-O2-1 → bind TC-O2-1 (needs the clock/decode patches)
- signed case not bound to a test: TC-O4-1 → bind TC-O4-1 (needs the clock/decode patches)
- criterion with no admitted test: O-1 → TC-O1-1, TC-O1-2
- criterion with no admitted test: O-12 → TC-O12-1
- criterion with no admitted test: O-19 → TC-O19-1, TC-O19-2, TC-O19-3, TC-O19-4, TC-O19-5
- criterion with no admitted test: O-2 → TC-O2-1, TC-O2-2
- criterion with no admitted test: O-4 → TC-O4-1
- criterion with no admitted test: O-6 → TC-O6-1
- criterion with no admitted test: O-7 → TC-O7-1

## P2 — test health
- redundant 16/62 (26%), mutation strength 14/31 (45%), health findings 62, quarantined 0

## P3 — spec and code drift

## Scorecard
- seeded detected (authored) 0/0; blind not run (M7)
- real defects 0/0
- triage correct on seeded rows 0/0
- localisation top1 0/0
- localisation top3 0/0
- localisation top5 0/0
- fail before / pass after: not run (M5)
