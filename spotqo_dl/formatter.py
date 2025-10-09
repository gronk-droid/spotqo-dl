"""
Format string utilities for spotqo-dl.
Based on spot-dl's formatter but simplified for our needs.
"""

import re
from pathlib import Path
from typing import Dict, Any


class TrackFormatter:
    """Formatter for track metadata using format strings."""
    
    # Available format variables
    AVAILABLE_VARS = [
        "{title}",
        "{artist}",
        "{artists}",
        "{album}",
        "{album-base}",
        "{album-version}",
        "{album-artist}",
        "{year}",
        "{track-number}",
        "{track-number:02d}",
        "{disc-number}",
        "{disc-number:02d}",
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
    
    def _parse_album_version(self, album_name: str) -> tuple[str, str]:
        """
        Parse album name to extract base name and version.
        
        Args:
            album_name: Full album name
            
        Returns:
            Tuple of (base_album_name, version)
        """
        if not album_name:
            return "", ""
        
        # Common version indicators
        version_patterns = [
            r'\s+\(Deluxe\)',
            r'\s+\(Deluxe Edition\)',
            r'\s+\(Extended\)',
            r'\s+\(Extended Edition\)',
            r'\s+\(Special Edition\)',
            r'\s+\(Remastered\)',
            r'\s+\(Remaster\)',
            r'\s+\(Anniversary Edition\)',
            r'\s+\(Collector\'s Edition\)',
            r'\s+\(Limited Edition\)',
            r'\s+\(Bonus Track Version\)',
            r'\s+\(Explicit\)',
            r'\s+\(Clean\)',
            r'\s+\(Instrumental\)',
            r'\s+\(Acoustic\)',
            r'\s+\(Live\)',
            r'\s+\(Studio\)',
            r'\s+\(Original\)',
            r'\s+\(Reissue\)',
            r'\s+\(Re-release\)',
        ]
        
        # Check for version patterns
        for pattern in version_patterns:
            match = re.search(pattern, album_name, re.IGNORECASE)
            if match:
                version = match.group(0).strip()
                base_name = album_name[:match.start()].strip()
                return base_name, version
        
        # If no version pattern found, return the original name as base
        return album_name, ""
    
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
        
        # Handle special formatting for disc numbers
        if "{disc-number:02d}" in template:
            disc_num = track.get("disc_number", 1)
            if isinstance(disc_num, (int, float)):
                formatted = formatted.replace("{disc-number:02d}", f"{int(disc_num):02d}")
        
        # Handle special formatting for playlist numbers
        if "{playlist-number:02d}" in template:
            playlist_num = track.get("playlist_number", 0)
            if isinstance(playlist_num, (int, float)):
                formatted = formatted.replace("{playlist-number:02d}", f"{int(playlist_num):02d}")
        
        # Parse album version information
        album_name = track.get("album", "")
        album_base, album_version = self._parse_album_version(album_name)
        
        # Clean up version string for filesystem use
        if album_version:
            # Remove parentheses and convert to lowercase
            clean_version = album_version.strip('()').lower()
            # Replace spaces with hyphens
            clean_version = clean_version.replace(' ', '-')
        else:
            clean_version = ""
        
        # Create a clean album name for filesystem use
        if clean_version:
            clean_album_name = f"{album_base}-{clean_version}"
        else:
            clean_album_name = album_base
        
        # Sanitize individual field values before template replacement
        # This prevents forward slashes in song titles from creating unwanted directories
        def sanitize_field_value(value: str) -> str:
            """Sanitize a single field value, removing forward slashes and other invalid chars."""
            if not value:
                return ""
            # Remove forward slashes and other invalid characters from individual fields
            sanitized = re.sub(r'[<>:"\\|?*/]', '', str(value))
            # Replace spaces and underscores with hyphens
            sanitized = re.sub(r'[\s_]+', '-', sanitized)
            # Remove special characters but keep hyphens and parentheses
            sanitized = re.sub(r'[^\w\-\()]', '', sanitized)
            # Remove multiple consecutive hyphens
            sanitized = re.sub(r'-+', '-', sanitized)
            # Remove leading/trailing hyphens
            sanitized = sanitized.strip('-')
            return sanitized if sanitized else "untitled"
        
        # Replace all variables with sanitized values
        replacements = {
            "{title}": sanitize_field_value(track.get("name", "")),
            "{artist}": sanitize_field_value(track.get("artist", "")),
            "{artists}": sanitize_field_value(track.get("artist", "")),  # For compatibility
            "{album}": sanitize_field_value(clean_album_name),
            "{album-base}": sanitize_field_value(album_base),
            "{album-version}": sanitize_field_value(clean_version),
            "{album-artist}": sanitize_field_value(track.get("album_artist", track.get("artist", ""))),
            "{year}": sanitize_field_value(str(track.get("year", ""))),
            "{track-number}": sanitize_field_value(str(track.get("track_number", ""))),
            "{disc-number}": sanitize_field_value(str(track.get("disc_number", "1"))),
            "{playlist-title}": sanitize_field_value(track.get("playlist_title", "")),
            "{playlist-number}": sanitize_field_value(str(track.get("playlist_number", ""))),
            "{duration}": sanitize_field_value(str(track.get("duration", ""))),
            "{isrc}": sanitize_field_value(track.get("isrc", "")),
            "{spotify-id}": sanitize_field_value(track.get("spotify_id", "")),
            "{spotify-url}": sanitize_field_value(track.get("spotify_url", "")),
        }
        
        for var, value in replacements.items():
            if var in formatted:
                formatted = formatted.replace(var, str(value))
        
        # Final sanitization to handle any remaining issues and convert to lowercase
        return self._final_sanitize(formatted)
    
    def _final_sanitize(self, filename: str) -> str:
        """
        Final sanitization step that converts to lowercase and handles any remaining issues.
        
        Args:
            filename: Filename to sanitize
            
        Returns:
            Sanitized filename
        """
        # Convert to lowercase
        filename = filename.lower()
        
        # Remove multiple consecutive hyphens
        filename = re.sub(r'-+', '-', filename)
        
        # Remove leading/trailing hyphens
        filename = filename.strip('-')
        
        # Ensure filename is not empty
        if not filename:
            filename = "untitled"
        
        return filename
    
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
        
        # Remove special characters but keep hyphens, forward slashes, and parentheses
        filename = re.sub(r'[^\w\-\/\(\)]', '', filename)
        
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
        # Use the same formatting logic as format_track, which now handles
        # forward slashes in field values properly
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
