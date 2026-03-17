#!/usr/bin/env python3.13
"""
Supprime le filigrane d'une cover art de fichier MP3 ou d'une image.
Le filigrane est détecté comme une zone très lumineuse/blanche dans le coin
spécifié (~8% de la dimension de l'image).

Usage :
  python3 supprimer_filigrane.py                          # tous les MP3 du dossier musiques/
  python3 supprimer_filigrane.py "Active.mp3"             # un fichier précis
  python3 supprimer_filigrane.py image.jpg                # une image directement
  python3 supprimer_filigrane.py image.jpg --coin bg      # bas-gauche  (bg)
  python3 supprimer_filigrane.py image.jpg --coin bd      # bas-droite  (bd, défaut)
  python3 supprimer_filigrane.py image.jpg --coin hg      # haut-gauche (hg)
  python3 supprimer_filigrane.py image.jpg --coin hd      # haut-droite (hd)
  python3 supprimer_filigrane.py image.jpg --coin tous    # les 4 coins
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from mutagen.id3 import ID3, APIC, ID3NoHeaderError, Encoding

DOSSIER = Path("musiques")


# ── Suppression du filigrane ────────────────────────────────────────────────

def _zone_coin(h: int, w: int, coin: str, marge: int):
    """Retourne (y_debut, y_fin, x_debut, x_fin) pour le coin demandé."""
    if coin == 'bd':   return h - marge, h, w - marge, w
    if coin == 'bg':   return h - marge, h, 0, marge
    if coin == 'hd':   return 0, marge, w - marge, w
    if coin == 'hg':   return 0, marge, 0, marge
    raise ValueError(f"Coin inconnu : {coin}")


def _supprimer_filigrane(img_rgb: np.ndarray, coin: str = 'bd') -> np.ndarray:
    """
    Détecte et supprime un filigrane dans le coin indiqué.
    coin : 'bd' bas-droite (défaut), 'bg' bas-gauche, 'hd' haut-droite, 'hg' haut-gauche
    Stratégie :
      1. Extraire la zone du coin (8 % de chaque côté)
      2. Créer un masque des pixels très lumineux (blanc/clair > 220)
      3. Dilater légèrement le masque pour couvrir les bords
      4. Inpainter la zone avec cv2.INPAINT_TELEA
    """
    h, w = img_rgb.shape[:2]
    marge = int(min(h, w) * 0.08)

    coins = ['bd', 'bg', 'hd', 'hg'] if coin == 'tous' else [coin]

    masque = np.zeros((h, w), dtype=np.uint8)
    trouve = False

    for c in coins:
        y0, y1, x0, x1 = _zone_coin(h, w, c, marge)
        zone = img_rgb[y0:y1, x0:x1]
        gris_zone = cv2.cvtColor(zone, cv2.COLOR_RGB2GRAY)
        _, masque_zone = cv2.threshold(gris_zone, 220, 255, cv2.THRESH_BINARY)
        if masque_zone.sum() > 0:
            masque[y0:y1, x0:x1] = masque_zone
            trouve = True

    if not trouve:
        return img_rgb

    # Dilater pour couvrir les anti-crénelages
    noyau = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    masque = cv2.dilate(masque, noyau, iterations=2)

    # Inpainting
    img_bgr  = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    resultat = cv2.inpaint(img_bgr, masque, inpaintRadius=4, flags=cv2.INPAINT_TELEA)
    return cv2.cvtColor(resultat, cv2.COLOR_BGR2RGB)


# ── Traitement d'un MP3 ──────────────────────────────────────────────────────

def traiter_mp3(chemin: Path, coin: str = 'bd') -> bool:
    """Lit la cover APIC, supprime le filigrane, réécrit le tag."""
    try:
        tags = ID3(str(chemin))
    except ID3NoHeaderError:
        print(f"  [SKIP] {chemin.name} — pas de tags ID3")
        return False

    cle_apic = None
    for cle in tags:
        if cle.startswith("APIC"):
            cle_apic = cle
            break

    if cle_apic is None:
        print(f"  [SKIP] {chemin.name} — pas de cover")
        return False

    frame = tags[cle_apic]
    try:
        img_pil = Image.open(io.BytesIO(frame.data)).convert("RGB")
    except Exception as e:
        print(f"  [ERR]  {chemin.name} — image invalide : {e}")
        return False

    img_arr     = np.array(img_pil)
    img_nettoye = _supprimer_filigrane(img_arr, coin)

    if np.array_equal(img_arr, img_nettoye):
        print(f"  [OK]   {chemin.name} — aucun filigrane détecté")
        return False

    buf = io.BytesIO()
    Image.fromarray(img_nettoye).save(buf, format="JPEG", quality=95)
    buf.seek(0)

    tags[cle_apic] = APIC(
        encoding=Encoding.UTF8,
        mime="image/jpeg",
        type=3,
        desc=frame.desc,
        data=buf.read(),
    )
    tags.save(str(chemin))
    print(f"  [DONE] {chemin.name} — filigrane supprimé ✓")
    return True


# ── Traitement d'une image seule ─────────────────────────────────────────────

def traiter_image(chemin: Path, coin: str = 'bd') -> bool:
    """Traite un fichier image directement (JPG/PNG) et écrase le fichier."""
    try:
        img_pil = Image.open(chemin).convert("RGB")
    except Exception as e:
        print(f"  [ERR]  {chemin.name} — {e}")
        return False

    img_arr     = np.array(img_pil)
    img_nettoye = _supprimer_filigrane(img_arr, coin)

    if np.array_equal(img_arr, img_nettoye):
        print(f"  [OK]   {chemin.name} — aucun filigrane détecté")
        return False

    Image.fromarray(img_nettoye).save(chemin, quality=95)
    print(f"  [DONE] {chemin.name} — filigrane supprimé ✓")
    return True


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Extraire --coin <valeur>
    coin = 'bd'
    args_filtres = []
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1].lower()
            if coin not in ('bd', 'bg', 'hd', 'hg', 'tous'):
                print(f"Coin invalide : '{coin}'. Valeurs : bd, bg, hd, hg, tous")
                sys.exit(1)
            i += 2
        else:
            args_filtres.append(args[i])
            i += 1

    coins_labels = {'bd': 'bas-droite', 'bg': 'bas-gauche', 'hd': 'haut-droite', 'hg': 'haut-gauche', 'tous': 'tous les coins'}
    print(f"Coin ciblé : {coins_labels[coin]}")

    if args_filtres:
        cibles = [Path(a) for a in args_filtres]
    else:
        if not DOSSIER.exists():
            print(f"Dossier '{DOSSIER}' introuvable.")
            sys.exit(1)
        cibles = sorted(DOSSIER.glob("*.mp3"))
        print(f"{len(cibles)} fichier(s) MP3 trouvé(s) dans '{DOSSIER}/'")

    traites = 0
    for c in cibles:
        if not c.exists():
            print(f"  [ERR]  {c} — fichier introuvable")
            continue
        if c.suffix.lower() == ".mp3":
            traites += traiter_mp3(c, coin)
        elif c.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            traites += traiter_image(c, coin)
        else:
            print(f"  [SKIP] {c.name} — format non supporté")

    print(f"\nTerminé : {traites} filigrane(s) supprimé(s).")


if __name__ == "__main__":
    main()
