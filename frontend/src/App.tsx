import { useState, useEffect, useRef, useCallback } from "react";
import Dashboard, { type ViewerProfile } from "./components/Dashboard";
import Config from "./components/Config";
import Logs from "./components/Logs";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Session {
  /** Cle Plex de lecture, utile pour distinguer deux lectures du meme episode. */
  session_key?: string | null;
  plex_title: string;
  plex_ep_title: string;
  episode: number;
  season: number | null;
  plex_percent: number;
  offset_ms: number;
  duration_ms: number;
  is_playing: boolean;
  anilist_title: string | null;
  anilist_media_id: number | null;
  anilist_progress: number | null;
  anilist_list_status: string | null;
  anilist_cover: string | null;
  anilist_score: number | null;
  anilist_episodes: number | null;
  anilist_site_url: string | null;
  anilist_format: string | null;
  threshold: number;
  threshold_reached: boolean;
  /** Cle stable utilisee pour le mapping manuel Plex -> AniList. */
  mapping_key: string | null;
  /** Indique si la correspondance provient d'un mapping manuel. */
  has_manual_mapping: boolean;
}

export interface Status {
  monitoring: boolean;
  current_session: Session | null;
  last_error: string | null;
  config_ok: boolean;
  log_count: number;
}

/** Entree de journal horodatee envoyee par le backend. */
export interface LogEntry { at: string; msg: string; }

/** Action historisee par le backend apres sync ou rollback. */
export interface HistoryAction {
  at: string;
  type: string;
  session_key?: string | null;
  media_id?: number;
  anilist_title?: string;
  plex_title?: string;
  episode?: number;
  season?: number | null;
  from_progress?: number;
  to_progress?: number;
  reverted_at?: string;
}

/** Toast ephemere affiche en bas a droite. */
interface Toast {
  id: number;
  text: string;
  ok: boolean;
}

// ── App ───────────────────────────────────────────────────────────────────────

type Tab = "dashboard" | "config" | "logs";

/** Racine React: orchestre les onglets, le WebSocket et les donnees partagees. */
export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [status, setStatus] = useState<Status | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [history, setHistory] = useState<HistoryAction[]>([]);
  const [viewer, setViewer] = useState<ViewerProfile | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastIdRef = useRef(0);

  // ── Toast helpers ──────────────────────────────────────────────────────────

  const pushToast = useCallback((text: string, ok = true) => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, text, ok }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);

  // ── Data fetchers ──────────────────────────────────────────────────────────

  /** Recharge l'historique et retourne les actions pour usage immediat. */
  const fetchHistory = useCallback(async (): Promise<HistoryAction[]> => {
    try {
      const r = await fetch("/api/history");
      if (r.ok) {
        const d = await r.json();
        const actions: HistoryAction[] = (d.actions ?? []).slice().reverse();
        setHistory(actions);
        return actions;
      }
    } catch {}
    return [];
  }, []);

  const fetchViewer = useCallback(async () => {
    try {
      const r = await fetch("/api/anilist/viewer");
      if (r.ok) {
        const d = await r.json();
        if (d.ok) setViewer({ id: d.id, name: d.name, avatar_url: d.avatar_url });
      }
    } catch {}
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────────

  const connect = useCallback(() => {
    // Le WebSocket pousse le statut et les nouveaux logs en temps réel.
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    wsRef.current = ws;

    ws.onopen = () => setWsConnected(true);

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "status") setStatus(msg.data);
        if (msg.type === "logs") {
          // Snapshot initial à la connexion : remplace la liste et initialise le compteur.
          setLogs(msg.data);
        }
        if (msg.type === "log_entry") {
          // Nouvelles entrées poussées unitairement par le hook on_new_log du monitor.
          setLogs((prev) => [...prev, msg.data].slice(-300));
        }
        if (msg.type === "history_updated") {
          fetchHistory().then((actions) => {
            const last = actions[0];
            if (last?.type === "update") {
              pushToast(
                `✓ Sync : ${last.anilist_title ?? last.plex_title ?? "?"} → ép. ${last.to_progress}`
              );
            }
          });
        }
        if (msg.type === "config_updated") fetchViewer();
      } catch {}
    };

    ws.onclose = () => {
      setWsConnected(false);
      // Reconnexion simple cote client pour survivre aux redemarrages du backend.
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, [fetchHistory, fetchViewer, pushToast]);

  useEffect(() => {
    connect();
    fetchHistory();
    fetchViewer();
    return () => {
      wsRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect, fetchHistory, fetchViewer]);

  // Polling fallback status quand le WS est déconnecté
  useEffect(() => {
    if (wsConnected) return;
    const id = setInterval(async () => {
      try {
        const r = await fetch("/api/status");
        if (r.ok) setStatus(await r.json());
      } catch {}
    }, 5000);
    return () => clearInterval(id);
  }, [wsConnected]);

  const monitoring = status?.monitoring ?? false;

  const handleMonitorToggle = async () => {
    const endpoint = monitoring ? "/api/monitoring/stop" : "/api/monitoring/start";
    await fetch(endpoint, { method: "POST" });
  };

  const TAB_LABELS: Record<Tab, string> = {
    dashboard: "Dashboard",
    config: "Configuration",
    logs: "Journal",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      {/* ── Header ── */}
      <header style={{
        background: "var(--surface)",
        borderBottom: "1px solid var(--border)",
        padding: "0 1.5rem",
        display: "flex",
        alignItems: "center",
        gap: "1.25rem",
        height: 52,
        flexShrink: 0,
      }}>
        <div style={{ fontWeight: 800, fontSize: 17, letterSpacing: "-0.03em" }}>
          <span style={{ color: "var(--accent)" }}>Plex</span>
          <span style={{ color: "var(--blue)" }}>ani</span>
        </div>

        <div style={{ width: 1, height: 18, background: "var(--border)" }} />

        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: wsConnected ? "var(--green)" : "var(--red)",
            display: "inline-block",
            boxShadow: wsConnected ? "0 0 6px var(--green)" : "none",
          }} />
          {wsConnected ? "connecté" : "reconnexion…"}
        </div>

        <div style={{ flex: 1 }} />

        <button
          onClick={handleMonitorToggle}
          disabled={!status}
          style={{
            padding: "0.35rem 1rem",
            borderRadius: 6,
            fontWeight: 600,
            fontSize: 12,
            background: monitoring ? "var(--red-dim)" : "var(--green-dim)",
            color: monitoring ? "var(--red)" : "var(--green)",
            border: `1px solid ${monitoring ? "var(--red)" : "var(--green)"}`,
            opacity: status ? 1 : 0.5,
            transition: "all 0.15s",
          }}
        >
          <span className="monitor-btn-label-long">{monitoring ? "⏹ Arrêter le suivi" : "▶ Lancer le suivi"}</span>
          <span className="monitor-btn-label-short">{monitoring ? "⏹" : "▶"}</span>
        </button>
      </header>

      {/* ── Tabs ── */}
      <nav style={{
        background: "var(--surface)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        padding: "0 1.5rem",
        gap: 2,
        flexShrink: 0,
      }}>
        {(["dashboard", "config", "logs"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "0.5rem 0.9rem",
              fontSize: 13,
              fontWeight: tab === t ? 600 : 400,
              color: tab === t ? "var(--fg)" : "var(--muted)",
              background: "transparent",
              borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
              borderLeft: "none", borderRight: "none", borderTop: "none",
              transition: "all 0.12s",
              position: "relative",
            }}
          >
            {TAB_LABELS[t]}
            {t === "logs" && logs.length > 0 && (
              <span style={{
                marginLeft: 5, fontSize: 10,
                background: "var(--accent-dim)", color: "var(--accent)",
                padding: "1px 5px", borderRadius: 10,
              }}>
                {logs.length}
              </span>
            )}
          </button>
        ))}
      </nav>

      {/* ── Content ── */}
      <main style={{ flex: 1, overflow: "auto" }}>
        {tab === "dashboard" && (
          <Dashboard
            status={status}
            history={history}
            onHistoryRefresh={fetchHistory}
            viewer={viewer}
          />
        )}
        {tab === "config" && <Config onSaved={() => setTab("dashboard")} />}
        {tab === "logs" && <Logs logs={logs} onClear={() => setLogs([])} />}
      </main>

      {/* ── Toasts ── */}
      <div style={{
        position: "fixed",
        bottom: "1.5rem",
        right: "1.5rem",
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        zIndex: 1000,
        pointerEvents: "none",
      }}>
        {toasts.map((t) => (
          <div
            key={t.id}
            style={{
              padding: "0.6rem 1rem",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 600,
              background: t.ok ? "var(--green-dim)" : "var(--red-dim)",
              color: t.ok ? "var(--green)" : "var(--red)",
              border: `1px solid ${t.ok ? "var(--green)" : "var(--red)"}`,
              boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
              animation: "fadeInUp 0.2s ease",
              maxWidth: 340,
              backdropFilter: "blur(4px)",
            }}
          >
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
