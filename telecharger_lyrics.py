#!/usr/bin/env python3
"""
Télécharge UNIQUEMENT les paroles synchronisées des sons likés sur Sonauto.ai.

Aucun MP3 n'est téléchargé. Chaque son reçoit son propre dossier :

    lyrics/
    ├── Empire Digital/
    │   └── paroles.lrc
    └── Active/
        └── paroles.lrc

Usage :
    python3 telecharger_lyrics.py                      # → dossier lyrics/
    python3 telecharger_lyrics.py --dossier mes_lyrics
    python3 telecharger_lyrics.py --force              # réécrit les fichiers existants
    python3 telecharger_lyrics.py --sans-sync          # inclut les sons sans horodatage
    python3 telecharger_lyrics.py --limite 10          # les 10 sons les plus récents

Le token est lu dans .config_sonauto.json (le même que le serveur web) et
renouvelé automatiquement s'il a expiré.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import httpx

from api.client_sonauto import ClientSonauto, SUPABASE_ANON_KEY, SUPABASE_URL
from api.modeles import Son
from api.service_telechargement import _generer_lrc

CONFIG = Path(".config_sonauto.json")
CONCURRENCE = 4          # requêtes lyrics simultanées — politesse envers Supabase


# ── Token ─────────────────────────────────────────────────────────────────────

def _charger_config() -> dict:
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def _est_expire(token: str) -> bool:
    """Décode le JWT et vérifie l'expiration (60 s de marge)."""
    try:
        p = token.split(".")[1].replace("-", "+").replace("_", "/")
        p += "=" * (4 - len(p) % 4)
        return json.loads(base64.b64decode(p)).get("exp", 0) < time.time() + 60
    except Exception:
        return False


async def _rafraichir(refresh_token: str) -> tuple[str, str] | None:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY},
            json={"refresh_token": refresh_token},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data["access_token"], data["refresh_token"]


async def obtenir_token() -> str:
    config = _charger_config()
    token   = config.get("token", "")
    refresh = config.get("refresh_token", "")

    if not token:
        sys.exit(
            "Aucun token dans .config_sonauto.json.\n"
            "Connecte-toi d'abord depuis l'interface web, ou colle le cookie via "
            "POST /api/config/cookie."
        )

    if _est_expire(token):
        print("  Token expiré — renouvellement…")
        if not refresh:
            sys.exit("Token expiré et aucun refresh_token disponible — reconnecte-toi.")
        nouveau = await _rafraichir(refresh)
        if not nouveau:
            sys.exit("Échec du renouvellement du token — reconnecte-toi.")
        token, refresh = nouveau
        CONFIG.write_text(json.dumps({"token": token, "refresh_token": refresh}, indent=2))
        CONFIG.chmod(0o600)
        print("  Token renouvelé ✓")

    return token


# ── Nommage des dossiers ──────────────────────────────────────────────────────

def _base_nom(titre: str) -> str:
    """Nom de dossier sûr à partir d'un titre."""
    return re.sub(r'[<>:"/\\|?*]', "", titre).strip(". ")[:100] or "sans_titre"


def _attribuer_dossiers(sons: list[Son]) -> list[str]:
    """
    Un dossier par son, de façon déterministe : relancer le script réutilise
    les mêmes dossiers au lieu d'en créer de nouveaux.

    Les titres uniques donnent le nom tel quel ; en cas de doublon de titre,
    TOUS les homonymes reçoivent leur identifiant court en suffixe.
    """
    from collections import Counter
    bases  = [_base_nom(s.titre) for s in sons]
    compte = Counter(bases)
    return [
        f"{base} ({son.id[:8]})" if compte[base] > 1 else base
        for son, base in zip(sons, bases)
    ]


# ── Traitement d'un son ───────────────────────────────────────────────────────

async def traiter(
    son: Son,
    dossier: Path,
    token: str,
    verrou: asyncio.Semaphore,
    force: bool,
    sans_sync: bool,
) -> tuple[str, str]:
    """Retourne (statut, détail). Statuts : écrit, existant, sans_sync, sans_paroles, erreur."""
    cible = dossier / "paroles.lrc"
    if cible.exists() and not force:
        return "existant", son.titre

    lyrics_id = son.donnees_brutes.get("lyrics_id") or ""
    if not lyrics_id:
        return "sans_paroles", son.titre

    async with verrou:
        try:
            async with ClientSonauto(token) as client:
                donnees = await client.obtenir_lyrics(lyrics_id)
        except Exception as e:
            return "erreur", f"{son.titre} — {e}"

    if not donnees:
        return "sans_paroles", son.titre

    aligned = donnees.get("aligned_lyrics") or []
    contenu = _generer_lrc(aligned) if aligned else ""

    if not contenu:
        if not sans_sync:
            return "sans_sync", son.titre
        contenu = donnees.get("lyrics") or ""
        if not contenu:
            return "sans_paroles", son.titre

    dossier.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    return "écrit", son.titre


# ── Point d'entrée ────────────────────────────────────────────────────────────

async def executer(args) -> int:
    racine = Path(args.dossier)
    token  = await obtenir_token()

    print("  Récupération des sons likés…")
    async with ClientSonauto(token) as client:
        try:
            sons = await client.lister_sons_likes()
        except PermissionError as e:
            sys.exit(f"Token refusé par Sonauto : {e}")

    if not sons:
        print("  Aucun son liké trouvé.")
        return 0

    if args.limite:
        sons = sons[: args.limite]
    print(f"  {len(sons)} son(s) à traiter → {racine.resolve()}\n")

    racine.mkdir(parents=True, exist_ok=True)
    cibles = [racine / nom for nom in _attribuer_dossiers(sons)]

    verrou    = asyncio.Semaphore(CONCURRENCE)
    resultats = await asyncio.gather(*(
        traiter(son, cible, token, verrou, args.force, args.sans_sync)
        for son, cible in zip(sons, cibles)
    ))

    bilan: dict[str, list[str]] = {}
    for statut, detail in resultats:
        bilan.setdefault(statut, []).append(detail)

    symboles = {
        "écrit":        "✓ écrits",
        "existant":     "· déjà présents",
        "sans_sync":    "~ sans horodatage (ignorés)",
        "sans_paroles": "∅ sans paroles",
        "erreur":       "✗ erreurs",
    }
    for statut, libelle in symboles.items():
        items = bilan.get(statut, [])
        if items:
            print(f"  {libelle} : {len(items)}")
            if statut in ("erreur", "sans_sync"):
                for item in items:
                    print(f"      {item}")

    if bilan.get("sans_sync") and not args.sans_sync:
        print("\n  Astuce : --sans-sync pour écrire quand même leurs paroles brutes.")

    return 1 if bilan.get("erreur") else 0


def main():
    p = argparse.ArgumentParser(
        description="Télécharge les paroles synchronisées (.lrc) des sons likés, un dossier par son.",
    )
    p.add_argument("--dossier", default="lyrics", help="dossier racine (défaut : lyrics)")
    p.add_argument("--force", action="store_true", help="réécrire les paroles.lrc existants")
    p.add_argument("--sans-sync", action="store_true",
                   help="écrire aussi les sons dont les paroles n'ont pas d'horodatage")
    p.add_argument("--limite", type=int, help="ne traiter que les N sons les plus récents")
    args = p.parse_args()

    try:
        sys.exit(asyncio.run(executer(args)))
    except KeyboardInterrupt:
        print("\n  Interrompu.")
        sys.exit(130)


if __name__ == "__main__":
    main()
