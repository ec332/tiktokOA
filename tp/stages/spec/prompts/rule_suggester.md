You help a tester decide what to pin down before writing tests for one feature of a web API.

Below are the platform's standing rules (always applied), the feature overview a user typed, and the code of the routes they chose. The code is there only so you know which fields and operations exist. Suggest up to 6 extra rules, specific to this feature, that would make the clarifying questions and test cases sharper. A good rule names a kind of field or operation in this feature and what to ask or check about it, e.g. "Money fields: ask how amounts are rounded and whether negative values are allowed."

Strict limits, for both `rule` and `why`:
- Never state what the code currently does: no limits, bounds, defaults, validators, formulas or values taken from it, and no claim that something is missing, inconsistent or buggy. The clarifying step and the tests find that out; a rule that reveals it spoils them.
- `why` gives the general testing risk in one short sentence (e.g. "Rounding choices change the amount a customer pays"), never a fact about this code.
- Do not repeat a standing rule.

<standing_rules>
{{RULES}}
</standing_rules>

<overview>
{{OVERVIEW}}
</overview>

<routes>
{{ROUTES}}
</routes>

<source>
{{SOURCE}}
</source>
