"""
Client HTTP pour l'API Sonauto.ai.
Gère l'authentification et la récupération des données.

Structure découverte via analyse du HTML Next.js :
  - CDN  : scdn.sonauto.ai
  - API  : sonauto.ai/api/...
  - Data : __NEXT_DATA__ embarqué dans le HTML (Next.js RSC)
"""
from __future__ import annotations
import re
import json
from typing import AsyncGenerator, Callable

import httpx

from .modeles import Son


CDN_BASE    = "https://scdn.sonauto.ai"
API_BASE    = "https://sonauto.ai"

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":       "https://sonauto.ai/",
    "Origin":        "https://sonauto.ai",
    "Accept":        "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
}

# Endpoints API testés dans l'ordre
ENDPOINTS_LIKES = [
    f"{API_BASE}/api/liked-songs",
    f"{API_BASE}/api/v1/liked-songs",
    f"{API_BASE}/api/songs/liked",
    f"{API_BASE}/api/library/liked",
    f"{API_BASE}/api/user/liked-songs",
    f"{API_BASE}/api/generations?liked=true&limit=200",
    f"{API_BASE}/api/my-songs?liked=true",
]

ENDPOINTS_GENERATION = [
    f"{API_BASE}/api/generations/{{id}}",
    f"{API_BASE}/api/v1/generations/{{id}}",
    f"{API_BASE}/api/songs/{{id}}",
]

# Patterns CDN audio découverts via analyse HTML
CDN_AUDIO_PATTERNS = [
    f"{CDN_BASE}/audio/{{id}}",
    f"{CDN_BASE}/audio/song-{{id}}",
    f"{CDN_BASE}/audio/genoninsert_{{id}}.mp3",
    f"{CDN_BASE}/generations/{{id}}/audio",
]


def _url_image_cdn(son_id: str) -> str:
    """Construit l'URL d'image CDN depuis l'ID de la génération."""
    return f"{CDN_BASE}/images/song-{son_id}?optimizer=image&width=256&quality=75"


def _normaliser_son(data: dict) -> Son:
    """Convertit un dict brut de l'API en objet Son."""
    gen_id = (
        data.get("id")
        or data.get("generation_id")
        or data.get("task_id")
        or "inconnu"
    )
    titre = (
        data.get("title")
        or data.get("name")
        or gen_id[:8]
    )
    # Chercher l'URL audio dans tous les champs possibles
    url_audio = (
        data.get("audio_url")
        or data.get("song_path")
        or data.get("mp3_url")
        or data.get("url")
        or data.get("audio_path")
        or data.get("file_url")
        or (data.get("song_paths") or [None])[0]
        or (data.get("audio_paths") or [None])[0]
        or ""
    )

    # Si l'URL est relative, compléter avec le CDN
    if url_audio and url_audio.startswith("/"):
        url_audio = f"{CDN_BASE}{url_audio}"

    return Son(
        id=gen_id,
        titre=titre,
        tags=str(data.get("tags") or data.get("style") or ""),
        paroles=str(data.get("lyrics") or data.get("prompt") or ""),
        url_audio=url_audio,
        url_editeur=f"{API_BASE}/editor/{gen_id}",
        donnees_brutes=data,
    )


def _extraire_liste_next_data(html: str) -> list[dict] | None:
    """
    Extrait les données des sons depuis le JSON __NEXT_DATA__ de Next.js.
    Next.js embarque les données RSC dans une balise <script id="__NEXT_DATA__">.
    """
    # Méthode 1 : balise script __NEXT_DATA__
    m = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*({.*?})\s*</script>',
        html, re.DOTALL
    )
    if m:
        try:
            payload = json.loads(m.group(1))
            # Chercher récursivement des listes de sons
            return _chercher_sons_dans_dict(payload)
        except json.JSONDecodeError:
            pass

    # Méthode 2 : variable JS window.__NEXT_DATA__
    m = re.search(r'window\.__NEXT_DATA__\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
    if m:
        try:
            payload = json.loads(m.group(1))
            return _chercher_sons_dans_dict(payload)
        except json.JSONDecodeError:
            pass

    # Méthode 3 : JSON inline dans les props de page
    for pattern in [
        r'"likedSongs"\s*:\s*(\[.*?\])',
        r'"liked_songs"\s*:\s*(\[.*?\])',
        r'"songs"\s*:\s*(\[.*?\])',
        r'"generations"\s*:\s*(\[.*?\])',
        r'"items"\s*:\s*(\[.*?\])',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                items = json.loads(m.group(1))
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    return items
            except json.JSONDecodeError:
                pass

    return None


def _chercher_sons_dans_dict(obj: dict | list, profondeur: int = 0) -> list[dict] | None:
    """Cherche récursivement une liste de sons dans un objet JSON."""
    if profondeur > 6:
        return None

    cles_cibles = {
        "likedSongs", "liked_songs", "songs", "generations",
        "items", "tracks", "data", "results", "myMusic"
    }

    if isinstance(obj, dict):
        for cle, val in obj.items():
            if cle in cles_cibles and isinstance(val, list) and val:
                if isinstance(val[0], dict) and (
                    "id" in val[0] or "generation_id" in val[0] or "audio_url" in val[0]
                ):
                    return val
        for val in obj.values():
            if isinstance(val, (dict, list)):
                res = _chercher_sons_dans_dict(val, profondeur + 1)
                if res:
                    return res

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                res = _chercher_sons_dans_dict(item, profondeur + 1)
                if res:
                    return res

    return None


class ClientSonauto:
    def __init__(self, token: str):
        self._token = token
        self._client = httpx.AsyncClient(
            headers={
                **HEADERS_BASE,
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            timeout=30,
            follow_redirects=True,
        )

    async def fermer(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.fermer()

    # ── Sons likés ────────────────────────────────────────────────────────────

    async def lister_sons_likes(self) -> list[Son]:
        """Tente plusieurs stratégies pour récupérer les sons likés."""

        # Stratégie 1 : endpoints API directs
        for endpoint in ENDPOINTS_LIKES:
            try:
                r = await self._client.get(endpoint, timeout=15)
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except Exception:
                        continue
                    raw_list = (
                        data if isinstance(data, list)
                        else next(
                            (
                                data[k] for k in
                                ("songs", "likedSongs", "liked_songs",
                                 "items", "generations", "data", "results")
                                if isinstance(data.get(k), list)
                            ),
                            None,
                        )
                    )
                    if raw_list:
                        return [_normaliser_son(s) for s in raw_list]
            except PermissionError:
                raise
            except Exception:
                pass

        # Stratégie 2 : scraping HTML + extraction __NEXT_DATA__
        return await self._lister_via_html()

    async def _lister_via_html(self) -> list[Son]:
        """Fallback : parse la page liked-songs pour extraire les données Next.js."""
        for url in [
            f"{API_BASE}/liked-songs",
            f"{API_BASE}/my-music",
            f"{API_BASE}/library",
        ]:
            try:
                r = await self._client.get(url, timeout=20,
                    headers={"Accept": "text/html,application/xhtml+xml"})
                if r.status_code != 200:
                    continue
                items = _extraire_liste_next_data(r.text)
                if items:
                    return [_normaliser_son(s) for s in items]
            except Exception:
                pass

        return []

    # ── Génération unique ─────────────────────────────────────────────────────

    async def obtenir_son(self, generation_id: str) -> Son | None:
        """Récupère les détails d'un son par son ID de génération."""
        for tmpl in ENDPOINTS_GENERATION:
            url = tmpl.format(id=generation_id)
            try:
                r = await self._client.get(url, timeout=15)
                if r.status_code == 200:
                    return _normaliser_son(r.json())
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
            except PermissionError:
                raise
            except Exception:
                pass

        # Fallback : scraper la page éditeur
        return await self._obtenir_via_editeur(generation_id)

    async def _obtenir_via_editeur(self, gen_id: str) -> Son | None:
        """Scrape la page /editor/{id} pour extraire les données du son."""
        try:
            r = await self._client.get(
                f"{API_BASE}/editor/{gen_id}",
                timeout=20,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            if r.status_code != 200:
                return None
            items = _extraire_liste_next_data(r.text)
            if items:
                return _normaliser_son(items[0])
        except Exception:
            pass
        return None

    # ── Résolution URL audio via CDN ──────────────────────────────────────────

    async def resoudre_url_audio(self, son_id: str) -> str | None:
        """
        Tente de trouver l'URL audio directe sur le CDN scdn.sonauto.ai.
        Utilise les patterns d'URL découverts dans le HTML de la page.
        """
        for pattern in CDN_AUDIO_PATTERNS:
            url = pattern.format(id=son_id)
            try:
                r = await self._client.head(url, timeout=10)
                if r.status_code in (200, 206):
                    return url
            except Exception:
                pass
        return None

    # ── Téléchargement streamé ────────────────────────────────────────────────

    async def stream_audio(
        self,
        url: str,
        callback_progression: Callable[[int, int], None] | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Générateur asynchrone de chunks audio avec suivi de progression."""
        async with self._client.stream("GET", url, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            recu  = 0
            async for chunk in r.aiter_bytes(chunk_size=16_384):
                recu += len(chunk)
                if callback_progression:
                    callback_progression(recu, total)
                yield chunk
