import { useState, useEffect } from "react";

interface ConfigData {
  plex_server_name: string;
  plex_username: string;
  anilist_client_id: string;
  anilist_client_secret: string;
  anilist_redirect_uri: string;
  poll_interval_seconds: number;
  sync_threshold_percent: number;
  sync_end_only: boolean;
  anime_auto_tracking_mode: string;
  autostart_monitoring: boolean;
  has_plex_token: boolean;
  has_anilist_token: boolean;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "1.1rem" }}>
      <label style={{ display: "block", fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{label}</label>
      {children}
      {hint && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--fg)",
  padding: "0.45rem 0.7rem",
  fontSize: 13,
  outline: "none",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "1.5rem" }}>
      <div style={{
        fontSize: 11, fontWeight: 700, letterSpacing: "0.08em",
        color: "var(--muted)", textTransform: "uppercase",
        marginBottom: "1rem", paddingBottom: "0.5rem",
        borderBottom: "1px solid var(--border)",
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

export default function Config({ onSaved }: { onSaved: () => void }) {
  const [cfg, setCfg] = useState<ConfigData | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Plex OAuth state
  const [plexAuthUrl, setPlexAuthUrl] = useState<string | null>(null);
  const [plexPolling, setPlexPolling] = useState(false);

  // Plex servers dropdown
  const [plexServers, setPlexServers] = useState<string[]>([]);
  const [loadingServers, setLoadingServers] = useState(false);

  const loadConfig = async () => {
    try {
      const r = await fetch("/api/config");
      if (r.ok) setCfg(await r.json());
    } catch {}
  };

  const fetchServers = async () => {
    setLoadingServers(true);
    try {
      const r = await fetch("/api/plex/servers");
      const d = await r.json();
      if (d.ok && d.servers.length > 0) {
        setPlexServers(d.servers);
      } else {
        setMsg({ ok: false, text: d.error ?? "Impossible de charger les serveurs Plex." });
      }
    } catch {
      setMsg({ ok: false, text: "Impossible de joindre le serveur." });
    } finally {
      setLoadingServers(false);
    }
  };

  useEffect(() => { loadConfig(); }, []);

  // Poll Plex OAuth quand en attente
  useEffect(() => {
    if (!plexPolling) return;
    const id = setInterval(async () => {
      try {
        const r = await fetch("/api/oauth/plex/poll");
        const d = await r.json();
        if (d.done) {
          setPlexPolling(false);
          setPlexAuthUrl(null);
          setMsg({ ok: true, text: `Plex connecté : ${d.username}` });
          await loadConfig();
          await fetchServers();
        }
      } catch {}
    }, 2000);
    return () => clearInterval(id);
  }, [plexPolling]);

  const handleSave = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plex_server_name: cfg.plex_server_name,
          plex_username: cfg.plex_username,
          anilist_client_id: cfg.anilist_client_id,
          anilist_client_secret: cfg.anilist_client_secret,
          anilist_redirect_uri: cfg.anilist_redirect_uri,
          poll_interval_seconds: cfg.poll_interval_seconds,
          sync_threshold_percent: cfg.sync_threshold_percent,
          sync_end_only: cfg.sync_end_only,
          anime_auto_tracking_mode: cfg.anime_auto_tracking_mode,
          autostart_monitoring: cfg.autostart_monitoring,
        }),
      });
      if (r.ok) {
        setMsg({ ok: true, text: "Configuration enregistrée." });
        setTimeout(onSaved, 800);
      } else {
        const d = await r.json();
        setMsg({ ok: false, text: d.detail ?? "Erreur inconnue." });
      }
    } catch {
      setMsg({ ok: false, text: "Impossible de joindre le serveur." });
    } finally {
      setSaving(false);
    }
  };

  const handlePlexConnect = async () => {
    try {
      const r = await fetch("/api/oauth/plex/init", { method: "POST" });
      const d = await r.json();
      if (d.ok && d.auth_url) {
        setPlexAuthUrl(d.auth_url);
        setPlexPolling(true);
        window.open(d.auth_url, "_blank");
      }
    } catch {
      setMsg({ ok: false, text: "Impossible de démarrer l'OAuth Plex." });
    }
  };

  const handleAniListConnect = async () => {
    try {
      const r = await fetch("/api/oauth/anilist/url");
      const d = await r.json();
      if (d.ok && d.url) {
        window.open(d.url, "_blank");
        setMsg({ ok: true, text: "Connecte-toi sur AniList, puis reviens ici." });
        // Reload config après 5s (le callback aura stocké le token)
        setTimeout(loadConfig, 5000);
        setTimeout(loadConfig, 10000);
      }
    } catch {
      setMsg({ ok: false, text: "Impossible d'obtenir l'URL AniList." });
    }
  };

  const set = (key: keyof ConfigData, value: unknown) =>
    setCfg((prev) => prev ? { ...prev, [key]: value } : prev);

  if (!cfg) {
    return <div style={{ padding: "2rem", color: "var(--muted)" }}>Chargement…</div>;
  }

  return (
    <div style={{ maxWidth: 680, margin: "0 auto", padding: "1.5rem" }}>
      {msg && (
        <div style={{
          padding: "0.7rem 1rem",
          borderRadius: 8,
          marginBottom: "1rem",
          background: msg.ok ? "var(--green-dim)" : "var(--red-dim)",
          color: msg.ok ? "var(--green)" : "var(--red)",
          border: `1px solid ${msg.ok ? "var(--green)" : "var(--red)"}`,
          fontSize: 13,
        }}>
          {msg.text}
        </div>
      )}

      {/* ── Plex ── */}
      <Section title="Plex">
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0.6rem 0.8rem",
          background: "var(--surface2)", borderRadius: 8,
          border: "1px solid var(--border)", marginBottom: "1rem",
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Compte Plex</div>
            <div style={{ fontSize: 12, color: cfg.has_plex_token ? "var(--green)" : "var(--muted)" }}>
              {cfg.has_plex_token ? "✓ Connecté" : "Non connecté"}
            </div>
          </div>
          <button
            onClick={handlePlexConnect}
            disabled={plexPolling}
            style={{
              padding: "0.4rem 0.9rem", borderRadius: 6, fontSize: 12, fontWeight: 600,
              background: "var(--accent-dim)", color: "var(--accent)",
              border: "1px solid var(--accent)", opacity: plexPolling ? 0.6 : 1,
            }}
          >
            {plexPolling ? "En attente…" : cfg.has_plex_token ? "Reconnecter" : "Connecter"}
          </button>
        </div>
        {plexAuthUrl && (
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "0.75rem" }}>
            Connecte-toi dans la fenêtre ouverte, puis reviens ici (détection automatique).
          </div>
        )}
        <Field label="Serveur Plex" hint="Sélectionne ton serveur dans la liste ou clique sur Actualiser.">
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            {plexServers.length > 0 ? (
              <select
                style={{ ...inputStyle, flex: 1 }}
                value={cfg.plex_server_name}
                onChange={(e) => set("plex_server_name", e.target.value)}
              >
                {!cfg.plex_server_name && <option value="">-- Choisir un serveur --</option>}
                {plexServers.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
                {cfg.plex_server_name && !plexServers.includes(cfg.plex_server_name) && (
                  <option value={cfg.plex_server_name}>{cfg.plex_server_name}</option>
                )}
              </select>
            ) : (
              <input
                style={{ ...inputStyle, flex: 1 }}
                value={cfg.plex_server_name}
                onChange={(e) => set("plex_server_name", e.target.value)}
                placeholder="Clique sur Actualiser pour charger la liste"
              />
            )}
            <button
              onClick={fetchServers}
              disabled={loadingServers || !cfg.has_plex_token}
              style={{
                padding: "0.45rem 0.8rem", borderRadius: 6, fontSize: 12, fontWeight: 600,
                background: "var(--surface2)", color: "var(--fg)",
                border: "1px solid var(--border)",
                opacity: (loadingServers || !cfg.has_plex_token) ? 0.5 : 1,
                whiteSpace: "nowrap",
              }}
            >
              {loadingServers ? "…" : "Actualiser"}
            </button>
          </div>
        </Field>
        <Field label="Nom d'utilisateur Plex" hint="Tel qu'affiché sur l'écran de lecture Plex.">
          <input
            style={inputStyle}
            value={cfg.plex_username}
            onChange={(e) => set("plex_username", e.target.value)}
            placeholder="MonPseudo"
          />
        </Field>
      </Section>

      {/* ── AniList ── */}
      <Section title="AniList">
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0.6rem 0.8rem",
          background: "var(--surface2)", borderRadius: 8,
          border: "1px solid var(--border)", marginBottom: "1rem",
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Compte AniList</div>
            <div style={{ fontSize: 12, color: cfg.has_anilist_token ? "var(--green)" : "var(--muted)" }}>
              {cfg.has_anilist_token ? "✓ Connecté" : "Non connecté"}
            </div>
          </div>
          <button
            onClick={handleAniListConnect}
            disabled={!cfg.anilist_client_id}
            style={{
              padding: "0.4rem 0.9rem", borderRadius: 6, fontSize: 12, fontWeight: 600,
              background: "var(--blue-dim)", color: "var(--blue)",
              border: "1px solid var(--blue)",
              opacity: cfg.anilist_client_id ? 1 : 0.5,
            }}
          >
            {cfg.has_anilist_token ? "Reconnecter" : "Connecter"}
          </button>
        </div>
        <Field label="Client ID" hint="anilist.co → Settings → Developer → Ton app.">
          <input
            style={inputStyle}
            value={cfg.anilist_client_id}
            onChange={(e) => set("anilist_client_id", e.target.value)}
            placeholder="12345"
          />
        </Field>
        <Field label="Client Secret">
          <input
            style={inputStyle}
            type="password"
            value={cfg.anilist_client_secret}
            onChange={(e) => set("anilist_client_secret", e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        <Field
          label="Redirect URI"
          hint="À enregistrer dans l'app AniList Developer. Ex : http://localhost:8765/api/oauth/anilist/callback"
        >
          <input
            style={inputStyle}
            value={cfg.anilist_redirect_uri}
            onChange={(e) => set("anilist_redirect_uri", e.target.value)}
          />
        </Field>
      </Section>

      {/* ── Comportement ── */}
      <Section title="Comportement du suivi">
        <Field label="Intervalle de sondage (secondes)" hint="Fréquence de vérification des sessions Plex (1–3600).">
          <input
            style={{ ...inputStyle, width: 120 }}
            type="number" min={1} max={3600}
            value={cfg.poll_interval_seconds}
            onChange={(e) => set("poll_interval_seconds", parseInt(e.target.value) || 10)}
          />
        </Field>
        <Field label="Seuil de sync (%)" hint="Progression à atteindre pour déclencher la mise à jour AniList (1–100).">
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <input
              style={{ ...inputStyle, width: 120 }}
              type="number" min={1} max={100} step={0.5}
              value={cfg.sync_threshold_percent}
              onChange={(e) => set("sync_threshold_percent", parseFloat(e.target.value) || 85)}
            />
            {[75, 85, 90, 95].map((p) => (
              <button
                key={p}
                onClick={() => set("sync_threshold_percent", p)}
                style={{
                  padding: "0.3rem 0.6rem", borderRadius: 5, fontSize: 12,
                  background: cfg.sync_threshold_percent === p ? "var(--accent-dim)" : "var(--surface2)",
                  color: cfg.sync_threshold_percent === p ? "var(--accent)" : "var(--muted)",
                  border: `1px solid ${cfg.sync_threshold_percent === p ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                {p}%
              </button>
            ))}
          </div>
        </Field>
        <Field label="Mode de suivi">
          <select
            style={{ ...inputStyle, width: "auto" }}
            value={cfg.anime_auto_tracking_mode}
            onChange={(e) => set("anime_auto_tracking_mode", e.target.value)}
          >
            <option value="video">Auto (au seuil de progression)</option>
            <option value="manual">Manuel uniquement</option>
          </select>
        </Field>
        <div style={{ display: "flex", gap: "1.5rem" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={cfg.sync_end_only} onChange={(e) => set("sync_end_only", e.target.checked)} />
            Seuil quasi-fin (≥ 97%)
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={cfg.autostart_monitoring} onChange={(e) => set("autostart_monitoring", e.target.checked)} />
            Démarrage automatique
          </label>
        </div>
      </Section>

      {/* ── Save ── */}
      <button
        onClick={handleSave}
        disabled={saving}
        style={{
          width: "100%", padding: "0.65rem", borderRadius: 8,
          fontWeight: 700, fontSize: 14,
          background: saving ? "var(--surface2)" : "var(--accent)",
          color: saving ? "var(--muted)" : "#000",
          border: "none", transition: "all 0.15s",
        }}
      >
        {saving ? "Enregistrement…" : "Enregistrer la configuration"}
      </button>
    </div>
  );
}
