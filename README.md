# Ppix-Videocoder

[![Build Windows EXE](https://github.com/Kahenis/Ppix-videocoder/actions/workflows/build-windows.yml/badge.svg)](https://github.com/Kahenis/Ppix-videocoder/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/Kahenis/Ppix-videocoder)](https://github.com/Kahenis/Ppix-videocoder/releases/latest)

Application **Windows** autonome pour optimiser les codecs vidéo de vos bibliothèques **Plex**.

> **Version publiable : 1.5.10**  
> Téléchargement : [Releases](https://github.com/Kahenis/Ppix-videocoder/releases/latest)

---

## Téléchargement

1. Ouvrez [la dernière release](https://github.com/Kahenis/Ppix-videocoder/releases/latest)
2. Téléchargez `Ppix-Videocoder-*-windows.zip`
3. Décompressez
4. Lancez `Ppix-Videocoder.exe`

**Rien d’autre à installer** : FFmpeg est embarqué dans l’archive.

Les réglages (token, chemins réseau, historique) sont conservés entre les versions dans :

`%USERPROFILE%\.ppix-videocoder\`

---

## Fonctionnalités

| Zone | Détail |
|------|--------|
| Connexion | Code PIN plex.tv/link ou token |
| Scan | Films + séries, pagination (grandes bibliothèques 7k+) |
| Codecs | H.265 (recommandé) ou H.264 — CPU / NVIDIA |
| Conteneurs | Conservation mkv→mkv, mp4→mp4 ; legacy → mkv |
| File | Cases à cocher → file ; progression sans clignotement |
| Plex | Analyse forcée après remplacement (codec à jour) |
| NAS | Racine réseau Windows + autodétection API |

---

## Utilisation rapide

1. **1. Connexion** → Se connecter via plex.tv/link  
2. **Paramètres** → Racine réseau Windows (`\\IP\Partage`) si besoin  
3. **2. Scanner** → Lancer le scan  
4. Cocher les fichiers à convertir (ajout automatique à la file)  
5. **3. Encoder** → Lancer l’encodage  

Astuce : activez le *mode essai* dans les paramètres pour une simulation sans écrire de fichiers.

---

## Avertissement

Développement personnel. Des bugs peuvent encore apparaître.  
Aucun engagement de responsabilité en cas de problème sur vos bibliothèques Plex — **faites des sauvegardes** et testez d’abord en mode essai.

---

## Développement

```text
Python 3.12 + CustomTkinter + plexapi + FFmpeg
Build : GitHub Actions → PyInstaller (Windows)
```

Déclencher un build / une release :

- **Tag** `v1.5.10` poussé sur `main` → build + release automatiques  
- Ou **Actions → Build Windows EXE → Run workflow** (case « Publish release »)

---

## Licence

Usage personnel libre.  
FFmpeg : LGPL/GPL selon la build.  
plexapi : BSD.
