"""
Client AniList GraphQL pour Plexani.

Gère l'authentification, la recherche d'anime, la mise à jour de la liste
(progression + statut) et le rollback. Toutes les erreurs API sont levées
sous forme d'AniListClientError.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"

# Limite officielle AniList : 90 requêtes par minute.
# On se laisse une marge de 5 pour absorber les éventuels décalages d'horloge.
_RATE_LIMIT = 85
_RATE_WINDOW = 60.0


class AniListClientError(Exception):
    """Erreur levee quand une operation AniList echoue."""


class AniListClient:
    """Encapsule les appels GraphQL AniList utilises par la synchronisation Plex."""

    def __init__(
        self,
        token: str,
        timeout: int = 20,
        viewer_id: Optional[int] = None,
        verbose: bool = False,
        logger: Optional[Any] = None,
    ) -> None:
        """Prepare une session HTTP authentifiee pour AniList.

        Args:
            token: Token OAuth AniList a envoyer en Bearer.
            timeout: Delai maximum des appels HTTP, en secondes.
            viewer_id: Identifiant AniList deja connu pour les requetes MediaList.
            verbose: Active les traces GraphQL detaillees.
            logger: Callable optionnel recevant les messages verbose.

        Raises:
            ValueError: Si aucun token AniList n'est fourni.
        """
        if not token:
            raise ValueError("AniList token is required.")
        self.token = token
        self.timeout = timeout
        self.viewer_id = int(viewer_id) if viewer_id else None
        self.verbose = verbose
        self._logger = logger  # callable(str) ou None — si fourni, reçoit les messages verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # Timestamps des appels récents pour le token bucket (fenêtre glissante 60s).
        self._req_times: List[float] = []

    def _vlog(self, label: str, data: Any = None) -> None:
        """Emet un log verbose AniList si le mode verbose est actif."""
        if not self.verbose:
            return
        if data is None:
            msg = f"[AniList] {label}"
        elif isinstance(data, (dict, list)):
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
            msg = f"[AniList] {label}\n{formatted}"
        else:
            msg = f"[AniList] {label} {data}"
        if callable(self._logger):
            self._logger(msg)
        else:
            print(msg)

    @staticmethod
    def status_for_progress_save(
        list_entry: Optional[Dict[str, Any]], new_progress: int
    ) -> str:
        """
        Statut envoye a SaveMediaListEntry lors d'une maj de progression.

        Lors d'un passage de « Plan to Watch » / « Pause » -> « Watching », le statut
        est promu automatiquement vers CURRENT sans popup.
        Args:
            list_entry: Entree AniList actuelle, ou None si l'anime est absent.
            new_progress: Progression cible, conservee pour de futures regles.

        Returns:
            Statut AniList a envoyer avec la mutation de sauvegarde.
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
        """Execute une requete GraphQL AniList et retourne le champ data."""
        # ── Token bucket : garde-fou contre le rate-limit AniList (90 req/min) ──
        now = time.monotonic()
        self._req_times = [t for t in self._req_times if now - t < _RATE_WINDOW]
        if len(self._req_times) >= _RATE_LIMIT:
            wait = _RATE_WINDOW - (now - self._req_times[0])
            if wait > 0:
                self._vlog(f"Rate-limit préventif : attente {wait:.1f}s")
                time.sleep(wait)
            self._req_times = []
        self._req_times.append(time.monotonic())

        self._vlog("─" * 60)
        self._vlog("GraphQL query:", query.strip())
        self._vlog("Variables", variables)
        payload = {"query": query, "variables": variables}
        try:
            response = self.session.post(
                ANILIST_GRAPHQL_URL,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AniListClientError(f"Network error while calling AniList: {exc}") from exc

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise AniListClientError(
                f"Rate limit AniList atteint — réessaie dans {retry_after}s"
            )

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

        result = data.get("data", {})
        self._vlog("Response", result)
        return result

    def verify_token(self) -> str:
        """Verifie que le token utilisateur est valide.

        Returns:
            Nom du Viewer AniList associe au token.

        Raises:
            AniListClientError: Si le Viewer est absent de la reponse.
        """
        query = "query { Viewer { name } }"
        data = self._request(query, {})
        viewer = data.get("Viewer")
        if not viewer:
            raise AniListClientError("Token AniList invalide ou expire (Viewer introuvable).")
        return str(viewer.get("name") or "")

    def get_viewer_profile(self) -> Dict[str, Any]:
        """Recupere le profil du Viewer AniList.

        Returns:
            Dictionnaire contenant id, name et avatar_url.

        Raises:
            AniListClientError: Si le Viewer est absent de la reponse.
        """
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
        Args:
            media_id: Identifiant AniList du media ANIME.

        Returns:
            Dictionnaire avec les cles media et list_entry.
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
    def _ordinal(n: int) -> str:
        """Retourne l'ordinal anglais utilise dans les recherches de saisons."""
        if 11 <= (n % 100) <= 13:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    @staticmethod
    def normalize_title(raw_title: str) -> str:
        """
        Nettoie les tags de release et la ponctuation pour ameliorer le matching.

        Args:
            raw_title: Titre brut provenant de Plex ou d'un nom de release.

        Returns:
            Titre simplifie pret a comparer ou chercher.

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
        """Recherche des candidats AniList pour un titre.

        Args:
            title: Titre Plex ou variante a chercher.
            per_page: Nombre maximum de resultats AniList a demander.

        Returns:
            Liste brute des medias retournes par AniList.
        """
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
              coverImage { medium }
            }
          }
        }
        """
        clean_title = self.normalize_title(title)
        data = self._request(query, {"search": clean_title, "perPage": per_page})
        media = data.get("Page", {}).get("media", [])
        results = media if isinstance(media, list) else []
        self._vlog(f"Candidats reçus pour '{clean_title}'")
        for item in results:
            self._vlog(
                f"  - id={item.get('id')} "
                f"romaji='{item.get('title', {}).get('romaji')}' "
                f"english='{item.get('title', {}).get('english')}'"
            )
        return results

    def _exact_match(self, candidates: List[Dict[str, Any]], cleaned: str) -> Optional[Tuple[int, str]]:
        """Retourne le premier candidat dont un titre normalise correspond exactement."""
        for item in candidates:
            # Compare le titre principal, les variantes AniList et les synonymes.
            names = [
                item.get("title", {}).get("romaji"),
                item.get("title", {}).get("english"),
                item.get("title", {}).get("native"),
                *(item.get("synonyms") or []),
            ]
            normalized_names = [self.normalize_title(n).casefold() for n in names if n]
            if cleaned in normalized_names:
                chosen_title = (
                    item.get("title", {}).get("english")
                    or item.get("title", {}).get("romaji")
                    or "Unknown"
                )
                return int(item["id"]), chosen_title
        return None

    def _follow_sequel_chain(self, media_id: int, depth: int) -> Optional[int]:
        """
        Remonte la chaîne de SEQUEL depuis media_id sur `depth` sauts TV/ONA.

        Les entrées dont le format est OAV, SPECIAL ou MOVIE sont ignorées
        et ne consomment pas de profondeur.
        Retourne le media_id atteint, ou None si la chaîne est trop courte.
        """
        query = """
        query ($id: Int!) {
          Media(id: $id, type: ANIME) {
            relations {
              edges {
                relationType
                node {
                  id
                  format
                  title { romaji }
                }
              }
            }
          }
        }
        """
        current_id = media_id
        for i in range(depth):
            data = self._request(query, {"id": current_id})
            edges = (
                data.get("Media", {})
                .get("relations", {})
                .get("edges", [])
            )
            sequel_edges = [e for e in edges if e.get("relationType") == "SEQUEL"]
            tv_ona_sequels = []
            for e in sequel_edges:
                node = e.get("node", {})
                fmt = node.get("format")
                if fmt in ("TV", "ONA"):
                    tv_ona_sequels.append(node)
                else:
                    self._vlog(f"  skip nœud {node.get('id')} (format={fmt}, non TV/ONA)")
            if not tv_ona_sequels:
                return None
            next_node = tv_ona_sequels[0]
            next_id = int(next_node["id"])
            self._vlog(f"  hop {i + 1} : {current_id} → {next_id} (format={next_node.get('format')})")
            current_id = next_id
        self._vlog(f"  ✓ SEQUEL chain résolue : media_id final = {current_id}")
        return current_id

    def find_best_anime_id(
        self, title: str, season_number: int = 1
    ) -> Optional[Tuple[int, str]]:
        """
        Cherche le meilleur match AniList pour un titre Plex.

        Stratégie pour season_number == 1 (comportement inchangé) :
        1. Cherche les 10 premiers résultats AniList pour le titre normalisé.
        2. Retourne le premier match exact (romaji / english / native / synonymes).
        3. Fallback : 1er résultat AniList (meilleur score search_match).

        Stratégie supplémentaire pour season_number >= 2 :
        Étape 1 — variantes de titre enrichies.
          Essaie dans l'ordre : "Title Season N", "Title Part N",
          "Title Nth Season", "Title Cour N", "Title N".
          Retourne immédiatement dès qu'un match exact est trouvé.
        Étape 2 — navigation par relations SEQUEL.
          Si aucune variante ne donne de match exact, trouve d'abord la S1
          puis remonte la chaîne de SEQUEL TV/ONA sur (season_number - 1) sauts.

        Retourne (media_id, titre_romaji) ou None si aucun résultat.
        """
        self._vlog(f"find_best_anime_id appelé : title='{title}', season_number={season_number}")
        clean_title = self.normalize_title(title)

        # --- Étape 1 : variantes de titre pour les saisons 2+ ---
        if season_number >= 2:
            self._vlog(f"Saison {season_number} détectée, essai des variantes de titre...")
            variants = [
                f"{clean_title} Season {season_number}",
                f"{clean_title} Part {season_number}",
                f"{clean_title} {self._ordinal(season_number)} Season",
                f"{clean_title} Cour {season_number}",
                f"{clean_title} {season_number}",
            ]
            for variant in variants:
                self._vlog(f"  → variante testée : '{variant}'")
                candidates = self.search_anime_candidates(title=variant, per_page=5)
                match = self._exact_match(candidates, self.normalize_title(variant).casefold())
                if match:
                    self._vlog(f"  ✓ match exact sur variante '{variant}' : id={match[0]}, titre={match[1]}")
                    return match
            self._vlog("Aucun match exact sur les variantes, passage à la SEQUEL chain...")

        # --- Recherche principale (titre original) ---
        candidates = self.search_anime_candidates(title=title, per_page=10)
        if not candidates:
            return None

        match = self._exact_match(candidates, clean_title.casefold())
        if match:
            if season_number <= 1:
                return match
            # --- Étape 2 : chaîne SEQUEL depuis la S1 ---
            self._vlog(f"SEQUEL chain : départ media_id={match[0]}, profondeur={season_number - 1}")
            sequel_id = self._follow_sequel_chain(match[0], depth=season_number - 1)
            if sequel_id is not None:
                return sequel_id, match[1]
            self._vlog("SEQUEL chain trop courte, fallback sur S1")
            return match

        # Fallback : premier résultat AniList (search_match score le plus élevé)
        first = candidates[0]
        chosen_title = (
            first.get("title", {}).get("english")
            or first.get("title", {}).get("romaji")
            or "Unknown"
        )
        first_result = int(first["id"]), chosen_title

        if season_number >= 2:
            self._vlog(f"SEQUEL chain : départ media_id={first_result[0]}, profondeur={season_number - 1}")
            sequel_id = self._follow_sequel_chain(first_result[0], depth=season_number - 1)
            if sequel_id is not None:
                return sequel_id, chosen_title
            self._vlog("SEQUEL chain trop courte, fallback sur S1")

        return first_result

    def save_progress(
        self, media_id: int, progress: int, status: str = "CURRENT"
    ) -> Dict[str, Any]:
        """Enregistre une progression AniList pour un media.

        Args:
            media_id: Identifiant AniList du media.
            progress: Episode cible a enregistrer.
            status: Statut MediaList a associer a la progression.

        Returns:
            Entree MediaList retournee par SaveMediaListEntry.

        Raises:
            AniListClientError: Si la mutation ne renvoie pas d'entree.
        """
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
        """Retourne la progression courante d'un media, ou 0 si absent."""
        entry = self.get_media_list_entry(media_id)
        if not entry:
            return 0
        return int(entry.get("progress") or 0)

    def rollback_to_progress(self, media_id: int, progress: int) -> Dict[str, Any]:
        """Ramene une entree AniList a une progression precedente."""
        # Le rollback conserve CURRENT pour permettre une reprise naturelle.
        return self.save_progress(media_id=media_id, progress=progress, status="CURRENT")
