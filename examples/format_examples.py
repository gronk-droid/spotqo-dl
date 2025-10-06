#!/usr/bin/env python3
"""
Examples of format string usage with spotqo-dl.
"""

from spotqo_dl.formatter import TrackFormatter

def main():
    """Demonstrate format string examples."""
    
    # Example track data
    track = {
        "name": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "year": "1975",
        "track_number": 11,
        "duration": 355,
        "isrc": "GBUM71029604",
        "spotify_id": "4u7EnebtmKWzUH433cf5Qv",
        "spotify_url": "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
    }
    
    formatter = TrackFormatter()
    
    # Example format strings
    formats = [
        "{artist} - {title}",  # Default
        "{artist}/{album}/{track-number:02d} - {title}",
        "{artist} - {album} ({year})/{track-number:02d} - {title}",
        "{track-number:02d} - {title}",
        "{album}/{track-number:02d} - {title}",
        "{artist} - {title} ({year})",
    ]
    
    print("=== Format String Examples ===")
    print(f"Track: {track['artist']} - {track['name']}")
    print(f"Album: {track['album']} ({track['year']})")
    print(f"Track #: {track['track_number']}")
    print()
    
    for i, format_str in enumerate(formats, 1):
        formatted = formatter.format_track(track, format_str)
        print(f"{i}. Format: {format_str}")
        print(f"   Result: {formatted}")
        print()
    
    # Show available variables
    print("=== Available Variables ===")
    variables = formatter.get_available_variables()
    for var in variables:
        print(f"  {var}")
    
    print()
    print("=== Usage Examples ===")
    print("# Default format")
    print('spotqo-dl "https://open.spotify.com/track/..."')
    print()
    print("# Custom format")
    print('spotqo-dl -f "{artist}/{album}/{track-number:02d} - {title}" "https://open.spotify.com/track/..."')
    print()
    print("# Custom folder structure")
    print('spotqo-dl --folder-format "{artist}/{album} ({year})" "https://open.spotify.com/album/..."')

if __name__ == "__main__":
    main()
