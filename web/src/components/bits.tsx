export function Labels({ labels }: { labels: string[] }) {
  return (
    <span className="row" style={{ gap: 4, display: "inline-flex" }}>
      {labels.map((l) => (
        <span key={l} className={`badge ${l}`} title={`label: ${l}`}>
          {l}
        </span>
      ))}
    </span>
  );
}

export function Status({ status }: { status: string }) {
  return <span className={`status ${status}`}>{status.replace("_", " ")}</span>;
}


export function Loading({ error }: { error?: Error | null }) {
  return error ? <p className="error">{error.message}</p> : <p className="muted">Loading…</p>;
}
