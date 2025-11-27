# Source Code Compilation Wizard

A PyQt6 graphical wizard for compiling and installing software from source tarballs on Fedora Linux. Automates the classic `./configure && make && make install` workflow with smart dependency resolution, error handling, and desktop integration.

Tested on Fedora 43 with KDE Plasma 6.

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Platform](https://img.shields.io/badge/platform-Fedora%20Linux-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

**Interactive Wizard Interface** — Guides you step-by-step from source archive to installed application. Supports both user-local (`~/.local`) and system-wide (`/usr/local`) installations.

**Automatic Build System Detection** — Identifies Autotools, CMake, Meson, or plain Makefile projects and uses the appropriate commands for each.

**Fedora Dependency Resolution** — Parses build errors to suggest missing packages and offers one-click installation via `dnf`. Includes mappings for 200+ common dependencies including Qt5/Qt6, GTK, Boost, and more.

**Git Versioning Fixer** — Detects CMake projects that expect `.git` metadata (for `git describe`, commit hashes, etc.) and offers an automatic fix by synthesizing version information from the tarball name.

**Parallel Compilation** — Uses optimal `-jN` based on CPU cores with automatic fallback if parallel builds fail.

**Comprehensive Logging** — Captures build output on failure and saves logs to `~/.local/share/source-compile-logs/` for troubleshooting or sharing.

**Desktop Integration** — Creates `.desktop` files, icon entries, and optional symlinks. Adds applications to your menu for GUI apps.

**KDE Plasma Integration** — Installs a Dolphin service menu: right-click any tarball → "Compile and Install (Wizard)".

**Uninstaller** — Tracks installed files and provides clean removal of wizard-installed applications.

## Installation

### Quick Install

```bash
git clone https://github.com/yourusername/source-compile-wizard.git
cd source-compile-wizard
chmod +x install.sh
./install.sh
```

The installer will:
- Check for PyQt6 and offer to install it (`sudo dnf install python3-pyqt6`)
- Verify build tools are available (gcc, make, cmake, etc.)
- Install the wizard to `~/.local/bin/source-compile-wizard.py`
- Install the KDE service menu for Dolphin
- Create the log directory at `~/.local/share/source-compile-logs/`

### Verify Installation

**In Dolphin:** Right-click a `.tar.gz` or `.tar.xz` file — you should see "Compile and Install (Wizard)".

**In terminal:**
```bash
source-compile-wizard.py --help
```

If the Dolphin menu doesn't appear immediately:
```bash
kbuildsycoca6
```

## Usage

### From Dolphin (Recommended)

1. Right-click a source tarball (`.tar.gz`, `.tar.xz`, `.tar.bz2`)
2. Select **"Compile and Install (Wizard)"**
3. Follow the wizard pages

### From Terminal

```bash
source-compile-wizard.py /path/to/source.tar.gz
```

### Wizard Flow

1. **Welcome** — Overview of what the wizard will do
2. **Installation Location** — Choose `~/.local` (user) or `/usr/local` (system)
3. **Build System Detection** — Autotools / CMake / Meson / Makefile
4. **Configuration** — Basic defaults or advanced options
5. **Dependency Resolution** — Detect and install missing packages
6. **Compilation** — Parallel build with live output
7. **Testing** — Run `make test` / `ctest` if available
8. **Installation** — Install to chosen prefix
9. **Desktop Integration** — Create menu entries and symlinks
10. **Summary** — Final status and log locations

## Supported Build Systems

| Build System | Detection | Configure | Build | Install |
|--------------|-----------|-----------|-------|---------|
| GNU Autotools | `configure` script | `./configure --prefix=…` | `make -jN` | `make install` |
| CMake | `CMakeLists.txt` | `cmake -DCMAKE_INSTALL_PREFIX=…` | `cmake --build .` | `cmake --install .` |
| Meson | `meson.build` | `meson setup --prefix=… build` | `ninja -C build` | `ninja -C build install` |
| Plain Makefile | `Makefile` only | N/A | `make -jN` | `make install` |

## Git Versioning Fixer

Many CMake projects embed version information via Git (`git describe --tags`, `GIT_COMMIT_ID`, etc.). When building from a release tarball without a `.git` directory, configuration fails.

The wizard detects these failures and offers an automatic fix:
- Initializes a minimal Git repo in the source directory
- Creates a synthetic commit and tag matching the release version
- Re-runs CMake configuration

You can decline the fix and handle versioning manually if preferred.

## Uninstalling

### Remove Wizard-Installed Applications

```bash
source-compile-wizard-uninstall.sh
```

This scans your install logs and lets you select applications to remove, cleaning up binaries, desktop entries, icons, and symlinks.

### Remove the Wizard Itself

```bash
rm -f ~/.local/bin/source-compile-wizard.py
rm -f ~/.local/bin/source-compile-wizard-uninstall.sh
rm -f ~/.local/share/kio/servicemenus/compile-source-wizard.desktop
kbuildsycoca6
```

Logs are preserved by default. To remove them:
```bash
rm -rf ~/.local/share/source-compile-logs/
```

## File Locations

| Component | Path |
|-----------|------|
| Wizard script | `~/.local/bin/source-compile-wizard.py` |
| Uninstaller | `~/.local/bin/source-compile-wizard-uninstall.sh` |
| KDE service menu | `~/.local/share/kio/servicemenus/compile-source-wizard.desktop` |
| Build logs | `~/.local/share/source-compile-logs/` |
| User installs | `~/.local/{bin,lib,share}` |
| System installs | `/usr/local/{bin,lib,share}` |

## Dependencies

**Required:**
- Python 3.9+
- PyQt6 (`dnf install python3-pyqt6`)
- Standard build tools: `gcc`, `g++`, `make`

**For specific build systems:**
- CMake projects: `cmake`
- Meson projects: `meson`, `ninja-build`
- Autotools projects: `autoconf`, `automake`, `libtool`

Individual source packages will have their own dependencies. The wizard surfaces missing ones during configuration so you can install them with `dnf`.

## Case Study: Building AppImageLauncher

This documents building AppImageLauncher 3.x from source on Fedora 43 KDE.

### Build Process

1. Download `AppImageLauncher-3.0.0-beta-3.tar.gz`
2. Right-click → "Compile and Install (Wizard)"
3. Choose installation location (`/usr/local` or `~/.local`)
4. The wizard detects CMake and runs configuration

### Issues Encountered and Fixes

**Missing 32-bit glibc headers:**
```
gnu/stubs-32.h: No such file or directory
```
Fix: `sudo dnf install glibc-devel.i686`

**Missing patchelf:**
```
patchelf: command not found
```
Fix: `sudo dnf install patchelf`

**Missing static libraries:**
```
cannot find -lstdc++
cannot find -lm
cannot find -lc
```
Fix: `sudo dnf install glibc-static libstdc++-static`

### Post-Install Configuration

**Set AppImageLauncher as the handler for AppImages:**
```bash
xdg-mime default appimagelauncher.desktop application/vnd.appimage
```

**Fix for missing `qtpaths` on Fedora with Qt6:**
```bash
sudo ln -s /usr/bin/qtpaths6 /usr/local/bin/qtpaths
```

**Fix the `.desktop` file** if `Exec=` points to settings instead of the launcher:
```ini
[Desktop Entry]
Type=Application
Name=AppImageLauncher
Comment=Integrate and run AppImage files
Exec=/usr/local/bin/AppImageLauncher %f
Icon=appimagelauncher
Categories=Utility;
MimeType=application/vnd.appimage;
```

### Result

- **Double-click** an AppImage: Runs directly (KDE default behavior)
- **Right-click → Open with AppImageLauncher**: Shows integration dialog

## Test Archives

The `test-archives/` directory contains minimal projects for testing:

- `hello-autotools-1.0.tar.gz` — Autotools project
- `hello-cmake-1.0.tar.gz` — CMake project
- `hello-meson-1.0.tar.gz` — Meson project
- `hello-make-1.0.tar.gz` — Plain Makefile project

These are useful for validating wizard behavior and regression testing.

## Contributing

Contributions welcome! The dependency map in particular benefits from additions — if you encounter a package mapping that's missing or incorrect, please open a PR or issue.

## License

MIT License. See [LICENSE](LICENSE) for details.
