"""Plex authentication and library scanning."""
from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass
from typing import Optional, List

from plexapi.myplex import MyPlexPinLogin, MyPlexAccount
from plexapi.server import PlexServer

from .config import CLIENT_IDENTIFIER, NON_OPTIMAL_CODECS, SUPPORTED_CODECS, is_codec_acceptable


@dataclass
class VideoInfo:
    rating_key: str
    title: str
    library: str
    library_type: str
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

    def start_pin_login(self) -> str:
        headers = {
            "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
            "X-Plex-Product": "Ppix-Videocoder",
            "X-Plex-Version": "1.1.0",
            "X-Plex-Device": "Windows",
            "X-Plex-Platform": "Windows",
        }
        self.pin_login = MyPlexPinLogin(headers=headers)
        _ = self.pin_login.pin
        return self.pin_login.pin

    def open_link_and_copy(self, pin: str) -> None:
        webbrowser.open("https://plex.tv/link")
        try:
            import pyperclip
            pyperclip.copy(pin)
        except Exception:
            pass

    def wait_for_pin(self, timeout: int = 300, poll: float = 1.5) -> bool:
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
        try:
            self.account = MyPlexAccount(token=token)
            self.token = token
            resources = self.account.resources()
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

    def scan_libraries(self, preferred_codec: str = "hevc", min_keep_codec: str = "h264", progress_cb=None) -> List[VideoInfo]:
        if not self.server:
            return []
        results: List[VideoInfo] = []
        sections = [s for s in self.server.library.sections() if s.type in ("movie", "show")]
        if not sections:
            if progress_cb:
                progress_cb(1, 1, "Aucune bibliothèque")
            return results
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
                    self._extract_videos(item, section, preferred_codec, min_keep_codec, results)
                except Exception:
                    pass
                done += 1
                if progress_cb and (done % 3 == 0 or done >= total):
                    progress_cb(done, total, f"{section.title} ({done}/{total})")
        if progress_cb:
            progress_cb(total, total, f"Terminé — {len(results)} fichier(s)")
        return results

    def _extract_videos(self, item, section, preferred_codec: str, min_keep_codec: str, results: List[VideoInfo]):
        if section.type == "show":
            for episode in item.episodes():
                self._process_media_item(episode, section, preferred_codec, min_keep_codec, results, series_title=item.title)
        else:
            self._process_media_item(item, section, preferred_codec, min_keep_codec, results)

    def _process_media_item(self, item, section, preferred_codec: str, min_keep_codec: str, results: List[VideoInfo], series_title: str = ""):
        if not hasattr(item, "media") or not item.media:
            return
        for media in item.media:
            video_codec = (media.videoCodec or "").lower()
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
                    rating_key=str(item.ratingKey), title=item.title, library=section.title,
                    library_type=section.type,
                    series_title=series_title or getattr(item, "grandparentTitle", ""),
                    season_title=season_title, season_number=season_number, episode_number=episode_number,
                    file_path=file_path, file_size=part.size or 0, duration_ms=media.duration or 0,
                    video_codec=video_codec, audio_codec=(media.audioCodec or "").lower(),
                    container=(media.container or "").lower(), width=media.width or 0, height=media.height or 0,
                    bitrate=media.bitrate or 0, video_profile=media.videoProfile or "",
                    recommended_codec=rec,
                    part_id=str(part.id) if hasattr(part, "id") else "",
                    media_id=str(media.id) if hasattr(media, "id") else "",
                )
                results.append(info)

    def refresh_item(self, rating_key: str) -> None:
        if not self.server or not rating_key:
            return
        rk = str(rating_key)
        try:
            self.server.query(f"/library/metadata/{rk}/analyze", method=self.server._session.put)
        except Exception:
            try:
                item = self.server.fetchItem(int(rk))
                if hasattr(item, "analyze"):
                    item.analyze()
            except Exception:
                pass
        try:
            self.server.query(f"/library/metadata/{rk}/refresh", method=self.server._session.put, params={"force": 1})
        except Exception:
            try:
                item = self.server.fetchItem(int(rk))
                item.refresh()
            except Exception:
                pass
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

    def detect_path_mapping(self) -> dict:
        out = {"plex_prefix": "", "network_root": "", "local_ip": "", "message": ""}
        if not self.server:
            out["message"] = "Serveur non connecté"
            return out
        local_ip = ""
        try:
            base = (self.server_url or getattr(self.server, "_baseurl", "") or "").rstrip("/")
            if "://" in base:
                host = base.split("://", 1)[1].split("/")[0].split(":")[0]
                if host and host not in ("127.0.0.1", "localhost"):
                    local_ip = host
        except Exception:
            pass
        out["local_ip"] = local_ip
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
        norm = [p.replace("\\", "/").rstrip("/") for p in locations]
        prefix = norm[0]
        for p in norm[1:]:
            while prefix and not p.startswith(prefix):
                prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
            if not prefix:
                break
        out["plex_prefix"] = prefix
        network_root = ""
        if local_ip and prefix:
            parts = [x for x in prefix.split("/") if x]
            if parts:
                network_root = f"\\\\{local_ip}\\{parts[0]}"
        out["network_root"] = network_root
        if network_root and prefix:
            out["message"] = f"Détecté : {network_root}  (préfixe Plex : {prefix})"
        elif prefix:
            out["message"] = f"Préfixe Plex trouvé ({prefix}) mais IP/partage non déterminés"
        else:
            out["message"] = "Récupération impossible"
        return out
