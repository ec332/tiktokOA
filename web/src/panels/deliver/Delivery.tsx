/** Stage F (autonomy/001 F3-F6): what happened to each defect after the report, with no click:
 *  a draft PR on our fork, or an issue draft with its reproduction. The score and rules show why. */
type Score = { score: number; threshold: number; parts: Record<string, number>; rules: Record<string, boolean> };
export type DeliveryBody = {
  fork: string | null; live: boolean;
  defects: { defect: string; ac: string; class: string; score: Score;
    pr?: { url: string; branch: string }; pr_failed?: string;
    issue?: { markdown: string; file: string; why: string[]; repro: { curl: string; schemathesis: string } } }[];
};

const parts = (s: Score) => `${Object.entries(s.parts).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" × ")} = ${s.score} (PR at ${s.threshold})`;

export function Delivery({ body }: { body: DeliveryBody }) {
  if (!body.defects.length) return <div className="line">No real defect to deliver.</div>;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <h3>Delivered</h3>
      {body.defects.map((d) => (
        <div key={d.defect} className="card" style={{ display: "grid", gap: 4 }}>
          <span><strong>{d.ac}</strong> <span className="muted">{d.class}</span>{" "}
            {d.pr ? <a href={d.pr.url} target="_blank" rel="noreferrer">Draft PR</a> : <span>Issue draft</span>}</span>
          <span className="muted" style={{ fontSize: 13 }}>{parts(d.score)}. Rules: {Object.entries(d.score.rules)
            .map(([k, ok]) => `${ok ? "✓" : "✗"} ${k.replace(/_/g, " ")}`).join(", ")}</span>
          {d.issue && (
            <details>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>Reproduction ({d.issue.file})</summary>
              <pre className="patch">{d.issue.repro.curl}</pre>
              <pre className="patch">{d.issue.repro.schemathesis}</pre>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
