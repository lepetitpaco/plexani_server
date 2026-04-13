import { useEffect, useRef } from "react";
import type { LogEntry } from "../App";

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("fr-FR", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

export default function Logs({ logs }: { logs: LogEntry[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  return (
    <div style={{
      maxWidth: 900, margin: "0 auto", padding: "1.5rem",
      height: "calc(100vh - 112px)", display: "flex", flexDirection: "column",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <h2 style={{ fontSize: 16, fontWeight: 700 }}>Journal</h2>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{logs.length} entrées</span>
      </div>
      <div style={{
        flex: 1, overflow: "auto",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "0.75rem",
        fontFamily: "monospace",
        fontSize: 12,
      }}>
        {logs.length === 0 ? (
          <div style={{ color: "var(--muted)", padding: "1rem 0" }}>Aucun log.</div>
        ) : (
          logs.map((l, i) => (
            <div key={i} style={{
              display: "flex", gap: "1rem",
              padding: "2px 0",
              borderBottom: i < logs.length - 1 ? "1px solid var(--border)" : "none",
            }}>
              <span style={{ color: "var(--muted)", flexShrink: 0 }}>{fmtTime(l.at)}</span>
              <span style={{ color: "var(--fg)" }}>{l.msg}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
