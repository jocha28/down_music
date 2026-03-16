"""
Routes FastAPI — toutes les routes de l'application.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, UploadFile, Form
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
# IMPORTANT : les routes statiques (/tous, /url) doivent être déclarées
# AVANT la route dynamique (/{generation_id}) sinon FastAPI les intercepte.

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


# ── Artistes ─────────────────────────────────────────────────────────────────

def _lire_tags_mp3(chemin: Path) -> dict:
    """Lit les tags ID3 d'un MP3 et retourne un dict normalisé."""
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.mp3 import MP3 as MutagenMP3
    result = {
        "nom": chemin.name,
        "titre": chemin.stem,
        "artiste": "",
        "album": "",
        "annee": "",
        "genre": "",
        "piste": "",
        "duree": 0,
        "cover": False,
        "lyrics": chemin.with_suffix(".lrc").exists(),
    }
    try:
        audio = MutagenMP3(str(chemin))
        result["duree"] = int(audio.info.length)
    except Exception:
        pass
    try:
        tags = ID3(str(chemin))
        def _t(k):
            f = tags.get(k)
            return str(f.text[0]) if f and hasattr(f, "text") and f.text else ""
        result["titre"]   = _t("TIT2") or chemin.stem
        result["artiste"] = _t("TPE1")
        result["album"]   = _t("TALB")
        result["annee"]   = _t("TDRC")
        result["genre"]   = _t("TCON")
        result["piste"]   = _t("TRCK").split("/")[0]
        result["cover"]   = any(k.startswith("APIC") for k in tags)
    except Exception:
        pass
    return result


@router.get(
    "/artistes",
    tags=["Artistes"],
    summary="Lister tous les artistes avec leurs statistiques",
)
async def lister_artistes():
    if not DOSSIER_MUSIQUES.exists():
        return []
    from collections import defaultdict
    artistes: dict = defaultdict(lambda: {"sons": 0, "albums": set(), "cover_exemple": None})
    for mp3 in sorted(DOSSIER_MUSIQUES.glob("*.mp3")):
        info = _lire_tags_mp3(mp3)
        nom = info["artiste"] or "Inconnu"
        artistes[nom]["sons"] += 1
        if info["album"]:
            artistes[nom]["albums"].add(info["album"])
        if info["cover"] and artistes[nom]["cover_exemple"] is None:
            artistes[nom]["cover_exemple"] = mp3.name
    return [
        {
            "nom":           nom,
            "nb_sons":       data["sons"],
            "nb_albums":     len(data["albums"]),
            "cover_exemple": data["cover_exemple"],
        }
        for nom, data in sorted(artistes.items(), key=lambda x: -x[1]["sons"])
    ]


@router.get(
    "/artistes/{nom}/profil",
    tags=["Artistes"],
    summary="Récupérer tous les sons et albums d'un artiste",
)
async def profil_artiste(nom: str):
    if not DOSSIER_MUSIQUES.exists():
        raise HTTPException(status_code=404, detail="Dossier musiques introuvable")
    from collections import defaultdict
    sons = []
    albums: dict = defaultdict(lambda: {"titre": "", "annee": "", "pistes": [], "cover": None})

    for mp3 in sorted(DOSSIER_MUSIQUES.glob("*.mp3")):
        info = _lire_tags_mp3(mp3)
        artiste_fichier = info["artiste"] or "Inconnu"
        if artiste_fichier.lower() != nom.lower():
            continue
        info["cover_url"] = f"/api/fichiers/{mp3.name}/cover" if info["cover"] else None
        info["audio_url"] = f"/api/fichiers/{mp3.name}"
        sons.append(info)
        if info["album"]:
            alb = albums[info["album"]]
            alb["titre"] = info["album"]
            alb["annee"] = info["annee"]
            piste_num = int(info["piste"]) if info["piste"].isdigit() else 999
            alb["pistes"].append({"nom": mp3.name, "titre": info["titre"], "piste": piste_num})
            if alb["cover"] is None and info["cover"]:
                alb["cover"] = f"/api/fichiers/{mp3.name}/cover"

    # Trier les sons : piste si disponible, sinon titre
    sons.sort(key=lambda s: (s["album"], int(s["piste"]) if s["piste"].isdigit() else 999, s["titre"]))

    # Trier les pistes dans chaque album
    albums_list = []
    for alb in albums.values():
        alb["pistes"].sort(key=lambda p: p["piste"])
        albums_list.append(alb)
    albums_list.sort(key=lambda a: a["annee"] or "0000", reverse=True)

    return {
        "nom":    nom,
        "sons":   sons,
        "albums": albums_list,
    }


# ── Artistes ─────────────────────────────────────────────────────────────────

def _lire_tags_mp3(chemin: Path) -> dict:
    """Lit les tags ID3 d'un MP3 et retourne un dict normalisé."""
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.mp3 import MP3 as MutagenMP3
    result = {
        "nom": chemin.name, "titre": chemin.stem,
        "artiste": "", "album": "", "annee": "", "genre": "",
        "piste": "", "duree": 0, "cover": False,
        "lyrics": chemin.with_suffix(".lrc").exists(),
    }
    try:
        result["duree"] = int(MutagenMP3(str(chemin)).info.length)
    except Exception:
        pass
    try:
        tags = ID3(str(chemin))
        def _t(k):
            f = tags.get(k)
            return str(f.text[0]) if f and hasattr(f, "text") and f.text else ""
        result["titre"]   = _t("TIT2") or chemin.stem
        result["artiste"] = _t("TPE1")
        result["album"]   = _t("TALB")
        result["annee"]   = _t("TDRC")
        result["genre"]   = _t("TCON")
        result["piste"]   = _t("TRCK").split("/")[0]
        result["cover"]   = any(k.startswith("APIC") for k in tags)
    except Exception:
        pass
    return result


@router.get("/artistes", tags=["Artistes"], summary="Lister tous les artistes avec statistiques")
async def lister_artistes():
    if not DOSSIER_MUSIQUES.exists():
        return []
    from collections import defaultdict
    artistes: dict = defaultdict(lambda: {"sons": 0, "albums": set(), "cover_exemple": None})
    for mp3 in sorted(DOSSIER_MUSIQUES.glob("*.mp3")):
        info = _lire_tags_mp3(mp3)
        nom = info["artiste"] or "Inconnu"
        artistes[nom]["sons"] += 1
        if info["album"]:
            artistes[nom]["albums"].add(info["album"])
        if info["cover"] and artistes[nom]["cover_exemple"] is None:
            artistes[nom]["cover_exemple"] = mp3.name
    return [
        {"nom": nom, "nb_sons": d["sons"], "nb_albums": len(d["albums"]), "cover_exemple": d["cover_exemple"]}
        for nom, d in sorted(artistes.items(), key=lambda x: -x[1]["sons"])
    ]


@router.get("/artistes/{nom}/profil", tags=["Artistes"], summary="Récupérer les sons et albums d'un artiste")
async def profil_artiste(nom: str):
    if not DOSSIER_MUSIQUES.exists():
        raise HTTPException(status_code=404, detail="Dossier musiques introuvable")
    from collections import defaultdict
    sons = []
    albums: dict = defaultdict(lambda: {"titre": "", "annee": "", "pistes": [], "cover": None})
    for mp3 in sorted(DOSSIER_MUSIQUES.glob("*.mp3")):
        info = _lire_tags_mp3(mp3)
        if (info["artiste"] or "Inconnu").lower() != nom.lower():
            continue
        info["cover_url"]  = f"/api/fichiers/{mp3.name}/cover" if info["cover"] else None
        info["audio_url"]  = f"/api/fichiers/{mp3.name}"
        sons.append(info)
        if info["album"]:
            alb = albums[info["album"]]
            alb["titre"] = info["album"]
            alb["annee"] = info["annee"]
            n = int(info["piste"]) if info["piste"].isdigit() else 999
            alb["pistes"].append({"nom": mp3.name, "titre": info["titre"], "piste": n})
            if alb["cover"] is None and info["cover"]:
                alb["cover"] = f"/api/fichiers/{mp3.name}/cover"
    sons.sort(key=lambda s: (s["album"], int(s["piste"]) if s["piste"].isdigit() else 999, s["titre"]))
    albums_list = sorted(albums.values(), key=lambda a: a["annee"] or "0000", reverse=True)
    for a in albums_list:
        a["pistes"].sort(key=lambda p: p["piste"])
    return {"nom": nom, "sons": sons, "albums": albums_list}


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


# ── Tags ID3 ──────────────────────────────────────────────────────────────────

@router.post(
    "/tags/synchroniser",
    tags=["Fichiers"],
    summary="Synchroniser genre/année depuis Sonauto pour tous les MP3 existants",
)
async def synchroniser_tags(request: Request):
    """
    Récupère tous les sons likés depuis Sonauto, fait correspondre
    les MP3 locaux par titre, et écrit genre (TCON) + année (TDRC)
    dans ceux qui n'ont pas encore ces tags.
    """
    import re as _re
    from mutagen.id3 import ID3, TCON, TDRC, ID3NoHeaderError, Encoding

    token = await _token_ou_rafraichi(request)
    async with ClientSonauto(token) as client:
        sons = await client.lister_sons_likes()

    if not sons:
        raise HTTPException(status_code=404, detail="Aucun son liké trouvé")

    if not DOSSIER_MUSIQUES.exists():
        raise HTTPException(status_code=404, detail="Dossier musiques introuvable")

    # Index titre → son (titre normalisé pour la comparaison)
    def _normaliser(s: str) -> str:
        return _re.sub(r'[^a-z0-9]', '', s.lower())

    index_sons = {_normaliser(s.titre): s for s in sons}

    resultats = {"mis_a_jour": [], "deja_tags": [], "non_trouve": [], "erreurs": []}

    # Pour chaque MP3 local
    mp3s = sorted(DOSSIER_MUSIQUES.glob("*.mp3"))
    for mp3 in mp3s:
        titre_fichier = mp3.stem  # ex: "Active"
        cle = _normaliser(titre_fichier)
        son = index_sons.get(cle)

        if not son:
            resultats["non_trouve"].append(mp3.name)
            continue

        try:
            try:
                tags = ID3(str(mp3))
            except ID3NoHeaderError:
                tags = ID3()

            frame_genre = tags.get("TCON")
            frame_annee = tags.get("TDRC")
            genre_actuel = str(frame_genre.text[0]) if frame_genre and hasattr(frame_genre, "text") and frame_genre.text else ""
            annee_actuelle = str(frame_annee.text[0]) if frame_annee and hasattr(frame_annee, "text") and frame_annee.text else ""

            if genre_actuel and annee_actuelle:
                resultats["deja_tags"].append(mp3.name)
                continue

            # Récupérer le genre depuis Sonauto
            track_params_id = son.donnees_brutes.get("track_params_id") or ""
            annee = (son.donnees_brutes.get("created_at") or "")[:4]

            async with ClientSonauto(token) as client:
                tags_son = await client.obtenir_tags(track_params_id)

            genre = ", ".join(tags_son) if tags_son else ""

            modifie = False
            if genre and not genre_actuel:
                tags["TCON"] = TCON(encoding=Encoding.UTF8, text=[genre])
                modifie = True
            if annee and not annee_actuelle:
                tags["TDRC"] = TDRC(encoding=Encoding.UTF8, text=[annee])
                modifie = True

            if modifie:
                tags.save(str(mp3))
                resultats["mis_a_jour"].append({"fichier": mp3.name, "genre": genre, "annee": annee})
            else:
                resultats["deja_tags"].append(mp3.name)

        except Exception as e:
            resultats["erreurs"].append({"fichier": mp3.name, "erreur": str(e)})

    return {
        "total_mp3": len(mp3s),
        "mis_a_jour": len(resultats["mis_a_jour"]),
        "deja_tags": len(resultats["deja_tags"]),
        "non_trouve": len(resultats["non_trouve"]),
        "erreurs": len(resultats["erreurs"]),
        "details": resultats,
    }


@router.get(
    "/fichiers/{nom_fichier}/cover",
    tags=["Fichiers"],
    summary="Retourner la cover art d'un fichier MP3",
)
async def obtenir_cover(nom_fichier: str):
    from mutagen.id3 import ID3, ID3NoHeaderError
    from fastapi.responses import Response

    chemin = DOSSIER_MUSIQUES / nom_fichier
    if not chemin.exists() or not chemin.is_file():
        raise HTTPException(status_code=404, detail="Fichier non trouvé")

    try:
        tags = ID3(str(chemin))
    except ID3NoHeaderError:
        raise HTTPException(status_code=404, detail="Pas de tags")

    for cle, frame in tags.items():
        if cle.startswith("APIC"):
            return Response(
                content=frame.data,
                media_type=frame.mime or "image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )

    raise HTTPException(status_code=404, detail="Pas de cover")


@router.get(
    "/fichiers/{nom_fichier}/tags",
    tags=["Fichiers"],
    summary="Lire les tags ID3 d'un fichier MP3",
)
async def lire_tags(nom_fichier: str):
    import base64 as _b64
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, ID3NoHeaderError

    chemin = DOSSIER_MUSIQUES / nom_fichier
    if not chemin.exists() or not chemin.is_file():
        raise HTTPException(status_code=404, detail="Fichier non trouvé")

    try:
        tags = ID3(str(chemin))
    except ID3NoHeaderError:
        tags = {}

    def _texte(tag):
        frame = tags.get(tag)
        if frame is None:
            return ""
        return str(frame.text[0]) if hasattr(frame, "text") and frame.text else ""

    # Piste : peut être "3" ou "3/12"
    trck_brut = _texte("TRCK")
    parties   = trck_brut.split("/") if trck_brut else ["", ""]
    piste          = parties[0] if len(parties) > 0 else ""
    total_pistes   = parties[1] if len(parties) > 1 else ""

    # Commentaire COMM (frame composite)
    commentaire = ""
    for cle, frame in tags.items():
        if cle.startswith("COMM"):
            commentaire = frame.text[0] if frame.text else ""
            break

    # Cover APIC
    cover_b64 = None
    for cle, frame in tags.items():
        if cle.startswith("APIC"):
            mime = frame.mime or "image/jpeg"
            data = _b64.b64encode(frame.data).decode()
            cover_b64 = f"data:{mime};base64,{data}"
            break

    return {
        "titre":        _texte("TIT2"),
        "artiste":      _texte("TPE1"),
        "album":        _texte("TALB"),
        "annee":        _texte("TDRC"),
        "genre":        _texte("TCON"),
        "piste":        piste,
        "total_pistes": total_pistes,
        "commentaire":  commentaire,
        "cover_base64": cover_b64,
    }


@router.post(
    "/fichiers/{nom_fichier}/tags",
    tags=["Fichiers"],
    summary="Écrire les tags ID3 d'un fichier MP3",
)
async def ecrire_tags(
    nom_fichier:  str,
    titre:        str | None = Form(None),
    artiste:      str | None = Form(None),
    album:        str | None = Form(None),
    annee:        str | None = Form(None),
    genre:        str | None = Form(None),
    piste:        str | None = Form(None),
    total_pistes: str | None = Form(None),
    commentaire:  str | None = Form(None),
    cover:        UploadFile | None = None,
):
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TIT2, TPE1, TALB, TDRC, TCON, TRCK, COMM, APIC,
        Encoding,
    )

    chemin = DOSSIER_MUSIQUES / nom_fichier
    if not chemin.exists() or not chemin.is_file():
        raise HTTPException(status_code=404, detail="Fichier non trouvé")

    try:
        tags = ID3(str(chemin))
    except ID3NoHeaderError:
        tags = ID3()

    def _set(frame_cls, tag_id, valeur, **kwargs):
        if valeur:
            tags[tag_id] = frame_cls(encoding=Encoding.UTF8, text=[valeur], **kwargs)

    _set(TIT2, "TIT2", titre)
    _set(TPE1, "TPE1", artiste)
    _set(TALB, "TALB", album)
    _set(TDRC, "TDRC", annee)
    _set(TCON, "TCON", genre)

    if piste:
        valeur_trck = f"{piste}/{total_pistes}" if total_pistes else piste
        tags["TRCK"] = TRCK(encoding=Encoding.UTF8, text=[valeur_trck])

    if commentaire:
        tags["COMM::fra"] = COMM(
            encoding=Encoding.UTF8,
            lang="fra",
            desc="",
            text=[commentaire],
        )

    if cover is not None and cover.filename:
        contenu = await cover.read()
        mime = cover.content_type or "image/jpeg"
        tags["APIC:"] = APIC(
            encoding=Encoding.UTF8,
            mime=mime,
            type=3,   # Front cover
            desc="Cover",
            data=contenu,
        )

    tags.save(str(chemin))
    return {"message": "Tags enregistrés avec succès"}
