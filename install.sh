#!/bin/bash
# Installer for Source Code Compilation Wizard
# Deploys the wizard and KDE service menu integration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/bin"
SERVICE_DIR="$HOME/.local/share/kio/servicemenus"
LOG_DIR="$HOME/.local/share/source-compile-logs"

echo "=========================================="
echo "Source Code Compilation Wizard Installer"
echo "=========================================="
echo

# Check for PyQt6
echo "Checking dependencies..."
if ! python3 -c "import PyQt6" 2>/dev/null; then
    echo "⚠️  PyQt6 not found. Installing..."
    if command -v dnf &>/dev/null; then
        sudo dnf install -y python3-pyqt6
    else
        echo "❌ Could not install PyQt6. Please install python3-pyqt6 manually."
        exit 1
    fi
fi
echo "✅ PyQt6 found"

# Check for essential build tools
echo "Checking for build tools..."
MISSING_TOOLS=()
for tool in gcc make; do
    if ! command -v "$tool" &>/dev/null; then
        MISSING_TOOLS+=("$tool")
    fi
done

if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
    echo "⚠️  Missing build tools: ${MISSING_TOOLS[*]}"
    echo "   Installing development tools..."
    sudo dnf groupinstall -y "Development Tools" || true
fi
echo "✅ Build tools available"

# Create directories
echo
echo "Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$SERVICE_DIR"
mkdir -p "$LOG_DIR"
echo "✅ Directories created"

# Install main script
echo
echo "Installing wizard..."
cp "$SCRIPT_DIR/source-compile-wizard.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/source-compile-wizard.py"
echo "✅ Wizard installed to $INSTALL_DIR/source-compile-wizard.py"

# Install uninstaller wizard
echo
echo "Installing uninstaller wizard..."
cp "$SCRIPT_DIR/source-compile-uninstaller.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/source-compile-uninstaller.py"
echo "✅ Uninstaller installed to $INSTALL_DIR/source-compile-uninstaller.py"

# Install uninstaller script
echo
echo "Installing uninstall script..."
cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/source-compile-wizard-uninstall.sh"
chmod +x "$INSTALL_DIR/source-compile-wizard-uninstall.sh"
echo "✅ Uninstall script installed to $INSTALL_DIR/source-compile-wizard-uninstall.sh"

# Install service menu
echo
echo "Installing KDE service menu..."
cp "$SCRIPT_DIR/compile-source-wizard.desktop" "$SERVICE_DIR/"
chmod +x "$SERVICE_DIR/compile-source-wizard.desktop"
echo "✅ Service menu installed"

# Install application menu entry for uninstaller
echo
echo "Installing application menu entry..."
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cp "$SCRIPT_DIR/source-compile-uninstaller.desktop" "$APPS_DIR/"
chmod +x "$APPS_DIR/source-compile-uninstaller.desktop"
echo "✅ Application menu entry installed"

# Refresh KDE services
echo
echo "Refreshing KDE services..."
if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 2>/dev/null || true
elif command -v kbuildsycoca5 &>/dev/null; then
    kbuildsycoca5 2>/dev/null || true
fi
echo "✅ KDE services refreshed"

# Verify ~/.local/bin is in PATH
echo
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "⚠️  Note: ~/.local/bin is not in your PATH"
    echo "   Add this to your ~/.bashrc or ~/.zshrc:"
    echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo
fi

echo
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo
echo "You can now delete this download folder - all files have been installed."
echo
echo "Usage:"
echo "  1. Right-click on a .tar.gz or .tar.xz source archive in Dolphin"
echo "  2. Select 'Compile and Install (Wizard)'"
echo
echo "Or run directly from terminal:"
echo "  source-compile-wizard.py /path/to/source.tar.gz"
echo
echo "To uninstall:"
echo "  source-compile-wizard-uninstall.sh"
echo
echo "Logs will be saved to:"
echo "  $LOG_DIR"
echo
