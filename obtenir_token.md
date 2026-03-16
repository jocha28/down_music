# Comment obtenir votre token Sonauto.ai

## Étapes (méthode Chrome/Firefox)

1. Ouvrir **https://sonauto.ai** et se connecter
2. Appuyer sur **F12** pour ouvrir les DevTools
3. Aller dans l'onglet **Réseau** (Network)
4. Actualiser la page (**F5**)
5. Dans la barre de filtre, taper : `liked` ou `api`
6. Cliquer sur une requête vers `sonauto.ai/api/...`
7. Dans le panneau de droite → onglet **En-têtes** (Headers)
8. Chercher **Authorization: Bearer eyJ...**
9. Copier tout ce qui est après `Bearer `

## Utilisation

```bash
# Première utilisation (saisie interactive du token)
python3 download_music.py --lister

# Ou passer le token directement
python3 download_music.py --lister --token "eyJhbGc..."

# Réinitialiser le token
python3 download_music.py --reset-token
```

## Exemples de commandes

```bash
# Lister tous les sons likés
python3 download_music.py --lister

# Télécharger tous les sons likés
python3 download_music.py --tous

# Télécharger un seul son via son URL
python3 download_music.py --url "https://sonauto.ai/editor/3cb9f8c3.../b75f3db5..."

# Télécharger par ID de génération
python3 download_music.py --id "b75f3db5-3062-40fd-86ed-9a5556b5c9b3"

# Choisir le dossier de sortie
python3 download_music.py --tous --sortie ~/Musique/Sonauto
```
