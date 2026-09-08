"""
Command line interface for spotqo-dl.
"""

import click
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from qobuz_dl.exceptions import AuthenticationError

from .spotify_parser import SpotifyParser
from .qobuz_parser import QobuzParser
from .qobuz_downloader import QobuzDownloader
from .tidal_downloader import TidalDownloader
from .config import Config
from .playlist import write_m3u
from .restructure import AudioFileRestructurer

console = Console()


def is_qobuz_url(url: str) -> bool:
    """Check if the URL is a Qobuz URL."""
    return "qobuz.com" in url.lower()


def is_tidal_url(url: str) -> bool:
    """Check if the URL is a Tidal URL or tiddl shorthand (e.g. 'track/123')."""
    lower = url.lower()
    if "tidal.com" in lower:
        return True
    # tiddl shorthand: "track/12345", "album/12345", "playlist/<uuid>", etc.
    tidal_types = ("track/", "album/", "playlist/", "artist/", "mix/", "video/")
    return any(lower.startswith(t) for t in tidal_types)


def setup_logging(verbose: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """
    spotqo-dl - Download Spotify/Qobuz/Tidal tracks with unified formatting.

    Use 'spotqo-dl download' to download tracks or 'spotqo-dl restructure' to
    reorganize existing files.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(ctx.get_help())


MAX_RETRIES = 2


def _download_single_url(
    url: str,
    qobuz_downloader: QobuzDownloader,
    spotify_parser: Optional[SpotifyParser],
    tidal_downloader: Optional[TidalDownloader],
    no_tidal_fallback: bool,
    logger: logging.Logger,
) -> None:
    """
    Download a single URL (Qobuz, Tidal, or Spotify).

    For Spotify URLs, Qobuz is tried first.  Any tracks Qobuz misses are
    handed off to the Tidal fallback (unless ``no_tidal_fallback`` is True
    or ``tidal_downloader`` is None).

    Playlist m3u generation is handled by the caller after this returns so
    that both registries can be merged.  This function therefore does NOT
    write playlists.

    Raises on unrecoverable failure.
    """
    if is_tidal_url(url):
        if tidal_downloader is None:
            raise RuntimeError(
                "Tidal URL detected but Tidal is not available. "
                "Run: tiddl auth login"
            )
        console.print(f"[blue]Downloading from Tidal URL: {url}[/blue]")
        tidal_downloader.download_tidal_url(url)
        return

    if is_qobuz_url(url):
        console.print(f"[blue]Downloading from Qobuz URL: {url}[/blue]")
        qobuz_downloader.download_qobuz_url(url)
        return

    # --- Spotify URL ---
    if spotify_parser is None:
        raise RuntimeError("Spotify credentials are required for Spotify URLs")

    console.print(f"[blue]Parsing Spotify URL: {url}[/blue]")
    spotify_data = spotify_parser.parse_url(url)

    if not spotify_data:
        raise RuntimeError(
            f"Could not parse Spotify URL or no tracks found: {url}"
        )

    tracks = spotify_data["tracks"]
    console.print(
        f"[green]Found {len(tracks)} track(s) to download[/green]"
    )

    # Primary: Qobuz
    qobuz_downloader.download_tracks(tracks)

    # Determine which Spotify tracks Qobuz missed
    missing = [
        t
        for t in tracks
        if t.get("spotify_id") not in qobuz_downloader._downloaded_file_paths
    ]

    if missing and not no_tidal_fallback:
        if tidal_downloader is None:
            console.print(
                f"[yellow]{len(missing)} track(s) not found on Qobuz; "
                "Tidal fallback unavailable (run: tiddl auth login)[/yellow]"
            )
        else:
            console.print(
                f"[yellow]{len(missing)} track(s) not found on Qobuz; "
                f"trying Tidal fallback...[/yellow]"
            )
            tidal_downloader.download_tracks(missing)
    elif missing:
        console.print(
            f"[yellow]{len(missing)} track(s) not found on Qobuz "
            "(Tidal fallback disabled)[/yellow]"
        )


def _maybe_write_m3u(
    spotify_data: Optional[dict],
    qobuz_downloader: QobuzDownloader,
    tidal_downloader: Optional[TidalDownloader],
    output_dir: str,
) -> None:
    """Write a merged m3u if this was a Spotify playlist download."""
    if not spotify_data:
        return
    if spotify_data.get("type") != "playlist":
        return
    playlist_name = spotify_data.get("name")
    if not playlist_name:
        return

    registries = [qobuz_downloader._downloaded_file_paths]
    if tidal_downloader is not None:
        registries.append(tidal_downloader._downloaded_file_paths)

    write_m3u(
        output_dir=output_dir,
        tracks=spotify_data["tracks"],
        playlist_name=playlist_name,
        path_registries=registries,
    )


def _apply_replaygain(
    qobuz_downloader: QobuzDownloader,
    tidal_downloader: Optional[TidalDownloader],
    target: float,
    album_gain: bool,
    prevent_clipping: bool,
    jobs: int,
) -> None:
    """Run loudness analysis over all files downloaded this session."""
    from .loudness import LoudnessProcessor

    session_files: list[Path] = []
    session_files.extend(qobuz_downloader._session_files)
    if tidal_downloader is not None:
        session_files.extend(tidal_downloader._session_files)

    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique_files = []
    for path in session_files:
        if path not in seen and path.exists():
            seen.add(path)
            unique_files.append(path)

    if not unique_files:
        return

    console.print(
        f"\n[bold cyan]Applying ReplayGain to {len(unique_files)} "
        f"track(s) (target {target:g} LUFS)...[/bold cyan]"
    )
    try:
        processor = LoudnessProcessor(
            target=target,
            album_mode=album_gain,
            prevent_clipping=prevent_clipping,
            jobs=jobs,
            console=console,
        )
        tagged = processor.process_files(unique_files)
        console.print(f"[green]ReplayGain tagged {tagged} track(s).[/green]")
    except Exception as exc:  # noqa: BLE001 - normalization is non-fatal
        console.print(f"[yellow]ReplayGain step failed: {exc}[/yellow]")


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=".",
    help="Output directory for downloads (default: current directory)",
)
@click.option(
    "--quality",
    "-q",
    type=click.Choice(["5", "6", "7", "27"]),
    default="6",
    help="Qobuz quality (5=MP3, 6=Lossless, 7=Hi-res <96kHz, 27=Hi-res >96kHz)",
)
@click.option(
    "--tidal-quality",
    type=click.Choice(["LOW", "HIGH", "LOSSLESS", "HI_RES_LOSSLESS"]),
    default="LOSSLESS",
    help="Tidal quality for fallback/direct downloads (default: LOSSLESS)",
)
@click.option(
    "--no-tidal-fallback",
    is_flag=True,
    default=False,
    help="Disable automatic Tidal fallback for Spotify URLs (Qobuz-only)",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
@click.option("--config", "-c", help="Path to config file")
@click.option("--spotify-client-id", help="Spotify client ID")
@click.option("--spotify-client-secret", help="Spotify client secret")
@click.option("--qobuz-email", help="Qobuz email")
@click.option("--qobuz-password", help="Qobuz password")
@click.option(
    "--format",
    "-f",
    default="{artist} - {title}",
    help=(
        'Format string for file naming '
        '(e.g., "{artist}/{album}/{track-number:02d} - {title}")'
    ),
)
@click.option(
    "--folder-format",
    default="{artist} - {album} ({year})",
    help="Format string for folder naming",
)
@click.option(
    "--replaygain/--no-replaygain",
    "replaygain",
    default=None,
    help=(
        "Analyze and tag downloaded tracks with ReplayGain loudness values "
        "(overrides the [loudness] config section)"
    ),
)
@click.option(
    "--rg-target",
    type=float,
    default=None,
    help="ReplayGain reference loudness in LUFS (default: -18.0)",
)
@click.option(
    "--tui",
    "use_tui",
    is_flag=True,
    help="Launch the interactive Terminal User Interface",
)
def download(
    urls: tuple,
    output_dir: str,
    quality: str,
    tidal_quality: str,
    no_tidal_fallback: bool,
    verbose: bool,
    config: Optional[str],
    spotify_client_id: Optional[str],
    spotify_client_secret: Optional[str],
    qobuz_email: Optional[str],
    qobuz_password: Optional[str],
    format: str,
    folder_format: str,
    replaygain: Optional[bool],
    rg_target: Optional[float],
    use_tui: bool,
) -> None:
    """
    Download tracks using Qobuz as the primary source, with automatic Tidal
    fallback for any tracks Qobuz cannot find.

    URLS can be one or more Spotify, Qobuz, or Tidal URLs.  When multiple
    URLs are provided they are downloaded in succession.  Failed URLs are
    retried up to two times before being skipped, and any remaining failures
    are printed at the end.
    """

    if use_tui:
        from .tui import run_tui

        run_tui()
        return

    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    try:
        config_manager = Config(config_path=config)
        config_manager.load()

        if spotify_client_id:
            config_manager.spotify_client_id = spotify_client_id
        if spotify_client_secret:
            config_manager.spotify_client_secret = spotify_client_secret
        if qobuz_email:
            config_manager.qobuz_email = qobuz_email
        if qobuz_password:
            config_manager.qobuz_password = qobuz_password

        # Resolve ReplayGain settings: CLI flags override the config file.
        rg_enabled = (
            replaygain if replaygain is not None else config_manager.loudness_enabled
        )
        rg_reference = (
            rg_target if rg_target is not None else config_manager.loudness_target
        )

        has_spotify_urls = any(
            not is_qobuz_url(u) and not is_tidal_url(u) for u in urls
        )

        if not config_manager.qobuz_email or not config_manager.qobuz_password:
            console.print("[red]Error: Missing Qobuz credentials.[/red]")
            console.print("\nYou can set Qobuz credentials via:")
            console.print("1. Command line options (--qobuz-email, --qobuz-password)")
            console.print("2. Environment variables (QOBUZ_EMAIL, QOBUZ_PASSWORD)")
            console.print("3. Config file (--config)")
            sys.exit(1)

        if has_spotify_urls and not config_manager.is_valid():
            console.print(
                "[red]Error: Missing Spotify credentials (needed for Spotify URLs).[/red]"
            )
            console.print("\nYou can set credentials via:")
            console.print("1. Command line options (--spotify-client-id, etc.)")
            console.print("2. Environment variables (SPOTIFY_CLIENT_ID, etc.)")
            console.print("3. Config file (--config)")
            sys.exit(1)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        qobuz_downloader = QobuzDownloader(
            email=config_manager.qobuz_email,
            password=config_manager.qobuz_password,
            output_dir=str(output_path),
            quality=int(quality),
            track_format=format,
            folder_format=folder_format,
        )

        spotify_parser: Optional[SpotifyParser] = None
        if has_spotify_urls:
            spotify_parser = SpotifyParser(
                client_id=config_manager.spotify_client_id,
                client_secret=config_manager.spotify_client_secret,
            )

        # Try to build Tidal downloader; gracefully degrade if not authenticated.
        tidal_downloader: Optional[TidalDownloader] = None
        has_tidal_urls = any(is_tidal_url(u) for u in urls)
        needs_tidal = has_tidal_urls or (has_spotify_urls and not no_tidal_fallback)
        if needs_tidal:
            try:
                tidal_downloader = TidalDownloader(
                    output_dir=str(output_path),
                    quality=tidal_quality,
                    track_format=format,
                    folder_format=folder_format,
                    console=console,
                )
                # Eagerly validate auth so we fail fast for direct Tidal URLs.
                if has_tidal_urls:
                    _ = tidal_downloader.api
            except RuntimeError as exc:
                if has_tidal_urls:
                    console.print(f"[red]Tidal error: {exc}[/red]")
                    sys.exit(1)
                else:
                    console.print(
                        f"[yellow]Tidal unavailable ({exc}); "
                        "fallback disabled.[/yellow]"
                    )
                    tidal_downloader = None

        total = len(urls)
        failed_urls: list[str] = []

        for idx, url in enumerate(urls, 1):
            console.print(
                f"\n[bold cyan]({idx}/{total})[/bold cyan] Processing: {url}"
            )
            success = False
            spotify_data: Optional[dict] = None

            for attempt in range(MAX_RETRIES + 1):
                try:
                    # For Spotify URLs, capture the parsed data so we can
                    # write the playlist m3u after both downloaders finish.
                    if (
                        not is_qobuz_url(url)
                        and not is_tidal_url(url)
                        and spotify_parser is not None
                    ):
                        spotify_data = spotify_parser.parse_url(url)
                        if not spotify_data:
                            raise RuntimeError(
                                f"Could not parse Spotify URL or no tracks found: {url}"
                            )

                    _download_single_url(
                        url=url,
                        qobuz_downloader=qobuz_downloader,
                        spotify_parser=spotify_parser,
                        tidal_downloader=tidal_downloader,
                        no_tidal_fallback=no_tidal_fallback,
                        logger=logger,
                    )

                    # Write merged playlist m3u if applicable
                    if spotify_data:
                        _maybe_write_m3u(
                            spotify_data=spotify_data,
                            qobuz_downloader=qobuz_downloader,
                            tidal_downloader=tidal_downloader,
                            output_dir=str(output_path),
                        )

                    success = True
                    break

                except AuthenticationError as exc:
                    console.print(f"[red]Authentication error: {exc}[/red]")
                    console.print(
                        "[red]Update your credentials in "
                        "~/.config/spotqo-dl/config.ini and try again.[/red]"
                    )
                    sys.exit(1)

                except Exception as exc:
                    if attempt < MAX_RETRIES:
                        console.print(
                            f"[yellow]Attempt {attempt + 1}/{MAX_RETRIES + 1} "
                            f"failed for {url}: {exc}[/yellow]"
                        )
                        console.print("[yellow]Retrying in 3 seconds...[/yellow]")
                        time.sleep(3)
                    else:
                        logger.error(
                            f"All {MAX_RETRIES + 1} attempts failed for {url}: {exc}"
                        )
                        console.print(
                            f"[red]All attempts failed for: {url}[/red]"
                        )

            if not success:
                failed_urls.append(url)

        # ReplayGain: analyze the whole session at once so album gain can be
        # computed per album folder.
        if rg_enabled:
            _apply_replaygain(
                qobuz_downloader=qobuz_downloader,
                tidal_downloader=tidal_downloader,
                target=rg_reference,
                album_gain=config_manager.loudness_album_gain,
                prevent_clipping=config_manager.loudness_prevent_clipping,
                jobs=config_manager.loudness_jobs,
            )

        console.print()
        succeeded = total - len(failed_urls)
        if not failed_urls:
            console.print(
                f"[bold green]All {total} URL(s) downloaded successfully![/bold green]"
            )
        else:
            console.print(
                f"[green]{succeeded}/{total} URL(s) downloaded successfully.[/green]"
            )
            console.print(
                f"[bold red]Failed URLs ({len(failed_urls)} failed):[/bold red]"
            )
            for furl in failed_urls:
                console.print(f"  {furl}")
            sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Download interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


@main.command()
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option(
    "--format",
    "-f",
    default="{track-number:02d} - {title}",
    help='Format string for file naming (e.g., "{track-number:02d} - {title}")',
)
@click.option(
    "--folder-format",
    default="{artist} - {album} ({year})",
    help="Format string for folder naming",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show what would be done without making changes",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
def restructure(
    directory: str,
    format: str,
    folder_format: str,
    dry_run: bool,
    verbose: bool,
) -> None:
    """
    Restructure audio files in a directory using metadata.

    Recursively finds all .flac and .mp3 files in DIRECTORY, extracts their
    metadata, and reorganizes them according to the specified format templates.

    Example:
        spotqo-dl restructure /path/to/music --format "{track-number:02d} - {title}"
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    try:
        console.print("[bold cyan]Audio File Restructurer[/bold cyan]\n")

        restructurer = AudioFileRestructurer(
            track_format=format,
            folder_format=folder_format,
        )
        restructurer.restructure_files(directory, dry_run=dry_run)

    except KeyboardInterrupt:
        console.print("\n[yellow]Restructuring interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


@main.command()
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.option(
    "--target",
    "-t",
    type=float,
    default=-18.0,
    show_default=True,
    help="Reference loudness in LUFS (-18 = ReplayGain 2.0, -14 = streaming)",
)
@click.option(
    "--album/--no-album",
    "album_mode",
    default=True,
    show_default=True,
    help="Also compute per-album gain (one gated measurement per folder)",
)
@click.option(
    "--prevent-clipping/--allow-clipping",
    "prevent_clipping",
    default=True,
    show_default=True,
    help="Reduce gain when it would push the true peak above -1 dBTP",
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-tag files that already carry ReplayGain tags",
)
@click.option(
    "--jobs",
    "-j",
    type=int,
    default=4,
    show_default=True,
    help="Number of files to analyze in parallel",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show measured loudness and computed gain without writing tags",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
def normalize(
    directory: str,
    target: float,
    album_mode: bool,
    prevent_clipping: bool,
    force: bool,
    jobs: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """
    Analyze audio files and write ReplayGain loudness tags (non-destructive).

    Recursively finds all .flac, .mp3, .m4a and .wav files in DIRECTORY,
    measures each file's loudness with ffmpeg's EBU R128 scanner, and writes
    ReplayGain 2.0 gain/peak tags.  The audio samples are never modified; a
    ReplayGain-aware player uses the tags to even out volume at playback.

    Works on files from any source (Tidal, Qobuz, Soulseek, CD rips, ...).

    Example:
        spotqo-dl normalize /path/to/music --target -18
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    try:
        from .loudness import LoudnessProcessor

        console.print("[bold cyan]Loudness Normalization (ReplayGain)[/bold cyan]\n")

        processor = LoudnessProcessor(
            target=target,
            album_mode=album_mode,
            prevent_clipping=prevent_clipping,
            force=force,
            jobs=jobs,
            dry_run=dry_run,
            console=console,
        )
        tagged = processor.process_directory(directory)

        if dry_run:
            console.print(
                "\n[yellow]Dry run - no tags were written.[/yellow]"
            )
        else:
            console.print(
                f"\n[bold green]Tagged {tagged} file(s) with ReplayGain.[/bold green]"
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Normalization interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


@main.command(name="normalize-export")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.argument(
    "dest",
    type=click.Path(file_okay=False, dir_okay=True),
)
@click.option(
    "--target",
    "-t",
    type=float,
    default=-18.0,
    show_default=True,
    help="Reference loudness in LUFS to normalize to",
)
@click.option(
    "--prevent-clipping/--allow-clipping",
    "prevent_clipping",
    default=True,
    show_default=True,
    help="Reduce gain when it would push the true peak above -1 dBTP",
)
@click.option(
    "--jobs",
    "-j",
    type=int,
    default=4,
    show_default=True,
    help="Number of files to process in parallel",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show what would be exported without writing files",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
def normalize_export(
    source: str,
    dest: str,
    target: float,
    prevent_clipping: bool,
    jobs: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """
    Write gain-applied lossless copies for players that ignore ReplayGain tags.

    Recursively finds lossless files (.flac, .wav) under SOURCE, bakes the
    loudness gain into fresh FLAC copies under DEST (mirroring the directory
    layout, with dither), and copies tags and cover art.  The originals are
    never modified.  Use this for DAPs, car stereos, or other hardware that
    does not honor ReplayGain tags; for everything else prefer `normalize`.

    Example:
        spotqo-dl normalize-export /music /music-normalized --target -18
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    from concurrent.futures import ThreadPoolExecutor

    try:
        from .loudness import (
            LOSSLESS_EXTENSIONS,
            export_with_gain,
        )

        console.print("[bold cyan]Loudness Export (baked gain)[/bold cyan]\n")

        source_path = Path(source)
        dest_path = Path(dest)

        files: list[Path] = []
        for ext in LOSSLESS_EXTENSIONS:
            files.extend(source_path.rglob(f"*{ext}"))
        files = sorted(files)

        if not files:
            console.print(
                f"[yellow]No lossless files (.flac/.wav) found in {source}[/yellow]"
            )
            return

        console.print(
            f"Exporting {len(files)} file(s) to {dest_path} "
            f"(target {target:g} LUFS)\n"
        )

        def _export_one(src_file: Path) -> tuple[Path, bool, str]:
            rel = src_file.relative_to(source_path)
            out = dest_path / rel
            if dry_run:
                return src_file, True, f"would export -> {rel.with_suffix('.flac')}"
            try:
                result = export_with_gain(
                    src_file,
                    out,
                    target_lufs=target,
                    prevent_clipping=prevent_clipping,
                )
                return src_file, True, f"exported -> {result.relative_to(dest_path)}"
            except Exception as exc:  # noqa: BLE001
                return src_file, False, str(exc)

        exported = 0
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for src_file, ok, message in pool.map(_export_one, files):
                if ok:
                    exported += 1
                    console.print(f"  [green]{src_file.name}[/green]: {message}")
                else:
                    console.print(f"  [red]{src_file.name}[/red]: {message}")

        if dry_run:
            console.print("\n[yellow]Dry run - no files were written.[/yellow]")
        else:
            console.print(
                f"\n[bold green]Exported {exported} file(s) to {dest_path}.[/bold green]"
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Export interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
