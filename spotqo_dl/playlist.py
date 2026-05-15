"""
M3U playlist generation helpers shared between Qobuz and Tidal downloaders.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def write_m3u(
    output_dir: str,
    tracks: List[Dict[str, Any]],
    playlist_name: str,
    path_registries: List[Dict[str, Path]],
) -> None:
    """
    Write an extended M3U playlist file for a set of downloaded tracks.

    The file is placed in ``{output_dir}/00_playlists/<slug>.m3u``.  All
    paths inside the file are relative to that directory so the playlist is
    portable as long as the library layout is preserved.

    Args:
        output_dir:       Root music output directory.
        tracks:           Ordered list of track dicts (as returned by
                          SpotifyParser).  Each dict must contain
                          ``spotify_id``, ``artist``, and ``name``.
        playlist_name:    Human-readable playlist name (will be slug-ified).
        path_registries:  One or more ``{spotify_id: Path}`` dicts from the
                          downloaders (Qobuz then Tidal, or any order).
                          The first registry that contains a ``spotify_id``
                          wins.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", playlist_name.lower()).strip("-")

    playlist_dir = Path(output_dir) / "00_playlists"
    playlist_dir.mkdir(parents=True, exist_ok=True)
    m3u_path = playlist_dir / f"{slug}.m3u"

    merged: Dict[str, Path] = {}
    for registry in path_registries:
        for sid, path in registry.items():
            if sid not in merged:
                merged[sid] = path

    lines = ["#EXTM3U"]
    missing = 0
    for track in tracks:
        sid = track.get("spotify_id")
        file_path = merged.get(sid) if sid else None
        if file_path and file_path.exists():
            rel = Path(os.path.relpath(file_path, playlist_dir))
            lines.append(str(rel))
        else:
            missing += 1
            logger.warning(
                f"No downloaded file for '{track.get('artist')} - {track.get('name')}'; "
                "skipping in playlist"
            )

    m3u_path.write_text("\n".join(lines) + "\n")
    saved = len(lines) - 1
    logger.info(f"Wrote playlist ({saved} tracks, {missing} skipped): {m3u_path}")
