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
            while prefix and not p.startswith(prefix):
                prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
            if not prefix:
                break
        return prefix

    def detect_path_mapping(self) -> Dict[str, Any]:
        """Deduce plex_prefix + Windows network_root from the Plex API.

        The API only exposes paths *as seen by the Plex Media Server*
        (section.locations + media part.file). There is no official endpoint
        for the client PC's UNC mapping — we reconstruct it from:
          1. Library Location paths (GET /library/sections → Location@path)
          2. Server LAN IP (connections, not *.plex.direct)
          3. If locations are already UNC/drive letters, use them directly
          4. Otherwise build candidate UNC roots and probe which exist (Windows)

        Returns keys: plex_prefix, network_root, local_ip, locations, candidates, message.
        """
        out: Dict[str, Any] = {
            "plex_prefix": "",
            "network_root": "",
            "local_ip": "",
            "locations": [],
            "candidates": [],
            "message": "",
        }
        if not self.server:
            out["message"] = "Serveur non connecté"
            return out

        # --- 1) LAN host (prefer real IPv4, never plex.direct) ---
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
            for prefer_local in (True, False):
                for c in list(getattr(self.server, "connections", []) or []):
                    local = getattr(c, "local", None)
                    if prefer_local and local is False:
                        continue
                    if not prefer_local and local is True:
                        continue
                    add_host(getattr(c, "address", "") or "")
                    uri = getattr(c, "uri", "") or ""
                    if "://" in uri:
                        add_host(uri.split("://", 1)[1].split("/")[0].split(":")[0])
        except Exception:
            pass
        ipv4 = [h for h in hosts if all(p.isdigit() for p in h.split(".")) and h.count(".") == 3]
        local_ip = ipv4[0] if ipv4 else (hosts[0] if hosts else "")
        out["local_ip"] = local_ip

        # --- 2) Library locations from API (server-side paths) ---
        locations: List[str] = []
        try:
            for section in self.server.library.sections():
                if section.type not in ("movie", "show"):
                    continue
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

        # --- 3) Already Windows UNC / drive letter? ---
        win_locs = [
            loc for loc in locations
            if loc.startswith("\\\\") or (len(loc) >= 3 and loc[1] == ":" and loc[0].isalpha())
        ]
        if win_locs:
            sample = win_locs[0].replace("/", "\\")
            if sample.startswith("\\\\"):
                bits = [b for b in sample.split("\\") if b]
                if len(bits) >= 2:
                    out["network_root"] = f"\\\\{bits[0]}\\{bits[1]}"
                    out["plex_prefix"] = f"\\\\{bits[0]}\\{bits[1]}"
                else:
                    out["network_root"] = sample
                    out["plex_prefix"] = self._common_prefix(win_locs).replace("/", "\\")
            else:
                out["network_root"] = sample[:2]
                out["plex_prefix"] = self._common_prefix(win_locs).replace("/", "\\") or sample
            out["message"] = (
                f"Chemins Windows détectés dans l'API Plex.\n"
                f"Racine proposée : {out['network_root']}\n"
                f"Préfixe Plex : {out['plex_prefix']}"
            )
            return out

        # --- 4) Linux/NAS paths → reconstruct UNC candidates ---
        prefix = self._common_prefix(locations)
        out["plex_prefix"] = prefix
        if not local_ip:
            out["message"] = (
                f"Préfixe Plex API : {prefix}\n"
                "Aucune IP LAN utilisable (adresses *.plex.direct ignorées).\n"
                "Saisissez manuellement \\\\IP_NAS\\NomDuPartage"
            )
            return out

        skip = {
            "volume1", "volume2", "volume3", "volume4", "volume5",
            "mnt", "export", "share", "media", "data", "srv", "home",
        }
        share_names: List[str] = []
        for loc in locations:
            parts = [x for x in loc.replace("\\", "/").split("/") if x]
            segs = list(parts)
            while len(segs) > 1 and segs[0].lower() in skip:
                segs = segs[1:]
            for s in segs[:2]:
                if s and s not in share_names and s.lower() not in skip:
                    share_names.append(s)

        unc_candidates: List[str] = []
        for share in share_names:
            unc = f"\\\\{local_ip}\\{share}"
            if unc not in unc_candidates:
                unc_candidates.append(unc)
        out["candidates"] = unc_candidates

        # --- 5) Probe which UNC roots exist on this Windows PC ---
        import os
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

        loc_preview = "\n".join(f"  • {l}" for l in locations[:6])
        if existing:
            out["message"] = (
                f"Racine accessible trouvée : {chosen}\n"
                f"Préfixe Plex : {out['plex_prefix']}\n"
                f"Emplacements API :\n{loc_preview}"
            )
        elif chosen:
            out["message"] = (
                f"Proposition (non vérifiée) : {chosen}\n"
                f"Préfixe Plex : {out['plex_prefix']}\n"
                f"Emplacements API :\n{loc_preview}\n"
                "Corrigez si le partage Windows a un autre nom."
            )
        else:
            out["message"] = (
                f"Emplacements API :\n{loc_preview}\n"
                "Impossible de construire un chemin UNC. Saisie manuelle requise."
            )
        return out
