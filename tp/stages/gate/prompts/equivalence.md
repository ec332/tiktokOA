You review surviving mutants from mutation testing of a web API backend. A mutant is **equivalent** when no request to the running application could ever behave differently from the original code: the change is unreachable, or produces the same observable result for every input. When in doubt, it is **not** equivalent: a survivor that a better test could kill is a test gap, and calling it equivalent hides that gap.

Each mutant shows the changed lines of one function (`-` original, `+` mutant) and where it is.

<survivors>
{{SURVIVORS}}
</survivors>

For every mutant, return `equivalent` (true/false) and a one-sentence `reason` naming the input or the code path that makes it equivalent, or that would expose it.
