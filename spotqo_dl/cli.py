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


if __name__ == "__main__":
    main()
