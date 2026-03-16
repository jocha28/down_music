#!/usr/bin/env python3.13
"""
Supprime le filigrane (petit logo bas-droite) de la cover art des fichiers MP3.
Le filigrane est détecté comme une zone très lumineuse/blanche dans le coin
bas-droite (~5% de la dimension de l'image).

Usage :
  python3 supprimer_filigrane.py                  # tous les MP3 du dossier musiques/
  python3 supprimer_filigrane.py "Active.mp3"     # un fichier précis
  python3 supprimer_filigrane.py image.jpg        # une image directement
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

def _supprimer_filigrane(img_rgb: np.ndarray) -> np.ndarray:
    """
    Détecte et supprime un filigrane dans le coin bas-droite.
    Stratégie :
      1. Extraire la zone bas-droite (8 % de chaque côté)
      2. Créer un masque des pixels très lumineux (blanc/clair)
      3. Dilater légèrement le masque pour couvrir les bords
      4. Inpainter la zone avec cv2.INPAINT_TELEA
    """
    h, w = img_rgb.shape[:2]
    marge = int(min(h, w) * 0.08)   # 8 % de la plus petite dimension

    # Zone bas-droite à analyser
    y0, x0 = h - marge, w - marge
    zone = img_rgb[y0:h, x0:w]

    # Masque : pixels dont la luminosité > 220 (quasi-blanc)
    gris_zone = cv2.cvtColor(zone, cv2.COLOR_RGB2GRAY)
    _, masque_zone = cv2.threshold(gris_zone, 220, 255, cv2.THRESH_BINARY)

    # Si aucun pixel très lumineux → pas de filigrane détecté
    if masque_zone.sum() == 0:
        return img_rgb

    # Construire le masque pleine image
    masque = np.zeros((h, w), dtype=np.uint8)
    masque[y0:h, x0:w] = masque_zone

    # Dilater pour couvrir les anti-crénelages
    noyau = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    masque = cv2.dilate(masque, noyau, iterations=2)

    # Inpainting
    img_bgr   = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    resultat  = cv2.inpaint(img_bgr, masque, inpaintRadius=4, flags=cv2.INPAINT_TELEA)
    return cv2.cvtColor(resultat, cv2.COLOR_BGR2RGB)


# ── Traitement d'un MP3 ──────────────────────────────────────────────────────

def traiter_mp3(chemin: Path) -> bool:
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
    img_nettoye = _supprimer_filigrane(img_arr)

    # Si aucun changement, inutile de réécrire
    if np.array_equal(img_arr, img_nettoye):
        print(f"  [OK]   {chemin.name} — aucun filigrane détecté")
        return False

    # Réencode en JPEG (qualité 95) et réécrit l'APIC
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

def traiter_image(chemin: Path) -> bool:
    """Traite un fichier image directement (JPG/PNG) et écrase le fichier."""
    try:
        img_pil = Image.open(chemin).convert("RGB")
    except Exception as e:
        print(f"  [ERR]  {chemin.name} — {e}")
        return False

    img_arr     = np.array(img_pil)
    img_nettoye = _supprimer_filigrane(img_arr)

    if np.array_equal(img_arr, img_nettoye):
        print(f"  [OK]   {chemin.name} — aucun filigrane détecté")
        return False

    Image.fromarray(img_nettoye).save(chemin, quality=95)
    print(f"  [DONE] {chemin.name} — filigrane supprimé ✓")
    return True


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if args:
        cibles = [Path(a) for a in args]
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
            traites += traiter_mp3(c)
        elif c.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            traites += traiter_image(c)
        else:
            print(f"  [SKIP] {c.name} — format non supporté")

    print(f"\nTerminé : {traites} filigrane(s) supprimé(s).")


if __name__ == "__main__":
    main()
