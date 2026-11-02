You suggest a fix for one defect that automated tests found in a web API. The evidence below is real: the failing request and response, the failed assertions, the requirement the test checks, and the source the platform's localiser points at.

Propose the smallest code change that makes the behaviour match the requirement. Express it as edits: each edit names a file, an exact `find` string copied from that file (it must occur exactly once; include enough surrounding text to make it unique), and its `replace` text. Change only what the fix needs; keep the code style. If the evidence does not point at a code defect you can fix with confidence, return no edits and say why.

Write `explanation` as two or three plain sentences: what goes wrong, for which input, and what the change does.

<requirement>
{{CRITERION}}
</requirement>

<failing_exchange>
{{OBSERVED}}
</failing_exchange>

<failed_assertions>
{{ASSERTIONS}}
</failed_assertions>

<root_cause>
{{ROOT_CAUSE}}
</root_cause>

<suspects>
{{SUSPECTS}}
</suspects>

<source>
{{SOURCE}}
</source>
