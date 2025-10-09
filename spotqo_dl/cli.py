"""
Command line interface for spotqo-dl.
"""

import click
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from .spotify_parser import SpotifyParser
from .qobuz_parser import QobuzParser
from .qobuz_downloader import QobuzDownloader
from .config import Config

console = Console()


def is_qobuz_url(url: str) -> bool:
    """Check if the URL is a Qobuz URL."""
    return 'qobuz.com' in url.lower()


def setup_logging(verbose: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)]
    )


@click.command()
@click.argument('url', type=str, required=False)
@click.option('--output', '-o', 'output_dir', 
              default='.',
              help='Output directory for downloads (default: current directory)')
@click.option('--quality', '-q', 
              type=click.Choice(['5', '6', '7', '27']),
              default='6',
              help='Download quality (5=MP3, 6=Lossless, 7=Hi-res <96kHz, 27=Hi-res >96kHz)')
@click.option('--verbose', '-v', 
              is_flag=True,
              help='Enable verbose logging')
@click.option('--config', '-c',
              help='Path to config file')
@click.option('--spotify-client-id',
              help='Spotify client ID')
@click.option('--spotify-client-secret',
              help='Spotify client secret')
@click.option('--qobuz-email',
              help='Qobuz email')
@click.option('--qobuz-password',
              help='Qobuz password')
@click.option('--format', '-f',
              default='{artist} - {title}',
              help='Format string for file naming (e.g., "{artist}/{album}/{track-number:02d} - {title}")')
@click.option('--folder-format',
              default='{artist} - {album} ({year})',
              help='Format string for folder naming')
@click.option('--tui', 'use_tui', is_flag=True,
              help='Launch the interactive Terminal User Interface')
def main(url: Optional[str], output_dir: str, quality: str, verbose: bool, 
         config: Optional[str], spotify_client_id: Optional[str],
         spotify_client_secret: Optional[str], qobuz_email: Optional[str],
         qobuz_password: Optional[str], format: str, folder_format: str, use_tui: bool) -> None:
    """
    Download Spotify tracks using Qobuz as the source.
    
    URL can be a Spotify track, album, or playlist URL, or a Qobuz URL.
    """
    
    # If --tui flag is used, launch TUI
    if use_tui:
        from .tui import run_tui
        run_tui()
        return
    
    # If no URL provided, show help
    if not url:
        click.echo("Error: URL is required. Use --help for usage information.")
        click.echo("For interactive mode, use: spotqo-dl --tui")
        sys.exit(1)
    
    # Run the download logic
    setup_logging(verbose)
    logger = logging.getLogger(__name__)
    
    try:
        # Load configuration
        config_manager = Config(config_path=config)
        config_manager.load()
        
        # Override with command line arguments if provided
        if spotify_client_id:
            config_manager.spotify_client_id = spotify_client_id
        if spotify_client_secret:
            config_manager.spotify_client_secret = spotify_client_secret
        if qobuz_email:
            config_manager.qobuz_email = qobuz_email
        if qobuz_password:
            config_manager.qobuz_password = qobuz_password
            
        # Validate configuration based on URL type
        if is_qobuz_url(url):
            # For Qobuz URLs, only need Qobuz credentials
            if not config_manager.qobuz_email or not config_manager.qobuz_password:
                console.print("[red]Error: Missing Qobuz credentials for Qobuz URL.[/red]")
                console.print("\nYou can set Qobuz credentials via:")
                console.print("1. Command line options (--qobuz-email, --qobuz-password)")
                console.print("2. Environment variables (QOBUZ_EMAIL, QOBUZ_PASSWORD)")
                console.print("3. Config file (--config)")
                sys.exit(1)
        else:
            # For Spotify URLs, need both Spotify and Qobuz credentials
            if not config_manager.is_valid():
                console.print("[red]Error: Missing required configuration. Please set up your credentials.[/red]")
                console.print("\nYou can set credentials via:")
                console.print("1. Command line options (--spotify-client-id, etc.)")
                console.print("2. Environment variables (SPOTIFY_CLIENT_ID, etc.)")
                console.print("3. Config file (--config)")
                sys.exit(1)
        
        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize Qobuz downloader
        qobuz_downloader = QobuzDownloader(
            email=config_manager.qobuz_email,
            password=config_manager.qobuz_password,
            output_dir=str(output_path),
            quality=int(quality),
            track_format=format,
            folder_format=folder_format
        )
        
        # Check if it's a Qobuz URL or Spotify URL
        if is_qobuz_url(url):
            # Handle Qobuz URL directly
            console.print(f"[blue]Downloading from Qobuz URL: {url}[/blue]")
            qobuz_downloader.download_qobuz_url(url)
            console.print("[green]Download completed![/green]")
        else:
            # Handle Spotify URL (existing logic)
            # Initialize Spotify parser
            spotify_parser = SpotifyParser(
                client_id=config_manager.spotify_client_id,
                client_secret=config_manager.spotify_client_secret
            )
            
            # Parse Spotify URL and get metadata
            console.print(f"[blue]Parsing Spotify URL: {url}[/blue]")
            spotify_data = spotify_parser.parse_url(url)
            
            if not spotify_data:
                console.print("[red]Error: Could not parse Spotify URL or no tracks found.[/red]")
                sys.exit(1)
            
            # Download tracks using Qobuz
            console.print(f"[green]Found {len(spotify_data['tracks'])} tracks to download[/green]")
            qobuz_downloader.download_tracks(spotify_data['tracks'])
            
            console.print("[green]Download completed![/green]")
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Download interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main()