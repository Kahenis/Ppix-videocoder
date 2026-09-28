"""Plex authentication and library scanning."""
from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from plexapi.myplex import MyPlexPinLogin, MyPlexAccount
from plexapi.server import PlexServer
from plexapi.exceptions import Unauthorized, NotFound

from .config import CLIENT_IDENTIFIER, NON_OPTIMAL_CODECS, SUPPORTED_CODECS, is_codec_acceptable


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
    file_path: str = ""
    file_size: int = 0
    duration_ms: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    container: str = ""
    width: int = 0
    height: int = 0
    bitrate: int = 0
    video_profile: str = ""
    recommended_codec: str = "hevc"
    part_id: str = ""
    media_id: str = ""

    @property
    def display_title(self) -> str:
        if self.library_type == "show" and self.series_title:
            ep = f"S{self.season_number:02d}E{self.episode_number:02d}" if self.episode_number else ""
            return f"{self.series_title} – {self.season_title} {ep} – {self.title}".strip(" –")
        return self.title

    @property
    def size_gb(self) -> float:
        return self.file_size / (1024 ** 3) if self.file_size else 0.0

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


class PlexClient:
    def __init__(self):
        self.account: Optional[MyPlexAccount] = None
        self.server: Optional[PlexServer] = None
        self.pin_login: Optional[MyPlexPinLogin] = None
        self.token: str = ""
        self.server_url: str = ""
        self.server_name: str = ""

    # ------------------------------------------------------------------
    # Authentication (PIN)
    # ------------------------------------------------------------------
    def start_pin_login(self) -> str:
        """Start PIN login and return the 4-character PIN."""
        headers = {
            "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
            "X-Plex-Product": "Ppix-Videocoder",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Device": "Windows",
            "X-Plex-Platform": "Windows",
        }
        self.pin_login = MyPlexPinLogin(headers=headers)
        # Trigger generation
        _ = self.pin_login.pin
        return self.pin_login.pin

    def open_link_and_copy(self, pin: str) -> None:
        """Open plex.tv/link and try to put the PIN in clipboard."""
        url = "https://plex.tv/link"
        webbrowser.open(url)
        try:
            import pyperclip
            pyperclip.copy(pin)
        except Exception:
            # fallback: just open the page
            pass

    def wait_for_pin(self, timeout: int = 300, poll: float = 1.5) -> bool:
        """Poll until the PIN is claimed (auto, no user click). Returns True on success."""
        if not self.pin_login:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.pin_login.checkLogin():
                    self.token = self.pin_login.token
                    self.account = MyPlexAccount(token=self.token)
                    return True
            except Exception:
                pass
            if getattr(self.pin_login, "expired", False):
                return False
            time.sleep(poll)
        return False

    def connect_with_token(self, token: str, server_name: str = "") -> bool:
        """Connect using an existing token (main account only)."""
        try:
            self.account = MyPlexAccount(token=token)
            self.token = token
            resources = self.account.resources()
            # Prefer owned servers (main account)
            owned = [r for r in resources if r.owned and r.provides == "server"]
            if not owned:
                owned = [r for r in resources if r.provides == "server"]
            if not owned:
                return False

            target = None
            if server_name:
                for r in owned:
                    if r.name == server_name:
                        target = r
                        break
            if target is None:
                target = owned[0]

            self.server = target.connect()
            self.server_name = target.name
            self.server_url = self.server._baseurl
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    def list_servers(self) -> List[str]:
        if not self.account:
            return []
        return [r.name for r in self.account.resources() if r.owned and r.provides == "server"]

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def scan_libraries(
        self,
        preferred_codec: str = "hevc",
        min_keep_codec: str = "h264",
        progress_cb=None,
    ) -> List[VideoInfo]:
        """Scan libraries; progress_cb(done, total, message) optional."""
        if not self.server:
            return []

        results: List[VideoInfo] = []
        sections = [s for s in self.server.library.sections() if s.type in ("movie", "show")]
        if not sections:
            if progress_cb:
                progress_cb(1, 1, "Aucune bibliothèque")
            return results

        # Première passe : compter les éléments (films + séries)
        counts = []
        for section in sections:
            try:
                items = section.all()
            except Exception:
                items = []
            counts.append((section, items))

        total = sum(len(items) for _, items in counts) or 1
        done = 0
        if progress_cb:
            progress_cb(0, total, "Démarrage du scan…")

        for section, items in counts:
            for item in items:
                try:
                    self._extract_videos(
                        item, section, preferred_codec, min_keep_codec, results,
                    )
                except Exception:
                    pass
                done += 1
                if progress_cb and (done % 3 == 0 or done >= total):
                    progress_cb(done, total, f"{section.title} ({done}/{total})")

        if progress_cb:
            progress_cb(total, total, f"Terminé — {len(results)} fichier(s)")
        return results

    def _extract_videos(self, item, section, preferred_codec: str, min_keep_codec: str,
                        results: List[VideoInfo]):
        if section.type == "show":
            for episode in item.episodes():
                self._process_media_item(
                    episode, section, preferred_codec, min_keep_codec, results,
                    series_title=item.title,
                )
        else:
            self._process_media_item(item, section, preferred_codec, min_keep_codec, results)

    def _process_media_item(self, item, section, preferred_codec: str, min_keep_codec: str,
                            results: List[VideoInfo], series_title: str = ""):
        if not hasattr(item, "media") or not item.media:
            return

        for media in item.media:
            video_codec = (media.videoCodec or "").lower()

            # Filtre "codec minimum acceptable" : ne pas lister si déjà assez bon
            if is_codec_acceptable(video_codec, min_keep_codec):
                continue

            for part in media.parts:
                file_path = part.file or ""
                if not file_path:
                    continue
                season_title = ""
                season_number = 0
                episode_number = 0
                if hasattr(item, "seasonNumber"):
                    season_number = item.seasonNumber or 0
                    season_title = getattr(item, "parentTitle", "") or f"Season {season_number}"
                if hasattr(item, "index"):
                    episode_number = item.index or 0
                rec = preferred_codec if preferred_codec in SUPPORTED_CODECS else "hevc"
                if preferred_codec == "auto":
                    rec = "hevc"
                info = VideoInfo(
                    rating_key=str(item.ratingKey),
                    title=item.title,
                    library=section.title,
                    library_type=section.type,
                    series_title=series_title or getattr(item, "grandparentTitle", ""),
                    season_title=season_title,
                    season_number=season_number,
                    episode_number=episode_number,
                    file_path=file_path,
                    file_size=part.size or 0,
                    duration_ms=media.duration or 0,
                    video_codec=video_codec,
                    audio_codec=(media.audioCodec or "").lower(),
                    container=(media.container or "").lower(),
                    width=media.width or 0,
                    height=media.height or 0,
                    bitrate=media.bitrate or 0,
                    video_profile=media.videoProfile or "",
                    recommended_codec=rec,
                    part_id=str(part.id) if hasattr(part, "id") else "",
                    media_id=str(media.id) if hasattr(media, "id") else "",
                )
                results.append(info)

    def refresh_item(self, rating_key: str) -> None:
        """Force Plex to re-analyze the media file so codec/size update after replace.

        item.refresh() alone only refreshes agent metadata and often keeps the
        old videoCodec in the database. analyze + force refresh fixes that.
        """
        if not self.server or not rating_key:
            return
        rk = str(rating_key)
        # 1) Analyze media streams (re-probe the file on disk)
        try:
            self.server.query(f"/library/metadata/{rk}/analyze", method=self.server._session.put)
        except Exception:
            try:
                item = self.server.fetchItem(int(rk))
                if hasattr(item, "analyze"):
                    item.analyze()
            except Exception:
                pass
        # 2) Force metadata refresh
        try:
            self.server.query(
                f"/library/metadata/{rk}/refresh",
                method=self.server._session.put,
                params={"force": 1},
            )
        except Exception:
            try:
                item = self.server.fetchItem(int(rk))
                item.refresh()
            except Exception:
                pass
        # 3) Reload so subsequent API reads see new codec
        try:
            item = self.server.fetchItem(int(rk))
            item.reload()
        except Exception:
            pass

    def refresh_library(self, library_name: str) -> None:
        if not self.server:
            return
        try:
            section = self.server.library.section(library_name)
            section.update()
        except Exception:
            pass

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
        # IPv4
        parts = h.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
        # Short LAN hostname (no dots) e.g. Nazgul
        if "." not in h and h.replace("-", "").isalnum():
            return True
        return False

    def detect_path_mapping(self) -> Dict[str, str]:
        """Try to deduce plex_prefix + network_root from the connected server.

        Returns dict with keys: plex_prefix, network_root, local_ip, message, candidates.
        Prefers real LAN IPv4 over plex.direct relay hostnames.
        """
        out: Dict[str, str] = {
            "plex_prefix": "",
            "network_root": "",
            "local_ip": "",
            "message": "",
        }
        if not self.server:
            out["message"] = "Serveur non connecté"
            return out

        # --- Collect candidate hosts (prefer real IPv4) ---
        candidates: List[str] = []

        def add_host(h: str) -> None:
            h = (h or "").strip()
            if h and h not in candidates and self._is_usable_lan_host(h):
                candidates.append(h)

        try:
            base = (self.server_url or getattr(self.server, "_baseurl", "") or "").rstrip("/")
            if "://" in base:
                add_host(base.split("://", 1)[1].split("/")[0].split(":")[0])
        except Exception:
            pass

        # plexapi server connections (local LAN first)
        try:
            conns = list(getattr(self.server, "connections", []) or [])
            # Prefer local=True
            for prefer_local in (True, False):
                for c in conns:
                    local = getattr(c, "local", None)
                    if prefer_local and local is False:
                        continue
                    if not prefer_local and local is True:
                        continue
                    add_host(getattr(c, "address", "") or "")
                    # uri may be http://192.168.x.x:32400
                    uri = getattr(c, "uri", "") or ""
                    if "://" in uri:
                        add_host(uri.split("://", 1)[1].split("/")[0].split(":")[0])
        except Exception:
            pass

        # Prefer pure IPv4 over hostnames
        ipv4 = [h for h in candidates if h.replace(".", "").isdigit()]
        local_ip = ipv4[0] if ipv4 else (candidates[0] if candidates else "")
        out["local_ip"] = local_ip

        # --- Library locations (paths as seen by the Plex server) ---
        locations: List[str] = []
        try:
            for section in self.server.library.sections():
                if section.type not in ("movie", "show"):
                    continue
                for loc in getattr(section, "locations", []) or []:
                    if loc:
                        locations.append(str(loc))
        except Exception as e:
            out["message"] = f"Impossible de lire les bibliothèques: {e}"
            return out

        if not locations:
            out["message"] = "Aucun chemin de bibliothèque trouvé"
            return out

        # Common path prefix (posix style from server)
        norm = [p.replace("\\", "/").rstrip("/") for p in locations]
        prefix = norm[0]
        for p in norm[1:]:
            while prefix and not p.startswith(prefix):
                prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
            if not prefix:
                break
        out["plex_prefix"] = prefix

        # Map server path → Windows UNC share.
        # Synology/QNAP: /volume1/Pour-tous/MediaCenter → share = Pour-tous
        # (skip volume1/volume2/mnt/export); network_root = \\IP\Pour-tous
        # plex_prefix kept as /volume1/Pour-tous so rest maps under MediaCenter/...
        network_root = ""
        if local_ip and prefix:
            parts = [x for x in prefix.split("/") if x]
            skip = {"volume1", "volume2", "volume3", "volume4", "mnt", "export", "share", "media"}
            share_parts = list(parts)
            # Drop leading volume markers when something follows
            while len(share_parts) > 1 and share_parts[0].lower() in skip:
                share_parts = share_parts[1:]
            if share_parts:
                share_name = share_parts[0]
                network_root = f"\\\\{local_ip}\\{share_name}"
                # Align plex_prefix to the folder that maps to the share root
                # e.g. /volume1/Pour-tous  (not deeper MediaCenter) when share is Pour-tous
                if share_name in parts:
                    idx = parts.index(share_name)
                    # prefix up to and including share_name on the server
                    aligned = "/" + "/".join(parts[: idx + 1])
                    out["plex_prefix"] = aligned
        out["network_root"] = network_root

        if network_root and out["plex_prefix"]:
            out["message"] = (
                f"Proposition : {network_root}\n"
                f"(préfixe Plex : {out['plex_prefix']})\n"
                f"Vérifiez / corrigez si besoin (ex: \\\\Nazgul\\Pour-tous)."
            )
        elif out["plex_prefix"]:
            out["message"] = (
                f"Préfixe Plex trouvé ({out['plex_prefix']}) mais "
                "aucune IP LAN utilisable (évite les adresses *.plex.direct)."
            )
        else:
            out["message"] = "Récupération impossible"
        return out
