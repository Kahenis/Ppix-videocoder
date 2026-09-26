# Ppix-Videocoder

Application Windows autonome pour optimiser les codecs vidéo de vos bibliothèques **Plex**.

- Connexion via code PIN (plex.tv/link)
- Scan des bibliothèques (Films + Séries groupées par série/saison)
- Détection des codecs non optimisés
- Réencodage H.265 (prioritaire) ou H.264 avec **FFmpeg inclus**
- File d’attente, barres de progression, arrêt possible
- Paramètres avancés (CRF, preset, NVENC/QSV/AMF, remplacement auto…)

**L’utilisateur n’a rien d’autre à télécharger** : l’EXE + FFmpeg sont fournis dans une seule archive.

## Téléchargement

Allez dans [Releases](https://github.com/Kahenis/Ppix-videocoder/releases) et téléchargez la dernière archive `Ppix-Videocoder-*-windows.zip`.

1. Décompressez
2. Lancez `Ppix-Videocoder.exe`
3. C’est tout (FFmpeg est déjà dedans)

## Build automatique (GitHub Actions)

À chaque tag `v*` (ex. `v1.0.0`) ou via *Actions → Build Windows EXE → Run workflow* :

1. Télécharge FFmpeg Windows essentials
2. Compile avec PyInstaller (dossier + FFmpeg embarqué)
3. Produit un zip prêt à distribuer
4. Crée une Release GitHub si un tag est poussé

### Déclencher un build manuellement

1. Onglet **Actions** du dépôt
2. **Build Windows EXE** → **Run workflow**
3. Téléchargez l’artifact `Ppix-Videocoder-windows`

### Créer une release officielle

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Développement local (Windows)

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
:: Placez ffmpeg.exe dans le dossier ffmpeg\
python main.py
```

## Licence

Usage personnel libre.  
FFmpeg : LGPL/GPL selon la build.  
plexapi : BSD.
