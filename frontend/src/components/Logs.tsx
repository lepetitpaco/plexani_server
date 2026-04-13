import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "../App";

/** Formate l'heure d'un log en heure locale francaise. */
function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("fr-FR", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

/** Journal temps reel du backend avec option de logs AniList verbose. */
export default function Logs({ logs, onClear }: { logs: LogEntry[]; onClear: () => void }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [verbose, setVerbose] = useState(false);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((d) => setVerbose(Boolean(d.verbose_anilist)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    // Auto-scroll en bas pour suivre les nouveaux logs sans action utilisateur.
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  const handleClear = async () => {
    onClear();
    try {
      await fetch("/api/logs/clear", { method: "POST" });
    } catch {}
  };

  const toggleVerbose = async (checked: boolean) => {
    setVerbose(checked);
    try {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verbose_anilist: checked }),
      });
    } catch {}
  };

  return (
    <div style={{
      maxWidth: 900, margin: "0 auto", padding: "1.5rem",
      height: "calc(100vh - 112px)", display: "flex", flexDirection: "column",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <h2 style={{ fontSize: 16, fontWeight: 700 }}>Journal</h2>
        <div style={{ display: "flex", alignItems: "center", gap: "1.25rem" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer", color: "var(--muted)" }}>
            <input
              type="checkbox"
              checked={verbose}
              onChange={(e) => toggleVerbose(e.target.checked)}
            />
            Logs verbose AniList
          </label>
          <button
            onClick={handleClear}
            style={{
              fontSize: 11, fontWeight: 600, padding: "3px 9px", borderRadius: 5,
              background: "var(--surface2)", color: "var(--muted)",
              border: "1px solid var(--border)", cursor: "pointer",
            }}
          >
            Vider
          </button>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{logs.length} entrées</span>
        </div>
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
