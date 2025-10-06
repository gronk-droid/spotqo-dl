#!/bin/bash
# Installation script for spotqo-dl

set -e

echo "🎵 Installing spotqo-dl..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install uv first:"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check if we're in the right directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Please run this script from the spotqo-dl directory"
    exit 1
fi

echo "📦 Installing dependencies (including qobuz-dl)..."
uv sync

echo "🔧 Installing spotqo-dl globally..."
# Try installing as a tool first (recommended for uv)
if uv tool install --help &> /dev/null; then
    echo "   Using uv tool install (recommended)..."
    uv tool install -e . || {
        echo "   uv tool install failed, falling back to pip install..."
        uv pip install -e . --force-reinstall
    }
else
    echo "   Using uv pip install..."
    uv pip install -e . --force-reinstall
fi

# Check if the command is available
if command -v spotqo-dl &> /dev/null; then
    echo "✅ spotqo-dl command is available"
else
    echo "⚠️  spotqo-dl command not found in PATH"
    echo "   You may need to restart your shell or run:"
    echo "   source ~/.bashrc  # or ~/.zshrc depending on your shell"
    echo "   Or use: uv run spotqo-dl 'https://open.spotify.com/track/...'"
fi

echo "✅ Installation complete!"
echo ""
echo "You can now use spotqo-dl from anywhere:"
echo "  spotqo-dl 'https://open.spotify.com/track/...'"
echo ""
echo "Don't forget to set up your credentials:"
echo "  export SPOTIFY_CLIENT_ID='your_client_id'"
echo "  export SPOTIFY_CLIENT_SECRET='your_client_secret'"
echo "  export QOBUZ_EMAIL='your_email'"
echo "  export QOBUZ_PASSWORD='your_password'"
