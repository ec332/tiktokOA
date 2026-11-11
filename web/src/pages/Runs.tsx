import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api, ok } from "../api/client";
import { PageInfo, Loading, Status } from "../components/bits";
import { when } from "../flow/time";

/** Run history for the happy path: only runs started from the New run screen. */
export function RunsPage() {
  const runs = useQuery({
    queryKey: ["runs", "focused"], refetchInterval: 5000,
    queryFn: () => ok(api.GET("/runs", { params: { query: { focused: true } } })),
  });
  return (
    <div className="page">
      <h1 className="row"><span className="grow">Runs</span><Link to="/new"><button className="primary">New run</button></Link></h1>
      <PageInfo>
        <p>Step 3 of the flow: every test run started from <strong>New run</strong>, newest first. It refreshes every
          few seconds. Discovery runs from the Targets page aren't listed here.</p>
        <ul>
          <li><strong>running</strong>: a step is working; open the run to follow it live.</li>
          <li><strong>awaiting review</strong>: the run needs you, at Clarify or Approve. Open it to answer.</li>
          <li><strong>succeeded</strong>: the report is ready, with any defects, their evidence and retest buttons.</li>
          <li><strong>failed</strong>: a step stopped. The run page shows which step and why. An environment failure is
            never counted as a product defect.</li>
        </ul>
        <p>Click a run's name to open its run page.</p>
      </PageInfo>
      {!runs.data ? <Loading error={runs.error} /> : runs.data.length === 0 ? (
        <p className="muted">No runs yet. <Link to="/new">Start one</Link>.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Run</th><th>Feature</th><th>Status</th><th>Started</th></tr></thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <td><Link to="/runs/$runId" params={{ runId: String(r.id) }}>{r.name}</Link></td>
                  <td><span className="trunc" style={{ maxWidth: 380 }} title={r.overview ?? ""}>{r.overview ?? "—"}</span></td>
                  <td><Status status={r.status} /></td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>{when(r.started)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
