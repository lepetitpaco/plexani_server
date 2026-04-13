import { useState } from "react";
import type { Status, HistoryAction } from "../App";

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_FR: Record<string, string> = {
  CURRENT:   "En cours",
  PLANNING:  "Planifié",
  COMPLETED: "Terminé",
  DROPPED:   "Abandonné",
  PAUSED:    "En pause",
  REPEATING: "Revisionnage",
};

const STATUS_COLOR: Record<string, string> = {
  CURRENT:   "#4ade80",
  PLANNING:  "#60a5fa",
  COMPLETED: "#e5a00d",
  DROPPED:   "#f87171",
  PAUSED:    "#7070a0",
  REPEATING: "#60a5fa",
};

const FORMAT_FR: Record<string, string> = {
  TV:         "Série TV",
  TV_SHORT:   "Série courte",
  MOVIE:      "Film",
  SPECIAL:    "Spécial",
  OVA:        "OVA",
  ONA:        "ONA",
  MUSIC:      "Musique",
};

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function fmtRemaining(ms: number): string {
  const m = Math.floor(ms / 60000);
  if (m <= 0) return "bientôt";
  if (m < 60) return `encore ${m} min`;
  return `encore ${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
}

function fmtRelative(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return "à l'instant";
    if (m < 60) return `il y a ${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `il y a ${h}h`;
    return new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
  } catch { return iso; }
}

// ── Section label ─────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, letterSpacing: "0.1em",
      color: "var(--muted)", textTransform: "uppercase", marginBottom: "0.75rem",
    }}>
      {children}
    </div>
  );
}

// ── Card shell ────────────────────────────────────────────────────────────────

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      padding: "1.1rem 1.25rem",
      ...style,
    }}>
      {children}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export interface ViewerProfile {
  name: string;
  avatar_url: string;
  id: number;
}

export default function Dashboard({
  status,
  history,
  onHistoryRefresh,
  viewer,
}: {
  status: Status | null;
  history: HistoryAction[];
  onHistoryRefresh: () => void;
  viewer: ViewerProfile | null;
}) {
  const [rolling, setRolling] = useState(false);
  const [rollMsg, setRollMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [syncing, setSyncing] = useState(false);

  const handleForceSync = async () => {
    setSyncing(true);
    setRollMsg(null);
    try {
      const r = await fetch("/api/monitoring/sync", { method: "POST" });
      const d = await r.json();
      if (r.ok && d.ok) {
        if (d.already_up_to_date) {
          setRollMsg({ ok: true, text: `Déjà à jour : ép. ${d.progress}` });
        } else {
          setRollMsg({ ok: true, text: `✓ Sync : ${d.anilist_title ?? ""} ép. ${d.from_progress} → ${d.progress}` });
          onHistoryRefresh();
        }
      } else {
        setRollMsg({ ok: false, text: d.detail ?? "Erreur." });
      }
    } catch {
      setRollMsg({ ok: false, text: "Impossible de joindre le serveur." });
    } finally {
      setSyncing(false);
    }
  };

  const handleRollback = async () => {
    setRolling(true);
    setRollMsg(null);
    try {
      const r = await fetch("/api/history/rollback", { method: "POST" });
      const d = await r.json();
      if (r.ok && d.ok) {
        setRollMsg({ ok: true, text: `↩ ${d.rollback?.anilist_title ?? ""} remis à l'ép. ${d.rollback?.to_progress ?? "?"}` });
        onHistoryRefresh();
      } else {
        setRollMsg({ ok: false, text: d.detail ?? d.message ?? "Erreur." });
      }
    } catch {
      setRollMsg({ ok: false, text: "Impossible de joindre le serveur." });
    } finally {
      setRolling(false);
    }
  };

  if (!status) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)", fontSize: 14 }}>
        Connexion au serveur…
      </div>
    );
  }

  const { monitoring, current_session: s, last_error, config_ok } = status;
  
  let canRollback = false;
  if (s?.anilist_media_id && s?.episode != null && s.session_key != null) {
      // Find the last update action
      const lastUpdate = history.find(a => a.type === "update");
      if (lastUpdate && lastUpdate.media_id === s.anilist_media_id && lastUpdate.episode === s.episode && lastUpdate.session_key === s.session_key) {
          canRollback = true;
      }
  }

  // Filter history to ONLY show actions for the currently playing episode
  const recentHistory = history
    .filter((a) => s && a.media_id === s.anilist_media_id && a.episode === s.episode)
    .slice(0, 5);

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1rem" }}>

      {/* ── Top bar: status + viewer ── */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
        {/* Status pill */}
        <div style={{
          display: "flex", alignItems: "center", gap: 7,
          padding: "0.3rem 0.75rem", borderRadius: 20, fontSize: 12, fontWeight: 600,
          background: monitoring ? "var(--green-dim)" : last_error ? "var(--red-dim)" : "var(--surface)",
          color: monitoring ? "var(--green)" : last_error ? "var(--red)" : "var(--muted)",
          border: `1px solid ${monitoring ? "var(--green)" : last_error ? "var(--red)" : "var(--border)"}`,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%", display: "inline-block",
            background: monitoring ? "var(--green)" : last_error ? "var(--red)" : "var(--muted)",
            boxShadow: monitoring ? "0 0 7px var(--green)" : "none",
            animation: monitoring ? "pulse 2s infinite" : "none",
          }} />
          {monitoring ? "Suivi actif" : last_error ? last_error : "Suivi arrêté"}
        </div>

        <div style={{ flex: 1 }} />

        {/* AniList viewer */}
        {viewer && (
          <a
            href={`https://anilist.co/user/${viewer.id}/`}
            target="_blank" rel="noreferrer"
            style={{ display: "flex", alignItems: "center", gap: 8, textDecoration: "none",
              background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 20,
              padding: "0.3rem 0.75rem 0.3rem 0.4rem",
            }}
          >
            {viewer.avatar_url ? (
              <img src={viewer.avatar_url} alt={viewer.name}
                style={{ width: 24, height: 24, borderRadius: "50%", objectFit: "cover" }} />
            ) : (
              <div style={{ width: 24, height: 24, borderRadius: "50%", background: "var(--blue-dim)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: "var(--blue)", fontWeight: 700 }}>
                {viewer.name[0]?.toUpperCase()}
              </div>
            )}
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--fg)" }}>{viewer.name}</span>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>AniList</span>
          </a>
        )}
      </div>

      {/* ── 2-column dashboard ── */}
      <div className="dash-grid">

        {/* ── LEFT: Plex ── */}
        <Card>
          <SectionLabel>Lecture en cours (Plex)</SectionLabel>

          {s ? (
            <>
              <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.025em", lineHeight: 1.2, marginBottom: "0.4rem" }}>
                {s.plex_title}
              </div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: "1.1rem", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{
                  background: "var(--accent-dim)", color: "var(--accent)",
                  fontWeight: 700, fontSize: 11, padding: "2px 7px", borderRadius: 4,
                }}>
                  {s.season != null ? `S${String(s.season).padStart(2, "0")}` : ""}E{String(s.episode).padStart(2, "0")}
                </span>
                {s.plex_ep_title && <span style={{ color: "var(--muted)" }}>{s.plex_ep_title}</span>}
                {!s.is_playing && <span style={{ color: "var(--accent)", fontSize: 11, fontWeight: 600 }}>⏸ Pause</span>}
              </div>

              {/* Progress bar */}
              <div style={{ position: "relative", height: 10, borderRadius: 5, background: "var(--surface2)", marginBottom: "0.5rem" }}>
                <div style={{
                  height: "100%", borderRadius: 5,
                  width: `${Math.min(100, s.plex_percent)}%`,
                  background: s.threshold_reached
                    ? "linear-gradient(90deg,#22c55e,#4ade80)"
                    : "linear-gradient(90deg,#d97706,#e5a00d)",
                  transition: "width 0.8s ease",
                }} />
                {/* Seuil marker */}
                <div style={{
                  position: "absolute", top: -2, bottom: -2,
                  left: `${s.threshold}%`,
                  width: 2, background: "rgba(255,255,255,0.25)", borderRadius: 1,
                  transform: "translateX(-50%)",
                }} />
              </div>

              {/* Times */}
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--muted)", marginBottom: "0.5rem" }}>
                <span style={{ fontFamily: "monospace" }}>{fmtMs(s.offset_ms)}</span>
                <span style={{ fontWeight: 700, color: s.threshold_reached ? "var(--green)" : "var(--fg)" }}>
                  {s.plex_percent.toFixed(1)}%
                </span>
                <span style={{ fontFamily: "monospace" }}>{fmtMs(s.duration_ms)}</span>
              </div>

              {/* Threshold hint */}
              <div style={{ fontSize: 11, color: s.threshold_reached ? "var(--green)" : "var(--muted)" }}>
                {s.threshold_reached
                  ? `✓ Seuil ${s.threshold}% atteint — sync déclenchée`
                  : `Seuil : ${s.threshold}% — ${fmtRemaining((s.threshold - s.plex_percent) / 100 * s.duration_ms)}`
                }
              </div>
            </>
          ) : (
            <div style={{ textAlign: "center", padding: "2rem 0", color: "var(--muted)", fontSize: 14 }}>
              {monitoring ? "En attente d'une lecture Plex…" : !config_ok ? "Configure Plex et AniList pour démarrer." : "Aucune lecture en cours."}
            </div>
          )}
        </Card>

        {/* ── RIGHT: AniList ── */}
        <Card>
          <SectionLabel>Correspondance AniList</SectionLabel>

          {s?.anilist_media_id ? (
            <div style={{ display: "flex", gap: "1rem" }}>
              {/* Cover */}
              <div style={{ flexShrink: 0 }}>
                {s.anilist_cover ? (
                  <img
                    src={`/api/proxy/image?url=${encodeURIComponent(s.anilist_cover)}`}
                    alt={s.anilist_title ?? ""}
                    style={{ width: 100, height: 145, objectFit: "cover", borderRadius: 8, display: "block" }}
                  />
                ) : (
                  <div style={{
                    width: 100, height: 145, borderRadius: 8,
                    background: "var(--surface2)", border: "1px solid var(--border)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 28, color: "var(--border)",
                  }}>🎌</div>
                )}
              </div>

              {/* Info */}
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                <div style={{ fontSize: 15, fontWeight: 800, lineHeight: 1.25, letterSpacing: "-0.02em" }}>
                  {s.anilist_title}
                </div>

                {/* Format */}
                {s.anilist_format && (
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    {FORMAT_FR[s.anilist_format] ?? s.anilist_format}
                    {s.anilist_episodes ? ` · ${s.anilist_episodes} épisodes` : ""}
                  </div>
                )}

                {/* List status */}
                {s.anilist_list_status && (
                  <span style={{
                    display: "inline-block", alignSelf: "flex-start",
                    fontSize: 11, fontWeight: 700,
                    color: STATUS_COLOR[s.anilist_list_status] ?? "var(--muted)",
                    background: "var(--surface2)", padding: "2px 7px", borderRadius: 4,
                  }}>
                    {STATUS_FR[s.anilist_list_status] ?? s.anilist_list_status}
                  </span>
                )}

                {/* Progress on list */}
                <div style={{ fontSize: 13, color: "var(--muted)" }}>
                  Sur ta liste :&nbsp;
                  <span style={{ color: "var(--fg)", fontWeight: 600 }}>ép. {s.anilist_progress ?? 0}</span>
                  {s.anilist_episodes
                    ? <span style={{ color: "var(--muted)" }}> / {s.anilist_episodes}</span>
                    : null}
                </div>

                {/* Plex reading */}
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  Lecture Plex :&nbsp;
                  <span style={{ color: s.threshold_reached ? "var(--green)" : "var(--fg)", fontWeight: 600 }}>
                    ép. {s.episode}
                  </span>
                  {s.episode > (s.anilist_progress ?? 0) && (
                    <span style={{ color: s.threshold_reached ? "var(--green)" : "var(--accent)", marginLeft: 5 }}>
                      {s.threshold_reached ? "✓ sync" : "→ en cours"}
                    </span>
                  )}
                </div>

                {/* Score */}
                {s.anilist_score != null && s.anilist_score > 0 && (
                  <div style={{ fontSize: 12, color: "var(--accent)" }}>
                    ⭐ {s.anilist_score} / 10
                  </div>
                )}

                {/* Link */}
                <div style={{ marginTop: "auto", paddingTop: "0.5rem" }}>
                  <a
                    href={s.anilist_site_url ?? `https://anilist.co/anime/${s.anilist_media_id}`}
                    target="_blank" rel="noreferrer"
                    style={{
                      fontSize: 11, fontWeight: 600,
                      padding: "0.3rem 0.7rem", borderRadius: 5,
                      background: "var(--blue-dim)", color: "var(--blue)",
                      border: "1px solid var(--blue)",
                      display: "inline-block",
                    }}
                  >
                    Ouvrir sur AniList ↗
                  </a>
                </div>
              </div>
            </div>
          ) : s ? (
            <div style={{ textAlign: "center", padding: "2rem 0", color: "var(--muted)", fontSize: 13 }}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>🔍</div>
              Recherche de correspondance AniList…
              <div style={{ fontSize: 11, marginTop: 4 }}>titre Plex : « {s.plex_title} »</div>
            </div>
          ) : (
            <div style={{ textAlign: "center", padding: "2rem 0", color: "var(--muted)", fontSize: 14 }}>
              —
            </div>
          )}
        </Card>
      </div>

      {/* ── History inline ── */}
      <Card style={{ padding: 0 }}>
        {/* Header */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0.75rem 1.1rem",
          borderBottom: "1px solid var(--border)",
        }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", color: "var(--muted)", textTransform: "uppercase" }}>
            Historique de l'épisode en cours
          </span>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button
              onClick={handleForceSync}
              disabled={syncing || !status?.current_session?.anilist_media_id}
              style={{
                padding: "0.25rem 0.7rem", borderRadius: 5, fontSize: 11, fontWeight: 600,
                background: "var(--surface2)", color: "var(--fg)",
                border: "1px solid var(--border)",
                opacity: (syncing || !status?.current_session?.anilist_media_id) ? 0.4 : 1,
                cursor: (syncing || !status?.current_session?.anilist_media_id) ? "default" : "pointer",
              }}
            >
              {syncing ? "…" : "⬆ Forcer maj ép."}
            </button>
            <button
              onClick={handleRollback}
              disabled={rolling || !canRollback}
              style={{
                padding: "0.25rem 0.7rem", borderRadius: 5, fontSize: 11, fontWeight: 600,
                background: "var(--red-dim)", color: "var(--red)",
                border: "1px solid var(--red)",
                opacity: (rolling || !canRollback) ? 0.4 : 1,
                cursor: (rolling || !canRollback) ? "default" : "pointer",
              }}
            >
              {rolling ? "…" : "↩ Annuler l'épisode"}
            </button>
          </div>
        </div>

        {rollMsg && (
          <div style={{
            padding: "0.45rem 1.1rem", fontSize: 12,
            color: rollMsg.ok ? "var(--green)" : "var(--red)",
            background: rollMsg.ok ? "var(--green-dim)" : "var(--red-dim)",
            borderBottom: "1px solid var(--border)",
          }}>
            {rollMsg.text}
          </div>
        )}

        {recentHistory.length === 0 ? (
          <div style={{ padding: "1.5rem", textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
            Aucune sync enregistrée.
          </div>
        ) : (
          recentHistory.map((a, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: "0.75rem",
              padding: "0.55rem 1.1rem",
              borderBottom: i < recentHistory.length - 1 ? "1px solid var(--border)" : "none",
            }}>
              <span style={{ fontSize: 13, flexShrink: 0 }}>
                {a.type === "update" ? "✅" : "↩"}
              </span>
              <span style={{ flex: 1, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {a.anilist_title ?? a.plex_title ?? `Media ${a.media_id}`}
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexShrink: 0 }}>
                {a.season != null && (
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>S{a.season}</span>
                )}
                <span style={{
                  fontSize: 11, fontWeight: 600, padding: "1px 6px", borderRadius: 4,
                  background: a.type === "update" ? "var(--green-dim)" : "var(--surface2)",
                  color: a.type === "update" ? "var(--green)" : "var(--muted)",
                }}>
                  {a.type === "update" ? `Ép. ${a.from_progress} → ${a.to_progress}` : `↩ Ép. ${a.to_progress}`}
                </span>
                <span style={{ fontSize: 11, color: "var(--muted)", minWidth: 64, textAlign: "right" }}>
                  {fmtRelative(a.at)}
                </span>
              </div>
            </div>
          ))
        )}
      </Card>

    </div>
  );
}
