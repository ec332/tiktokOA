# Stage 7: Execute (technical reference)

[← Guide](../GUIDE.md) · previous: [Env](06-env.md) · next: [Triage](08-triage.md)

| | |
|---|---|
| Module | `tp/stages/execute/stage.py` (`ExecuteStage`), `decide.py`, `record.py` |
| Version | `1.2.0` |
| Consumes | `gate_report`, `bound_tests`, `env_spec`, `run_options` |
| Produces | `run_records` |
| Model | none |
| Person | none |
| Events | `execute_variant`, `execute_sealed` |

## Purpose

Execute runs every gated test against the real system, over the network, several times, on the reference variant and on each seeded variant. It records every exchange, turns the attempts into a verdict for each test, and seals the records so they can't be changed later.

## Algorithm

1. **Write-once.** If `data/runs/execute-<id>/records.sha256` exists, the stage fails: `… is already sealed (write-once)`.
2. **Rows.** Every gate row runs, except those whose status is `REJECTED` (legacy v1), `UNBOUND` or `EXCLUDED`. Each row runs in a **lane** that labels why it runs: `main`, `main:candidate` (CANDIDATE_DEFECT), `main:spec_gap` or `main:weak`.
3. **Variants.** `run_options.variants` if given, otherwise `reference` plus `bug:<flag>` for every seeded flag. `reference` always runs first, because escalation needs it.
4. **For each variant:**
   1. `reach.provision(variant)`. If bring-up fails, every attempt is `NOT_RUN` with reason `ENV: bring-up failed`.
   2. A reference whose `spec_hash` differs from `env_spec` gets `fingerprint_mismatch: true`, and its results are not trusted (`SUSPECT_ENV`).
   3. **K attempts** (`K = 3`, or the `k` option). Each attempt is one pytest **batch**, run between two probes:
      - **pre-probe** fails: the batch and the rest of the variant are `NOT_RUN` (`ENV: pre-probe failed`);
      - the runner errors or times out: missing nodes are `NOT_RUN`;
      - **post-probe** fails: the rows are `SUSPECT_ENV`;
      - a failed attempt that met **a 429** anywhere, with no injected fault, is `SUSPECT_ENV` (`ENV: rate limited (429)`).

      `TP_RUN=x<exec>.<variant>.a<n>` names everything the attempt creates. On the last attempt (when k ≥ 3), `TP_SHUFFLE=<random seed>` checks that test order doesn't matter.
   4. **Observation lane.** Tests that are `INTERMITTENT` on this variant run `ESCALATE_K = 10` more times here. On a seeded variant they also run 10 times on `reference`.
   5. The variant's logs are collected, then the variant is torn down. `reference` stays up.
5. **Verdicts** (`decide.verdict`, over the main-lane attempts of each (test, variant)).
6. **Seal.** `record.write` writes these files, hashes each one into `records.sha256`, and returns the seal's own hash (`root`):
   - `results.jsonl`;
   - `probes.json`;
   - `dependencies.json` (what stand-ins received);
   - `manifest.json` (k, variants with their fingerprints and shuffle seeds, gate_report, bound_tests);
   - `logs/<variant>.log`.

## Verdicts

| Verdict | Rule | `dist` |
|---|---|---|
| `SUSPECT_ENV` | Any attempt is `SUSPECT_ENV` | `?/n` |
| `NOT_RUN` | Any attempt is `NOT_RUN` (with its reasons) | `?/n` |
| `PASS` | No attempt failed | `0/n` |
| `CONSISTENT_FAIL` | All failed with one signature (origin + failed assertion ids) | `n/n` |
| `TIMEOUT` | All failed, and the one origin is `TIMEOUT` | `n/n` |
| `INTERMITTENT` | Some failed, or the failures differ | `f/n` |

**Detection power.** A `PASS` carries `max_undetected_rate = 1 − 0.05^(1/k)`: the highest failure rate that would still pass k of k runs with 5% probability. That is 0.63 at k = 3 and 0.45 at k = 5. The report states it so a PASS isn't over-read.

**Escalation.** An `INTERMITTENT` verdict gets `escalation{variant: "f/10", reference: "f/10", escalated}`. `escalated` is true when the test fails on the variant but never on reference. Triage uses it to tell `INTERMITTENT_PRODUCT` apart from a flaky test.

## A result row (`results.jsonl`)

Each row is one test attempt. `platform_rt/record.py` writes it in the runner:

```
{id: "ex:<variant>/a<n>/<case>", node, nodeid, case_id, variant, batch, lane, status: RAN|SUSPECT_ENV|NOT_RUN, reason?,
 outcome, origin, trace_id, observed, assertions[{id, ok, expected, actual}],
 exchanges[{step, method, path, query, json, form, body, status, auth, setup, traceparent, x_test_trace}],
 under_test, failure{phase, origin, error, line}, reached, retries?, fault?}
```

A trace header links each exchange to the backend log lines it produced. Triage follows that link.

## Output: `run_records`

```
{folder: "runs/execute-<id>", root: "<sha256 of records.sha256>", execution, k,
 variants{v: {spec_hash, flags, reproduce, shuffle_seed, fingerprint_mismatch, fingerprint} | {up_error}},
 summary{variant: {VERDICT: n}},
 verdicts[{node, case_id, unit, ac, variant, gate_status, lane, seeded, attempts[ids], verdict, dist, origin?, assertions?, escalation?, max_undetected_rate?}],
 logs{variant: path}}
```

## Verifying the seal

`tp execute verify-run <run_records id>` recomputes every hash and prints `TAMPERED <file>` for each mismatch. Triage and the retest check the seal before they read any record.

## Files and tests

Tests: `test_decide.py`, `test_record.py`, `test_stage.py`.
