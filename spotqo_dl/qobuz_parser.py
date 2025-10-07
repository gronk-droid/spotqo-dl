"""
Qobuz URL parsing and metadata extraction.
"""

import logging
import re
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class QobuzParser:
    """Parser for Qobuz URLs and metadata extraction."""
    
    def __init__(self):
        """Initialize Qobuz parser."""
        pass
    
    def parse_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Parse Qobuz URL and extract metadata.
        
        Args:
            url: Qobuz URL (track, album, or playlist)
            
        Returns:
            Dictionary with metadata and tracks, or None if parsing fails
        """
        try:
            url_type, item_id = self._extract_id_from_url(url)
            if not item_id:
                logger.error(f"Could not extract ID from URL: {url}")
                return None
            
            if url_type == "track":
                return self._parse_track(item_id, url)
            elif url_type == "album":
                return self._parse_album(item_id, url)
            elif url_type == "playlist":
                return self._parse_playlist(item_id, url)
            else:
                logger.error(f"Unsupported URL type: {url_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {e}")
            return None
    
    def _extract_id_from_url(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Extract type and ID from Qobuz URL."""
        # Handle different Qobuz URL formats
        patterns = [
            r'https?://www\.qobuz\.com/[^/]+/[^/]+/([^/]+)/([a-zA-Z0-9]+)',
            r'https?://www\.qobuz\.com/[^/]+/[^/]+/([^/]+)/([a-zA-Z0-9]+)/',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                # Extract the type from the URL path
                url_parts = url.split('/')
                if 'album' in url_parts:
                    return 'album', match.group(2)
                elif 'track' in url_parts:
                    return 'track', match.group(2)
                elif 'playlist' in url_parts:
                    return 'playlist', match.group(2)
                else:
                    # Default to album for most Qobuz URLs
                    return 'album', match.group(2)
        
        return None, None
    
    def _parse_track(self, track_id: str, url: str) -> Optional[Dict[str, Any]]:
        """Parse a single track from Qobuz URL."""
        try:
            # For Qobuz URLs, we'll return a placeholder structure
            # The actual metadata will be extracted by QobuzDL when downloading
            return {
                "type": "track",
                "name": "Unknown Track",
                "artist": "Unknown Artist", 
                "album": "Unknown Album",
                "tracks": [{
                    "name": "Unknown Track",
                    "artist": "Unknown Artist",
                    "album": "Unknown Album",
                    "track_number": 1,
                    "duration": 0,
                    "year": "",
                    "isrc": "",
                    "qobuz_id": track_id,
                    "qobuz_url": url
                }]
            }
        except Exception as e:
            logger.error(f"Error parsing track {track_id}: {e}")
            return None
    
    def _parse_album(self, album_id: str, url: str) -> Optional[Dict[str, Any]]:
        """Parse an album from Qobuz URL."""
        try:
            # For Qobuz URLs, we'll return a placeholder structure
            # The actual metadata will be extracted by QobuzDL when downloading
            return {
                "type": "album",
                "name": "Unknown Album",
                "artist": "Unknown Artist",
                "tracks": [{
                    "name": "Unknown Track",
                    "artist": "Unknown Artist", 
                    "album": "Unknown Album",
                    "track_number": 1,
                    "duration": 0,
                    "year": "",
                    "isrc": "",
                    "qobuz_id": album_id,
                    "qobuz_url": url
                }]
            }
        except Exception as e:
            logger.error(f"Error parsing album {album_id}: {e}")
            return None
    
    def _parse_playlist(self, playlist_id: str, url: str) -> Optional[Dict[str, Any]]:
        """Parse a playlist from Qobuz URL."""
        try:
            # For Qobuz URLs, we'll return a placeholder structure
            # The actual metadata will be extracted by QobuzDL when downloading
            return {
                "type": "playlist",
                "name": "Unknown Playlist",
                "artist": "Unknown Artist",
                "tracks": [{
                    "name": "Unknown Track",
                    "artist": "Unknown Artist",
                    "album": "Unknown Album", 
                    "track_number": 1,
                    "duration": 0,
                    "year": "",
                    "isrc": "",
                    "qobuz_id": playlist_id,
                    "qobuz_url": url
                }]
            }
        except Exception as e:
            logger.error(f"Error parsing playlist {playlist_id}: {e}")
            return None
