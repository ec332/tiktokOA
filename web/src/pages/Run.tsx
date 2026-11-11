import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { api, ok, type S } from "../api/client";
import { Labels, Loading, PageInfo, Status } from "../components/bits";
import { when } from "../flow/time";
import { Flow } from "./Flow";

const frac = (c: S["Count"]) => (c.d ? `${c.n}/${c.d}` : `${c.n}`);

/** The claim-first header (cohesion/001 D5): target · reach · isolation · ceiling · what ran,
 *  and what the results rest on. The report's header comes from the same function. */
function ClaimLine({ id }: { id: number }) {
  const c = useQuery({
    queryKey: ["claim", id],
    queryFn: () => ok(api.GET("/runs/{run_id}/claim", { params: { path: { run_id: id } } })),
  }).data;
  if (!c) return null;
  const ran = c.ran
    ? `ran ${c.ran.n} of ${c.ran.d}` + (c.blocked.length ? `; blocked ▸ ${c.blocked.map((b) => `${b.why} ${frac(b.cases)}`).join("; ")}` : "")
    : "not run yet";
  const level = c.isolation == null ? null : `isolation ${c.isolation} (${c.isolation_words})`;
  return (
    <p className="row" style={{ fontSize: 13, gap: 6, flexWrap: "wrap" }} aria-label="What this run claims">
      <strong>{[c.target ?? "no target", c.reach, level, c.ceiling, ran].filter(Boolean).join(" · ")}</strong>
      {Object.keys(c.basis).length > 0 && <span className="muted">rests on</span>}
      {Object.entries(c.basis).map(([b, n]) => (
        <span key={b} className="row" style={{ gap: 2, display: "inline-flex" }}><Labels labels={[b]} /> {frac(n)}</span>
      ))}
    </p>
  );
}

export function RunPage() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const id = Number(runId);
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => ok(api.GET("/runs/{run_id}", { params: { path: { run_id: id } } })),
  });

  // Live updates: every event refreshes the run; EventSource reconnects when the stream ends.
  useEffect(() => {
    const es = new EventSource(`/api/runs/${id}/events`);
    es.onmessage = () => {
      qc.invalidateQueries({ queryKey: ["run", id] });
      qc.invalidateQueries({ queryKey: ["events", id] });
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["claim", id] });
    };
    return () => es.close();
  }, [id, qc]);

  if (!run.data) return <Loading error={run.error} />;
  return (
    <>
      <h1 className="row">
        {run.data.name} <Status status={run.data.status} />
        <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>started {when(run.data.started)}</span>
      </h1>
      <ClaimLine id={id} />
      <PageInfo>
        <p>One page from start to report. The bold line above is the run's claim: which target, how it was reached, its
          isolation (0 isolated · 1 with local stand-ins · 2 a live system), how many planned checks ran, and what the
          results rest on (real or simulated).</p>
        <h3>The steps</h3>
        <ol>
          <li><strong>Understand the feature</strong> (AI): requirements from your text and the code, each citing real
            code lines.</li>
          <li><strong>Clarify</strong> (you): questions about gaps, and "the code does this; is it intended?". Your
            answers override the AI. <em>Let the platform assume</em> makes failures spec gaps, not defects.</li>
          <li><strong>Design and approve tests</strong> (AI, then you): test cases grouped P0 likely defects · P1 the
            feature · P2 other checks. Expected values come from fixed calculators, never the model. Untick what you
            don't want; only signed cases become code.</li>
          <li><strong>Build and check</strong> (AI + fixed code): the AI writes the test code; the gate runs each test
            several times plus once shuffled and plants bugs; tests that are flaky or catch nothing can't produce a
            defect. Then every test runs against the real system, and every request, response and log line is
            sealed.</li>
          <li><strong>Report</strong> (fixed code): defects with their request, response, failed assertion and evidence;
            problematic tests; areas to improve. Every reference links to its record.</li>
        </ol>
        <p>A step that needs you shows <strong>Needs you</strong>, and the browser tab title changes. Click a finished
          step's name (▸) to see what it produced. A discovery run shows <em>Discover the target</em> and its setup
          questions here instead.</p>
        <h3>After the report</h3>
        <ul>
          <li><strong>Confirm defect</strong> / <strong>Not a bug</strong>: your decision, recorded; nothing re-runs.</li>
          <li><strong>Try suggested fix</strong> (when the AI patch applies cleanly) or <strong>Retest my fix</strong>
            (after you commit one): reruns the same sealed tests. FIXED_CONFIRMED only if they failed before, pass on
            every run after, with no regressions. One passing rerun is never proof.</li>
          <li><strong>Export tests…</strong>: writes the trusted tests to a new branch of your repo.</li>
          <li>On a black-box target, a defect is located at the API (the request, the response, and the backend log in
            the evidence) rather than a file and line.</li>
        </ul>
      </PageInfo>
      <Flow run={run.data} />
    </>
  );
}
