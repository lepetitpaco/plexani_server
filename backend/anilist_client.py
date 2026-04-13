"""
Client AniList GraphQL pour Plexani.

Gère l'authentification, la recherche d'anime, la mise à jour de la liste
(progression + statut) et le rollback. Toutes les erreurs API sont levées
sous forme d'AniListClientError.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

import requests


ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"


class AniListClientError(Exception):
    """Raised when AniList API operations fail."""


class AniListClient:
    def __init__(self, token: str, timeout: int = 20, viewer_id: Optional[int] = None) -> None:
        if not token:
            raise ValueError("AniList token is required.")
        self.token = token
        self.timeout = timeout
        self.viewer_id = int(viewer_id) if viewer_id else None
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    @staticmethod
    def status_for_progress_save(
        list_entry: Optional[Dict[str, Any]], new_progress: int
    ) -> str:
        """
        Statut envoye a SaveMediaListEntry lors d'une maj de progression.

        Lors d'un passage de « Plan to Watch » / « Pause » -> « Watching », le statut
        est promu automatiquement vers CURRENT sans popup.
        """
        _ = new_progress  # reserve si regles plus fines (ex. dernier ep -> COMPLETED)
        if not list_entry:
            return "CURRENT"
        st = str(list_entry.get("status") or "")
        if st in ("PLANNING", "PAUSED", "DROPPED"):
            return "CURRENT"
        if st in ("CURRENT", "COMPLETED", "REPEATING"):
            return st
        return "CURRENT"

    def _request(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables}
        try:
            response = self.session.post(
                ANILIST_GRAPHQL_URL,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AniListClientError(f"Network error while calling AniList: {exc}") from exc

        if response.status_code != 200:
            raise AniListClientError(
                f"AniList returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AniListClientError("AniList returned non-JSON response.") from exc

        if data.get("errors"):
            first = data["errors"][0]
            msg = first.get("message", "Unknown AniList error")
            raise AniListClientError(f"AniList GraphQL error: {msg}")

        return data.get("data", {})

    def verify_token(self) -> str:
        """Verifie que le token utilisateur est valide. Retourne le nom du Viewer AniList."""
        query = "query { Viewer { name } }"
        data = self._request(query, {})
        viewer = data.get("Viewer")
        if not viewer:
            raise AniListClientError("Token AniList invalide ou expire (Viewer introuvable).")
        return str(viewer.get("name") or "")

    def get_viewer_profile(self) -> Dict[str, Any]:
        """Profil Viewer : id, nom, URL avatar (grande)."""
        query = """
        query {
          Viewer {
            id
            name
            avatar {
              large
              medium
            }
          }
        }
        """
        data = self._request(query, {})
        viewer = data.get("Viewer")
        if not viewer:
            raise AniListClientError("Viewer introuvable.")
        return {
            "id": int(viewer.get("id", 0)),
            "name": str(viewer.get("name") or ""),
            "avatar_url": str((viewer.get("avatar") or {}).get("large") or (viewer.get("avatar") or {}).get("medium") or ""),
        }

    def get_media_with_list_entry(self, media_id: int) -> Dict[str, Any]:
        """Fiche anime + entree de liste (MediaList) du compte.

        Deux requêtes séparées : Media (toujours présent) et MediaList (peut
        être absent si l'anime n'est pas encore dans la liste → 404).
        """
        media_query = """
        query ($id: Int!) {
          Media(id: $id, type: ANIME) {
            id
            siteUrl
            title { userPreferred romaji english }
            episodes
            coverImage { extraLarge large medium }
            bannerImage
            format
            status
          }
        }
        """
        media_data = self._request(media_query, {"id": int(media_id)})
        media = media_data.get("Media") or {}

        list_entry = self.get_media_list_entry(media_id)

        return {"media": media, "list_entry": list_entry}

    @staticmethod
    def normalize_title(raw_title: str) -> str:
        """
        Clean noisy release tags and punctuation for better matching.
        Example: "[SubsPlease] Frieren - 03 [1080p]" -> "Frieren"
        """
        if not raw_title:
            return ""

        title = raw_title.strip()
        title = re.sub(r"\[[^\]]*\]", " ", title)
        title = re.sub(r"\([^\)]*\)", " ", title)
        title = re.sub(r"\{[^\}]*\}", " ", title)
        title = re.sub(r"\s-\s\d{1,3}\b.*$", " ", title)
        title = re.sub(r"\b(S\d+E\d+|Episode\s*\d+)\b.*$", " ", title, flags=re.IGNORECASE)
        title = re.sub(r"[_\.]", " ", title)
        title = re.sub(r"\s+", " ", title)
        return title.strip()

    def search_anime_candidates(self, title: str, per_page: int = 5) -> List[Dict[str, Any]]:
        query = """
        query ($search: String, $perPage: Int) {
          Page(perPage: $perPage) {
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
              id
              title {
                romaji
                english
                native
              }
              synonyms
            }
          }
        }
        """
        clean_title = self.normalize_title(title)
        data = self._request(query, {"search": clean_title, "perPage": per_page})
        media = data.get("Page", {}).get("media", [])
        return media if isinstance(media, list) else []

    def find_best_anime_id(self, title: str) -> Optional[Tuple[int, str]]:
        """
        Cherche le meilleur match AniList pour un titre Plex.

        Stratégie :
        1. Cherche les 10 premiers résultats AniList pour le titre normalisé.
        2. Cherche une correspondance exacte (après normalisation) dans romaji / english /
           native / synonymes — retourne le premier match exact.
        3. Si aucune correspondance exacte : retourne le 1er résultat AniList (best guess).

        Retourne (media_id, titre_romaji) ou None si aucun résultat.
        """
        candidates = self.search_anime_candidates(title=title, per_page=10)
        if not candidates:
            return None

        cleaned = self.normalize_title(title).casefold()
        for item in candidates:
            names = [
                item.get("title", {}).get("romaji"),
                item.get("title", {}).get("english"),
                item.get("title", {}).get("native"),
                *(item.get("synonyms") or []),
            ]
            normalized_names = [self.normalize_title(n).casefold() for n in names if n]
            if cleaned in normalized_names:
                chosen_title = item.get("title", {}).get("romaji") or item.get("title", {}).get("english") or "Unknown"
                return int(item["id"]), chosen_title

        # Fallback : premier résultat AniList (search_match score le plus élevé)
        first = candidates[0]
        chosen_title = first.get("title", {}).get("romaji") or first.get("title", {}).get("english") or "Unknown"
        return int(first["id"]), chosen_title

    def save_progress(
        self, media_id: int, progress: int, status: str = "CURRENT"
    ) -> Dict[str, Any]:
        mutation = """
        mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
          SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
            id
            mediaId
            progress
            status
            updatedAt
          }
        }
        """
        data = self._request(
            mutation,
            {"mediaId": int(media_id), "progress": int(progress), "status": status},
        )
        entry = data.get("SaveMediaListEntry")
        if not entry:
            raise AniListClientError("AniList mutation did not return list entry.")
        return entry

    def get_media_list_entry(self, media_id: int) -> Optional[Dict[str, Any]]:
        """Entree de liste utilisateur pour ce media, ou None si absent de la liste."""
        if not self.viewer_id:
            try:
                prof = self.get_viewer_profile()
                self.viewer_id = int(prof.get("id", 0)) or None
            except Exception:
                pass

        query = """
        query ($mediaId: Int!, $userId: Int) {
          MediaList(mediaId: $mediaId, userId: $userId, type: ANIME) {
            progress
            status
          }
        }
        """
        try:
            data = self._request(
                query,
                {"mediaId": int(media_id), "userId": self.viewer_id},
            )
        except AniListClientError as exc:
            if "Not Found" in str(exc) or "404" in str(exc):
                return None
            raise
        media_list = data.get("MediaList")
        if not media_list:
            return None
        return {
            "progress": int(media_list.get("progress") or 0),
            "status": str(media_list.get("status") or ""),
        }

    def get_entry_progress(self, media_id: int) -> int:
        entry = self.get_media_list_entry(media_id)
        if not entry:
            return 0
        return int(entry.get("progress") or 0)

    def rollback_to_progress(self, media_id: int, progress: int) -> Dict[str, Any]:
        # Rollback keeps status as CURRENT so user can resume naturally.
        return self.save_progress(media_id=media_id, progress=progress, status="CURRENT")
