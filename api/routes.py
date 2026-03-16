"""
Routes FastAPI — toutes les routes de l'application.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .modeles import (
    ConfigToken, ReponseTelechargement,
    Son, ProgressionTelechargement, StatutTelecharge,
)
from .client_sonauto import ClientSonauto
from . import service_telechargement as svc

router = APIRouter()

DOSSIER_MUSIQUES = Path("musiques")


# ── Utilitaire token ──────────────────────────────────────────────────────────

def _token(request: Request) -> str:
    token = getattr(request.app.state, "token", None)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Token non configuré — POST /config/cookie d'abord"
        )
    return token


async def _token_ou_rafraichi(request: Request) -> str:
    """Retourne le token, en le renouvelant d'abord si nécessaire."""
    import base64, time, json as _json
    token = _token(request)
    try:
        parts = token.split(".")
        p = parts[1].replace("-", "+").replace("_", "/")
        p += "=" * (4 - len(p) % 4)
        payload = _json.loads(base64.b64decode(p))
        if payload.get("exp", 0) < time.time() + 30:
            from main import _rafraichir_token
            await _rafraichir_token(request.app)
            token = request.app.state.token
    except Exception:
        pass
    return token


# ── Santé ─────────────────────────────────────────────────────────────────────

@router.get("/sante", tags=["Système"], summary="Vérification de l'état de l'API")
async def sante(request: Request):
    return {
        "statut":       "en ligne",
        "token_defini": bool(getattr(request.app.state, "token", None)),
        "musiques_dir": str(DOSSIER_MUSIQUES.resolve()),
    }


# ── Configuration ─────────────────────────────────────────────────────────────

@router.post("/config/token", tags=["Configuration"], summary="Définir le token Sonauto.ai")
async def definir_token(body: ConfigToken, request: Request):
    token   = body.token.removeprefix("Bearer ").strip()
    refresh = body.refresh_token.strip()
    request.app.state.token         = token
    request.app.state.refresh_token = refresh

    # Persister dans .config_sonauto.json
    config_path = Path(".config_sonauto.json")
    config = {"token": token}
    if refresh:
        config["refresh_token"] = refresh
    config_path.write_text(json.dumps(config, indent=2))
    config_path.chmod(0o600)

    return {"message": "Token enregistré avec succès"}


@router.post(
    "/config/cookie",
    tags=["Configuration"],
    summary="Configurer via le cookie sb-db-auth-token (base64-...)",
)
async def configurer_via_cookie(body: dict, request: Request):
    """
    Accepte la valeur brute du cookie sb-db-auth-token.0 ou .1 (format base64-...)
    et extrait automatiquement access_token + refresh_token.

    Dans la console F12 de sonauto.ai :
    document.cookie.match(/sb-db-auth-token\\.0=([^;]+)/)?.[1]
    """
    import base64, re as _re, time
    valeur = body.get("cookie", "").strip()
    if not valeur:
        raise HTTPException(status_code=400, detail="Champ 'cookie' requis")

    # Décoder le base64
    b64 = valeur.removeprefix("base64-")
    while len(b64) % 4 != 0:
        b64 = b64[:-1]
    try:
        decoded = base64.b64decode(b64 + "==").decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(status_code=400, detail="Cookie base64 invalide")

    m_at = _re.search(r'"access_token":"([^"]+)"', decoded)
    m_rt = _re.search(r'"refresh_token":"([^"]+)"', decoded)
    access_token   = m_at.group(1) if m_at else ""
    refresh_token_ = m_rt.group(1) if m_rt else ""

    if not refresh_token_:
        raise HTTPException(status_code=400, detail="refresh_token introuvable dans le cookie")

    # Toujours rafraîchir via refresh_token pour obtenir un token propre
    # (le token du cookie peut avoir une signature invalide pour PostgREST)
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=15) as hclient:
            r = await hclient.post(
                "https://db.sonauto.ai/auth/v1/token?grant_type=refresh_token",
                headers={"Content-Type": "application/json",
                         "apikey": "sb_publishable_Ap6tbqA6iU0D4uuvjB5v0A_C0paifNR"},
                json={"refresh_token": refresh_token_},
            )
            if r.status_code == 200:
                data = r.json()
                access_token   = data["access_token"]
                refresh_token_ = data["refresh_token"]
            else:
                raise HTTPException(status_code=401, detail=f"Refresh échoué: {r.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur refresh: {e}")

    # Sauvegarder
    request.app.state.token         = access_token
    request.app.state.refresh_token = refresh_token_
    config = {"token": access_token, "refresh_token": refresh_token_}
    config_path = Path(".config_sonauto.json")
    config_path.write_text(json.dumps(config, indent=2))
    config_path.chmod(0o600)

    return {
        "message":        "Token configuré avec succès",
        "apercu":         f"{access_token[:12]}...",
        "refresh_token":  bool(refresh_token_),
    }


@router.get("/config/token", tags=["Configuration"], summary="Vérifier si un token est configuré")
async def verifier_token(request: Request):
    token = getattr(request.app.state, "token", None)
    return {
        "token_defini": bool(token),
        "apercu": (f"{token[:8]}..." if token else None),
    }


# ── Sons likés ────────────────────────────────────────────────────────────────

@router.get(
    "/sons",
    response_model=list[Son],
    tags=["Sons"],
    summary="Lister tous les sons likés",
)
async def lister_sons(request: Request) -> list[Son]:
    async with ClientSonauto(await _token_ou_rafraichi(request)) as client:
        try:
            return await client.lister_sons_likes()
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))


@router.get(
    "/sons/{generation_id}",
    response_model=Son,
    tags=["Sons"],
    summary="Obtenir les détails d'un son par son ID",
)
async def obtenir_son(generation_id: str, request: Request) -> Son:
    async with ClientSonauto(await _token_ou_rafraichi(request)) as client:
        son = await client.obtenir_son(generation_id)
    if not son:
        raise HTTPException(status_code=404, detail="Son non trouvé")
    return son


# ── Téléchargements ───────────────────────────────────────────────────────────

@router.post(
    "/telecharger/{generation_id}",
    response_model=ReponseTelechargement,
    tags=["Téléchargements"],
    summary="Télécharger un son par son ID",
)
async def telecharger_son(generation_id: str, request: Request):
    token = await _token_ou_rafraichi(request)
    async with ClientSonauto(token) as client:
        son = await client.obtenir_son(generation_id)
    if not son:
        raise HTTPException(status_code=404, detail="Son non trouvé")

    tache_id = await svc.lancer_telechargement(son, token)
    return ReponseTelechargement(
        tache_id=tache_id,
        message=f"Téléchargement lancé pour « {son.titre} »",
    )


@router.post(
    "/telecharger/url",
    response_model=ReponseTelechargement,
    tags=["Téléchargements"],
    summary="Télécharger via l'URL de l'éditeur Sonauto",
)
async def telecharger_par_url(body: dict, request: Request):
    import re
    url = body.get("url", "")
    m   = re.search(r'/editor/([^/]+)/([^/?]+)', url)
    if not m:
        raise HTTPException(status_code=400, detail="URL non reconnue")
    generation_id = m.group(2)
    return await telecharger_son(generation_id, request)


@router.post(
    "/telecharger/tous",
    response_model=list[ReponseTelechargement],
    tags=["Téléchargements"],
    summary="Télécharger tous les sons likés",
)
async def telecharger_tous(request: Request):
    token = await _token_ou_rafraichi(request)
    async with ClientSonauto(token) as client:
        sons = await client.lister_sons_likes()

    if not sons:
        raise HTTPException(status_code=404, detail="Aucun son liké trouvé")

    tache_ids = await svc.lancer_tous(sons, token)
    return [
        ReponseTelechargement(
            tache_id=tid,
            message=f"Téléchargement lancé pour « {son.titre} »"
        )
        for tid, son in zip(tache_ids, sons)
    ]


# ── Suivi des tâches ──────────────────────────────────────────────────────────

@router.get(
    "/taches",
    response_model=list[ProgressionTelechargement],
    tags=["Tâches"],
    summary="Lister toutes les tâches de téléchargement",
)
async def lister_taches():
    return list(svc.obtenir_taches().values())


@router.get(
    "/taches/{tache_id}",
    response_model=ProgressionTelechargement,
    tags=["Tâches"],
    summary="Obtenir la progression d'une tâche",
)
async def obtenir_tache(tache_id: str):
    tache = svc.obtenir_tache(tache_id)
    if not tache:
        raise HTTPException(status_code=404, detail="Tâche non trouvée")
    return tache


@router.get(
    "/taches/{tache_id}/flux",
    tags=["Tâches"],
    summary="Progression en temps réel (Server-Sent Events)",
)
async def flux_progression(tache_id: str):
    """
    Stream SSE : envoie la progression toutes les 500ms jusqu'à la fin.
    Connectez-vous avec : EventSource('/taches/{id}/flux')
    """
    async def generateur() -> AsyncGenerator[str, None]:
        while True:
            tache = svc.obtenir_tache(tache_id)
            if not tache:
                yield f"data: {json.dumps({'erreur': 'Tâche non trouvée'})}\n\n"
                break
            yield f"data: {tache.model_dump_json()}\n\n"
            if tache.statut in (
                StatutTelecharge.TERMINE,
                StatutTelecharge.ERREUR,
                StatutTelecharge.DEJA_PRESENT,
            ):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generateur(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Diagnostic ───────────────────────────────────────────────────────────────

@router.get(
    "/debug/api-brute",
    tags=["Debug"],
    summary="Tester un endpoint Sonauto brut (pour trouver le bon endpoint)",
)
async def debug_api_brute(url: str, request: Request):
    """
    Envoie une requête GET authentifiée à l'URL fournie et retourne la réponse brute.
    Utile pour explorer l'API et trouver les bons endpoints.

    Exemple : ?url=https://sonauto.ai/api/liked-songs
    """
    import httpx
    from .client_sonauto import HEADERS_BASE
    token = _token(request)
    async with httpx.AsyncClient(headers={
        **HEADERS_BASE,
        "Authorization": f"Bearer {token}",
    }, follow_redirects=True, timeout=15) as client:
        r = await client.get(url)
        try:
            corps = r.json()
        except Exception:
            corps = r.text[:2000]
    return {
        "statut":    r.status_code,
        "url":       str(r.url),
        "en_tetes":  dict(r.headers),
        "corps":     corps,
    }


@router.get(
    "/debug/resoudre-cdn/{son_id}",
    tags=["Debug"],
    summary="Tester la résolution d'URL audio CDN pour un ID donné",
)
async def debug_resoudre_cdn(son_id: str, request: Request):
    """Tente de trouver l'URL audio d'un son sur le CDN sonauto.ai."""
    from .client_sonauto import CDN_AUDIO_PATTERNS
    async with ClientSonauto(_token(request)) as client:
        url = await client.resoudre_url_audio(son_id)
    return {
        "son_id":      son_id,
        "url_trouvee": url,
        "patterns_testes": [p.format(id=son_id) for p in CDN_AUDIO_PATTERNS],
    }


# ── Fichiers téléchargés ──────────────────────────────────────────────────────

@router.get(
    "/fichiers",
    tags=["Fichiers"],
    summary="Lister les fichiers téléchargés",
)
async def lister_fichiers():
    if not DOSSIER_MUSIQUES.exists():
        return []
    return [
        {
            "nom":    f.name,
            "taille": f.stat().st_size,
            "url":    f"/fichiers/{f.name}",
            "lyrics": f.with_suffix(".lrc").exists(),
        }
        for f in sorted(DOSSIER_MUSIQUES.glob("*.mp3"))
    ]


@router.get(
    "/fichiers/{nom_fichier}",
    tags=["Fichiers"],
    summary="Télécharger un fichier MP3",
)
async def servir_fichier(nom_fichier: str):
    chemin = DOSSIER_MUSIQUES / nom_fichier
    if not chemin.exists() or not chemin.is_file():
        raise HTTPException(status_code=404, detail="Fichier non trouvé")
    return FileResponse(
        path=chemin,
        media_type="audio/mpeg",
        filename=nom_fichier,
    )


@router.get(
    "/fichiers/{nom_fichier}/lyrics",
    tags=["Fichiers"],
    summary="Obtenir les lyrics d'un fichier",
)
async def obtenir_lyrics(nom_fichier: str):
    base    = Path(nom_fichier).stem
    chemin  = DOSSIER_MUSIQUES / f"{base}.lrc"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Lyrics non disponibles")
    return {"lyrics": chemin.read_text(encoding="utf-8")}
