"""
Format string utilities for spotqo-dl.
Based on spot-dl's formatter but simplified for our needs.
"""

import re
from pathlib import Path
from typing import Dict, Any, Optional


class TrackFormatter:
    """Formatter for track metadata using format strings."""
    
    # Available format variables
    AVAILABLE_VARS = [
        "{title}",
        "{artist}",
        "{artists}",
        "{album}",
        "{album-artist}",
        "{year}",
        "{track-number}",
        "{track-number:02d}",
        "{playlist-title}",
        "{playlist-number}",
        "{playlist-number:02d}",
        "{duration}",
        "{isrc}",
        "{spotify-id}",
        "{spotify-url}",
    ]
    
    def __init__(self):
        """Initialize the formatter."""
        pass
    
    def format_track(self, track: Dict[str, Any], template: str) -> str:
        """
        Format a track using the given template.
        
        Args:
            track: Track metadata dictionary
            template: Format string template
            
        Returns:
            Formatted string
        """
        # Create a copy to avoid modifying the original
        formatted = template
        
        # Handle special formatting for track numbers
        if "{track-number:02d}" in template:
            track_num = track.get("track_number", 0)
            if isinstance(track_num, (int, float)):
                formatted = formatted.replace("{track-number:02d}", f"{int(track_num):02d}")
        
        # Handle special formatting for playlist numbers
        if "{playlist-number:02d}" in template:
            playlist_num = track.get("playlist_number", 0)
            if isinstance(playlist_num, (int, float)):
                formatted = formatted.replace("{playlist-number:02d}", f"{int(playlist_num):02d}")
        
        # Replace all variables
        replacements = {
            "{title}": track.get("name", ""),
            "{artist}": track.get("artist", ""),
            "{artists}": track.get("artist", ""),  # For compatibility
            "{album}": track.get("album", ""),
            "{album-artist}": track.get("album_artist", track.get("artist", "")),
            "{year}": track.get("year", ""),
            "{track-number}": str(track.get("track_number", "")),
            "{playlist-title}": track.get("playlist_title", ""),
            "{playlist-number}": str(track.get("playlist_number", "")),
            "{duration}": str(track.get("duration", "")),
            "{isrc}": track.get("isrc", ""),
            "{spotify-id}": track.get("spotify_id", ""),
            "{spotify-url}": track.get("spotify_url", ""),
        }
        
        for var, value in replacements.items():
            if var in formatted:
                formatted = formatted.replace(var, str(value))
        
        return self._sanitize_filename(formatted)
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename for filesystem use with URL-friendly format.
        
        Args:
            filename: Filename to sanitize
            
        Returns:
            Sanitized filename
        """
        # Convert to lowercase
        filename = filename.lower()
        
        # Remove or replace invalid characters, but preserve forward slashes for directory separators
        invalid_chars = r'[<>:"\\|?*]'
        filename = re.sub(invalid_chars, '', filename)
        
        # Replace spaces and underscores with hyphens
        filename = re.sub(r'[\s_]+', '-', filename)
        
        # Remove special characters but keep hyphens and forward slashes
        filename = re.sub(r'[^\w\-\/]', '', filename)
        
        # Remove multiple consecutive hyphens
        filename = re.sub(r'-+', '-', filename)
        
        # Remove leading/trailing hyphens
        filename = filename.strip('-')
        
        # Ensure filename is not empty
        if not filename:
            filename = "untitled"
        
        return filename
    
    def create_path(self, track: Dict[str, Any], folder_template: str, 
                   file_template: str, output_dir: str) -> Path:
        """
        Create a Path object for the track.
        
        Args:
            track: Track metadata
            folder_template: Folder format template
            file_template: File format template
            output_dir: Base output directory
            
        Returns:
            Path object for the track
        """
        # Format folder name
        folder_name = self.format_folder(track, folder_template)
        
        # Format file name
        file_name = self.format_track(track, file_template)
        
        # Create the full path
        full_path = Path(output_dir) / folder_name / file_name
        
        return full_path
    
    def format_folder(self, track: Dict[str, Any], template: str) -> str:
        """
        Format a folder name using the template.
        
        Args:
            track: Track metadata
            template: Folder format template
            
        Returns:
            Formatted folder name
        """
        # Handle the specific case of "{artist}/{album}" format
        if template == "{artist}/{album}":
            artist = self._sanitize_filename(track.get("artist", ""))
            album = self._sanitize_filename(track.get("album", ""))
            return f"{artist}/{album}"
        
        # For other templates, use the regular formatting
        return self.format_track(track, template)
    
    def get_available_variables(self) -> list:
        """Get list of available format variables."""
        return self.AVAILABLE_VARS.copy()
    
    def validate_template(self, template: str) -> bool:
        """
        Validate if a template contains only supported variables.
        
        Args:
            template: Template to validate
            
        Returns:
            True if valid, False otherwise
        """
        # Find all variables in the template
        variables = re.findall(r'\{[^}]+\}', template)
        
        # Check if all variables are supported
        for var in variables:
            if var not in self.AVAILABLE_VARS:
                return False
        
        return True
