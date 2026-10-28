"""Plex authentication and library scanning."""
from __future__ import annotations

import os
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from plexapi.myplex import MyPlexPinLogin, MyPlexAccount
from plexapi.server import PlexServer

from .config import (
    APP_VERSION, CLIENT_IDENTIFIER, SUPPORTED_CODECS, normalize_codec, optimization_reason,
)
from .logger import log, log_exception

PAGE_SIZE = 400


class ScanCancelled(Exception):
    pass


@dataclass
class VideoInfo:
    """Represents a video file that may need re-encoding."""
    rating_key: str
    title: str
    library: str
    library_type: str          # movie | show
    series_title: str = ""
    season_title: str = ""
    season_number: int = 0
    episode_number: int = 0
    year: int = 0
    file_path: str = ""
    file_size: int = 0
    duration_ms: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    audio_channels: int = 0
    container: str = ""
    width: int = 0
    height: int = 0
    bitrate: int = 0           # kb/s
    video_profile: str = ""
    recommended_codec: str = "hevc"
    reason: str = ""
    audio_only: bool = False   # mode « Audio seule » : TrueHD → AC3, vidéo copiée
    part_id: str = ""
    media_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.rating_key}|{self.file_path}"

    @property
    def display_title(self) -> str:
        if self.library_type == "show" and self.series_title:
            ep = f"S{self.season_number:02d}E{self.episode_number:02d}" if self.episode_number else ""
            return " – ".join(x for x in (self.series_title, ep, self.title) if x)
        return f"{self.title} ({self.year})" if self.year else self.title

    @property
    def size_gb(self) -> float:
        return self.file_size / (1024 ** 3) if self.file_size else 0.0

    @property
    def size_label(self) -> str:
        if self.file_size >= 1024 ** 3:
            return f"{self.size_gb:.2f} Go"
        return f"{self.file_size / 1024 ** 2:.0f} Mo"

    @property
    def duration_str(self) -> str:
        if not self.duration_ms:
            return "–"
        s = self.duration_ms // 1000
        h, m = divmod(s // 60, 60)
        sec = s % 60
        if h:
            return f"{h}h{m:02d}m"
        return f"{m}m{sec:02d}s"

    @property
    def resolution_label(self) -> str:
        h = self.height or 0
        w = self.width or 0
        if w >= 3200 or h >= 1800:
            return "4K"
        if w >= 1700 or h >= 1000:
            return "1080p"
        if w >= 1200 or h >= 700:
            return "720p"
        return f"{h}p" if h else "?"

    def estimated_output_bytes(self, codec: str = "hevc") -> int:
        """Estimation grossière (pour le tri / la file) : HEVC ≈ 35-55 % d'un codec ancien."""
        if not self.file_size:
            return 0
        if self.audio_only:
            # TrueHD ≈ 3-5 Mb/s remplacé par 640 kb/s : ~4 Mb/s gagnés sur la durée
            saved = 4_000_000 / 8 * (self.duration_ms or 0) / 1000
            return max(int(self.file_size * 0.5), int(self.file_size - saved))
        vc = normalize_codec(self.video_codec)
        if vc == "h264":
            ratio = 0.55 if codec == "hevc" else 0.85
        else:
            ratio = 0.45 if codec == "hevc" else 0.65
        return int(self.file_size * ratio)


class PlexClient:
    def __init__(self):
        self.account: Optional[MyPlexAccount] = None
        self.server: Optional[PlexServer] = None
        self.pin_login: Optional[MyPlexPinLogin] = None
        self.token: str = ""
        self.server_url: str = ""
        self.server_name: str = ""
        self.last_error: str = ""
        self.scan_errors: int = 0

    # ------------------------------------------------------------------
    # Authentication (PIN)
    # ------------------------------------------------------------------
    @staticmethod
    def _headers() -> Dict[str, str]:
        return {
            "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
            "X-Plex-Product": "Ppix-Videocoder",
            "X-Plex-Version": APP_VERSION,
            "X-Plex-Device": "Windows",
            "X-Plex-Platform": "Windows",
        }

    def start_pin_login(self) -> str:
        """Start PIN login and return the 4-character PIN."""
        self.pin_login = MyPlexPinLogin(headers=self._headers())
        return self.pin_login.pin

    @staticmethod
    def open_link_and_copy(pin: str) -> None:
        """Open plex.tv/link and try to put the PIN in clipboard."""
        try:
            import pyperclip
            pyperclip.copy(pin)
        except Exception:
            pass
        webbrowser.open("https://plex.tv/link")

    def wait_for_pin(self, timeout: int = 600, poll: float = 1.5,
                     stop_event: Optional[threading.Event] = None) -> bool:
        """Poll until the PIN is claimed. Returns True on success."""
        if not self.pin_login:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            try:
                if self.pin_login.checkLogin():
                    self.token = self.pin_login.token
                    self.account = MyPlexAccount(token=self.token)
                    return True
            except Exception as e:
                log(f"PIN poll: {e}", level="DEBUG")
            time.sleep(poll)
        return False

    def connect_with_token(self, token: str, server_name: str = "") -> bool:
        """Connect using an existing token (owned servers first)."""
        self.last_error = ""
        try:
            self.account = MyPlexAccount(token=token)
            self.token = token
            resources = [r for r in self.account.resources() if "server" in (r.provides or "")]
            owned = [r for r in resources if r.owned] or resources
            if not owned:
                self.last_error = "Aucun serveur Plex associé à ce compte."
                return False
            target = next((r for r in owned if server_name and r.name == server_name), owned[0])
            self.server = target.connect(timeout=10)
            self.server_name = target.name
            self.server_url = self.server._baseurl
            log(f"Connecté à {self.server_name} via {self.server_url}")
            return True
        except Exception as e:
            self.last_error = str(e)
            log(f"Connexion Plex: {e}", level="ERROR")
            return False

    def list_servers(self) -> List[str]:
        if not self.account:
            return []
        try:
            res = [r for r in self.account.resources() if "server" in (r.provides or "")]
            owned = [r.name for r in res if r.owned]
            return owned or [r.name for r in res]
        except Exception as e:
            log(f"list_servers: {e}", level="ERROR")
            return []

    @property
    def machine_id(self) -> str:
        try:
            return getattr(self.server, "machineIdentifier", "") or ""
        except Exception:
            return ""

    def web_url_for(self, rating_key: str) -> str:
        """Lien Plex Web sans token dans l'URL."""
        mid = self.machine_id
        if not mid:
            return ""
        return (f"https://app.plex.tv/desktop/#!/server/{mid}/details"
                f"?key=%2Flibrary%2Fmetadata%2F{rating_key}")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def list_sections(self) -> List[Any]:
        if not self.server:
            return []
        return [s for s in self.server.library.sections() if s.type in ("movie", "show")]

    def scan_libraries(
        self,
        preferred_codec: str = "hevc",
        min_keep_codec: str = "h264",
        heavy_mbps: float = 0,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        stop_event: Optional[threading.Event] = None,
        sections: Optional[List[str]] = None,
        audio_only: bool = False,
    ) -> List[VideoInfo]:
        """Scan libraries by pages (films + épisodes en une requête par page).

        Une seule requête paginée « libtype=episode » par bibliothèque de séries
        au lieu d'un appel par série → beaucoup plus rapide sur les grosses
        bibliothèques.
        """
        if not self.server:
            return []
        self.scan_errors = 0
        results: List[VideoInfo] = []
        all_sections = self.list_sections()
        if sections:
            all_sections = [s for s in all_sections if s.title in sections]
        if not all_sections:
            if progress_cb:
                progress_cb(1, 1, "Aucune bibliothèque")
            return results

        plan = []
        for section in all_sections:
            libtype = "episode" if section.type == "show" else "movie"
            try:
                n = section.totalViewSize(libtype=libtype, includeCollections=False)
            except Exception:
                n = 0
            plan.append((section, libtype, n))
        total = sum(n for _, _, n in plan) or 1
        done = 0
        if progress_cb:
            progress_cb(0, total, "Démarrage du scan…")

        for section, libtype, n in plan:
            start = 0
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise ScanCancelled()
                try:
                    batch = section.search(libtype=libtype, container_start=start,
                                           container_size=PAGE_SIZE, maxresults=PAGE_SIZE)
                except Exception as e:
                    self.scan_errors += 1
                    log(f"Scan {section.title} @ {start}: {e}", level="ERROR")
                    break
                if not batch:
                    break
                for item in batch:
                    try:
                        self._process_media_item(item, section, preferred_codec, min_keep_codec,
                                                 heavy_mbps, results, audio_only)
                    except Exception:
                        self.scan_errors += 1
                        log_exception(f"Scan élément {getattr(item, 'title', '?')}")
                start += len(batch)
                done += len(batch)
                if progress_cb:
                    progress_cb(min(done, total), total,
                                f"{section.title} ({done}/{total}) — {len(results)} à optimiser")
                if len(batch) < PAGE_SIZE or (n and start >= n):
                    break

        if progress_cb:
            err = f" — {self.scan_errors} erreur(s), voir journal" if self.scan_errors else ""
            progress_cb(total, total, f"Terminé — {len(results)} fichier(s){err}")
        return results

    def _process_media_item(self, item, section, preferred_codec: str, min_keep_codec: str,
                            heavy_mbps: float, results: List[VideoInfo], audio_only: bool = False) -> None:
        medias = getattr(item, "media", None) or []
        is_show = section.type == "show"
        for media in medias:
            video_codec = (media.videoCodec or "").lower()
            if audio_only:
                # Mode « Audio seule » : seule la piste TrueHD compte, la vidéo est ignorée
                reason = "audio TrueHD" if (media.audioCodec or "").lower() == "truehd" else ""
            else:
                reason = optimization_reason(video_codec, media.bitrate or 0, media.width or 0,
                                             media.height or 0, min_keep_codec, heavy_mbps)
            if not reason:
                continue
            rec = preferred_codec if preferred_codec in SUPPORTED_CODECS else "hevc"
            if normalize_codec(video_codec) == "h264":
                rec = "hevc"  # réencoder un H.264 trop lourd en H.264 n'a pas de sens
            for part in media.parts or []:
                file_path = part.file or ""
                if not file_path:
                    continue
                info = VideoInfo(
                    rating_key=str(item.ratingKey),
                    title=item.title or "",
                    library=section.title,
                    library_type=section.type,
                    series_title=(getattr(item, "grandparentTitle", "") or "") if is_show else "",
                    season_title=(getattr(item, "parentTitle", "") or "") if is_show else "",
                    season_number=int(getattr(item, "parentIndex", 0) or 0) if is_show else 0,
                    episode_number=int(getattr(item, "index", 0) or 0) if is_show else 0,
                    year=int(getattr(item, "year", 0) or 0),
                    file_path=file_path,
                    file_size=part.size or 0,
                    duration_ms=part.duration or media.duration or 0,
                    video_codec=video_codec,
                    audio_codec=(media.audioCodec or "").lower(),
                    audio_channels=int(media.audioChannels or 0),
                    container=(part.container or media.container or "").lower(),
                    width=media.width or 0,
                    height=media.height or 0,
                    bitrate=media.bitrate or 0,
                    video_profile=media.videoProfile or "",
                    recommended_codec=rec,
                    reason=reason,
                    audio_only=audio_only,
                    part_id=str(getattr(part, "id", "") or ""),
                    media_id=str(getattr(media, "id", "") or ""),
                )
                results.append(info)

    # ------------------------------------------------------------------
    # Après remplacement
    # ------------------------------------------------------------------
    def refresh_after_replace(self, rating_key: str, library: str, plex_folder: str,
                              extension_changed: bool) -> str:
        """Met Plex à jour après remplacement.

        - Même nom de fichier : /analyze (re-lecture des flux) suffit.
        - Extension changée (film.avi → film.mkv) : l'ancien fichier n'existe
          plus → scan partiel du dossier pour que Plex trouve le nouveau.
        """
        if not self.server:
            return "Plex non connecté"
        done = []
        if extension_changed and library and plex_folder:
            try:
                self.server.library.section(library).update(path=plex_folder)
                done.append("scan du dossier")
            except Exception as e:
                log(f"Scan partiel {plex_folder}: {e}", level="WARN")
        if rating_key and not extension_changed:
            try:
                self.server.query(f"/library/metadata/{rating_key}/analyze", method=self.server._session.put)
                done.append("analyse")
            except Exception as e:
                log(f"Analyze {rating_key}: {e}", level="WARN")
        return ", ".join(done) or "aucune action"

    # ------------------------------------------------------------------
    # Affiches des vidéos personnelles
    # ------------------------------------------------------------------
    def is_personal_library(self, library: str) -> bool:
        from .posters import PERSONAL_AGENTS
        cache = self.__dict__.setdefault("_personal_cache", {})
        if library in cache:
            return cache[library]
        val = False
        try:
            if self.server and library:
                val = (getattr(self.server.library.section(library), "agent", "") or "") in PERSONAL_AGENTS
        except Exception as e:
            log(f"Agent de la bibliothèque {library}: {e}", level="DEBUG")
        cache[library] = val
        return val

    @staticmethod
    def _norm(path: str) -> str:
        return (path or "").replace("\\", "/").rstrip("/").lower()

    @staticmethod
    def _item_files(item) -> List[str]:
        out = []
        for m in getattr(item, "media", None) or []:
            for p in getattr(m, "parts", None) or []:
                if getattr(p, "file", ""):
                    out.append(p.file)
        return out

    def find_item_by_file(self, plex_path: str, library: str = "", hint_rating_key: str = ""):
        """Retrouve l'élément Plex d'un fichier (après remplacement, la clé peut changer)."""
        if not self.server or not plex_path:
            return None
        target = self._norm(plex_path)
        if hint_rating_key:
            try:
                item = self.server.fetchItem(int(hint_rating_key))
                if any(self._norm(f) == target for f in self._item_files(item)):
                    return item
            except Exception:
                pass
        try:
            sections = [self.server.library.section(library)] if library else self.list_sections()
        except Exception:
            sections = self.list_sections()
        for sec in sections:
            libtype = "episode" if sec.type == "show" else "movie"
            try:
                for item in sec.recentlyAdded(maxresults=200, libtype=libtype):
                    if any(self._norm(f) == target for f in self._item_files(item)):
                        return item
            except Exception as e:
                log(f"Recherche {plex_path} dans {sec.title}: {e}", level="DEBUG")
        return None

    def build_file_index(self, sections=None, stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Index chemin Plex → élément pour des bibliothèques (paginé)."""
        index: Dict[str, Any] = {}
        for sec in sections if sections is not None else self.list_sections():
            libtype = "episode" if sec.type == "show" else "movie"
            start = 0
            while True:
                if stop_event is not None and stop_event.is_set():
                    return index
                try:
                    batch = sec.search(libtype=libtype, container_start=start,
                                       container_size=PAGE_SIZE, maxresults=PAGE_SIZE)
                except Exception as e:
                    log(f"Index {sec.title}: {e}", level="WARN")
                    break
                for item in batch:
                    for f in self._item_files(item):
                        index[self._norm(f)] = item
                start += len(batch)
                if len(batch) < PAGE_SIZE:
                    break
        return index

    def lookup(self, index: Dict[str, Any], plex_path: str):
        return index.get(self._norm(plex_path))

    @staticmethod
    def upload_poster(item, jpg_path: str) -> bool:
        try:
            item.uploadPoster(filepath=jpg_path)
            try:
                item.lockPoster()
            except Exception:
                pass
            return True
        except Exception as e:
            log(f"Envoi de l'affiche à Plex impossible : {e}", level="WARN")
            return False

    # ------------------------------------------------------------------
    # Détection du chemin réseau
    # ------------------------------------------------------------------
    @staticmethod
    def _is_usable_lan_host(host: str) -> bool:
        """Reject loopback, plex.direct relay hostnames, keep real LAN IP/host."""
        if not host:
            return False
        h = host.strip().lower()
        if h in ("127.0.0.1", "localhost", "::1"):
            return False
        if "plex.direct" in h:
            return False
        parts = h.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
        if "." not in h and h.replace("-", "").isalnum():
            return True
        return False

    @staticmethod
    def _common_prefix(paths: List[str]) -> str:
        if not paths:
            return ""
        norm = [p.replace("\\", "/").rstrip("/") for p in paths if p]
        if not norm:
            return ""
        prefix = norm[0]
        for p in norm[1:]:
            while prefix and not (p == prefix or p.startswith(prefix + "/")):
                prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
            if not prefix:
                break
        return prefix

    def detect_path_mapping(self) -> Dict[str, Any]:
        """Deduce plex_prefix + Windows network_root from the Plex API.

        The API only exposes paths *as seen by the Plex Media Server*. We
        reconstruct the client PC's UNC mapping from library locations + the
        server LAN IP, then probe which UNC roots exist (Windows).
        """
        out: Dict[str, Any] = {
            "plex_prefix": "", "network_root": "", "local_ip": "",
            "locations": [], "candidates": [], "message": "",
        }
        if not self.server:
            out["message"] = "Serveur non connecté"
            return out

        hosts: List[str] = []

        def add_host(h: str) -> None:
            h = (h or "").strip()
            if h and h not in hosts and self._is_usable_lan_host(h):
                hosts.append(h)

        try:
            base = (self.server_url or getattr(self.server, "_baseurl", "") or "").rstrip("/")
            if "://" in base:
                add_host(base.split("://", 1)[1].split("/")[0].split(":")[0])
        except Exception:
            pass
        try:
            res = next((r for r in (self.account.resources() if self.account else [])
                        if r.clientIdentifier == self.machine_id), None)
            conns = list(getattr(res, "connections", []) or []) if res else []
            for prefer_local in (True, False):
                for c in conns:
                    if bool(getattr(c, "local", False)) != prefer_local:
                        continue
                    add_host(getattr(c, "address", "") or "")
        except Exception as e:
            log(f"detect_path_mapping connexions: {e}", level="DEBUG")
        ipv4 = [h for h in hosts if h.count(".") == 3 and all(p.isdigit() for p in h.split("."))]
        local_ip = ipv4[0] if ipv4 else (hosts[0] if hosts else "")
        out["local_ip"] = local_ip

        locations: List[str] = []
        try:
            for section in self.list_sections():
                for loc in getattr(section, "locations", []) or []:
                    if loc and str(loc) not in locations:
                        locations.append(str(loc))
        except Exception as e:
            out["message"] = f"Impossible de lire les bibliothèques: {e}"
            return out
        out["locations"] = locations
        if not locations:
            out["message"] = "Aucun chemin de bibliothèque dans l'API Plex"
            return out

        win_locs = [
            loc for loc in locations
            if loc.startswith("\\\\") or (len(loc) >= 3 and loc[1] == ":" and loc[0].isalpha())
        ]
        if win_locs:
            # Plex tourne sous Windows : les chemins sont déjà utilisables tels quels
            out["network_root"] = ""
            out["plex_prefix"] = ""
            out["message"] = ("Plex utilise déjà des chemins Windows "
                              f"({win_locs[0]}) : aucune racine réseau n'est nécessaire "
                              "si ce PC y a accès.")
            return out

        prefix = self._common_prefix(locations)
        out["plex_prefix"] = prefix
        if not local_ip:
            out["message"] = (
                f"Préfixe Plex API : {prefix}\n"
                "Aucune IP LAN utilisable (adresses *.plex.direct ignorées).\n"
                "Saisissez manuellement \\\\IP_NAS\\NomDuPartage"
            )
            return out

        skip = {"volume1", "volume2", "volume3", "volume4", "volume5",
                "mnt", "export", "share", "media", "data", "srv", "home",
                "share_data", "cachedev1_data", "ce_cachedev1_data", "pool", "tank"}
        share_names: List[str] = []
        for loc in locations:
            parts = [x for x in loc.replace("\\", "/").split("/") if x]
            segs = list(parts)
            while len(segs) > 1 and segs[0].lower() in skip:
                segs = segs[1:]
            for s in segs[:2]:
                if s and s not in share_names and s.lower() not in skip:
                    share_names.append(s)

        unc_candidates = []
        for share in share_names:
            unc = f"\\\\{local_ip}\\{share}"
            if unc not in unc_candidates:
                unc_candidates.append(unc)
        out["candidates"] = unc_candidates

        existing: List[str] = []
        if os.name == "nt":
            for unc in unc_candidates:
                try:
                    if os.path.isdir(unc):
                        existing.append(unc)
                except Exception:
                    pass

        chosen = existing[0] if existing else (unc_candidates[0] if unc_candidates else "")
        if chosen and prefix:
            share = chosen.rstrip("\\").split("\\")[-1]
            parts = [x for x in prefix.split("/") if x]
            if share in parts:
                idx = parts.index(share)
                out["plex_prefix"] = "/" + "/".join(parts[: idx + 1])
        out["network_root"] = chosen

        loc_preview = "\n".join(f"  • {loc}" for loc in locations[:6])
        if existing:
            out["message"] = (f"Racine accessible trouvée : {chosen}\n"
                              f"Préfixe Plex : {out['plex_prefix']}\nEmplacements API :\n{loc_preview}")
        elif chosen:
            out["message"] = (f"Proposition (non vérifiée) : {chosen}\n"
                              f"Préfixe Plex : {out['plex_prefix']}\nEmplacements API :\n{loc_preview}\n"
                              "Corrigez si le partage Windows a un autre nom.")
        else:
            out["message"] = (f"Emplacements API :\n{loc_preview}\n"
                              "Impossible de construire un chemin UNC. Saisie manuelle requise.")
        return out
