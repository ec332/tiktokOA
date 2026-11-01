/** Generic JSON viewer: collapsible objects/arrays; artifact ids in strings become links. */
import { ArtifactLink } from "./bits";

export function JsonTree({ value, name, depth = 0 }: { value: unknown; name?: string; depth?: number }) {
  const key = name !== undefined ? <span className="k">{JSON.stringify(name)}: </span> : null;
  if (value !== null && typeof value === "object") {
    const entries = Array.isArray(value)
      ? value.map((v, i) => [String(i), v] as const)
      : Object.entries(value as Record<string, unknown>);
    const [open, close] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
    if (entries.length === 0) return <div className="leaf">{key}{open}{close}</div>;
    return (
      <details open={depth < 2}>
        <summary>
          {key}
          {open} <span className="muted">{entries.length} {Array.isArray(value) ? "items" : "keys"}</span>
        </summary>
        {entries.map(([k, v]) => (
          <JsonTree key={k} name={Array.isArray(value) ? undefined : k} value={v} depth={depth + 1} />
        ))}
        <div>{close}</div>
      </details>
    );
  }
  let leaf;
  if (typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value)) leaf = <ArtifactLink id={value} />;
  else if (typeof value === "string") leaf = <span className="s">{JSON.stringify(value)}</span>;
  else leaf = <span className="n">{JSON.stringify(value)}</span>;
  return <div className="leaf">{key}{leaf}</div>;
}
