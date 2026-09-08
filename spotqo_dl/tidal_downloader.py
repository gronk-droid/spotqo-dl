"""
Tidal downloader backed by the tiddl library.

Handles two use cases:
  1. Direct Tidal URL downloads (tracks, albums, playlists).
  2. Spotify-fallback downloads: when Qobuz cannot find a track, search Tidal
     and download the best match, preserving Spotify ordering metadata.

Auth is read from tiddl's own auth file (~/.tiddl/auth.json) so users only
need to run `tiddl auth login` once.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from .formatter import TrackFormatter
from .utils import titles_match

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality mapping
# ---------------------------------------------------------------------------

# Maps the user-facing "--tidal-quality" values (TrackQuality enum strings)
# to tiddl's TRACK_QUALITY_LITERAL used internally by Downloader.__init__.
_TIDAL_QUALITY_MAP: Dict[str, str] = {
    "LOW": "low",
    "HIGH": "normal",
    "LOSSLESS": "high",
    "HI_RES_LOSSLESS": "max",
}


def _load_tiddl_api():
    """
    Build a (TidalClient, TidalAPI) pair from tiddl's saved auth data.

    Raises RuntimeError with a helpful message if tiddl is not authenticated.
    """
    try:
        from tiddl.core.api import TidalAPI
        from tiddl.core.api.client import TidalClient
        from tiddl.cli.const import APP_PATH
        from tiddl.cli.utils.auth.core import load_auth_data, save_auth_data
        from tiddl.core.auth import AuthAPI
    except ImportError as exc:
        raise RuntimeError(
            "tiddl is not installed. Run: uv sync"
        ) from exc

    auth_data = load_auth_data()

    if not auth_data.token:
        raise RuntimeError(
            "Tidal is not authenticated. Run: tiddl auth login"
        )
    if not auth_data.user_id or not auth_data.country_code:
        raise RuntimeError(
            "Tidal auth data is incomplete. Run: tiddl auth login"
        )

    refresh_token = auth_data.refresh_token
    auth_api = AuthAPI()

    def on_token_expiry() -> Optional[str]:
        if not refresh_token:
            return None
        try:
            resp = auth_api.refresh_token(refresh_token)
            auth_data.token = resp.access_token
            auth_data.expires_at = resp.expires_in + int(time())
            save_auth_data(auth_data)
            return resp.access_token
        except Exception as exc:
            logger.warning(f"Tidal token refresh failed: {exc}")
            return None

    client = TidalClient(
        token=auth_data.token,
        cache_name=APP_PATH / "api_cache",
        on_token_expiry=on_token_expiry,
    )
    api = TidalAPI(client, auth_data.user_id, auth_data.country_code)
    return api


class TidalDownloader:
    """
    Downloads tracks from Tidal using tiddl internals, then renames them
    according to spotqo-dl's TrackFormatter so output layout is identical
    to the Qobuz pathway.
    """

    def __init__(
        self,
        output_dir: str,
        quality: str = "LOSSLESS",
        track_format: str = "{artist} - {title}",
        folder_format: str = "{artist} - {album} ({year})",
        console: Optional[Console] = None,
    ) -> None:
        self.output_dir = output_dir
        self.quality = quality  # TrackQuality: LOW / HIGH / LOSSLESS / HI_RES_LOSSLESS
        self.track_format = track_format
        self.folder_format = folder_format
        self.console = console or Console()
        self.formatter = TrackFormatter()
        self._api: Any = None

        self.cache_dir = Path(output_dir) / ".spotqo_cache_tidal"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # spotify_id → final Path; mirrors QobuzDownloader._downloaded_file_paths
        # so the CLI can merge registries for combined m3u generation.
        self._downloaded_file_paths: Dict[str, Path] = {}

        # Every file finalized this session, for post-download loudness
        # normalization (captures direct-URL downloads with no spotify_id too).
        self._session_files: List[Path] = []

    # ------------------------------------------------------------------
    # Auth / API
    # ------------------------------------------------------------------

    @property
    def api(self):
        if self._api is None:
            self._api = _load_tiddl_api()
        return self._api

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download_tidal_url(self, url: str) -> None:
        """
        Download a track, album, or playlist from a direct Tidal URL.

        Metadata is taken from Tidal.  Files are renamed with TrackFormatter
        using the track's own Tidal metadata so the on-disk layout matches
        the Qobuz/Spotify pathway.
        """
        from tiddl.cli.utils.resource import TidalResource

        resource = TidalResource.from_string(url)
        logger.info(f"Tidal resource: {resource.type}/{resource.id}")

        track_album_pairs = self._collect_tracks(resource)
        logger.info(f"Found {len(track_album_pairs)} track(s) to download from Tidal")

        for track_obj, album_obj in track_album_pairs:
            try:
                downloaded_path = asyncio.run(
                    self._download_track_to_cache(track_obj)
                )
                if downloaded_path:
                    track_dict = self._tidal_to_dict(track_obj, album_obj)
                    final_path = self._move_to_output(downloaded_path, track_dict)
                    if final_path:
                        self._write_metadata(final_path, track_obj, album_obj)
                        self._session_files.append(final_path)
                else:
                    logger.warning(
                        f"Tidal download returned no path for: {track_obj.title}"
                    )
            except Exception as exc:
                logger.error(
                    f"Error downloading Tidal track {track_obj.id} "
                    f"({track_obj.title}): {exc}"
                )

        self._cleanup_cache()

    def download_tracks(self, tracks: List[Dict[str, Any]]) -> set:
        """
        Spotify-fallback path.  For each Spotify track dict that Qobuz
        couldn't find, search Tidal, validate the match, download, and
        rename using the Spotify-side metadata so track numbering stays
        correct relative to the original Spotify album/playlist.

        Returns the set of ``spotify_id`` values that were successfully
        downloaded, so the caller can determine what is still missing.
        """
        downloaded_ids: set = set()

        for i, track in enumerate(tracks, 1):
            label = f"{track.get('artist', '?')} - {track.get('name', '?')}"
            logger.info(
                f"[Tidal fallback] {i}/{len(tracks)}: {label}"
            )

            try:
                tidal_track = self._search_track(track)
                if not tidal_track:
                    logger.warning(f"[Tidal fallback] Not found on Tidal: {label}")
                    continue

                downloaded_path = asyncio.run(
                    self._download_track_to_cache(tidal_track)
                )
                if not downloaded_path:
                    logger.warning(
                        f"[Tidal fallback] Download returned no path for: {label}"
                    )
                    continue

                final_path = self._move_to_output(downloaded_path, track)
                if final_path:
                    album_obj = None
                    try:
                        album_obj = self.api.get_album(tidal_track.album.id)
                    except Exception as exc:
                        logger.warning(
                            f"[Tidal fallback] Could not fetch album for "
                            f"metadata ({label}): {exc}"
                        )
                    self._write_metadata(final_path, tidal_track, album_obj)
                    self._session_files.append(final_path)

                    sid = track.get("spotify_id")
                    if sid:
                        self._downloaded_file_paths[sid] = final_path
                    downloaded_ids.add(sid)
                    logger.info(f"[Tidal fallback] Downloaded: {label}")

            except Exception as exc:
                logger.error(f"[Tidal fallback] Error for {label}: {exc}")

        self._cleanup_cache()
        return downloaded_ids

    # ------------------------------------------------------------------
    # Internal helpers — Tidal resource fetching
    # ------------------------------------------------------------------

    def _collect_tracks(self, resource) -> List[Tuple[Any, Any]]:
        """Return a list of (Track, Album) tuples for the given resource."""
        rtype = resource.type

        if rtype == "track":
            track = self.api.get_track(resource.id)
            album = self.api.get_album(track.album.id)
            return [(track, album)]

        if rtype == "album":
            return self._collect_album_tracks(resource.id)

        if rtype == "playlist":
            return self._collect_playlist_tracks(resource.id)

        logger.warning(f"Unsupported Tidal resource type: {rtype}")
        return []

    def _collect_album_tracks(self, album_id: str) -> List[Tuple[Any, Any]]:
        album = self.api.get_album(album_id)
        pairs: List[Tuple[Any, Any]] = []
        offset = 0
        while True:
            items = self.api.get_album_items(album_id, offset=offset)
            for item_wrapper in items.items:
                if hasattr(item_wrapper, "item") and hasattr(
                    item_wrapper.item, "trackNumber"
                ):
                    pairs.append((item_wrapper.item, album))
            offset += items.limit
            if offset >= items.totalNumberOfItems:
                break
        return pairs

    def _collect_playlist_tracks(self, playlist_uuid: str) -> List[Tuple[Any, Any]]:
        pairs: List[Tuple[Any, Any]] = []
        offset = 0
        while True:
            items = self.api.get_playlist_items(playlist_uuid, offset=offset)
            for item_wrapper in items.items:
                if item_wrapper.type == "track":
                    track = item_wrapper.item
                    try:
                        album = self.api.get_album(track.album.id)
                        pairs.append((track, album))
                    except Exception as exc:
                        logger.warning(
                            f"Could not fetch album for track {track.id}: {exc}"
                        )
            offset += items.limit
            if offset >= items.totalNumberOfItems:
                break
        return pairs

    # ------------------------------------------------------------------
    # Internal helpers — Tidal search (Spotify fallback)
    # ------------------------------------------------------------------

    def _search_track(self, spotify_track: Dict[str, Any]) -> Optional[Any]:
        """Search Tidal for a track matching the Spotify track metadata."""
        query = f"{spotify_track.get('artist', '')} {spotify_track.get('name', '')}"
        logger.debug(f"Tidal search: '{query}'")

        try:
            results = self.api.get_search(query)
        except Exception as exc:
            logger.error(f"Tidal search failed for '{query}': {exc}")
            return None

        for candidate in results.tracks.items:
            candidate_artist = (
                candidate.artist.name if candidate.artist else ""
            )
            if titles_match(candidate.title, spotify_track.get("name", "")) and (
                titles_match(candidate_artist, spotify_track.get("artist", ""))
                or titles_match(
                    candidate.album.title, spotify_track.get("album", "")
                )
            ):
                logger.debug(
                    f"Tidal match: '{candidate.title}' by '{candidate_artist}'"
                )
                return candidate

        logger.debug(f"No Tidal match for '{query}'")
        return None

    # ------------------------------------------------------------------
    # Internal helpers — downloading
    # ------------------------------------------------------------------

    async def _download_track_to_cache(self, track: Any) -> Optional[Path]:
        """Run tiddl's Downloader to fetch one track into the cache dir."""
        from tiddl.cli.commands.download.downloader import Downloader
        from tiddl.cli.commands.download.output import RichOutput

        tiddl_quality = _TIDAL_QUALITY_MAP.get(self.quality, "high")

        rich_output = RichOutput(self.console)
        downloader = Downloader(
            tidal_api=self.api,
            threads_count=1,
            rich_output=rich_output,
            track_quality=tiddl_quality,
            video_quality="fhd",
            videos_filter="none",
            skip_existing=False,
            download_path=self.cache_dir,
            scan_path=self.cache_dir,
        )

        stem = Path(str(track.id))
        result_path, _ = await downloader.download(item=track, file_path=stem)
        return result_path

    # ------------------------------------------------------------------
    # Internal helpers — file renaming / moving
    # ------------------------------------------------------------------

    def _tidal_to_dict(self, track: Any, album: Any) -> Dict[str, Any]:
        """Convert tiddl Track + Album objects to our unified track dict."""
        artist = track.artist.name if track.artist else ""
        album_artist = album.artist.name if album.artist else artist
        year = ""
        if album.releaseDate:
            try:
                year = str(album.releaseDate.year)
            except Exception:
                year = str(album.releaseDate)[:4]

        return {
            "name": track.title,
            "artist": artist,
            "album": album.title,
            "album_artist": album_artist,
            "track_number": track.trackNumber,
            "disc_number": track.volumeNumber,
            "year": year,
            "duration": track.duration,
            "isrc": track.isrc or "",
        }

    def _move_to_output(
        self, cache_path: Path, track_dict: Dict[str, Any]
    ) -> Optional[Path]:
        """
        Compute the target path for *track_dict* using TrackFormatter and
        move *cache_path* there.  Returns the final path on success.
        """
        try:
            if "/" in self.track_format:
                folder_part, file_part = self.track_format.rsplit("/", 1)
                folder_name = self.formatter.format_folder(track_dict, folder_part)
                new_filename = self.formatter.format_track(track_dict, file_part)
            else:
                folder_name = self.formatter.format_folder(
                    track_dict, self.folder_format
                )
                new_filename = self.formatter.format_track(
                    track_dict, self.track_format
                )

            disc_number = track_dict.get("disc_number", 1)
            if disc_number and disc_number > 1:
                target = (
                    Path(self.output_dir) / folder_name / f"disc-{disc_number}" / new_filename
                )
            else:
                target = Path(self.output_dir) / folder_name / new_filename

            target.parent.mkdir(parents=True, exist_ok=True)
            final_path = target.with_suffix(cache_path.suffix)
            shutil.move(str(cache_path), str(final_path))
            logger.info(f"Tidal: saved {final_path.name}")
            return final_path

        except Exception as exc:
            logger.error(f"Error moving Tidal file {cache_path}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Internal helpers — metadata / cover art
    # ------------------------------------------------------------------

    def _write_metadata(self, path: Path, track: Any, album: Any) -> None:
        """
        Embed tags and cover art into a downloaded Tidal file.

        Reuses tiddl's own metadata writers so the FLAC/M4A gets TITLE,
        ARTIST, ALBUMARTIST, ALBUM, TRACKNUMBER, DISCNUMBER, DATE, ISRC and
        an embedded front-cover picture.  Failures here are non-fatal: the
        file was already downloaded and renamed successfully.
        """
        try:
            from tiddl.core.metadata import add_track_metadata, Cover

            cover_uid = album.cover if album else None
            if not cover_uid and getattr(track, "album", None):
                cover_uid = track.album.cover

            cover_data = None
            if cover_uid:
                try:
                    cover_data = Cover(cover_uid).fetch_data() or None
                except Exception as exc:
                    logger.warning(
                        f"Could not fetch Tidal cover art for {path.name}: {exc}"
                    )

            if album and album.artist:
                album_artist = album.artist.name
            elif track.artist:
                album_artist = track.artist.name
            else:
                album_artist = ""

            date = ""
            if album and album.releaseDate:
                date = str(album.releaseDate)

            add_track_metadata(
                path=path,
                track=track,
                album_artist=album_artist,
                cover_data=cover_data,
                date=date,
            )
            logger.debug(f"Wrote metadata for {path.name}")

        except Exception as exc:
            logger.warning(f"Could not write metadata for {path.name}: {exc}")

    # ------------------------------------------------------------------
    # Cache cleanup
    # ------------------------------------------------------------------

    def _cleanup_cache(self) -> None:
        try:
            if self.cache_dir.exists():
                for item in self.cache_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
        except Exception as exc:
            logger.warning(f"Tidal cache cleanup error: {exc}")

    def __del__(self) -> None:
        try:
            self._cleanup_cache()
        except Exception:
            pass
