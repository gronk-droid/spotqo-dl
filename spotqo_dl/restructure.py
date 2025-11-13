"""
File restructuring utilities for spotqo-dl.
Allows renaming and restructuring existing audio files using the formatter.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import logging

from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .formatter import TrackFormatter

console = Console()
logger = logging.getLogger(__name__)


class AudioFileRestructurer:
    """Restructures audio files based on their metadata."""
    
    SUPPORTED_EXTENSIONS = ['.flac', '.mp3']
    
    def __init__(self, track_format: str = "{track-number:02d} - {title}", 
                 folder_format: str = "{artist} - {album} ({year})"):
        """
        Initialize the restructurer.
        
        Args:
            track_format: Format string for track filenames
            folder_format: Format string for folder names
        """
        self.formatter = TrackFormatter()
        self.track_format = track_format
        self.folder_format = folder_format
    
    def find_audio_files(self, directory: str) -> List[Path]:
        """
        Recursively find all audio files in a directory.
        
        Args:
            directory: Directory to search
            
        Returns:
            List of Path objects for audio files
        """
        audio_files = []
        directory_path = Path(directory)
        
        for ext in self.SUPPORTED_EXTENSIONS:
            audio_files.extend(directory_path.rglob(f"*{ext}"))
        
        return sorted(audio_files)
    
    def extract_metadata(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Extract metadata from an audio file.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            Dictionary containing metadata or None if extraction fails
        """
        try:
            ext = file_path.suffix.lower()
            
            if ext == '.flac':
                audio = FLAC(str(file_path))
                metadata = {
                    'name': audio.get('title', [''])[0] if audio.get('title') else '',
                    'artist': audio.get('artist', [''])[0] if audio.get('artist') else '',
                    'album': audio.get('album', [''])[0] if audio.get('album') else '',
                    'album_artist': audio.get('albumartist', [''])[0] if audio.get('albumartist') else '',
                    'year': audio.get('date', [''])[0][:4] if audio.get('date') else '',
                    'track_number': int(audio.get('tracknumber', ['0'])[0].split('/')[0]) if audio.get('tracknumber') else 0,
                    'disc_number': int(audio.get('discnumber', ['1'])[0].split('/')[0]) if audio.get('discnumber') else 1,
                    'total_discs': int(audio.get('disctotal', ['1'])[0]) if audio.get('disctotal') else 1,
                }
            elif ext == '.mp3':
                audio = MP3(str(file_path))
                tags = audio.tags if audio.tags else {}
                
                # Helper function to get ID3 tag value
                def get_tag(tag_name):
                    tag = tags.get(tag_name)
                    if tag:
                        return str(tag.text[0]) if hasattr(tag, 'text') else str(tag)
                    return ''
                
                # Extract track number
                track_str = get_tag('TRCK')
                track_number = 0
                if track_str:
                    track_number = int(track_str.split('/')[0]) if '/' in track_str else int(track_str)
                
                # Extract disc number
                disc_str = get_tag('TPOS')
                disc_number = 1
                total_discs = 1
                if disc_str:
                    if '/' in disc_str:
                        disc_parts = disc_str.split('/')
                        disc_number = int(disc_parts[0])
                        total_discs = int(disc_parts[1])
                    else:
                        disc_number = int(disc_str)
                
                metadata = {
                    'name': get_tag('TIT2'),
                    'artist': get_tag('TPE1'),
                    'album': get_tag('TALB'),
                    'album_artist': get_tag('TPE2'),
                    'year': get_tag('TDRC')[:4] if get_tag('TDRC') else '',
                    'track_number': track_number,
                    'disc_number': disc_number,
                    'total_discs': total_discs,
                }
            else:
                return None
            
            # Use album_artist as artist if artist is empty
            if not metadata['artist'] and metadata['album_artist']:
                metadata['artist'] = metadata['album_artist']
            
            # Use artist as album_artist if album_artist is empty
            if not metadata['album_artist'] and metadata['artist']:
                metadata['album_artist'] = metadata['artist']
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error extracting metadata from {file_path}: {e}")
            return None
    
    def display_metadata_summary(self, files: List[Path], metadata_list: List[Dict[str, Any]]) -> None:
        """
        Display a summary of extracted metadata.
        
        Args:
            files: List of file paths
            metadata_list: List of metadata dictionaries
        """
        if not metadata_list:
            console.print("[yellow]No metadata extracted[/yellow]")
            return
        
        # Group by album
        albums = {}
        for file_path, metadata in zip(files, metadata_list):
            if metadata:
                album_key = (metadata.get('album_artist', ''), metadata.get('album', ''), metadata.get('year', ''))
                if album_key not in albums:
                    albums[album_key] = {
                        'files': [],
                        'metadata': [],
                        'discs': set()
                    }
                albums[album_key]['files'].append(file_path)
                albums[album_key]['metadata'].append(metadata)
                albums[album_key]['discs'].add(metadata.get('disc_number', 1))
        
        console.print(f"\n[bold cyan]Found {len(files)} audio files in {len(albums)} album(s)[/bold cyan]\n")
        
        for (album_artist, album, year), data in albums.items():
            table = Table(title=f"{album_artist} - {album} ({year})")
            table.add_column("File", style="cyan")
            table.add_column("Track", style="magenta")
            table.add_column("Disc", style="yellow")
            table.add_column("Title", style="green")
            
            for file_path, metadata in zip(data['files'], data['metadata']):
                table.add_row(
                    file_path.name,
                    str(metadata.get('track_number', '')),
                    str(metadata.get('disc_number', 1)),
                    metadata.get('name', '')
                )
            
            console.print(table)
            console.print()
    
    def prompt_for_metadata(self, files: List[Path], metadata_list: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Prompt user to confirm or edit metadata.
        
        Args:
            files: List of file paths
            metadata_list: List of metadata dictionaries
            
        Returns:
            Tuple of (updated metadata list, whether to proceed)
        """
        self.display_metadata_summary(files, metadata_list)
        
        if not Confirm.ask("\n[bold]Is the metadata correct?[/bold]", default=True):
            console.print("\n[yellow]Let's update the metadata manually.[/yellow]")
            
            # Group files by album
            albums = {}
            for file_path, metadata in zip(files, metadata_list):
                if metadata:
                    album_key = (metadata.get('album_artist', ''), metadata.get('album', ''), metadata.get('year', ''))
                    if album_key not in albums:
                        albums[album_key] = []
                    albums[album_key].append((file_path, metadata))
            
            # Update each album's metadata
            updated_metadata_list = []
            for idx, ((album_artist, album, year), album_files) in enumerate(albums.items()):
                console.print(f"\n[bold cyan]Album {idx + 1}/{len(albums)}[/bold cyan]")
                
                new_artist = Prompt.ask("Artist", default=album_artist)
                new_album = Prompt.ask("Album", default=album)
                new_year = Prompt.ask("Year", default=year)
                
                # Update metadata for all files in this album
                for file_path, metadata in album_files:
                    metadata['album_artist'] = new_artist
                    metadata['artist'] = new_artist  # Update artist too
                    metadata['album'] = new_album
                    metadata['year'] = new_year
                    updated_metadata_list.append(metadata)
            
            # Show updated summary
            self.display_metadata_summary(files, updated_metadata_list)
            
            if not Confirm.ask("\n[bold]Proceed with restructuring?[/bold]", default=True):
                return updated_metadata_list, False
            
            return updated_metadata_list, True
        
        return metadata_list, True
    
    def restructure_files(self, directory: str, dry_run: bool = False) -> None:
        """
        Restructure audio files in a directory.
        
        Args:
            directory: Directory containing audio files
            dry_run: If True, only show what would be done without making changes
        """
        console.print(f"[bold]Scanning directory: {directory}[/bold]\n")
        
        # Find all audio files
        audio_files = self.find_audio_files(directory)
        
        if not audio_files:
            console.print("[yellow]No audio files found[/yellow]")
            return
        
        console.print(f"[green]Found {len(audio_files)} audio file(s)[/green]\n")
        
        # Extract metadata from all files
        metadata_list = []
        for file_path in audio_files:
            metadata = self.extract_metadata(file_path)
            if metadata:
                metadata_list.append(metadata)
            else:
                console.print(f"[yellow]Warning: Could not extract metadata from {file_path}[/yellow]")
                metadata_list.append(None)
        
        # Filter out files with no metadata
        valid_files = [(f, m) for f, m in zip(audio_files, metadata_list) if m is not None]
        
        if not valid_files:
            console.print("[red]No valid metadata found in any files[/red]")
            return
        
        audio_files, metadata_list = zip(*valid_files)
        audio_files = list(audio_files)
        metadata_list = list(metadata_list)
        
        # Prompt for metadata confirmation
        metadata_list, proceed = self.prompt_for_metadata(audio_files, metadata_list)
        
        if not proceed:
            console.print("[yellow]Restructuring cancelled[/yellow]")
            return
        
        # Group files by album and disc
        album_groups = {}
        for file_path, metadata in zip(audio_files, metadata_list):
            album_key = (
                metadata.get('album_artist', ''),
                metadata.get('album', ''),
                metadata.get('year', '')
            )
            disc_num = metadata.get('disc_number', 1)
            total_discs = metadata.get('total_discs', 1)
            
            if album_key not in album_groups:
                album_groups[album_key] = {
                    'total_discs': total_discs,
                    'discs': {}
                }
            
            if disc_num not in album_groups[album_key]['discs']:
                album_groups[album_key]['discs'][disc_num] = []
            
            album_groups[album_key]['discs'][disc_num].append((file_path, metadata))
        
        # Create new file structure
        base_dir = Path(directory)
        moves = []
        
        for album_key, album_data in album_groups.items():
            total_discs = album_data['total_discs']
            
            for disc_num, disc_files in album_data['discs'].items():
                for file_path, metadata in disc_files:
                    # Create folder name
                    folder_name = self.formatter.format_folder(metadata, self.folder_format)
                    
                    # If multiple discs, create disc subdirectories
                    if total_discs > 1:
                        folder_path = base_dir / folder_name / f"disc-{disc_num}"
                    else:
                        folder_path = base_dir / folder_name
                    
                    # Create file name
                    file_name = self.formatter.format_track(metadata, self.track_format)
                    file_name += file_path.suffix  # Keep original extension
                    
                    new_path = folder_path / file_name
                    
                    # Avoid moving to the same location
                    if file_path.resolve() != new_path.resolve():
                        moves.append((file_path, new_path))
        
        # Display planned moves
        console.print(f"\n[bold cyan]Planned restructuring ({len(moves)} files):[/bold cyan]\n")
        
        for old_path, new_path in moves[:10]:  # Show first 10
            console.print(f"[yellow]{old_path.relative_to(base_dir)}[/yellow]")
            console.print(f"  → [green]{new_path.relative_to(base_dir)}[/green]\n")
        
        if len(moves) > 10:
            console.print(f"[dim]... and {len(moves) - 10} more files[/dim]\n")
        
        if dry_run:
            console.print("[yellow]Dry run - no changes made[/yellow]")
            return
        
        # Confirm before proceeding
        if not Confirm.ask("\n[bold]Proceed with restructuring?[/bold]", default=True):
            console.print("[yellow]Restructuring cancelled[/yellow]")
            return
        
        # Perform the moves
        console.print("\n[bold]Restructuring files...[/bold]\n")
        
        success_count = 0
        error_count = 0
        
        for old_path, new_path in moves:
            try:
                # Create target directory
                new_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Move the file
                shutil.move(str(old_path), str(new_path))
                success_count += 1
                console.print(f"[green]✓[/green] {old_path.name} → {new_path.relative_to(base_dir)}")
                
            except Exception as e:
                error_count += 1
                console.print(f"[red]✗[/red] Failed to move {old_path.name}: {e}")
                logger.error(f"Error moving {old_path} to {new_path}: {e}")
        
        # Clean up empty directories
        console.print("\n[bold]Cleaning up empty directories...[/bold]")
        self._cleanup_empty_dirs(base_dir)
        
        # Summary
        console.print(f"\n[bold green]Restructuring complete![/bold green]")
        console.print(f"Successfully moved: {success_count} files")
        if error_count > 0:
            console.print(f"[yellow]Errors: {error_count} files[/yellow]")
    
    def _cleanup_empty_dirs(self, directory: Path) -> None:
        """
        Remove empty directories recursively.
        
        Args:
            directory: Directory to clean up
        """
        for dirpath, dirnames, filenames in os.walk(str(directory), topdown=False):
            dir_path = Path(dirpath)
            
            # Skip the base directory
            if dir_path == directory:
                continue
            
            # Check if directory is empty
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    console.print(f"[dim]Removed empty directory: {dir_path.relative_to(directory)}[/dim]")
            except Exception as e:
                logger.debug(f"Could not remove directory {dir_path}: {e}")

