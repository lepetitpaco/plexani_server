import { useState, useEffect } from "react";
import type { HistoryAction } from "../App";

/** Formate un horodatage ISO pour la liste d'historique complete. */
function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("fr-FR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

/** Affiche l'historique global des synchronisations et rollbacks. */
export default function History() {
  const [actions, setActions] = useState<HistoryAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [rolling, setRolling] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  /** Recharge les actions historisees depuis le backend. */
  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/history");
      if (r.ok) {
        const d = await r.json();
        setActions((d.actions ?? []).slice().reverse());
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleRollback = async () => {
    setRolling(true);
    setMsg(null);
    try {
      const r = await fetch("/api/history/rollback", { method: "POST" });
      const d = await r.json();
      if (r.ok && d.ok) {
        setMsg({ ok: true, text: `Rollback effectué : ${d.rollback?.anilist_title ?? ""} → ép.${d.rollback?.to_progress ?? "?"}` });
        await load();
      } else {
        setMsg({ ok: false, text: d.detail ?? d.message ?? "Erreur." });
      }
    } catch {
      setMsg({ ok: false, text: "Impossible de joindre le serveur." });
    } finally {
      setRolling(false);
    }
  };

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <h2 style={{ fontSize: 16, fontWeight: 700 }}>Historique des syncs</h2>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleRollback}
            disabled={rolling || actions.filter((a) => a.type === "update").length === 0}
            style={{
              padding: "0.4rem 0.9rem", borderRadius: 6, fontSize: 12, fontWeight: 600,
              background: "var(--red-dim)", color: "var(--red)",
              border: "1px solid var(--red)",
              opacity: rolling ? 0.6 : 1,
            }}
          >
            {rolling ? "…" : "↩ Annuler dernière maj"}
          </button>
          <button
            onClick={load}
            style={{
              padding: "0.4rem 0.9rem", borderRadius: 6, fontSize: 12,
              background: "var(--surface2)", color: "var(--fg)",
              border: "1px solid var(--border)",
            }}
          >
            Actualiser
          </button>
        </div>
      </div>

      {msg && (
        <div style={{
          padding: "0.6rem 0.9rem", borderRadius: 7, marginBottom: "0.75rem",
          background: msg.ok ? "var(--green-dim)" : "var(--red-dim)",
          color: msg.ok ? "var(--green)" : "var(--red)",
          border: `1px solid ${msg.ok ? "var(--green)" : "var(--red)"}`,
          fontSize: 13,
        }}>
          {msg.text}
        </div>
      )}

      {loading ? (
        <div style={{ color: "var(--muted)", textAlign: "center", padding: "2rem" }}>Chargement…</div>
      ) : actions.length === 0 ? (
        <div style={{ color: "var(--muted)", textAlign: "center", padding: "2rem" }}>
          Aucun événement enregistré.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {actions.map((a, i) => (
            <div
              key={i}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "0.75rem 1rem",
                display: "flex",
                alignItems: "center",
                gap: "0.75rem",
              }}
            >
              <span style={{ fontSize: 18, flexShrink: 0 }}>
                {a.type === "update" ? "✅" : a.type === "rollback" ? "↩" : "•"}
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {a.anilist_title ?? a.plex_title ?? `Media ${a.media_id}`}
                </div>
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  {a.type === "update" ? (
                    <>Ép. {a.episode} — {a.from_progress} → {a.to_progress}</>
                  ) : (
                    <>Rollback → ép. {a.to_progress}</>
                  )}
                  {a.season != null && <span> · Saison {a.season}</span>}
                </div>
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}>
                {fmtDate(a.at)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
