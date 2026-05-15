"""
Qobuz downloader for searching and downloading tracks.
"""

import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

from qobuz_dl.core import QobuzDL
from qobuz_dl.exceptions import AuthenticationError
from .formatter import TrackFormatter
from .utils import titles_match as _titles_match

logger = logging.getLogger(__name__)


class QobuzDownloader:
    """Downloader that uses Qobuz to find and download tracks."""
    
    def __init__(self, email: str, password: str, output_dir: str, quality: int = 6,
                 track_format: str = "{artist} - {title}", folder_format: str = "{artist} - {album} ({year})"):
        """Initialize Qobuz downloader."""
        self.email = email
        self.password = password
        self.output_dir = output_dir
        self.quality = quality
        self.track_format = track_format
        self.folder_format = folder_format
        self._qobuz_dl = None
        self.formatter = TrackFormatter()
        
        # Create cache directory for temporary downloads
        self.cache_dir = Path(output_dir) / ".spotqo_cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        # Maps spotify_id → final output path; populated by _rename_single_file()
        self._downloaded_file_paths: Dict[str, Path] = {}
        
    @property
    def qobuz_dl(self) -> QobuzDL:
        """Get QobuzDL instance, initializing if needed."""
        if self._qobuz_dl is None:
            instance = QobuzDL(
                directory=str(self.cache_dir),
                quality=self.quality,
                embed_art=True,
                no_cover=False,
                quality_fallback=True
            )
            try:
                instance.get_tokens()
                instance.initialize_client(self.email, self.password,
                                           instance.app_id, instance.secrets)
            except AuthenticationError as exc:
                config_path = "~/.config/spotqo-dl/config.ini"
                raise AuthenticationError(
                    f"Qobuz authentication failed.\n"
                    f"Qobuz no longer accepts email/password login via the API.\n"
                    f"You need to store a user_auth_token as the 'password' in {config_path}.\n\n"
                    "How to get your token:\n"
                    "  1. Log into play.qobuz.com in Firefox or Chrome\n"
                    "  2. Open DevTools (F12) → Network tab\n"
                    "  3. Play any track — a POST to 'user/login' will appear\n"
                    "  4. Click that request → Response tab → copy 'user_auth_token'\n"
                    "     (In Chrome/Safari you can also find it under\n"
                    "      Application → Local Storage → play.qobuz.com → 'localuser')\n"
                    f"  5. Open {config_path} and set:\n"
                    "        [qobuz]\n"
                    "        email    = your@email.com\n"
                    "        password = <paste the long token here>"
                ) from exc
            self._qobuz_dl = instance
        return self._qobuz_dl
    
    def download_tracks(self, tracks: List[Dict[str, Any]]) -> None:
        """
        Download tracks by first finding the album on Qobuz, then downloading individual tracks.
        
        Tracks are grouped by (album_artist, album) so that multi-artist playlists are handled
        correctly: each album group is searched and downloaded independently rather than letting
        the most-common-artist heuristic apply to the whole batch.

        Successfully downloaded tracks are recorded in ``self._downloaded_file_paths``
        (keyed by spotify_id) so that the caller can detect which tracks were missed and
        generate a merged playlist after optionally running a Tidal fallback.
        
        Args:
            tracks: List of track dictionaries with metadata
        """
        logger.info(f"Starting download of {len(tracks)} tracks")
        
        if not tracks:
            logger.warning("No tracks to download")
            return
        
        # Group by (album_artist, album) so multi-artist playlists are handled correctly.
        # Each group is then processed as its own album unit.
        from collections import defaultdict
        groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
        for track in tracks:
            artist_key = track.get('album_artist') or track.get('artist', '')
            album_key = track.get('album', '')
            groups[(artist_key, album_key)].append(track)
        
        logger.info(f"Grouped {len(tracks)} tracks into {len(groups)} album group(s)")
        
        for (artist, album_name), group_tracks in groups.items():
            logger.info(f"Looking for album: {artist} - {album_name}")
            album_info = self._extract_album_context(group_tracks)
            
            qobuz_album = self._search_album(album_info)
            if qobuz_album:
                logger.info(f"Found album on Qobuz: {qobuz_album.get('title', 'Unknown')}")
                downloaded_tracks = self._download_album_tracks(qobuz_album, group_tracks)
                
                if downloaded_tracks is not None and len(downloaded_tracks) < len(group_tracks):
                    downloaded_spotify_ids = {t.get('spotify_id') for t in downloaded_tracks}
                    missing_tracks = [t for t in group_tracks if t.get('spotify_id') not in downloaded_spotify_ids]
                    logger.info(f"Some tracks were not found in the album. Trying individual search for {len(missing_tracks)} remaining tracks...")
                    self._download_tracks_individually(missing_tracks)
            else:
                logger.warning(f"Could not find album '{album_name}' by '{artist}' on Qobuz")
                logger.info("Falling back to individual track search...")
                self._download_tracks_individually(group_tracks)
    
    def download_qobuz_url(self, url: str) -> None:
        """
        Download directly from a Qobuz URL.
        
        Args:
            url: Qobuz URL to download from
        """
        logger.info(f"Starting download from Qobuz URL: {url}")
        
        try:
            # Use QobuzDL to download directly from the URL
            self.qobuz_dl.handle_url(url)
            
            # Find and rename all downloaded files
            self._rename_downloaded_files_from_qobuz()
            
            logger.info("Download completed successfully")
            
        except Exception as e:
            logger.error(f"Error downloading from Qobuz URL {url}: {e}")
            raise
    
    def _extract_album_context(self, tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extract album context from tracks for validation.
        
        Args:
            tracks: List of tracks from the same album
            
        Returns:
            Album context information
        """
        if not tracks:
            return {}
            
        # Get the most common artist and album from the tracks
        artists = [track.get('artist', '') for track in tracks if track.get('artist')]
        albums = [track.get('album', '') for track in tracks if track.get('album')]
        
        # Find the most common artist and album
        from collections import Counter
        most_common_artist = Counter(artists).most_common(1)[0][0] if artists else ''
        most_common_album = Counter(albums).most_common(1)[0][0] if albums else ''
        
        return {
            'expected_artist': most_common_artist,
            'expected_album': most_common_album,
            'total_tracks': len(tracks)
        }
    
    def _search_album(self, album_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Search for an album on Qobuz using lucky search (like qobuz-dl lucky).
        
        Args:
            album_info: Album information extracted from tracks
            
        Returns:
            Qobuz album data if found and validated, None otherwise
        """
        try:
            artist = album_info.get('expected_artist', '')
            album = album_info.get('expected_album', '')
            
            if not artist or not album:
                logger.warning("Missing artist or album information for search")
                return None
            
            # Use lucky search approach - search for the album and take the first result
            query = f"{artist} {album}"
            logger.info(f"Searching for album (lucky mode): '{query}'")
            
            results = self.qobuz_dl.search_by_type(query, "album", limit=5)
            
            if not results:
                logger.warning(f"No album results found for: {query}")
                return None
            
            # Try to find a matching album from the results
            for result in results:
                if self._validate_album_match(result, artist, album):
                    # Parse the text field to extract artist and album for logging
                    text = result.get('text', '')
                    result_artist = ''
                    result_album = ''
                    
                    if text:
                        parts = text.split(' - ')
                        if len(parts) >= 2:
                            result_artist = parts[0].strip()
                            result_album = parts[1].strip()
                    
                    logger.info(f"Found validated album: '{result_album}' by '{result_artist}'")
                    return result
            
            # If no validated match found, log the first result for debugging
            first_result = results[0]
            text = first_result.get('text', '')
            if text:
                parts = text.split(' - ')
                if len(parts) >= 2:
                    result_artist = parts[0].strip()
                    result_album = parts[1].strip()
                    logger.warning(f"No validated album match found. First result was: '{result_album}' by '{result_artist}'")
            
            logger.warning(f"Could not find a matching album for: {artist} - {album}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for album {album_info.get('expected_album')}: {e}")
            return None
    
    def _download_album_tracks(self, qobuz_album: Dict[str, Any], spotify_tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Download tracks from a specific Qobuz album.
        
        Args:
            qobuz_album: Album found on Qobuz
            spotify_tracks: Original tracks from Spotify
            
        Returns:
            List of tracks that were successfully downloaded
        """
        try:
            album_url = qobuz_album.get('url', '')
            if not album_url:
                logger.error("No URL found in Qobuz album result")
                return []
            
            logger.info(f"Downloading album from URL: {album_url}")
            
            # Use QobuzDL to download the entire album
            self.qobuz_dl.handle_url(album_url)
            
            # Rename all downloaded files using Spotify metadata and return successfully processed tracks
            return self._rename_album_files(spotify_tracks)
            
        except Exception as e:
            logger.error(f"Error downloading album {qobuz_album.get('title', 'Unknown')}: {e}")
            raise
    
    def _rename_album_files(self, spotify_tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Rename downloaded album files using Spotify metadata.
        
        Scans ALL files in the cache (not just the top-N by mtime) to allow correct matching
        when the downloaded album contains more tracks than the requested subset.  Each Spotify
        track ID can only be matched once, and the positional fallback is limited to tracks that
        were not already title-matched, so surplus album files are never incorrectly renamed.
        
        Args:
            spotify_tracks: Original tracks from Spotify
            
        Returns:
            List of tracks that were successfully processed
        """
        try:
            # Find all recently downloaded audio files in cache directory
            audio_extensions = ['.mp3', '.flac', '.m4a', '.wav']
            downloaded_files = []
            
            for ext in audio_extensions:
                downloaded_files.extend(self.cache_dir.rglob(f"*{ext}"))
            
            if not downloaded_files:
                logger.warning("No audio files found to rename")
                return []
            
            # Sort by modification time (newest first) for deterministic ordering
            downloaded_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            
            # Scan ALL downloaded files for title matches — do not cap at len(spotify_tracks).
            # An album download may contain more tracks than the requested subset.
            matched_files = []
            unmatched_files = []
            matched_spotify_ids: set = set()
            successfully_processed = []
            
            import subprocess
            for audio_file in downloaded_files:
                matched = False
                try:
                    result = subprocess.run(
                        ['metaflac', '--show-tag=TITLE', str(audio_file)],
                        capture_output=True, text=True, check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        original_title = result.stdout.strip().replace('TITLE=', '')
                        for track in spotify_tracks:
                            sid = track.get('spotify_id')
                            if sid not in matched_spotify_ids and \
                               self._titles_match(original_title, track.get('name', '')):
                                matched_files.append((audio_file, track))
                                matched_spotify_ids.add(sid)
                                matched = True
                                break
                except Exception:
                    pass
                
                if not matched:
                    unmatched_files.append(audio_file)
            
            # Rename title-matched files
            for audio_file, track in matched_files:
                self._rename_single_file(audio_file, track)
                successfully_processed.append(track)
            
            # Positional fallback — only for Spotify tracks that were NOT already matched.
            # Surplus downloaded files (extra album tracks not in the playlist) are skipped.
            unmatched_tracks = sorted(
                [t for t in spotify_tracks if t.get('spotify_id') not in matched_spotify_ids],
                key=lambda x: x.get('track_number', 0),
            )
            for i, audio_file in enumerate(unmatched_files):
                if i < len(unmatched_tracks):
                    self._rename_single_file(audio_file, unmatched_tracks[i])
                    successfully_processed.append(unmatched_tracks[i])
                # else: extra album file not in our playlist — leave for cache cleanup
            
            # Clean up cache directory after processing
            self._cleanup_cache()
            
            logger.info(f"Successfully processed {len(successfully_processed)} tracks from album")
            return successfully_processed
                
        except Exception as e:
            logger.error(f"Error renaming album files: {e}")
            return []
    
    def _titles_match(self, title1: str, title2: str) -> bool:
        """Delegate to the shared titles_match helper."""
        return _titles_match(title1, title2)
    
    def _validate_track_match(self, qobuz_track: Dict[str, Any], spotify_track: Dict[str, Any]) -> bool:
        """
        Validate that a Qobuz track matches the requested Spotify track.
        
        Args:
            qobuz_track: Track data from Qobuz search result
            spotify_track: Original track data from Spotify
            
        Returns:
            True if tracks match, False otherwise
        """
        try:
            # Extract artist and title from Qobuz result
            text = qobuz_track.get('text', '')
            if not text:
                logger.warning("No text field in Qobuz track result")
                return False
            
            # Parse the text field to extract artist and title
            # Format is typically: "Artist - Title - Duration [Quality]"
            parts = text.split(' - ')
            if len(parts) < 2:
                logger.warning(f"Could not parse Qobuz track text: {text}")
                return False
            
            qobuz_artist = parts[0].strip()
            qobuz_title = parts[1].strip()
            
            # Get Spotify track info
            spotify_artist = spotify_track.get('artist', '')
            spotify_title = spotify_track.get('name', '')
            
            # Check if artist and title match
            artist_match = self._titles_match(qobuz_artist, spotify_artist)
            title_match = self._titles_match(qobuz_title, spotify_title)
            
            if artist_match and title_match:
                logger.info(f"Track validation passed: '{qobuz_title}' by '{qobuz_artist}' matches '{spotify_title}' by '{spotify_artist}'")
                return True
            else:
                logger.warning(f"Track validation failed: Qobuz '{qobuz_title}' by '{qobuz_artist}' does not match Spotify '{spotify_title}' by '{spotify_artist}'")
                return False
                
        except Exception as e:
            logger.error(f"Error validating track match: {e}")
            return False
    
    def _validate_album_match(self, qobuz_album: Dict[str, Any], expected_artist: str, expected_album: str) -> bool:
        """
        Validate that a Qobuz album matches the expected artist and album.
        
        Args:
            qobuz_album: Album data from Qobuz search result
            expected_artist: Expected artist name from Spotify
            expected_album: Expected album name from Spotify
            
        Returns:
            True if album matches, False otherwise
        """
        try:
            # Extract artist and album from Qobuz result
            text = qobuz_album.get('text', '')
            if not text:
                logger.warning("No text field in Qobuz album result")
                return False
            
            # Parse the text field to extract artist and album
            # Format is typically: "Artist - Album - Duration [Quality]"
            parts = text.split(' - ')
            if len(parts) < 2:
                logger.warning(f"Could not parse Qobuz album text: {text}")
                return False
            
            qobuz_artist = parts[0].strip()
            qobuz_album_name = parts[1].strip()
            
            # Check if artist and album match
            artist_match = self._titles_match(qobuz_artist, expected_artist)
            album_match = self._titles_match(qobuz_album_name, expected_album)
            
            if artist_match and album_match:
                logger.info(f"Album validation passed: '{qobuz_album_name}' by '{qobuz_artist}' matches '{expected_album}' by '{expected_artist}'")
                return True
            else:
                logger.warning(f"Album validation failed: Qobuz '{qobuz_album_name}' by '{qobuz_artist}' does not match expected '{expected_album}' by '{expected_artist}'")
                return False
                
        except Exception as e:
            logger.error(f"Error validating album match: {e}")
            return False
    
    def _rename_single_file(self, audio_file: Path, track: Dict[str, Any]) -> Optional[Path]:
        """
        Rename a single audio file using track metadata.
        
        Args:
            audio_file: Path to the audio file
            track: Track metadata from Spotify
            
        Returns:
            The final Path the file was moved to, or None on error.
        """
        try:
            # Parse the format string to extract folder and file parts
            if "/" in self.track_format:
                # Split the format string into folder and file parts
                folder_part, file_part = self.track_format.rsplit("/", 1)
                folder_name = self.formatter.format_folder(track, folder_part)
                new_filename = self.formatter.format_track(track, file_part)
            else:
                # Use the default folder format and the track format as filename
                folder_name = self.formatter.format_folder(track, self.folder_format)
                new_filename = self.formatter.format_track(track, self.track_format)
            
            # Check if we need to add disc folder for multi-disc albums
            disc_number = track.get('disc_number', 1)
            if disc_number > 1:
                # Add disc folder for multi-disc albums
                disc_folder = f"disc-{disc_number}"
                new_path = Path(self.output_dir) / folder_name / disc_folder / new_filename
            else:
                new_path = Path(self.output_dir) / folder_name / new_filename
            
            # Create the directory if it doesn't exist
            new_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Determine the correct extension
            ext = audio_file.suffix
            final_path = new_path.with_suffix(ext)
            
            # Move and rename the audio file
            shutil.move(str(audio_file), str(final_path))
            logger.info(f"Renamed {audio_file.name} to {final_path.name}")
            
            # Record the output path keyed by spotify_id for m3u generation
            spotify_id = track.get('spotify_id')
            if spotify_id:
                self._downloaded_file_paths[spotify_id] = final_path
            
            # Update metadata tags to match Spotify track order
            self._update_metadata_tags(final_path, track)
            
            # Also move any associated files (cover art, etc.)
            for related_file in audio_file.parent.glob(f"{audio_file.stem}.*"):
                if related_file != audio_file:
                    related_ext = related_file.suffix
                    if disc_number > 1:
                        related_final = Path(self.output_dir) / folder_name / disc_folder / (new_filename + related_ext)
                    else:
                        related_final = Path(self.output_dir) / folder_name / (new_filename + related_ext)
                    shutil.move(str(related_file), str(related_final))
                    logger.info(f"Moved {related_file.name} to {related_final.name}")
            
            # Clean up empty directories
            try:
                if audio_file.parent.exists() and not any(audio_file.parent.iterdir()):
                    audio_file.parent.rmdir()
                    logger.debug(f"Removed empty directory: {audio_file.parent}")
            except OSError:
                pass  # Directory not empty or other error, ignore
            
            return final_path
                
        except Exception as e:
            logger.error(f"Error renaming file {audio_file.name}: {e}")
            return None
    
    def _update_metadata_tags(self, audio_file: Path, track: Dict[str, Any]) -> None:
        """
        Update metadata tags in the audio file to match Spotify track order.
        
        Args:
            audio_file: Path to the audio file
            track: Track metadata from Spotify
        """
        try:
            if audio_file.suffix.lower() == '.flac':
                # Use metaflac to update FLAC metadata
                import subprocess
                
                # Remove old track number tags first, then set new one
                track_number = track.get('track_number', 1)
                subprocess.run(['metaflac', '--remove-tag=TRACKNUMBER', str(audio_file)], 
                             check=False, capture_output=True)  # Don't fail if no tag exists
                subprocess.run(['metaflac', f'--set-tag=TRACKNUMBER={track_number}', str(audio_file)], 
                             check=True, capture_output=True)
                
                # Update other metadata if available (remove old tags first)
                if track.get('artist'):
                    subprocess.run(['metaflac', '--remove-tag=ARTIST', str(audio_file)], 
                                 check=False, capture_output=True)
                    subprocess.run(['metaflac', f'--set-tag=ARTIST={track["artist"]}', str(audio_file)], 
                                 check=True, capture_output=True)
                
                if track.get('name'):
                    subprocess.run(['metaflac', '--remove-tag=TITLE', str(audio_file)], 
                                 check=False, capture_output=True)
                    subprocess.run(['metaflac', f'--set-tag=TITLE={track["name"]}', str(audio_file)], 
                                 check=True, capture_output=True)
                
                if track.get('album'):
                    subprocess.run(['metaflac', '--remove-tag=ALBUM', str(audio_file)], 
                                 check=False, capture_output=True)
                    subprocess.run(['metaflac', f'--set-tag=ALBUM={track["album"]}', str(audio_file)], 
                                 check=True, capture_output=True)
                
                if track.get('year'):
                    subprocess.run(['metaflac', '--remove-tag=DATE', str(audio_file)], 
                                 check=False, capture_output=True)
                    subprocess.run(['metaflac', f'--set-tag=DATE={track["year"]}', str(audio_file)], 
                                 check=True, capture_output=True)
                
                logger.debug(f"Updated metadata for {audio_file.name}: track {track_number}")
                
        except Exception as e:
            logger.warning(f"Could not update metadata for {audio_file.name}: {e}")
            # Don't raise the exception as the file was successfully renamed
    
    def _download_tracks_individually(self, tracks: List[Dict[str, Any]]) -> None:
        """
        Fallback method to download tracks individually.
        
        Args:
            tracks: List of track dictionaries with metadata
        """
        logger.info("Using individual track search as fallback")
        
        for i, track in enumerate(tracks, 1):
            logger.info(f"Processing track {i}/{len(tracks)}: {track['artist']} - {track['name']}")
            
            # Search for the track on Qobuz
            qobuz_track = self._search_track(track)
            if qobuz_track:
                try:
                    # Download the track
                    self._download_track(qobuz_track, track)
                    logger.info(f"Successfully downloaded: {track['artist']} - {track['name']}")
                except Exception as e:
                    logger.error(f"Failed to download {track['artist']} - {track['name']}: {e}")
            else:
                logger.warning(f"Could not find track on Qobuz: {track['artist']} - {track['name']}")
    
    
    def _search_track(self, track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Search for a track on Qobuz using lucky search (like qobuz-dl lucky).
        
        Args:
            track: Track metadata from Spotify
            
        Returns:
            Qobuz track data if found and validated, None otherwise
        """
        try:
            # Search for the track using lucky approach
            query = f"{track['artist']} {track['name']}"
            logger.info(f"Searching for track (lucky mode): '{query}'")
            
            results = self.qobuz_dl.search_by_type(query, "track", limit=5)
            
            if not results:
                logger.warning(f"No track results found for: {query}")
                return None
            
            # Try to find a matching track from the results
            for result in results:
                if self._validate_track_match(result, track):
                    # Parse the text field to extract artist and title for logging
                    text = result.get('text', '')
                    result_artist = ''
                    result_title = ''
                    
                    if text:
                        parts = text.split(' - ')
                        if len(parts) >= 2:
                            result_artist = parts[0].strip()
                            result_title = parts[1].strip()
                    
                    logger.info(f"Found validated track: '{result_title}' by '{result_artist}'")
                    return result
            
            # If no validated match found, log the first result for debugging
            first_result = results[0]
            text = first_result.get('text', '')
            if text:
                parts = text.split(' - ')
                if len(parts) >= 2:
                    result_artist = parts[0].strip()
                    result_title = parts[1].strip()
                    logger.warning(f"No validated match found. First result was: '{result_title}' by '{result_artist}'")
            
            logger.warning(f"Could not find a matching track for: {track['artist']} - {track['name']}")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for track {track['name']}: {e}")
            return None
    
    
    def _download_track(self, qobuz_track: Dict[str, Any], spotify_track: Dict[str, Any]) -> None:
        """
        Download a track from Qobuz and rename it using Spotify metadata.
        
        Args:
            qobuz_track: Track data from Qobuz
            spotify_track: Original track data from Spotify
        """
        try:
            # Extract track ID from Qobuz result
            track_url = qobuz_track.get('url', '')
            if not track_url:
                raise ValueError("No URL found in Qobuz track result")
            
            # Use QobuzDL to download the track
            self.qobuz_dl.handle_url(track_url)
            
            # Always rename files using Spotify metadata and format strings
            self._rename_downloaded_files(spotify_track)
            
        except Exception as e:
            logger.error(f"Error downloading track {spotify_track['name']}: {e}")
            raise
    
    def _rename_downloaded_files(self, track: Dict[str, Any]) -> None:
        """
        Rename the most recently downloaded file using Spotify metadata.
        
        Delegates to _rename_single_file() so that the output path is recorded
        in _downloaded_file_paths and appears in any generated m3u playlist.
        
        Args:
            track: Track metadata from Spotify
        """
        try:
            audio_extensions = ['.mp3', '.flac', '.m4a', '.wav']
            downloaded_files = []
            
            for ext in audio_extensions:
                downloaded_files.extend(self.cache_dir.rglob(f"*{ext}"))
            
            if not downloaded_files:
                logger.warning(f"No audio files found to rename for {track['name']}")
                return
            
            # Pick the most recently modified file — the one we just downloaded
            downloaded_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            audio_file = downloaded_files[0]
            
            # Delegate to _rename_single_file so the path is tracked for m3u generation
            self._rename_single_file(audio_file, track)
            
            # Clean up any remaining cache files
            self._cleanup_cache()
            
        except Exception as e:
            logger.error(f"Error renaming files for {track['name']}: {e}")
            # Don't raise the exception as the download was successful
    
    def _rename_downloaded_files_from_qobuz(self) -> None:
        """
        Rename downloaded files from Qobuz URL using their original metadata.
        """
        try:
            # Find all recently downloaded audio files in cache directory
            audio_extensions = ['.mp3', '.flac', '.m4a', '.wav']
            downloaded_files = []
            
            for ext in audio_extensions:
                pattern = f"*{ext}"
                downloaded_files.extend(self.cache_dir.rglob(pattern))
            
            if not downloaded_files:
                logger.warning("No audio files found to rename")
                return
            
            # Sort by modification time to get the most recent files
            downloaded_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            
            # Process each downloaded file
            for audio_file in downloaded_files:
                try:
                    # Extract metadata from the downloaded file
                    track_metadata = self._extract_metadata_from_file(audio_file)
                    
                    if track_metadata:
                        # Parse the format string to extract folder and file parts
                        if "/" in self.track_format:
                            # Split the format string into folder and file parts
                            folder_part, file_part = self.track_format.rsplit("/", 1)
                            folder_name = self.formatter.format_folder(track_metadata, folder_part)
                            new_filename = self.formatter.format_track(track_metadata, file_part)
                        else:
                            # Use the default folder format and the track format as filename
                            folder_name = self.formatter.format_folder(track_metadata, self.folder_format)
                            new_filename = self.formatter.format_track(track_metadata, self.track_format)
                        
                        # Check if we need to add disc folder for multi-disc albums
                        disc_number = track_metadata.get('disc_number', 1)
                        if disc_number > 1:
                            # Add disc folder for multi-disc albums
                            disc_folder = f"disc-{disc_number}"
                            new_path = Path(self.output_dir) / folder_name / disc_folder / new_filename
                        else:
                            new_path = Path(self.output_dir) / folder_name / new_filename
                        
                        # Create the directory if it doesn't exist
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        
                        # Determine the correct extension
                        ext = audio_file.suffix
                        final_path = new_path.with_suffix(ext)
                        
                        # Move and rename the audio file
                        shutil.move(str(audio_file), str(final_path))
                        logger.info(f"Renamed {audio_file.name} to {final_path.name}")
                        
                        # Also move any associated files (cover art, etc.)
                        for related_file in audio_file.parent.glob(f"{audio_file.stem}.*"):
                            if related_file != audio_file:
                                related_ext = related_file.suffix
                                if disc_number > 1:
                                    related_final = Path(self.output_dir) / folder_name / disc_folder / (new_filename + related_ext)
                                else:
                                    related_final = Path(self.output_dir) / folder_name / (new_filename + related_ext)
                                shutil.move(str(related_file), str(related_final))
                                logger.info(f"Moved {related_file.name} to {related_final.name}")
                    else:
                        # If we can't extract metadata, use the original filename
                        new_path = Path(self.output_dir) / audio_file.name
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(audio_file), str(new_path))
                        logger.info(f"Moved {audio_file.name} to {new_path}")
                        
                except Exception as e:
                    logger.error(f"Error processing file {audio_file.name}: {e}")
                    # Move the file anyway to avoid losing it
                    new_path = Path(self.output_dir) / audio_file.name
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(audio_file), str(new_path))
            
            # Clean up cache directory after processing
            self._cleanup_cache()
            
        except Exception as e:
            logger.error(f"Error renaming files from Qobuz: {e}")
    
    def _extract_metadata_from_file(self, audio_file: Path) -> Optional[Dict[str, Any]]:
        """
        Extract metadata from an audio file.
        
        Args:
            audio_file: Path to the audio file
            
        Returns:
            Dictionary with track metadata, or None if extraction fails
        """
        try:
            if audio_file.suffix.lower() == '.flac':
                # Use metaflac to extract FLAC metadata
                import subprocess
                
                metadata = {}
                
                # Extract various metadata fields
                tags = ['TITLE', 'ARTIST', 'ALBUM', 'TRACKNUMBER', 'DISCNUMBER', 'DATE']
                for tag in tags:
                    result = subprocess.run(['metaflac', f'--show-tag={tag}', str(audio_file)], 
                                          capture_output=True, text=True, check=False)
                    if result.returncode == 0 and result.stdout.strip():
                        value = result.stdout.strip().replace(f'{tag}=', '')
                        if tag == 'TRACKNUMBER':
                            try:
                                metadata['track_number'] = int(value)
                            except ValueError:
                                metadata['track_number'] = 1
                        elif tag == 'DISCNUMBER':
                            try:
                                metadata['disc_number'] = int(value)
                            except ValueError:
                                metadata['disc_number'] = 1
                        elif tag == 'DATE':
                            metadata['year'] = value[:4] if value else ''
                        else:
                            metadata[tag.lower()] = value
                
                # Set defaults for missing fields
                metadata.setdefault('name', metadata.get('title', 'Unknown Track'))
                metadata.setdefault('artist', 'Unknown Artist')
                metadata.setdefault('album', 'Unknown Album')
                metadata.setdefault('track_number', 1)
                metadata.setdefault('disc_number', 1)
                metadata.setdefault('year', '')
                metadata.setdefault('duration', 0)
                metadata.setdefault('isrc', '')
                
                return metadata
            else:
                # For other formats, return basic metadata
                return {
                    'name': audio_file.stem,
                    'artist': 'Unknown Artist',
                    'album': 'Unknown Album',
                    'track_number': 1,
                    'year': '',
                    'duration': 0,
                    'isrc': ''
                }
                
        except Exception as e:
            logger.warning(f"Could not extract metadata from {audio_file.name}: {e}")
            return None

    def _cleanup_cache(self) -> None:
        """
        Clean up the cache directory by removing all temporary files and folders.
        """
        try:
            if self.cache_dir.exists():
                # Remove all files and directories in the cache
                for item in self.cache_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                        logger.debug(f"Removed cache file: {item.name}")
                    elif item.is_dir():
                        shutil.rmtree(item)
                        logger.debug(f"Removed cache directory: {item.name}")
                
                logger.debug("Cache directory cleaned up successfully")
        except Exception as e:
            logger.warning(f"Error cleaning up cache directory: {e}")
            # Don't raise the exception as this is cleanup
    
    def __del__(self):
        """Clean up cache directory when downloader is destroyed."""
        try:
            self._cleanup_cache()
        except Exception:
            pass  # Ignore errors during cleanup
