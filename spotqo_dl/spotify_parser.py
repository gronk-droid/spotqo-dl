"""
Spotify URL parsing and metadata extraction.
"""

import logging
import re
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)


class SpotifyParser:
    """Parser for Spotify URLs and metadata extraction."""
    
    def __init__(self, client_id: str, client_secret: str):
        """Initialize Spotify parser with API credentials."""
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = None
    
    @property
    def client(self) -> spotipy.Spotify:
        """Get Spotify client, initializing if needed."""
        if self._client is None:
            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            self._client = spotipy.Spotify(auth_manager=auth_manager)
        return self._client
    
    def parse_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Parse Spotify URL and extract metadata.
        
        Args:
            url: Spotify URL (track, album, or playlist)
            
        Returns:
            Dictionary with metadata and tracks, or None if parsing fails
        """
        try:
            url_type, item_id = self._extract_id_from_url(url)
            if not item_id:
                logger.error(f"Could not extract ID from URL: {url}")
                return None
            
            if url_type == "track":
                return self._parse_track(item_id)
            elif url_type == "album":
                return self._parse_album(item_id)
            elif url_type == "playlist":
                return self._parse_playlist(item_id)
            else:
                logger.error(f"Unsupported URL type: {url_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {e}")
            return None
    
    def _extract_id_from_url(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Extract type and ID from Spotify URL."""
        # Handle different Spotify URL formats
        patterns = [
            r'https?://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)',
            r'spotify:(track|album|playlist):([a-zA-Z0-9]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)
        
        return None, None
    
    def _parse_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Parse a single track."""
        try:
            track = self.client.track(track_id)
            if not track:
                return None
            
            return {
                "type": "track",
                "name": track["name"],
                "artist": track["artists"][0]["name"],
                "album": track["album"]["name"],
                "tracks": [self._track_to_dict(track)]
            }
        except Exception as e:
            logger.error(f"Error parsing track {track_id}: {e}")
            return None
    
    def _parse_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Parse an album and its tracks."""
        try:
            album = self.client.album(album_id)
            if not album:
                return None
            
            # Get all tracks from the album
            tracks = []
            results = self.client.album_tracks(album_id)
            
            while results:
                for track in results["items"]:
                    if not track.get("is_local", False):
                        tracks.append(self._track_to_dict(track, album))
                
                results = self.client.next(results) if results["next"] else None
            
            return {
                "type": "album",
                "name": album["name"],
                "artist": album["artists"][0]["name"],
                "tracks": tracks
            }
        except Exception as e:
            logger.error(f"Error parsing album {album_id}: {e}")
            return None
    
    def _parse_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Parse a playlist and its tracks."""
        try:
            playlist = self.client.playlist(playlist_id)
            if not playlist:
                return None
            
            # Get all tracks from the playlist
            tracks = []
            results = self.client.playlist_items(playlist_id)
            
            while results:
                for item in results["items"]:
                    track = item.get("track")
                    if track and not track.get("is_local", False) and track.get("type") == "track":
                        tracks.append(self._track_to_dict(track))
                
                results = self.client.next(results) if results["next"] else None
            
            # Add playlist context to each track
            playlist_name = playlist["name"]
            for i, track in enumerate(tracks, 1):
                track["playlist_title"] = playlist_name
                track["playlist_number"] = i
            
            return {
                "type": "playlist",
                "name": playlist["name"],
                "artist": playlist["owner"]["display_name"],
                "tracks": tracks
            }
        except Exception as e:
            logger.error(f"Error parsing playlist {playlist_id}: {e}")
            return None
    
    def _track_to_dict(self, track: Dict[str, Any], album: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convert Spotify track object to our format."""
        album_info = album or track.get("album", {})
        
        return {
            "name": track["name"],
            "artist": track["artists"][0]["name"],
            "album": album_info.get("name", ""),
            "album_artist": album_info.get("artists", [{}])[0].get("name", track["artists"][0]["name"]),
            "track_number": track.get("track_number", 0),
            "duration": track.get("duration_ms", 0) // 1000,  # Convert to seconds
            "year": album_info.get("release_date", "")[:4] if album_info.get("release_date") else "",
            "isrc": track.get("external_ids", {}).get("isrc", ""),
            "spotify_id": track["id"],
            "spotify_url": track["external_urls"]["spotify"]
        }
