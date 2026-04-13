"""
Boucle de surveillance Plex → AniList (sans UI).

Adaptée de plexani/main.py : même logique de polling, matching, sync et historique,
mais sans tkinter — toute la communication se fait via le state dict partagé.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from anilist_client import AniListClient, AniListClientError

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
HISTORY_PATH = DATA_DIR / "history.json"
CONFIG_PATH = DATA_DIR / "config.json"

CONFIG_DEFAULTS: Dict[str, Any] = {
    "plex_token": "",
    "plex_server_name": "",
    "plex_username": "",
    "anilist_token": "",
    "anilist_client_id": "",
    "anilist_client_secret": "",
    "anilist_redirect_uri": "http://localhost:8765/api/oauth/anilist/callback",
    "poll_interval_seconds": 10,
    "sync_threshold_percent": 85.0,
    "sync_end_only": False,
    "anime_auto_tracking_mode": "video",
    "autostart_monitoring": True,
    "verbose_anilist": False,
    "manual_mappings": {},
}

SYNC_END_ONLY_MIN_PERCENT = 97.0


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(CONFIG_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            merged.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except Exception:
            pass
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def effective_threshold(cfg: Dict[str, Any]) -> float:
    try:
        raw = max(1.0, min(100.0, float(cfg.get("sync_threshold_percent", 85.0))))
    except (TypeError, ValueError):
        raw = 85.0
    if bool(cfg.get("sync_end_only")):
        return max(raw, SYNC_END_ONLY_MIN_PERCENT)
    return raw


# ── History ───────────────────────────────────────────────────────────────────

def load_history() -> Dict[str, Any]:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {"processed": {}, "actions": []}


def save_history(history: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), "utf-8")


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Monitor class ─────────────────────────────────────────────────────────────

class Monitor:
    """
    Gère le thread de surveillance Plex et l'état partagé.
    Toutes les mutations de state passent par self._lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # État partagé (lu par l'API)
        self.monitoring = False
        self.current_session: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.logs: List[Dict[str, str]] = []           # [{at, msg}]
        self.history: Dict[str, Any] = load_history()

        # Contexte du cycle en cours (non exposé directement)
        self._notified_episode_keys: set = set()
        self._map_fail_key: str = ""
        self._plex_diag_last: float = 0.0
        self._resolve_cache: Dict[str, Tuple[int, str, float]] = {}

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        entry = {"at": utc_now_iso(), "msg": msg}
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]
        print(f"[plexani] {msg}")

    def get_logs(self, since_index: int = 0) -> List[Dict[str, str]]:
        with self._lock:
            return list(self.logs[since_index:])

    def clear_logs(self) -> None:
        with self._lock:
            self.logs = []

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        with self._lock:
            self.monitoring = True
            self.last_error = None
        return True

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self.monitoring = False
            self.current_session = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Status snapshot ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "monitoring": self.monitoring and self.is_running(),
                "current_session": dict(self.current_session) if self.current_session else None,
                "last_error": self.last_error,
                "log_count": len(self.logs),
            }

    # ── History helpers ───────────────────────────────────────────────────────

    def get_history(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "actions": list(self.history.get("actions", [])),
            }

    def clear_resolve_cache_key(self, key: str) -> None:
        self._resolve_cache.pop(key, None)

    def _migrate_mapping_keys(self, cfg: Dict[str, Any]) -> None:
        """Migre l'ancienne structure à plat vers la structure imbriquée par serveur."""
        server_name = str(cfg.get("plex_server_name", "")).strip()
        mappings = cfg.get("manual_mappings", {})
        if any(isinstance(v, int) for v in mappings.values()):
            cfg["manual_mappings"] = {server_name: mappings}
            save_config(cfg)

    @staticmethod
    def _build_mapping_key(session: Any, season_number: int) -> str:
        grk = getattr(session, "grandparentRatingKey", None)
        if grk:
            return f"plex:{grk}:s{season_number}"
        title = getattr(session, "grandparentTitle", "Unknown")
        return f"{title.strip().casefold()}:s{season_number}"

    def rollback_last(self, anilist_token: str) -> Dict[str, Any]:
        """Annule la dernière action update de l'historique pour l'épisode en cours."""
        with self._lock:
            session = dict(self.current_session) if self.current_session else None
            actions = list(self.history.get("actions", []))

        if not session:
            raise ValueError("Lance une lecture sur Plex sur l'anime concerné pour annuler la sync de cet épisode.")
            
        cur_media_id = session.get("anilist_media_id")
        cur_episode = session.get("episode")
        cur_session_key = session.get("session_key")
        
        if not cur_media_id or not cur_episode or not cur_session_key:
            raise ValueError("Impossible d'annuler : lecture non identifiée ou terminée.")

        target_action = None
        for action in reversed(actions):
            if action.get("type") == "update":
                # Verifie qu'on est EXACTEMENT sur la même lecture physique sans interruption
                if int(action.get("media_id", 0)) == int(cur_media_id) and int(action.get("episode", -1)) == int(cur_episode) and action.get("session_key") == cur_session_key:
                    target_action = action
                    break

        if not target_action:
            raise ValueError("Rien à annuler pour l'épisode en cours : déjà annulé, ou aucune maj enregistrée pour cet épisode.")

        action = target_action
        media_id = action.get("media_id")
        from_progress = action.get("from_progress", 0)

        verbose = bool(load_config().get("verbose_anilist", False))
        client = AniListClient(token=anilist_token, verbose=verbose, logger=self.log if verbose else None)
        client.rollback_to_progress(media_id=int(media_id), progress=int(from_progress))

        rb = {
            "at": utc_now_iso(),
            "type": "rollback",
            "media_id": media_id,
            "anilist_title": action.get("anilist_title"),
            "plex_title": action.get("plex_title"),
            "episode": action.get("episode"),
            "reverted_at": action.get("at"),
            "to_progress": from_progress,
        }
        with self._lock:
            new_actions = list(self.history.get("actions", []))
            new_actions.append(rb)
            # Retire les processed keys pour permettre une re-sync
            processed = dict(self.history.get("processed", {}))
            for key in self._processed_keys_for_action(action):
                processed.pop(key, None)
            self.history["actions"] = new_actions[-200:]
            self.history["processed"] = processed
            save_history(self.history)

        self.log(f"Rollback : {action.get('anilist_title')} ep.{action.get('episode')} → ep.{from_progress}")
        return rb

    @staticmethod
    def _processed_keys_for_action(action: Dict[str, Any]) -> List[str]:
        keys: List[str] = []
        for raw in (action.get("episode_key"), action.get("plex_rating_key")):
            if raw is not None and str(raw).strip():
                keys.append(str(raw))
        pt = action.get("plex_title")
        ep = action.get("episode")
        if pt and ep is not None:
            keys.append(f"{pt}:{int(ep)}")
        seen: set = set()
        return [k for k in keys if k and k not in seen and not seen.add(k)]  # type: ignore[func-returns-value]

    def force_sync_episode(self, anilist_token: str) -> Dict[str, Any]:
        """Force la mise à jour AniList de l'épisode Plex en cours."""
        with self._lock:
            session = dict(self.current_session) if self.current_session else None

        if not session:
            raise ValueError("Aucune session Plex en cours.")
        media_id = session.get("anilist_media_id")
        episode = session.get("episode")
        if not media_id:
            raise ValueError("Aucune correspondance AniList pour la session en cours.")
        if not episode:
            raise ValueError("Numéro d'épisode introuvable.")

        verbose = bool(load_config().get("verbose_anilist", False))
        client = AniListClient(token=anilist_token, verbose=verbose, logger=self.log if verbose else None)
        list_entry = client.get_media_list_entry(media_id=int(media_id))
        prev_progress = int((list_entry or {}).get("progress") or 0)
        new_progress = max(prev_progress, int(episode))

        if new_progress <= prev_progress:
            self.log(f"Déjà à jour sur AniList : {session.get('anilist_title')} ep.{prev_progress}")
            return {"already_up_to_date": True, "progress": prev_progress}

        save_st = AniListClient.status_for_progress_save(list_entry, new_progress)
        result = client.save_progress(media_id=int(media_id), progress=new_progress, status=save_st)
        to_p = int(result["progress"])

        with self._lock:
            if self.current_session:
                self.current_session["anilist_progress"] = to_p

        self.log(f"Sync manuelle : {session.get('anilist_title')} ep.{to_p} (avant : {prev_progress})")

        episode_key = session.get("episode_key") or f"{session.get('plex_title')}:{episode}"
        action = {
            "at": utc_now_iso(),
            "type": "update",
            "session_key": session.get("session_key"),
            "episode_key": episode_key,
            "plex_title": session.get("plex_title"),
            "episode": episode,
            "season": session.get("season"),
            "media_id": int(media_id),
            "anilist_title": session.get("anilist_title"),
            "from_progress": prev_progress,
            "to_progress": to_p,
        }
        with self._lock:
            processed = dict(self.history.get("processed", {}))
            processed[episode_key] = utc_now_iso()
            actions = list(self.history.get("actions", []))
            actions.append(action)
            self.history["processed"] = processed
            self.history["actions"] = actions[-200:]
            save_history(self.history)

        return {"already_up_to_date": False, "progress": to_p, "from_progress": prev_progress,
                "anilist_title": session.get("anilist_title")}

    # ── Monitoring loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        """Thread principal de surveillance."""
        self.log("Démarrage du suivi — connexion Plex + AniList…")
        try:
            plex_server, anilist_client, target_cf, target_local = self._bootstrap()
        except Exception as exc:
            msg = str(exc)
            self.log(f"Connexion échouée : {msg}")
            with self._lock:
                self.monitoring = False
                self.last_error = msg
            return

        self.log("Connecté à Plex et AniList.")

        while not self._stop_event.is_set():
            cfg = load_config()
            interval = max(1, int(cfg.get("poll_interval_seconds", 10)))
            threshold = effective_threshold(cfg)
            auto_mode = str(cfg.get("anime_auto_tracking_mode", "video")).strip().lower()
            if auto_mode not in ("video", "manual"):
                auto_mode = "video"

            deadline = time.monotonic() + interval

            try:
                sessions = plex_server.sessions()
                matched = self._find_user_session(sessions, target_cf, target_local)
                if not matched:
                    with self._lock:
                        self.current_session = None
                    self._maybe_log_session_help(sessions, cfg.get("plex_username", ""))
                else:
                    self._playback_cycle(matched, anilist_client, threshold, auto_mode, cfg)
            except Exception as exc:
                self.log(f"Erreur pendant le suivi : {exc}")

            remaining = deadline - time.monotonic()
            self._stop_event.wait(max(0.05, remaining))

        with self._lock:
            self.monitoring = False
            self.current_session = None
        self.log("Suivi arrêté.")

    def _bootstrap(self):
        """Charge la config, valide les tokens et connecte Plex + AniList."""
        from plexapi.myplex import MyPlexAccount

        cfg = load_config()
        plex_token = str(cfg.get("plex_token", "")).strip()
        plex_server_name = str(cfg.get("plex_server_name", "")).strip()
        plex_username = str(cfg.get("plex_username", "")).strip()
        anilist_token = str(cfg.get("anilist_token", "")).strip()

        if not plex_token:
            raise RuntimeError("Token Plex manquant — connecte ton compte dans la configuration.")
        if not plex_server_name:
            raise RuntimeError("Nom du serveur Plex manquant.")
        if not plex_username:
            raise RuntimeError("Nom d'utilisateur Plex manquant.")
        if not anilist_token:
            raise RuntimeError("Token AniList manquant — connecte ton compte AniList.")

        account = MyPlexAccount(token=plex_token)
        plex_server = account.resource(plex_server_name).connect()

        verbose = bool(cfg.get("verbose_anilist", False))
        anilist_client = AniListClient(token=anilist_token, verbose=verbose, logger=self.log if verbose else None)
        anilist_client.verify_token()
        # Récupère le viewer_id pour des requêtes MediaList fiables
        try:
            profile = anilist_client.get_viewer_profile()
            anilist_client.viewer_id = int(profile.get("id") or 0) or None
        except Exception:
            pass

        self._migrate_mapping_keys(cfg)

        target_cf = plex_username.strip().casefold()
        target_local = target_cf.split("@", 1)[0] if "@" in target_cf else target_cf
        return plex_server, anilist_client, target_cf, target_local

    # ── Session helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _session_viewer_ids(session: Any) -> List[str]:
        out: List[str] = []
        raw_list = getattr(session, "usernames", None)
        if isinstance(raw_list, (list, tuple)):
            out.extend(str(x) for x in raw_list if x)
        elif raw_list:
            out.append(str(raw_list))
        for attr in ("_username", "username"):
            v = getattr(session, attr, None)
            if v:
                out.append(str(v))
        seen: set = set()
        return [x.strip() for x in out if x.strip() and not seen.add(x.strip().casefold())]  # type: ignore[func-returns-value]

    def _viewer_matches(self, session: Any, target_cf: str, target_local: str) -> bool:
        for ident in self._session_viewer_ids(session):
            icf = ident.casefold()
            if icf == target_cf:
                return True
            ilocal = icf.split("@", 1)[0] if "@" in icf else icf
            if ilocal == target_local:
                return True
        return False

    def _find_user_session(self, sessions: List[Any], target_cf: str, target_local: str) -> Optional[Any]:
        for s in sessions:
            if self._viewer_matches(s, target_cf, target_local):
                return s
        return None

    def _maybe_log_session_help(self, sessions: List[Any], username: str) -> None:
        now = time.monotonic()
        if now - self._plex_diag_last < 60.0:
            return
        self._plex_diag_last = now
        if not sessions:
            self.log("Plex : aucune lecture en cours sur ce serveur.")
            return
        parts = []
        for s in sessions:
            ids = self._session_viewer_ids(s)
            title = getattr(s, "grandparentTitle", None) or getattr(s, "title", "?")
            parts.append(f"{ids} → {title}")
        self.log(
            f"Plex : {len(sessions)} lecture(s) mais aucune pour « {username} ». "
            f"En cours : {' | '.join(parts)}"
        )

    @staticmethod
    def _episode_key(session: Any) -> str:
        rating_key = getattr(session, "ratingKey", None)
        grandparent = getattr(session, "grandparentTitle", "Unknown")
        episode_idx = getattr(session, "index", 0)
        return str(rating_key or f"{grandparent}:{episode_idx}")

    @staticmethod
    def _is_playing(session: Any) -> bool:
        players = getattr(session, "players", None) or []
        if not players:
            return True
        return all(str(getattr(pl, "state", "") or "").lower() != "paused" for pl in players)

    # ── Playback cycle ────────────────────────────────────────────────────────

    def _playback_cycle(
        self,
        session: Any,
        anilist_client: AniListClient,
        threshold: float,
        auto_mode: str,
        cfg: Dict[str, Any],
    ) -> None:
        title = getattr(session, "grandparentTitle", "Unknown")
        ep_num = int(getattr(session, "index", 0) or 0)
        season = getattr(session, "parentIndex", None)
        ep_title = getattr(session, "title", "") or ""
        duration = float(getattr(session, "duration", 0) or 0)
        offset = float(getattr(session, "viewOffset", 0) or 0)
        pct = (offset / duration * 100.0) if duration > 0 else 0.0
        episode_key = self._episode_key(session)
        session_key = str(getattr(session, "sessionKey", "")) or None

        season_number = int(season) if season is not None and int(season) >= 1 else 1
        mapping_key = self._build_mapping_key(session, season_number)
        server_name = str(cfg.get("plex_server_name", "")).strip()
        manual_mappings: Dict[str, Any] = (cfg.get("manual_mappings") or {}).get(server_name, {})
        has_manual_mapping = mapping_key in manual_mappings

        # Mise à jour UI (session Plex seule, avant résolution AniList)
        with self._lock:
            self.current_session = {
                "session_key": session_key,
                "episode_key": episode_key,
                "plex_title": title,
                "plex_ep_title": ep_title,
                "episode": ep_num,
                "season": int(season) if season is not None else None,
                "plex_percent": round(pct, 1),
                "offset_ms": offset,
                "duration_ms": duration,
                "is_playing": self._is_playing(session),
                "mapping_key": mapping_key,
                "has_manual_mapping": has_manual_mapping,
                "anilist_title": self.current_session.get("anilist_title") if self.current_session else None,
                "anilist_media_id": self.current_session.get("anilist_media_id") if self.current_session else None,
                "anilist_progress": self.current_session.get("anilist_progress") if self.current_session else None,
                "anilist_list_status": self.current_session.get("anilist_list_status") if self.current_session else None,
                "anilist_cover": self.current_session.get("anilist_cover") if self.current_session else None,
                "threshold": threshold,
                "threshold_reached": pct >= threshold,
            }

        # Résolution AniList
        now = time.monotonic()
        media_id: Optional[int] = None
        matched_title = ""

        if has_manual_mapping:
            # Mapping manuel prioritaire — ignore le resolve_cache
            media_id = int(manual_mappings[mapping_key])
            self.log(f"Mapping manuel : {mapping_key} → media_id={media_id}")
        elif mapping_key in self._resolve_cache and now - self._resolve_cache[mapping_key][2] < 300.0:
            media_id, matched_title, _ = self._resolve_cache[mapping_key]
        else:
            best = anilist_client.find_best_anime_id(title, season_number=season_number)
            if best:
                media_id, matched_title = best
                self._resolve_cache[mapping_key] = (media_id, matched_title, now)
                self._map_fail_key = ""
            else:
                self._resolve_cache.pop(mapping_key, None)
                if self._map_fail_key != mapping_key:
                    self._map_fail_key = mapping_key
                    self.log(f"Anime introuvable sur AniList pour « {title} ».")
                with self._lock:
                    if self.current_session:
                        self.current_session["anilist_title"] = None
                        self.current_session["anilist_media_id"] = None
                return

        # Récupère la fiche AniList complète (cover, progrès) (Cachée 45s pour la limite d'API)
        try:
            if not hasattr(self, "_card_cache"):
                self._card_cache = {}
                
            if media_id in self._card_cache and now - self._card_cache[media_id][1] < 45.0:
                card = self._card_cache[media_id][0]
            else:
                card = anilist_client.get_media_with_list_entry(media_id)
                self._card_cache[media_id] = (card, now)
                
            media = card.get("media") or {}
            list_entry = card.get("list_entry")
            # Résoudre le titre depuis l'API quand on vient d'un mapping manuel
            if not matched_title:
                t = media.get("title") or {}
                matched_title = t.get("english") or t.get("userPreferred") or t.get("romaji") or f"#{media_id}"
            ci = media.get("coverImage") or {}
            cover = ci.get("extraLarge") or ci.get("large") or ci.get("medium") or None
            anilist_progress = int((list_entry or {}).get("progress") or 0)
            anilist_status = str((list_entry or {}).get("status") or "")
            anilist_score = (list_entry or {}).get("score")
            anilist_episodes = media.get("episodes")
            anilist_site_url = media.get("siteUrl") or None
            anilist_format = media.get("format") or None
        except Exception:
            cover = None
            anilist_progress = 0
            anilist_status = ""
            anilist_score = None
            anilist_episodes = None
            anilist_site_url = None
            anilist_format = None

        with self._lock:
            if self.current_session:
                self.current_session["anilist_title"] = matched_title
                self.current_session["anilist_media_id"] = int(media_id)
                self.current_session["anilist_progress"] = anilist_progress
                self.current_session["anilist_list_status"] = anilist_status
                self.current_session["anilist_cover"] = cover
                self.current_session["anilist_score"] = anilist_score
                self.current_session["anilist_episodes"] = anilist_episodes
                self.current_session["anilist_site_url"] = anilist_site_url
                self.current_session["anilist_format"] = anilist_format

        # Notification première détection
        ek_str = str(episode_key)
        if ek_str not in self._notified_episode_keys:
            self._notified_episode_keys.add(ek_str)
            self.log(f"Détecté : {matched_title} — S{season}E{ep_num} « {ep_title} »")

        if auto_mode == "manual":
            return

        # Gate : seuil + non en pause
        if pct < threshold or not self._is_playing(session):
            return

        # Déjà traité ?
        with self._lock:
            if self.history.get("processed", {}).get(ek_str):
                return

        # Sync AniList
        try:
            list_entry_now = anilist_client.get_media_list_entry(media_id=int(media_id))
            prev_progress = int((list_entry_now or {}).get("progress") or 0)
            list_status = str((list_entry_now or {}).get("status") or "")
            new_progress = max(prev_progress, ep_num)

            if new_progress == prev_progress:
                with self._lock:
                    processed = dict(self.history.get("processed", {}))
                    processed[ek_str] = utc_now_iso()
                    self.history["processed"] = processed
                self.log(f"Déjà à jour sur AniList : {matched_title} ep.{prev_progress}")
                return

            save_st = AniListClient.status_for_progress_save(list_entry_now, new_progress)
            result = anilist_client.save_progress(
                media_id=int(media_id), progress=new_progress, status=save_st
            )
            self.log(
                f"Sync : {matched_title} — ep.{result['progress']} "
                f"(avant : {prev_progress}, statut : {list_status})"
            )
            # Mise à jour immédiate de la session affichée
            with self._lock:
                if self.current_session:
                    self.current_session["anilist_progress"] = int(result["progress"])

            action = {
                "at": utc_now_iso(),
                "type": "update",
                "session_key": session_key,
                "episode_key": episode_key,
                "plex_rating_key": getattr(session, "ratingKey", None),
                "plex_title": title,
                "episode": ep_num,
                "season": int(season) if season is not None else None,
                "media_id": int(media_id),
                "anilist_title": matched_title,
                "from_progress": prev_progress,
                "to_progress": int(result["progress"]),
            }
            with self._lock:
                processed = dict(self.history.get("processed", {}))
                processed[ek_str] = utc_now_iso()
                actions = list(self.history.get("actions", []))
                actions.append(action)
                self.history["processed"] = processed
                self.history["actions"] = actions[-200:]
                save_history(self.history)

        except AniListClientError as exc:
            self.log(f"Erreur AniList lors de la sync : {exc}")
