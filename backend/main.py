"""
Plexani Server — API FastAPI.

Endpoints REST + WebSocket pour le dashboard web.
Sert aussi les fichiers statiques du frontend React (build Vite).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from monitor import Monitor, load_config, save_config, utc_now_iso

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Plexani Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

monitor = Monitor()

# Plex OAuth state (PIN en attente)
_plex_pin_login: Any = None

# ── WebSocket broadcast ───────────────────────────────────────────────────────

_ws_clients: set[WebSocket] = set()


async def _broadcast(data: dict) -> None:
    """Diffuse un message JSON a tous les WebSockets encore valides."""
    dead: set[WebSocket] = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


async def _status_push_loop() -> None:
    """Pousse le status à tous les clients toutes les 2 secondes."""
    while True:
        await asyncio.sleep(2)
        if _ws_clients:
            await _broadcast({"type": "status", "data": monitor.get_status()})


@app.on_event("startup")
async def startup() -> None:
    """Initialise les taches de fond et le suivi automatique au demarrage."""
    asyncio.create_task(_status_push_loop())

    cfg = load_config()
    if cfg.get("autostart_monitoring") and _has_required_tokens(cfg):
        monitor.start()
        monitor.log("Démarrage automatique activé.")


def _has_required_tokens(cfg: Dict[str, Any]) -> bool:
    """Verifie que la configuration minimale Plex + AniList est presente."""
    return bool(
        str(cfg.get("plex_token", "")).strip()
        and str(cfg.get("anilist_token", "")).strip()
        and str(cfg.get("plex_server_name", "")).strip()
        and str(cfg.get("plex_username", "")).strip()
    )


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Maintient le canal temps reel du dashboard."""
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # Envoie le status initial dès la connexion
        await ws.send_json({"type": "status", "data": monitor.get_status()})
        # Envoie les derniers logs
        await ws.send_json({"type": "logs", "data": monitor.get_logs()})
        while True:
            # Reste connecté (côté client ping)
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ── Status & Monitoring ───────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    """Retourne le statut courant du monitor enrichi par l'etat de config."""
    cfg = load_config()
    status = monitor.get_status()
    status["config_ok"] = _has_required_tokens(cfg)
    return status


@app.post("/api/monitoring/start")
async def start_monitoring() -> Dict[str, Any]:
    """Demarre la surveillance Plex si la configuration est complete."""
    cfg = load_config()
    if not _has_required_tokens(cfg):
        raise HTTPException(400, "Tokens manquants — configure Plex et AniList d'abord.")
    started = monitor.start()
    if not started:
        return {"ok": False, "message": "Suivi déjà actif."}
    await _broadcast({"type": "status", "data": monitor.get_status()})
    return {"ok": True, "message": "Suivi lancé."}


@app.post("/api/monitoring/sync")
async def force_sync() -> Dict[str, Any]:
    """Force la synchronisation AniList de l'episode Plex courant."""
    cfg = load_config()
    token = str(cfg.get("anilist_token", "")).strip()
    if not token:
        raise HTTPException(400, "Token AniList manquant.")
    try:
        result = monitor.force_sync_episode(anilist_token=token)
        await _broadcast({"type": "history_updated"})
        await _broadcast({"type": "status", "data": monitor.get_status()})
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/monitoring/stop")
async def stop_monitoring() -> Dict[str, Any]:
    """Arrete la surveillance Plex et notifie le dashboard."""
    monitor.stop()
    await _broadcast({"type": "status", "data": monitor.get_status()})
    return {"ok": True, "message": "Suivi arrêté."}


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config() -> Dict[str, Any]:
    """Expose la configuration sans renvoyer les tokens secrets."""
    cfg = load_config()
    # On masque les tokens dans la réponse (indique juste si présent)
    safe = {k: v for k, v in cfg.items() if k not in ("plex_token", "anilist_token")}
    safe["has_plex_token"] = bool(str(cfg.get("plex_token", "")).strip())
    safe["has_anilist_token"] = bool(str(cfg.get("anilist_token", "")).strip())
    return safe


@app.post("/api/config")
async def update_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Met a jour les champs de configuration autorises."""
    cfg = load_config()
    allowed = {
        "plex_server_name", "plex_username",
        "anilist_client_id", "anilist_client_secret", "anilist_redirect_uri",
        "poll_interval_seconds", "sync_threshold_percent", "sync_end_only",
        "anime_auto_tracking_mode", "autostart_monitoring", "verbose_anilist",
    }
    for key in allowed:
        if key in body:
            cfg[key] = body[key]
    # Validation basique
    try:
        cfg["poll_interval_seconds"] = max(1, min(3600, int(cfg["poll_interval_seconds"])))
        cfg["sync_threshold_percent"] = max(1.0, min(100.0, float(cfg["sync_threshold_percent"])))
    except (TypeError, ValueError):
        raise HTTPException(400, "Valeurs invalides pour interval ou seuil.")
    save_config(cfg)
    return {"ok": True}


# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history() -> Dict[str, Any]:
    """Retourne l'historique des actions de sync."""
    return monitor.get_history()


@app.post("/api/history/rollback")
async def rollback_last() -> Dict[str, Any]:
    """Annule la derniere sync correspondant a la lecture courante."""
    cfg = load_config()
    token = str(cfg.get("anilist_token", "")).strip()
    if not token:
        raise HTTPException(400, "Token AniList manquant.")
    try:
        rb = monitor.rollback_last(anilist_token=token)
        await _broadcast({"type": "history_updated"})
        return {"ok": True, "rollback": rb}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def get_logs(since: int = 0) -> Dict[str, Any]:
    """Retourne les logs du monitor depuis un index donne."""
    return {"logs": monitor.get_logs(since_index=since)}


@app.post("/api/logs/clear")
async def clear_logs() -> Dict[str, Any]:
    """Vide les logs en memoire et previent les clients connectes."""
    monitor.clear_logs()
    await _broadcast({"type": "logs", "data": []})
    return {"ok": True}


# ── OAuth Plex ────────────────────────────────────────────────────────────────

@app.post("/api/oauth/plex/init")
async def plex_oauth_init() -> Dict[str, Any]:
    """Demarre le flux OAuth PIN de Plex et renvoie l'URL d'autorisation."""
    global _plex_pin_login
    try:
        from plexapi.myplex import MyPlexPinLogin
        _plex_pin_login = MyPlexPinLogin(oauth=True)
        _plex_pin_login.run(timeout=300)
        return {"ok": True, "auth_url": _plex_pin_login.oauthUrl()}
    except Exception as exc:
        raise HTTPException(500, f"Impossible d'initier l'OAuth Plex : {exc}")


@app.get("/api/oauth/plex/poll")
async def plex_oauth_poll() -> Dict[str, Any]:
    """Interroge le flux OAuth Plex en attente et sauvegarde le token si pret."""
    global _plex_pin_login
    if _plex_pin_login is None:
        return {"done": False, "error": "Aucun flux OAuth Plex en cours."}
    try:
        token = _plex_pin_login.token
        if not token:
            return {"done": False}
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        username = (account.username or account.title or "").strip()
        cfg = load_config()
        cfg["plex_token"] = token
        cfg["plex_username"] = username or cfg.get("plex_username", "")
        save_config(cfg)
        _plex_pin_login = None
        await _broadcast({"type": "config_updated"})
        return {"done": True, "username": username}
    except Exception as exc:
        return {"done": False, "error": str(exc)}


# ── OAuth AniList ─────────────────────────────────────────────────────────────

@app.get("/api/oauth/anilist/url")
async def anilist_oauth_url() -> Dict[str, Any]:
    """Construit l'URL d'autorisation OAuth AniList."""
    cfg = load_config()
    client_id = str(cfg.get("anilist_client_id", "")).strip()
    redirect_uri = str(cfg.get("anilist_redirect_uri", "http://localhost:8765/api/oauth/anilist/callback")).strip()
    if not client_id:
        raise HTTPException(400, "Client ID AniList manquant dans la configuration.")
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    })
    return {"ok": True, "url": f"https://anilist.co/api/v2/oauth/authorize?{params}"}


@app.get("/api/oauth/anilist/callback")
async def anilist_oauth_callback(code: Optional[str] = None, error: Optional[str] = None) -> HTMLResponse:
    """Echange le code OAuth AniList contre un token et affiche le resultat."""
    if error or not code:
        return HTMLResponse(
            "<html><body><p style='color:red'>Erreur OAuth AniList. Ferme et réessaie.</p></body></html>"
        )
    cfg = load_config()
    client_id = str(cfg.get("anilist_client_id", "")).strip()
    client_secret = str(cfg.get("anilist_client_secret", "")).strip()
    redirect_uri = str(cfg.get("anilist_redirect_uri", "http://localhost:8765/api/oauth/anilist/callback")).strip()

    if not client_id or not client_secret:
        return HTMLResponse(
            "<html><body><p style='color:red'>Client ID ou Secret AniList manquant.</p></body></html>"
        )

    try:
        import requests as req
        resp = req.post(
            "https://anilist.co/api/v2/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=20,
        )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(data.get("message") or data.get("error") or "Token absent")

        cfg["anilist_token"] = token
        save_config(cfg)
        asyncio.create_task(_broadcast({"type": "config_updated"}))
        monitor.log("AniList : connexion OAuth réussie.")
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#111;color:#eee;padding:2rem'>"
            "<h2 style='color:#4ade80'>✓ AniList connecté</h2>"
            "<p>Tu peux fermer cet onglet et revenir sur le dashboard.</p>"
            "</body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><p style='color:red'>Erreur : {exc}</p></body></html>"
        )


# ── Mapping manuel Plex → AniList ────────────────────────────────────────────

@app.post("/api/mapping/set")
async def mapping_set(body: Dict[str, Any]) -> Dict[str, Any]:
    """Enregistre un mapping manuel Plex vers AniList pour le serveur courant."""
    mapping_key = str(body.get("mapping_key", "")).strip()
    media_id = body.get("media_id")
    if not mapping_key or media_id is None:
        raise HTTPException(400, "mapping_key et media_id sont requis.")
    cfg = load_config()
    server_name = str(cfg.get("plex_server_name", "")).strip()
    all_mappings = dict(cfg.get("manual_mappings") or {})
    server_mappings = dict(all_mappings.get(server_name, {}))
    server_mappings[mapping_key] = int(media_id)
    all_mappings[server_name] = server_mappings
    cfg["manual_mappings"] = all_mappings
    save_config(cfg)
    monitor.clear_resolve_cache_key(mapping_key)
    monitor.log(f"Mapping manuel enregistré : {mapping_key} → {media_id}")
    return {"ok": True}


@app.delete("/api/mapping/remove")
async def mapping_remove(body: Dict[str, Any]) -> Dict[str, Any]:
    """Supprime un mapping manuel du serveur courant."""
    mapping_key = str(body.get("mapping_key", "")).strip()
    if not mapping_key:
        raise HTTPException(400, "mapping_key est requis.")
    cfg = load_config()
    server_name = str(cfg.get("plex_server_name", "")).strip()
    all_mappings = dict(cfg.get("manual_mappings") or {})
    all_mappings.get(server_name, {}).pop(mapping_key, None)
    cfg["manual_mappings"] = all_mappings
    save_config(cfg)
    monitor.clear_resolve_cache_key(mapping_key)
    monitor.log(f"Mapping manuel supprimé : {mapping_key}")
    return {"ok": True}


@app.get("/api/mapping/search")
async def mapping_search(q: str = "") -> Dict[str, Any]:
    """Recherche des candidats AniList pour aider le mapping manuel."""
    if not q.strip():
        return {"candidates": []}
    cfg = load_config()
    token = str(cfg.get("anilist_token", "")).strip()
    if not token:
        raise HTTPException(400, "Token AniList manquant.")
    try:
        from anilist_client import AniListClient
        client = AniListClient(token=token)
        candidates = client.search_anime_candidates(title=q, per_page=10)
        results = [
            {
                "id": c.get("id"),
                "title": c.get("title", {}),
                "coverImage": c.get("coverImage", {}),
            }
            for c in candidates
        ]
        return {"candidates": results}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Image proxy (AniList CDN) ─────────────────────────────────────────────────

# Hote AniList acceptes par le proxy pour eviter un proxy ouvert.
_ALLOWED_IMAGE_HOSTS = {"s4.anilist.co", "s1.anilist.co", "img.anilist.co", "cdn.anilist.co"}

@app.get("/api/proxy/image")
async def proxy_image(url: str) -> Response:
    """Proxyfie une image AniList en limitant les hotes CDN autorises."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname not in _ALLOWED_IMAGE_HOSTS:
        raise HTTPException(400, "Hôte non autorisé.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"User-Agent": "plexani/1.0"}, follow_redirects=True)
        content_type = r.headers.get("content-type", "image/jpeg")
        return Response(content=r.content, media_type=content_type, headers={
            "Cache-Control": "public, max-age=86400",
        })
    except Exception:
        raise HTTPException(502, "Impossible de récupérer l'image.")


# ── AniList viewer profile ────────────────────────────────────────────────────

@app.get("/api/anilist/viewer")
async def get_anilist_viewer() -> Dict[str, Any]:
    """Retourne le profil Viewer AniList associe au token configure."""
    cfg = load_config()
    token = str(cfg.get("anilist_token", "")).strip()
    if not token:
        return {"ok": False, "error": "Token AniList manquant."}
    try:
        from anilist_client import AniListClient
        verbose = bool(cfg.get("verbose_anilist", False))
        client = AniListClient(token=token, verbose=verbose)
        profile = client.get_viewer_profile()
        return {"ok": True, **profile}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Plex servers list ────────────────────────────────────────────────────────

@app.get("/api/plex/servers")
async def get_plex_servers() -> Dict[str, Any]:
    """Liste les serveurs Plex accessibles avec le token configure."""
    cfg = load_config()
    token = str(cfg.get("plex_token", "")).strip()
    if not token:
        return {"ok": False, "servers": [], "error": "Token Plex manquant — connecte-toi d'abord."}
    try:
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        names = sorted({r.name for r in account.resources() if getattr(r, "name", None)})
        return {"ok": True, "servers": names}
    except Exception as exc:
        return {"ok": False, "servers": [], "error": str(exc)}


# ── Frontend static (servi en dernier) ───────────────────────────────────────

# Build Vite servi par FastAPI quand il existe dans l'image de production.
_STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if _STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="assets")

    # Sert index.html pour toutes les routes non-API (SPA routing)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> Any:
        """Sert l'application React pour les routes non API."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text("utf-8"))
        return HTMLResponse("<h1>Frontend non buildé</h1>", status_code=503)
