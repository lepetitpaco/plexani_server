# Plexani Server

Service auto-hébergé qui synchronise automatiquement ta progression Plex vers AniList.  
Quand tu regardes un épisode d'anime sur Plex, Plexani détecte la lecture et met à jour ta liste AniList sans intervention manuelle.

## Fonctionnement

1. Le backend Python surveille les sessions actives de ton serveur Plex via polling.
2. Dès qu'un épisode dépasse le seuil de visionnage configuré (par défaut 85 %), l'entrée AniList correspondante est mise à jour.
3. Le dashboard React affiche en temps réel la session en cours, les logs et l'historique des synchronisations.

## Stack

| Couche | Technologie |
|--------|-------------|
| Backend | Python 3.12, FastAPI, uvicorn |
| Frontend | React 18, TypeScript, Vite |
| Plex | plexapi |
| AniList | GraphQL API (OAuth 2.0) |
| Déploiement | Docker (multi-stage build) |

## Prérequis

- Docker & Docker Compose
- Un serveur Plex accessible depuis le conteneur
- Un compte AniList avec une application OAuth créée sur [anilist.co/settings/developer](https://anilist.co/settings/developer)

## Démarrage rapide

```bash
git clone <repo>
cd plexani_server
docker compose up -d
```

Le dashboard est accessible sur **http://localhost:8765**.

## Configuration

La configuration se fait entièrement depuis le dashboard web.

### 1. Connecter Plex

Clique sur **"Connexion Plex"** — un flux OAuth s'ouvre, autorise l'accès, le token est sauvegardé automatiquement.

### 2. Connecter AniList

Renseigne d'abord ton **Client ID** et **Client Secret** AniList (depuis les paramètres développeur), puis clique sur **"Connexion AniList"**.  
L'URL de callback à configurer dans l'app AniList : `http://localhost:8765/api/oauth/anilist/callback`

### 3. Choisir le serveur Plex

Une fois connecté, sélectionne le serveur Plex cible dans la liste déroulante.

### Paramètres avancés

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `poll_interval_seconds` | `10` | Fréquence de polling Plex (1–3600 s) |
| `sync_threshold_percent` | `85` | % de lecture à partir duquel la sync se déclenche |
| `sync_end_only` | `false` | Force le seuil à 97 % minimum (dernier segment uniquement) |
| `anime_auto_tracking_mode` | `video` | `video` = auto, `manual` = sync manuelle uniquement |
| `autostart_monitoring` | `true` | Lance le suivi automatiquement au démarrage du conteneur |

## Données persistantes

Les fichiers de configuration et l'historique sont stockés dans le volume Docker `plexani-data` (`/data` dans le conteneur) :

```
/data/
  config.json   — tokens et paramètres
  history.json  — historique des synchronisations (200 dernières actions)
```

## API

Le backend expose une API REST + WebSocket sur le port 8765.

### REST

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/status` | État du suivi et de la session en cours |
| `POST` | `/api/monitoring/start` | Lance le suivi |
| `POST` | `/api/monitoring/stop` | Arrête le suivi |
| `POST` | `/api/monitoring/sync` | Force la sync de l'épisode en cours |
| `GET` | `/api/config` | Lit la configuration (tokens masqués) |
| `POST` | `/api/config` | Met à jour la configuration |
| `GET` | `/api/history` | Historique des actions |
| `POST` | `/api/history/rollback` | Annule la dernière sync de l'épisode en cours |
| `GET` | `/api/logs` | Logs applicatifs |
| `POST` | `/api/oauth/plex/init` | Initie le flux OAuth Plex |
| `GET` | `/api/oauth/plex/poll` | Récupère le token Plex après autorisation |
| `GET` | `/api/oauth/anilist/url` | Génère l'URL d'autorisation AniList |
| `GET` | `/api/oauth/anilist/callback` | Callback OAuth AniList |
| `GET` | `/api/plex/servers` | Liste les serveurs Plex du compte |
| `GET` | `/api/anilist/viewer` | Profil AniList connecté |

### WebSocket

Connexion sur `ws://localhost:8765/ws` — le serveur pousse les événements suivants :

| Type | Contenu |
|------|---------|
| `status` | État du suivi et session en cours (toutes les 2 s) |
| `logs` | Tous les logs à la connexion initiale |
| `history_updated` | Notifie une mise à jour de l'historique |
| `config_updated` | Notifie un changement de configuration |

## Développement local

```bash
# Backend
cd backend
pip install -r requirements.txt
DATA_DIR=./data uvicorn main:app --reload --port 8765

# Frontend (autre terminal)
cd frontend
npm install
npm run dev   # proxy vers :8765 configuré dans vite.config.ts
```

## Build Docker manuel

```bash
docker build -t plexani-server .
docker run -p 8765:8765 -v plexani-data:/data plexani-server
```
