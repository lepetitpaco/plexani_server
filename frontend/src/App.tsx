import { useState, useEffect, useRef, useCallback } from "react";
import Dashboard, { type ViewerProfile } from "./components/Dashboard";
import Config from "./components/Config";
import Logs from "./components/Logs";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Session {
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
}

export interface Status {
  monitoring: boolean;
  current_session: Session | null;
  last_error: string | null;
  config_ok: boolean;
  log_count: number;
}

export interface LogEntry { at: string; msg: string; }

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

// ── App ───────────────────────────────────────────────────────────────────────

type Tab = "dashboard" | "config" | "logs";

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [status, setStatus] = useState<Status | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [history, setHistory] = useState<HistoryAction[]>([]);
  const [viewer, setViewer] = useState<ViewerProfile | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await fetch("/api/history");
      if (r.ok) {
        const d = await r.json();
        setHistory((d.actions ?? []).slice().reverse());
      }
    } catch {}
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

  const connect = useCallback(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    wsRef.current = ws;

    ws.onopen = () => setWsConnected(true);

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "status") setStatus(msg.data);
        if (msg.type === "logs") setLogs(msg.data);
        if (msg.type === "history_updated") fetchHistory();
        if (msg.type === "config_updated") fetchViewer();
      } catch {}
    };

    ws.onclose = () => {
      setWsConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, [fetchHistory]);

  useEffect(() => {
    connect();
    fetchHistory();
    fetchViewer();
    return () => {
      wsRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect, fetchHistory, fetchViewer]);

  // Polling fallback status
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

  // Nouveaux logs via polling
  const logCountRef = useRef(0);
  useEffect(() => {
    const id = setInterval(async () => {
      if (!wsConnected) return;
      const since = logCountRef.current;
      try {
        const r = await fetch(`/api/logs?since=${since}`);
        if (!r.ok) return;
        const data = await r.json();
        const newLogs: LogEntry[] = data.logs ?? [];
        if (newLogs.length > 0) {
          setLogs((prev) => {
            const combined = [...prev, ...newLogs].slice(-300);
            logCountRef.current = since + newLogs.length;
            return combined;
          });
        }
      } catch {}
    }, 3000);
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
        {tab === "logs" && <Logs logs={logs} />}
      </main>
    </div>
  );
}
