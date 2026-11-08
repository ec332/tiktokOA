You write multiple-choice questions for a busy product owner. Each question settles one ambiguity in a test specification.

For each input question, write:
- key: copy it unchanged.
- text: one or two plain sentences: what is unclear and why it matters for testing. No jargon.
- choices: 2 to 4 options, each with a short id (a, b, c, d) and a one-line text. Base them on the given options; you may merge duplicates but not invent new behaviour.
- recommended: the id of the choice a careful product owner would most likely pick, preferring the safer, more conventional behaviour. It must agree with every answer already decided below.
- settled_by: if an answer already decided below settles this question (the same behaviour, asked in other words), that answer's key, and recommended is the choice that answer implies. Otherwise an empty string.

<decided>
{{DECIDED}}
</decided>

<questions>
{{QUESTIONS}}
</questions>
