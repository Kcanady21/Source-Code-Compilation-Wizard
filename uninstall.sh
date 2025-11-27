#!/bin/bash
# Uninstaller for Source Code Compilation Wizard
# Can be run from anywhere - removes all installed components

set -e

INSTALL_DIR="$HOME/.local/bin"
SERVICE_DIR="$HOME/.local/share/kio/servicemenus"

echo "=========================================="
echo "Source Code Compilation Wizard Uninstaller"
echo "=========================================="
echo

read -p "This will remove the Source Code Compilation Wizard. Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# Remove main script
echo "Removing wizard script..."
rm -f "$INSTALL_DIR/source-compile-wizard.py"
echo "✅ Script removed"

# Remove uninstaller wizard
echo "Removing uninstaller wizard..."
rm -f "$INSTALL_DIR/source-compile-uninstaller.py"
echo "✅ Uninstaller wizard removed"

# Remove service menu
echo "Removing service menu..."
rm -f "$SERVICE_DIR/compile-source-wizard.desktop"
echo "✅ Service menu removed"

# Remove application menu entry
echo "Removing application menu entry..."
rm -f "$HOME/.local/share/applications/source-compile-uninstaller.desktop"
echo "✅ Application menu entry removed"

# Refresh KDE services
echo "Refreshing KDE services..."
if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 2>/dev/null || true
elif command -v kbuildsycoca5 &>/dev/null; then
    kbuildsycoca5 2>/dev/null || true
fi
echo "✅ KDE services refreshed"

echo
echo "=========================================="
echo "Uninstallation Complete!"
echo "=========================================="
echo
echo "Note: Log files in ~/.local/share/source-compile-logs/"
echo "      have been preserved. Remove manually if desired:"
echo "      rm -rf ~/.local/share/source-compile-logs/"
echo

# Remove self (uninstaller) last
rm -f "$INSTALL_DIR/source-compile-wizard-uninstall.sh" 2>/dev/null || true
