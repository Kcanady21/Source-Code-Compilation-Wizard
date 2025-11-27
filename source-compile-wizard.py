#!/usr/bin/env python3
"""
Source Code Compilation Wizard
A PyQt6 wizard for compiling and installing software from source tarballs on Fedora Linux.
Integrates with KDE Plasma 6 desktop environment.
"""

import sys
import os
import subprocess
import shutil
import tarfile
import tempfile
import re
import signal
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any
from abc import ABC, abstractmethod
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QRadioButton, QButtonGroup, QTextEdit, QProgressBar,
    QPushButton, QCheckBox, QLineEdit, QComboBox, QScrollArea,
    QWidget, QGroupBox, QFormLayout, QFileDialog, QMessageBox,
    QFrame, QSizePolicy, QSpacerItem, QPlainTextEdit
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QProcess, QTimer, QSize
)
from PyQt6.QtGui import QFont, QTextCursor, QIcon, QPixmap


# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class InstallLocation(Enum):
    """Installation location choices."""
    USER_LOCAL = auto()    # ~/.local
    SYSTEM_WIDE = auto()   # /usr/local


class ConfigMode(Enum):
    """Configuration mode choices."""
    BASIC = auto()
    ADVANCED = auto()


class BuildStage(Enum):
    """Current stage in the build process."""
    EXTRACTION = auto()
    DETECTION = auto()
    CONFIGURATION = auto()
    DEPENDENCY_RESOLUTION = auto()
    COMPILATION = auto()
    TESTING = auto()
    INSTALLATION = auto()
    DESKTOP_INTEGRATION = auto()
    COMPLETE = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ConfigOption:
    """Represents a configuration option from ./configure --help."""
    name: str
    description: str
    is_feature: bool = True  # --enable/--disable vs --with/--without
    default_enabled: bool = False
    selected: bool = False


@dataclass
class DependencyInfo:
    """Information about a detected dependency."""
    name: str                          # Name from error message
    fedora_package: Optional[str]      # Mapped Fedora package name
    description: str = ""
    installed: bool = False
    install_selected: bool = True
    is_header_only: bool = False       # Can be installed by downloading headers
    manual_install_url: str = ""       # URL for manual download
    manual_install_cmd: str = ""       # Command for manual installation
    copr_repo: str = ""                # COPR repository if available
    not_in_repos: bool = False         # True if not in standard Fedora repos


@dataclass
class InstalledFile:
    """Information about an installed file."""
    path: str
    is_executable: bool = False
    is_elf: bool = False
    is_main_binary: bool = False


@dataclass
class WizardState:
    """Complete state of the wizard throughout execution."""
    # Input
    tarball_path: str = ""
    project_name: str = ""
    
    # Extraction
    extract_dir: str = ""
    source_dir: str = ""
    
    # User choices
    install_location: InstallLocation = InstallLocation.USER_LOCAL
    config_mode: ConfigMode = ConfigMode.BASIC
    run_tests: bool = True
    
    # Configuration
    config_options: List[ConfigOption] = field(default_factory=list)
    selected_options: List[str] = field(default_factory=list)
    
    # Build system
    build_system_name: str = ""
    build_system_forced: bool = False
    
    # Dependencies
    dependencies: List[DependencyInfo] = field(default_factory=list)
    
    # Installation
    prefix: str = ""
    installed_files: List[InstalledFile] = field(default_factory=list)
    main_executable: str = ""
    
    # Desktop integration
    is_gui_app: bool = False
    has_desktop_file: bool = False
    created_desktop_file: str = ""
    created_symlink: str = ""
    desktop_app_name: str = ""
    desktop_categories: str = "Utility"
    desktop_comment: str = ""
    desktop_icon: str = ""
    
    # Logging
    log_file: str = ""
    full_stdout: str = ""
    full_stderr: str = ""
    
    # Status
    current_stage: BuildStage = BuildStage.EXTRACTION
    error_message: str = ""
    error_stage: str = ""
    
    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    # System info (for error logs)
    system_info: Dict[str, str] = field(default_factory=dict)
    include_system_info: bool = False


# =============================================================================
# BUILD SYSTEM CLASSES
# =============================================================================

class BuildSystem(ABC):
    """Abstract base class for build systems."""
    
    name: str = "Unknown"
    
    def __init__(self, source_dir: str, state: WizardState):
        self.source_dir = source_dir
        self.state = state
        self.process: Optional[QProcess] = None
    
    @classmethod
    @abstractmethod
    def detect(cls, source_dir: str) -> bool:
        """Check if this build system is present in the source directory."""
        pass
    
    @abstractmethod
    def get_configure_command(self) -> List[str]:
        """Get the configure command and arguments."""
        pass
    
    @abstractmethod
    def get_build_command(self, jobs: int) -> List[str]:
        """Get the build command and arguments."""
        pass
    
    @abstractmethod
    def get_install_command(self) -> List[str]:
        """Get the install command and arguments."""
        pass
    
    @abstractmethod
    def get_test_command(self) -> Optional[List[str]]:
        """Get the test command, or None if no tests available."""
        pass
    
    @abstractmethod
    def get_help_output(self) -> str:
        """Get the configuration help output for parsing options."""
        pass
    
    @abstractmethod
    def parse_config_options(self, help_text: str) -> List[ConfigOption]:
        """Parse configuration options from help output."""
        pass
    
    def get_prefix_option(self) -> str:
        """Get the prefix option for installation location."""
        return f"--prefix={self.state.prefix}"


class AutotoolsBuildSystem(BuildSystem):
    """GNU Autotools build system (./configure && make)."""
    
    name = "GNU Autotools"
    
    @classmethod
    def detect(cls, source_dir: str) -> bool:
        """Check for configure script."""
        configure_path = os.path.join(source_dir, "configure")
        return os.path.isfile(configure_path) and os.access(configure_path, os.X_OK)
    
    def get_configure_command(self) -> List[str]:
        """Get ./configure command with options."""
        cmd = ["./configure", self.get_prefix_option()]
        
        # Add selected options
        for opt in self.state.selected_options:
            cmd.append(opt)
        
        return cmd
    
    def get_build_command(self, jobs: int) -> List[str]:
        """Get make command with parallelization."""
        return ["make", f"-j{jobs}"]
    
    def get_install_command(self) -> List[str]:
        """Get make install command."""
        if self.state.install_location == InstallLocation.SYSTEM_WIDE:
            return ["sudo", "make", "install"]
        return ["make", "install"]
    
    def get_test_command(self) -> Optional[List[str]]:
        """Check for test targets in Makefile."""
        makefile = os.path.join(self.source_dir, "Makefile")
        if os.path.exists(makefile):
            with open(makefile, 'r', errors='ignore') as f:
                content = f.read()
                if re.search(r'^check\s*:', content, re.MULTILINE):
                    return ["make", "check"]
                if re.search(r'^test\s*:', content, re.MULTILINE):
                    return ["make", "test"]
        return None
    
    def get_help_output(self) -> str:
        """Run ./configure --help."""
        try:
            result = subprocess.run(
                ["./configure", "--help"],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout
        except Exception as e:
            return f"Error getting help: {e}"
    
    def parse_config_options(self, help_text: str) -> List[ConfigOption]:
        """Parse options from ./configure --help output."""
        options = []
        
        # Match --enable-FEATURE and --disable-FEATURE patterns
        enable_pattern = r'--enable-(\S+)\s+(.*?)(?=\n\s*--|$)'
        disable_pattern = r'--disable-(\S+)\s+(.*?)(?=\n\s*--|$)'
        with_pattern = r'--with-(\S+)\s+(.*?)(?=\n\s*--|$)'
        without_pattern = r'--without-(\S+)\s+(.*?)(?=\n\s*--|$)'
        
        seen_features = set()
        
        # Parse --enable options
        for match in re.finditer(enable_pattern, help_text, re.DOTALL):
            name = match.group(1).strip()
            desc = match.group(2).strip().replace('\n', ' ')
            if name not in seen_features:
                seen_features.add(name)
                options.append(ConfigOption(
                    name=f"--enable-{name}",
                    description=desc[:200],  # Truncate long descriptions
                    is_feature=True,
                    default_enabled=False
                ))
        
        # Parse --with options
        for match in re.finditer(with_pattern, help_text, re.DOTALL):
            name = match.group(1).strip()
            desc = match.group(2).strip().replace('\n', ' ')
            if name not in seen_features:
                seen_features.add(name)
                options.append(ConfigOption(
                    name=f"--with-{name}",
                    description=desc[:200],
                    is_feature=False,
                    default_enabled=False
                ))
        
        return options


class CMakeBuildSystem(BuildSystem):
    """CMake build system."""
    
    name = "CMake"
    
    def __init__(self, source_dir: str, state: WizardState):
        super().__init__(source_dir, state)
        self.build_dir = os.path.join(source_dir, "build")
    
    @classmethod
    def detect(cls, source_dir: str) -> bool:
        """Check for CMakeLists.txt."""
        return os.path.isfile(os.path.join(source_dir, "CMakeLists.txt"))
    
    def get_configure_command(self) -> List[str]:
        """Get cmake configuration command."""
        os.makedirs(self.build_dir, exist_ok=True)
        cmd = [
            "cmake",
            f"-DCMAKE_INSTALL_PREFIX={self.state.prefix}",
            "-S", self.source_dir,
            "-B", self.build_dir
        ]
        
        # Add selected options
        for opt in self.state.selected_options:
            cmd.append(opt)
        
        return cmd
    
    def get_build_command(self, jobs: int) -> List[str]:
        """Get cmake build command."""
        return ["cmake", "--build", self.build_dir, "-j", str(jobs)]
    
    def get_install_command(self) -> List[str]:
        """Get cmake install command."""
        if self.state.install_location == InstallLocation.SYSTEM_WIDE:
            return ["sudo", "cmake", "--install", self.build_dir]
        return ["cmake", "--install", self.build_dir]
    
    def get_test_command(self) -> Optional[List[str]]:
        """Check for CTest."""
        if os.path.exists(os.path.join(self.build_dir, "CTestTestfile.cmake")):
            return ["ctest", "--test-dir", self.build_dir]
        return None
    
    def get_help_output(self) -> str:
        """Get CMake cache variables."""
        try:
            # First run cmake to generate cache
            subprocess.run(
                ["cmake", "-S", self.source_dir, "-B", self.build_dir, "-N"],
                capture_output=True,
                timeout=60
            )
            # Then list cache variables
            result = subprocess.run(
                ["cmake", "-L", "-B", self.build_dir],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout
        except Exception as e:
            return f"Error getting CMake options: {e}"
    
    def parse_config_options(self, help_text: str) -> List[ConfigOption]:
        """Parse CMake cache variables."""
        options = []
        
        # Match VARIABLE:TYPE=VALUE patterns
        pattern = r'^(\w+):(\w+)=(.*)$'
        
        for line in help_text.split('\n'):
            match = re.match(pattern, line.strip())
            if match:
                name, vtype, default = match.groups()
                # Skip internal variables
                if name.startswith('CMAKE_') and name not in [
                    'CMAKE_BUILD_TYPE', 'CMAKE_INSTALL_PREFIX'
                ]:
                    continue
                
                options.append(ConfigOption(
                    name=f"-D{name}",
                    description=f"Type: {vtype}, Default: {default}",
                    is_feature=True,
                    default_enabled=default.lower() in ('on', 'true', '1')
                ))
        
        return options


class MesonBuildSystem(BuildSystem):
    """Meson build system."""
    
    name = "Meson"
    
    def __init__(self, source_dir: str, state: WizardState):
        super().__init__(source_dir, state)
        self.build_dir = os.path.join(source_dir, "builddir")
    
    @classmethod
    def detect(cls, source_dir: str) -> bool:
        """Check for meson.build."""
        return os.path.isfile(os.path.join(source_dir, "meson.build"))
    
    def get_configure_command(self) -> List[str]:
        """Get meson setup command."""
        cmd = [
            "meson", "setup",
            f"--prefix={self.state.prefix}",
            self.build_dir,
            self.source_dir
        ]
        
        for opt in self.state.selected_options:
            cmd.append(opt)
        
        return cmd
    
    def get_build_command(self, jobs: int) -> List[str]:
        """Get ninja build command."""
        return ["ninja", "-C", self.build_dir, "-j", str(jobs)]
    
    def get_install_command(self) -> List[str]:
        """Get meson install command."""
        if self.state.install_location == InstallLocation.SYSTEM_WIDE:
            return ["sudo", "ninja", "-C", self.build_dir, "install"]
        return ["ninja", "-C", self.build_dir, "install"]
    
    def get_test_command(self) -> Optional[List[str]]:
        """Get meson test command."""
        return ["meson", "test", "-C", self.build_dir]
    
    def get_help_output(self) -> str:
        """Get meson configure options."""
        try:
            result = subprocess.run(
                ["meson", "configure", self.source_dir],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout
        except Exception as e:
            return f"Error getting Meson options: {e}"
    
    def parse_config_options(self, help_text: str) -> List[ConfigOption]:
        """Parse meson configure output."""
        options = []
        # Meson configure output is complex, simplified parsing
        return options


class PlainMakefileBuildSystem(BuildSystem):
    """Plain Makefile without configure script."""
    
    name = "Plain Makefile"
    
    @classmethod
    def detect(cls, source_dir: str) -> bool:
        """Check for Makefile without configure."""
        has_makefile = os.path.isfile(os.path.join(source_dir, "Makefile")) or \
                       os.path.isfile(os.path.join(source_dir, "makefile")) or \
                       os.path.isfile(os.path.join(source_dir, "GNUmakefile"))
        has_configure = os.path.isfile(os.path.join(source_dir, "configure"))
        return has_makefile and not has_configure
    
    def get_configure_command(self) -> List[str]:
        """No configure step needed."""
        return []
    
    def get_build_command(self, jobs: int) -> List[str]:
        """Get make command."""
        return ["make", f"-j{jobs}", f"PREFIX={self.state.prefix}"]
    
    def get_install_command(self) -> List[str]:
        """Get make install command."""
        if self.state.install_location == InstallLocation.SYSTEM_WIDE:
            return ["sudo", "make", "install", f"PREFIX={self.state.prefix}"]
        return ["make", "install", f"PREFIX={self.state.prefix}"]
    
    def get_test_command(self) -> Optional[List[str]]:
        """Check for test target."""
        for makefile in ["Makefile", "makefile", "GNUmakefile"]:
            path = os.path.join(self.source_dir, makefile)
            if os.path.exists(path):
                with open(path, 'r', errors='ignore') as f:
                    content = f.read()
                    if re.search(r'^test\s*:', content, re.MULTILINE):
                        return ["make", "test"]
        return None
    
    def get_help_output(self) -> str:
        """Plain Makefiles typically don't have help."""
        return "Plain Makefile detected. Limited configuration options available."
    
    def parse_config_options(self, help_text: str) -> List[ConfigOption]:
        """No options for plain Makefiles."""
        return []


# Build system registry for detection
BUILD_SYSTEMS = [
    AutotoolsBuildSystem,
    CMakeBuildSystem,
    MesonBuildSystem,
    PlainMakefileBuildSystem,
]


def detect_build_system(source_dir: str, state: WizardState) -> Optional[BuildSystem]:
    """Detect and return appropriate build system."""
    for bs_class in BUILD_SYSTEMS:
        if bs_class.detect(source_dir):
            return bs_class(source_dir, state)
    return None


# =============================================================================
# GIT VERSIONING FIX SYSTEM
# =============================================================================

@dataclass
class GitVersioningIssue:
    """Detected git versioning issue."""
    issue_type: str              # 'version_file', 'git_describe', 'cmake_git', 'generic'
    description: str
    cmake_file: Optional[str] = None
    cache_file_path: Optional[str] = None
    fix_available: bool = True
    fix_description: str = ""


class GitVersioningFixer:
    """
    Handles git versioning issues for source tarballs.
    
    Many projects use git for versioning (git describe, git rev-parse, etc.)
    and fail when built from a tarball that lacks the .git directory.
    This class provides multiple strategies to work around these issues.
    """
    
    def __init__(self, source_dir: str, tarball_name: str = ""):
        self.source_dir = source_dir
        self.tarball_name = tarball_name
        self.detected_issues: List[GitVersioningIssue] = []
        self._extracted_version = self._extract_version_from_tarball_name()
        self._detected_cache_file: Optional[str] = None
        self.progress_callback: Optional[callable] = None  # For UI feedback
    
    def _extract_version_from_tarball_name(self) -> str:
        """Extract version number from tarball filename."""
        if not self.tarball_name:
            return "0.0.0"
        
        # Common patterns: project-1.2.3.tar.gz, project-v1.2.3.tar.gz
        patterns = [
            r'-v?(\d+\.\d+\.\d+(?:-\w+)?)',  # project-1.2.3 or project-v1.2.3
            r'-v?(\d+\.\d+)',                  # project-1.2
            r'_v?(\d+\.\d+\.\d+)',             # project_1.2.3
            r'\.v?(\d+\.\d+\.\d+)',            # project.1.2.3
        ]
        
        basename = os.path.basename(self.tarball_name)
        for pattern in patterns:
            match = re.search(pattern, basename, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return "0.0.0"
    
    def detect_issues(self, cmake_output: str) -> List[GitVersioningIssue]:
        """Analyze CMake output to detect git versioning issues."""
        self.detected_issues = []
        output_lower = cmake_output.lower()
        
        # Pattern 1: Cache file pattern (like AppImageLauncher)
        # Try to extract the exact cache file path from the error
        cache_file_match = re.search(
            r'Could not find git commit cache file[,\s]*([^\s,\n]+)?',
            cmake_output, re.IGNORECASE
        )
        if not cache_file_match:
            cache_file_match = re.search(
                r'trying to read cache file[:\s]*([^\s,\n]+)?',
                cmake_output, re.IGNORECASE
            )
        
        if ("cache file" in output_lower or "cache" in output_lower) and \
           ("git commit" in output_lower or "commit id" in output_lower):
            # Try to find the specific cache file path from versioning.cmake
            cache_file = self._find_cache_file_from_cmake_script(cmake_output)
            self._detected_cache_file = cache_file
            self.detected_issues.append(GitVersioningIssue(
                issue_type='version_file',
                description="Project expects a version/commit cache file for tarball builds",
                cache_file_path=cache_file,
                fix_description=f"Create cache file at: {cache_file}" if cache_file else "Create VERSION file"
            ))
        
        # Pattern 2: git describe failure
        if "git describe" in output_lower and ("failed" in output_lower or "error" in output_lower):
            self.detected_issues.append(GitVersioningIssue(
                issue_type='git_describe',
                description="Project uses 'git describe' for versioning",
                fix_description="Initialize git repo with tagged commit"
            ))
        
        # Pattern 3: git rev-parse failure
        if "git rev-parse" in output_lower or "git log" in output_lower:
            if not any(i.issue_type == 'git_describe' for i in self.detected_issues):
                self.detected_issues.append(GitVersioningIssue(
                    issue_type='git_describe',
                    description="Project uses git commands for versioning",
                    fix_description="Initialize git repo with commit"
                ))
        
        # Pattern 4: CMake GIT_VERSION variables
        if "git_version" in output_lower or "git_commit" in output_lower or \
           "git_hash" in output_lower or "git_tag" in output_lower:
            cmake_file = self._find_versioning_cmake_file()
            self.detected_issues.append(GitVersioningIssue(
                issue_type='cmake_git',
                description="Project uses CMake git version variables",
                cmake_file=cmake_file,
                fix_description="Set version variables via CMake cache"
            ))
        
        # Pattern 5: Generic "gather commit" pattern
        if "gather commit" in output_lower or "gathering commit" in output_lower:
            if not self.detected_issues:  # Only if we haven't detected something more specific
                self.detected_issues.append(GitVersioningIssue(
                    issue_type='generic',
                    description="Project requires git commit information",
                    fix_description="Initialize git repo or create version file"
                ))
        
        return self.detected_issues
    
    def _find_cache_file_from_cmake_script(self, cmake_output: str) -> Optional[str]:
        """
        Parse the versioning.cmake file to find exactly what cache file it expects.
        This handles projects like AppImageLauncher that have specific cache file formats.
        """
        # First, find the versioning cmake file
        versioning_files = [
            'cmake/versioning.cmake',
            'cmake/version.cmake', 
            'cmake/GitVersion.cmake',
            'cmake/GetGitRevisionDescription.cmake',
        ]
        
        versioning_cmake = None
        for rel_path in versioning_files:
            full_path = os.path.join(self.source_dir, rel_path)
            if os.path.exists(full_path):
                versioning_cmake = full_path
                break
        
        if not versioning_cmake:
            return None
        
        try:
            with open(versioning_cmake, 'r') as f:
                content = f.read()
            
            # Look for cache file patterns in the cmake script
            # Common patterns:
            # file(READ "${CMAKE_SOURCE_DIR}/.git-commit-id" ...)
            # file(READ "${PROJECT_SOURCE_DIR}/GIT_COMMIT_CACHE" ...)
            # set(CACHE_FILE "${CMAKE_CURRENT_SOURCE_DIR}/.version")
            
            patterns = [
                r'file\s*\(\s*READ\s+["\$\{]*(?:CMAKE_SOURCE_DIR|PROJECT_SOURCE_DIR|CMAKE_CURRENT_SOURCE_DIR)[}\s/"]*([^"\s\)]+)',
                r'set\s*\(\s*\w*CACHE\w*\s+["\$\{]*(?:CMAKE_SOURCE_DIR|PROJECT_SOURCE_DIR)[}\s/"]*([^"\s\)]+)',
                r'if\s*\(\s*EXISTS\s+["\$\{]*(?:CMAKE_SOURCE_DIR|PROJECT_SOURCE_DIR)[}\s/"]*([^"\s\)]+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    cache_file = match.group(1).strip('"\'/')
                    # Filter out non-cache files
                    if any(x in cache_file.lower() for x in ['commit', 'version', 'cache', '.git']):
                        return cache_file
            
            # AppImageLauncher specific: look for the exact variable name
            if 'GIT_COMMIT_CACHE_FILE' in content or 'git commit cache' in content.lower():
                # Try to find what file it reads
                cache_match = re.search(r'["\'](/[^"\']+|[^"\'\s]+\.(?:txt|cache|id))["\']', content)
                if cache_match:
                    return cache_match.group(1).lstrip('/')
            
        except Exception:
            pass
        
        # Default fallback names to try
        return None
    
    def _find_cache_file_reference(self, output: str) -> Optional[str]:
        """Try to find what cache file the project is looking for."""
        # Look for common cache file names in error output
        patterns = [
            r'cache file[:\s]+["\']?([^\s"\']+)["\']?',
            r'reading\s+["\']?([^\s"\']+cache[^\s"\']*)["\']?',
            r'file\s+["\']?([^\s"\']*commit[^\s"\']*)["\']?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _find_versioning_cmake_file(self) -> Optional[str]:
        """Find the CMake file that handles versioning."""
        versioning_files = [
            'cmake/versioning.cmake',
            'cmake/version.cmake',
            'cmake/GitVersion.cmake',
            'cmake/GetGitRevisionDescription.cmake',
            'cmake/modules/GetGitRevisionDescription.cmake',
        ]
        
        for rel_path in versioning_files:
            full_path = os.path.join(self.source_dir, rel_path)
            if os.path.exists(full_path):
                return rel_path
        return None

    def _emit_progress(self, message: str):
        """Emit progress message if callback is set."""
        if self.progress_callback:
            self.progress_callback(message)
    
    def apply_fixes(self) -> Tuple[bool, str]:
        """
        Apply fixes for detected git versioning issues.
        Returns (success, message).
        """
        if not self.detected_issues:
            return True, "No git versioning issues to fix"
        
        fixes_applied = []
        errors = []
        
        # Always try to initialize git repo first - this is the most reliable fix
        # Many projects simply need git to be present
        self._emit_progress("Initializing git repository...")
        git_success, git_msg = self._fix_git_describe()
        if git_success:
            fixes_applied.append(git_msg)
        
        for issue in self.detected_issues:
            try:
                if issue.issue_type == 'version_file':
                    self._emit_progress("Creating version cache files...")
                    success, msg = self._fix_version_file(issue)
                    if success:
                        fixes_applied.append(msg)
                    else:
                        errors.append(msg)
                elif issue.issue_type == 'cmake_git':
                    self._emit_progress("Creating CMake version variables...")
                    success, msg = self._fix_cmake_variables(issue)
                    if success:
                        fixes_applied.append(msg)
                    else:
                        errors.append(msg)
                elif issue.issue_type == 'generic':
                    self._emit_progress("Applying generic fixes...")
                    success, msg = self._fix_generic()
                    if success:
                        fixes_applied.append(msg)
                    else:
                        errors.append(msg)
                # Skip git_describe since we already did it above
            except Exception as e:
                errors.append(f"Error fixing {issue.issue_type}: {str(e)}")
        
        # Also patch the versioning.cmake file if it exists
        self._emit_progress("Patching CMake files...")
        patch_success, patch_msg = self._patch_versioning_cmake()
        if patch_success:
            fixes_applied.append(patch_msg)
        
        self._emit_progress("Done applying fixes")
        
        if not fixes_applied:
            return False, "No fixes could be applied. Errors: " + "; ".join(errors)
        
        if errors:
            return True, "Applied fixes: " + "; ".join(fixes_applied) + " (Some errors: " + "; ".join(errors) + ")"
        
        return True, "Applied fixes: " + "; ".join(fixes_applied)
    
    def _fix_git_describe(self) -> Tuple[bool, str]:
        """Initialize a git repository with a fake commit and version tag."""
        import hashlib
        
        try:
            # Check if .git already exists
            git_dir = os.path.join(self.source_dir, '.git')
            if os.path.exists(git_dir):
                return True, "Git repository already exists"
            
            # Initialize git repo
            result = subprocess.run(
                ['git', 'init'],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return False, f"git init failed: {result.stderr}"
            
            # Configure git user (required for commit)
            subprocess.run(
                ['git', 'config', 'user.email', 'build@localhost'],
                cwd=self.source_dir,
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ['git', 'config', 'user.name', 'Build System'],
                cwd=self.source_dir,
                capture_output=True,
                timeout=10
            )
            
            # Add all files and create initial commit
            subprocess.run(
                ['git', 'add', '-A'],
                cwd=self.source_dir,
                capture_output=True,
                timeout=60
            )
            
            result = subprocess.run(
                ['git', 'commit', '-m', f'Tarball build v{self._extracted_version}', '--allow-empty'],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Create version tag
            tag_name = f'v{self._extracted_version}'
            subprocess.run(
                ['git', 'tag', '-a', tag_name, '-m', f'Version {self._extracted_version}'],
                cwd=self.source_dir,
                capture_output=True,
                timeout=30
            )
            
            return True, f"Initialized git repo with tag {tag_name}"
            
        except subprocess.TimeoutExpired:
            return False, "Git command timed out"
        except FileNotFoundError:
            return False, "Git is not installed"
        except Exception as e:
            return False, f"Git initialization failed: {str(e)}"
    
    def _fix_version_file(self, issue: GitVersioningIssue) -> Tuple[bool, str]:
        """Create version/commit cache file for projects that expect one."""
        import hashlib
        
        fake_hash = hashlib.sha1(
            f"{self.tarball_name}-{self._extracted_version}".encode()
        ).hexdigest()
        
        files_created = []
        
        # If we detected a specific cache file path, create it
        if issue.cache_file_path:
            cache_path = os.path.join(self.source_dir, issue.cache_file_path)
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w') as f:
                    f.write(fake_hash[:7] + '\n')
                files_created.append(issue.cache_file_path)
            except Exception as e:
                pass
        
        # Also create common version file names
        version_files = [
            ('VERSION', self._extracted_version),
            ('.version', self._extracted_version),
            ('version.txt', self._extracted_version),
            ('.git-commit-id', fake_hash[:7]),
            ('GIT_COMMIT_ID', fake_hash[:7]),
        ]
        
        for filename, content in version_files:
            filepath = os.path.join(self.source_dir, filename)
            if not os.path.exists(filepath):
                try:
                    with open(filepath, 'w') as f:
                        f.write(content + '\n')
                    files_created.append(filename)
                except Exception:
                    pass
        
        if files_created:
            return True, f"Created version files: {', '.join(files_created)}"
        return False, "Failed to create version files"
    
    def _fix_cmake_variables(self, issue: GitVersioningIssue) -> Tuple[bool, str]:
        """Create a CMake cache file with version variables pre-set."""
        import hashlib
        
        fake_hash = hashlib.sha1(
            f"{self.tarball_name}-{self._extracted_version}".encode()
        ).hexdigest()
        
        cache_content = f'''# Generated by Source Compile Wizard for tarball builds
# This file provides version information normally obtained from git

set(GIT_COMMIT_ID "{fake_hash[:7]}" CACHE STRING "Git commit ID (tarball build)")
set(GIT_COMMIT_HASH "{fake_hash}" CACHE STRING "Git commit hash (tarball build)")
set(GIT_COMMIT "{fake_hash[:7]}" CACHE STRING "Git commit (tarball build)")
set(GIT_VERSION "{self._extracted_version}" CACHE STRING "Git version (tarball build)")
set(GIT_TAG "v{self._extracted_version}" CACHE STRING "Git tag (tarball build)")
set(GIT_DESCRIBE "v{self._extracted_version}" CACHE STRING "Git describe (tarball build)")
set(GIT_BRANCH "tarball" CACHE STRING "Git branch (tarball build)")
set(VERSION "{self._extracted_version}" CACHE STRING "Version (tarball build)")
set(PROJECT_VERSION "{self._extracted_version}" CACHE STRING "Project version (tarball build)")
'''
        
        cache_path = os.path.join(self.source_dir, 'cmake_version_cache.cmake')
        try:
            with open(cache_path, 'w') as f:
                f.write(cache_content)
            return True, f"Created CMake cache file: cmake_version_cache.cmake"
        except Exception as e:
            return False, f"Failed to create CMake cache: {str(e)}"
    
    def _fix_generic(self) -> Tuple[bool, str]:
        """Apply generic fixes for unspecified git versioning issues."""
        fixes = []
        
        # Try creating common version files
        import hashlib
        fake_hash = hashlib.sha1(
            f"{self.tarball_name}-{self._extracted_version}".encode()
        ).hexdigest()[:7]
        
        version_files = [
            ('VERSION', self._extracted_version),
            ('version.txt', self._extracted_version),
            ('.version', self._extracted_version),
            ('GIT_VERSION', fake_hash[:7]),
        ]
        
        for filename, content in version_files:
            filepath = os.path.join(self.source_dir, filename)
            if not os.path.exists(filepath):
                try:
                    with open(filepath, 'w') as f:
                        f.write(content + '\n')
                    fixes.append(filename)
                except Exception:
                    pass
        
        if fixes:
            return True, f"Created version files: {', '.join(fixes)}"
        return False, "No generic fixes applied"

    def _patch_versioning_cmake(self) -> Tuple[bool, str]:
        """
        Patch the versioning.cmake file to not fail when git is not available.
        This is a last-resort fix that modifies the build system.
        """
        versioning_files = [
            'cmake/versioning.cmake',
            'cmake/version.cmake',
            'cmake/GitVersion.cmake',
        ]
        
        patched_files = []
        
        for rel_path in versioning_files:
            full_path = os.path.join(self.source_dir, rel_path)
            if not os.path.exists(full_path):
                continue
            
            try:
                with open(full_path, 'r') as f:
                    content = f.read()
                
                original_content = content
                
                # Replace FATAL_ERROR with WARNING for git-related errors
                content = re.sub(
                    r'message\s*\(\s*FATAL_ERROR\s+([^)]*(?:git|commit|version|cache)[^)]*)\)',
                    r'message(WARNING \1)',
                    content,
                    flags=re.IGNORECASE
                )
                
                # Add fallback values for common version variables if they'd cause errors
                # Look for places where variables are used without being set
                fallback_additions = []
                
                import hashlib
                fake_hash = hashlib.sha1(
                    f"{self.tarball_name}-{self._extracted_version}".encode()
                ).hexdigest()[:7]
                
                # Check if GIT_COMMIT or similar variables are used
                if 'GIT_COMMIT' in content and 'set(GIT_COMMIT' not in content:
                    fallback_additions.append(f'set(GIT_COMMIT "{fake_hash}" CACHE STRING "Git commit (tarball build)")')
                if 'GIT_COMMIT_ID' in content and 'set(GIT_COMMIT_ID' not in content:
                    fallback_additions.append(f'set(GIT_COMMIT_ID "{fake_hash}" CACHE STRING "Git commit ID (tarball build)")')
                if 'GIT_VERSION' in content and 'set(GIT_VERSION' not in content:
                    fallback_additions.append(f'set(GIT_VERSION "{self._extracted_version}" CACHE STRING "Version (tarball build)")')
                
                if fallback_additions:
                    # Add fallbacks at the beginning of the file
                    fallback_block = "\n# Fallback values for tarball builds (added by Source Compile Wizard)\n"
                    fallback_block += "if(NOT DEFINED GIT_COMMIT)\n"
                    fallback_block += "\n".join(f"  {line}" for line in fallback_additions)
                    fallback_block += "\nendif()\n\n"
                    content = fallback_block + content
                
                if content != original_content:
                    # Backup original
                    backup_path = full_path + '.orig'
                    if not os.path.exists(backup_path):
                        with open(backup_path, 'w') as f:
                            f.write(original_content)
                    
                    with open(full_path, 'w') as f:
                        f.write(content)
                    patched_files.append(rel_path)
                    
            except Exception as e:
                continue
        
        if patched_files:
            return True, f"Patched CMake files: {', '.join(patched_files)}"
        return False, "No CMake files needed patching"
    
    def get_cmake_extra_args(self) -> List[str]:
        """
        Get additional CMake arguments to help with version issues.
        These can be passed to the cmake command.
        """
        import hashlib
        fake_hash = hashlib.sha1(
            f"{self.tarball_name}-{self._extracted_version}".encode()
        ).hexdigest()[:7]
        
        args = [
            f"-DGIT_COMMIT_ID={fake_hash}",
            f"-DGIT_COMMIT_HASH={fake_hash}",
            f"-DGIT_VERSION={self._extracted_version}",
            f"-DGIT_TAG=v{self._extracted_version}",
            f"-DGIT_DESCRIBE=v{self._extracted_version}",
        ]
        
        # Check if we created a cache file
        cache_path = os.path.join(self.source_dir, 'cmake_version_cache.cmake')
        if os.path.exists(cache_path):
            args.insert(0, f"-C{cache_path}")
        
        return args


def is_git_versioning_error(output: str) -> bool:
    """Check if configuration output indicates a git versioning error."""
    output_lower = output.lower()
    
    git_keywords = [
        'git commit', 'git describe', 'commit id', 'gather commit',
        'git rev-parse', 'git log', 'git version', 'git hash',
        'git tag', '.git directory', 'git repository'
    ]
    
    error_keywords = [
        'not found', 'failed', 'could not find', 'not available',
        'error', 'cannot', 'unable to', 'missing'
    ]
    
    has_git_ref = any(kw in output_lower for kw in git_keywords)
    has_error = any(kw in output_lower for kw in error_keywords)
    
    # Also check for explicit "not a git repository" type messages
    explicit_patterns = [
        r'not\s+a\s+git\s+repository',
        r'fatal:\s+not\s+a\s+git',
        r'cache\s+file.*not\s+found',
        r'version.*file.*not\s+found',
    ]
    
    has_explicit = any(re.search(p, output_lower) for p in explicit_patterns)
    
    return (has_git_ref and has_error) or has_explicit


# =============================================================================
# DEPENDENCY MAPPING
# =============================================================================

# Common dependency name to Fedora package mapping
DEPENDENCY_MAP = {
    # Graphics/GUI
    'gtk': 'gtk3-devel',
    'gtk2': 'gtk2-devel',
    'gtk3': 'gtk3-devel',
    'gtk4': 'gtk4-devel',
    'gtk+-2.0': 'gtk2-devel',
    'gtk+-3.0': 'gtk3-devel',
    'gtk4': 'gtk4-devel',
    'qt': 'qt5-qtbase-devel',
    'qt5': 'qt5-qtbase-devel',
    'qt6': 'qt6-qtbase-devel',
    'sdl': 'SDL-devel',
    'sdl2': 'SDL2-devel',
    'opengl': 'mesa-libGL-devel',
    'glew': 'glew-devel',
    'glut': 'freeglut-devel',
    'vulkan': 'vulkan-devel',
    'x11': 'libX11-devel',
    'xext': 'libXext-devel',
    'xrandr': 'libXrandr-devel',
    'xcursor': 'libXcursor-devel',
    'xi': 'libXi-devel',
    'wayland': 'wayland-devel',
    
    # SVG/Graphics libraries
    'librsvg': 'librsvg2-devel',
    'librsvg-2.0': 'librsvg2-devel',
    'rsvg': 'librsvg2-devel',
    'rsvg-2.0': 'librsvg2-devel',
    'gdk-pixbuf': 'gdk-pixbuf2-devel',
    'gdk-pixbuf-2.0': 'gdk-pixbuf2-devel',
    
    # Audio
    'alsa': 'alsa-lib-devel',
    'pulseaudio': 'pulseaudio-libs-devel',
    'openal': 'openal-soft-devel',
    'portaudio': 'portaudio-devel',
    
    # Compression
    'zlib': 'zlib-devel',
    'bz2': 'bzip2-devel',
    'bzip2': 'bzip2-devel',
    'lzma': 'xz-devel',
    'xz': 'xz-devel',
    'lz4': 'lz4-devel',
    'zstd': 'libzstd-devel',
    
    # Crypto
    'openssl': 'openssl-devel',
    'gnutls': 'gnutls-devel',
    'libsodium': 'libsodium-devel',
    'gcrypt': 'libgcrypt-devel',
    'libgcrypt': 'libgcrypt-devel',
    'gpg-error': 'libgpg-error-devel',
    'libgpg-error': 'libgpg-error-devel',
    
    # Image
    'png': 'libpng-devel',
    'jpeg': 'libjpeg-turbo-devel',
    'tiff': 'libtiff-devel',
    'webp': 'libwebp-devel',
    'gif': 'giflib-devel',
    
    # Text/XML
    'xml2': 'libxml2-devel',
    'libxml2': 'libxml2-devel',
    'libxml-2.0': 'libxml2-devel',
    'xslt': 'libxslt-devel',
    'json-c': 'json-c-devel',
    'yaml': 'libyaml-devel',
    'expat': 'expat-devel',
    
    # Database
    'sqlite': 'sqlite-devel',
    'sqlite3': 'sqlite-devel',
    'postgresql': 'postgresql-devel',
    'mysql': 'mariadb-connector-c-devel',
    
    # Network
    'curl': 'libcurl-devel',
    'libcurl': 'libcurl-devel',
    'ssh': 'libssh-devel',
    'ssh2': 'libssh2-devel',
    'libssh2': 'libssh2-devel',
    
    # Math/Science
    'fftw': 'fftw-devel',
    'fftw3': 'fftw-devel',
    'gsl': 'gsl-devel',
    'lapack': 'lapack-devel',
    'blas': 'blas-devel',
    
    # GLib/GObject ecosystem (pkg-config names)
    'glib': 'glib2-devel',
    'glib-2.0': 'glib2-devel',
    'gobject-2.0': 'glib2-devel',
    'gio-2.0': 'glib2-devel',
    'gmodule-2.0': 'glib2-devel',
    'gthread-2.0': 'glib2-devel',
    
    # Misc
    'python': 'python3-devel',
    'python3': 'python3-devel',
    'perl': 'perl-devel',
    'lua': 'lua-devel',
    'dbus': 'dbus-devel',
    'dbus-1': 'dbus-devel',
    'udev': 'systemd-devel',
    'libudev': 'systemd-devel',
    'pcre': 'pcre-devel',
    'pcre2': 'pcre2-devel',
    'readline': 'readline-devel',
    'ncurses': 'ncurses-devel',
    'freetype': 'freetype-devel',
    'freetype2': 'freetype-devel',
    'fontconfig': 'fontconfig-devel',
    'cairo': 'cairo-devel',
    'pango': 'pango-devel',
    'harfbuzz': 'harfbuzz-devel',
    'boost': 'boost-devel',
    'eigen3': 'eigen3-devel',
    'fuse': 'fuse-devel',
    'fuse3': 'fuse3-devel',
    'libarchive': 'libarchive-devel',
    'squashfuse': 'squashfuse-devel',
    'argp': 'argp-standalone',
    
    # AppImage related
    'libappimage': 'libappimage-devel',
    'squashfs': 'squashfs-tools',
    
    # Build tools
    'aclocal': 'automake',
    'automake': 'automake',
    'autoconf': 'autoconf',
    'autoreconf': 'autoconf',
    'libtool': 'libtool',
    'libtoolize': 'libtool',
    'm4': 'm4',
    'pkg-config': 'pkgconf-pkg-config',
    'pkgconfig': 'pkgconf-pkg-config',
    'gettext': 'gettext-devel',
    'msgfmt': 'gettext',
    'intltool': 'intltool',
    'flex': 'flex',
    'bison': 'bison',
    'yacc': 'bison',
    'nasm': 'nasm',
    'yasm': 'yasm',
    'patch': 'patch',
    'sed': 'sed',
    'make': 'make',
    'autoheader': 'autoconf',
    'xxd': 'vim-common',  # hex dump tool bundled with vim
    
    # JSON/Config libraries (CMake find_package names)
    'nlohmann_json': 'json-devel',
    'nlohmann-json': 'json-devel',
    'json': 'json-devel',
    'rapidjson': 'rapidjson-devel',
    
    # Common CMake find_package names
    'threads': None,  # Built-in, no package needed
    'x11': 'libX11-devel',
    'x11_xpm': 'libXpm-devel',
    'libxpm': 'libXpm-devel',
    'xpm': 'libXpm-devel',
    
    # Qt5 packages (CMake names -> Fedora packages)
    # Keys must be lowercase for lookup to work
    'qt5': 'qt5-qtbase-devel',
    'qt5core': 'qt5-qtbase-devel',
    'qt5gui': 'qt5-qtbase-devel',
    'qt5widgets': 'qt5-qtbase-devel',
    'qt5network': 'qt5-qtbase-devel',
    'qt5quick': 'qt5-qtdeclarative-devel',
    'qt5qml': 'qt5-qtdeclarative-devel',
    'qt5quickcontrols2': 'qt5-qtquickcontrols2-devel',
    'qt5svg': 'qt5-qtsvg-devel',
    'qt5dbus': 'qt5-qtbase-devel',
    'qt5xml': 'qt5-qtbase-devel',
    'qt5concurrent': 'qt5-qtbase-devel',
    'qt5printsupport': 'qt5-qtbase-devel',
    'qt5opengl': 'qt5-qtbase-devel',
    'qt5multimedia': 'qt5-qtmultimedia-devel',
    'qt5webengine': 'qt5-qtwebengine-devel',
    'qt5websockets': 'qt5-qtwebsockets-devel',
    'qt5x11extras': 'qt5-qtx11extras-devel',
    'qt5waylandclient': 'qt5-qtwayland-devel',
    
    # Qt6 packages
    'qt6': 'qt6-qtbase-devel',
    'qt6core': 'qt6-qtbase-devel',
    'qt6gui': 'qt6-qtbase-devel',
    'qt6widgets': 'qt6-qtbase-devel',
    'qt6quick': 'qt6-qtdeclarative-devel',
    'qt6qml': 'qt6-qtdeclarative-devel',
    'qt6svg': 'qt6-qtsvg-devel',

    "lupdate": {
    "fedora": ["qt5-linguist"],
    "ubuntu": ["qttools5-dev-tools"],
    "debian": ["qttools5-dev-tools"],
    "arch": ["qt5-tools"],
    "opensuse": ["libqt5-linguist"],
    },
}

# Dependencies that are NOT in standard Fedora repos
# These require manual installation or COPR repos
UNPACKAGED_DEPENDENCIES = {
    'argagg': {
        'description': 'A simple C++11 command line argument parser (header-only)',
        'is_header_only': True,
        'github_url': 'https://github.com/vietjtnguyen/argagg',
        'install_instructions': '''# argagg is a header-only library
# Option 1: Download and install headers manually
git clone https://github.com/vietjtnguyen/argagg.git /tmp/argagg
sudo mkdir -p /usr/local/include/argagg
sudo cp /tmp/argagg/include/argagg/argagg.hpp /usr/local/include/argagg/
rm -rf /tmp/argagg

# Option 2: Use the header directly in your include path
# Download: https://raw.githubusercontent.com/vietjtnguyen/argagg/master/include/argagg/argagg.hpp''',
        'quick_install': [
            'git clone --depth 1 https://github.com/vietjtnguyen/argagg.git /tmp/argagg',
            'sudo mkdir -p /usr/local/include/argagg',
            'sudo cp /tmp/argagg/include/argagg/argagg.hpp /usr/local/include/argagg/',
            'rm -rf /tmp/argagg',
        ],
    },
    'nlohmann_json': {
        'description': 'JSON for Modern C++ (header-only)',
        'is_header_only': True,
        'fedora_package': 'json-devel',  # Actually available in Fedora
        'github_url': 'https://github.com/nlohmann/json',
    },
    'catch2': {
        'description': 'C++ test framework (header-only v2, compiled v3)',
        'is_header_only': True,
        'fedora_package': 'catch-devel',  # Available in Fedora
        'github_url': 'https://github.com/catchorg/Catch2',
    },
    'doctest': {
        'description': 'C++ testing framework (header-only)',
        'is_header_only': True,
        'github_url': 'https://github.com/doctest/doctest',
        'install_instructions': '''# doctest is header-only
wget https://raw.githubusercontent.com/doctest/doctest/master/doctest/doctest.h
sudo mkdir -p /usr/local/include/doctest
sudo mv doctest.h /usr/local/include/doctest/''',
        'quick_install': [
            'wget -q https://raw.githubusercontent.com/doctest/doctest/master/doctest/doctest.h -O /tmp/doctest.h',
            'sudo mkdir -p /usr/local/include/doctest',
            'sudo mv /tmp/doctest.h /usr/local/include/doctest/',
        ],
    },
    'fmt': {
        'description': 'Modern formatting library',
        'fedora_package': 'fmt-devel',  # Available in Fedora
        'github_url': 'https://github.com/fmtlib/fmt',
    },
    'spdlog': {
        'description': 'Fast C++ logging library',
        'fedora_package': 'spdlog-devel',  # Available in Fedora
        'github_url': 'https://github.com/gabime/spdlog',
    },
    'rang': {
        'description': 'Terminal colors for C++ (header-only)',
        'is_header_only': True,
        'github_url': 'https://github.com/agauniyal/rang',
        'install_instructions': '''# rang is header-only
wget https://raw.githubusercontent.com/agauniyal/rang/master/include/rang.hpp
sudo mv rang.hpp /usr/local/include/''',
        'quick_install': [
            'wget -q https://raw.githubusercontent.com/agauniyal/rang/master/include/rang.hpp -O /tmp/rang.hpp',
            'sudo mv /tmp/rang.hpp /usr/local/include/',
        ],
    },
    'cxxopts': {
        'description': 'Lightweight C++ option parser (header-only)',
        'is_header_only': True,
        'github_url': 'https://github.com/jarro2783/cxxopts',
        'fedora_package': 'cxxopts-devel',  # May be available
        'install_instructions': '''# cxxopts is header-only
git clone --depth 1 https://github.com/jarro2783/cxxopts.git /tmp/cxxopts
sudo cp -r /tmp/cxxopts/include/cxxopts.hpp /usr/local/include/
rm -rf /tmp/cxxopts''',
        'quick_install': [
            'git clone --depth 1 https://github.com/jarro2783/cxxopts.git /tmp/cxxopts',
            'sudo cp /tmp/cxxopts/include/cxxopts.hpp /usr/local/include/',
            'rm -rf /tmp/cxxopts',
        ],
    },
    'indicators': {
        'description': 'Activity indicators for C++ (header-only)',
        'is_header_only': True,
        'github_url': 'https://github.com/p-ranav/indicators',
        'install_instructions': '''# indicators is header-only
git clone --depth 1 https://github.com/p-ranav/indicators.git /tmp/indicators
sudo cp -r /tmp/indicators/include/indicators /usr/local/include/
rm -rf /tmp/indicators''',
        'quick_install': [
            'git clone --depth 1 https://github.com/p-ranav/indicators.git /tmp/indicators',
            'sudo cp -r /tmp/indicators/include/indicators /usr/local/include/',
            'rm -rf /tmp/indicators',
        ],
    },
}


def map_dependency_to_package(dep_name: str) -> Optional[str]:
    """Map a dependency name to a Fedora package name."""
    # Clean the dependency name - remove quotes, extra characters
    dep_clean = dep_name.strip().strip("'\"")
    dep_lower = dep_clean.lower()
    
    # Check unpackaged dependencies first (they may have fedora_package alternatives)
    if dep_lower in UNPACKAGED_DEPENDENCIES:
        unpackaged = UNPACKAGED_DEPENDENCIES[dep_lower]
        if 'fedora_package' in unpackaged:
            return unpackaged['fedora_package']
    
    # Direct lookup in main map
    if dep_lower in DEPENDENCY_MAP:
        return DEPENDENCY_MAP[dep_lower]
    
    # Try removing lib prefix
    if dep_lower.startswith('lib'):
        stripped = dep_lower[3:]
        if stripped in DEPENDENCY_MAP:
            return DEPENDENCY_MAP[stripped]
    
    # Try adding lib prefix
    with_lib = 'lib' + dep_lower
    if with_lib in DEPENDENCY_MAP:
        return DEPENDENCY_MAP[with_lib]
    
    # Try adding -devel suffix as guess, but use cleaned name
    return f"{dep_clean}-devel"


def get_dependency_info(dep_name: str, from_error: str = "") -> DependencyInfo:
    """
    Get comprehensive dependency information including unpackaged deps.
    Returns a DependencyInfo with all available metadata.
    """
    dep_clean = dep_name.strip().strip("'\"")
    dep_lower = dep_clean.lower()
    
    # Check if this is a known unpackaged dependency
    if dep_lower in UNPACKAGED_DEPENDENCIES:
        unpackaged = UNPACKAGED_DEPENDENCIES[dep_lower]
        return DependencyInfo(
            name=dep_clean,
            fedora_package=unpackaged.get('fedora_package'),
            description=unpackaged.get('description', ''),
            is_header_only=unpackaged.get('is_header_only', False),
            manual_install_url=unpackaged.get('github_url', ''),
            manual_install_cmd=unpackaged.get('install_instructions', ''),
            copr_repo=unpackaged.get('copr_repo', ''),
            not_in_repos=unpackaged.get('fedora_package') is None,
            install_selected=True,
        )
    
    # Standard dependency
    fedora_pkg = map_dependency_to_package(dep_clean)
    return DependencyInfo(
        name=dep_clean,
        fedora_package=fedora_pkg,
        description=from_error[:100] if from_error else f"Required by configure",
        install_selected=True,
    )


def check_package_available(package_name: str) -> bool:
    """Check if a package is available in Fedora repos."""
    try:
        result = subprocess.run(
            ['dnf', 'info', package_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except Exception:
        return True  # Assume available if we can't check


def parse_configure_errors(output: str) -> List[DependencyInfo]:
    """Parse configure output to find missing dependencies."""
    dependencies = []
    seen_packages = set()

    # Skip lines that are clearly not dependency errors
    skip_patterns = [
        r'^Command not found',
        r'^The required build tool',
        r'^Install with:',
        r'^Error running command',
        r'git commit',
        r'git command',
        r'git describe',
        r'gather commit ID',
        r'versioning',
        r'Call Stack',
        r'cmake_minimum_required',
        r'Compatibility with CMake',
        r'Update the VERSION',
    ]
    
    patterns = [
        # "checking for X... no"
        r'checking for (\S+)\.\.\. no',
        # "Package 'X' not found"
        r"Package '([^']+)' not found",
        # "No package 'X' found"
        r"No package '([^']+)' found",
        # "could not find X" (but not "Command not found")
        r'(?<!Command )could not find (\S+)',
        # "Could not find required program X" - CMake style
        r'Could not find required program (\S+)',
        r'Could not find program (\S+)',
        # "library X not found" or "libX not found"
        r'(?:library |lib)(\S+) not found',
        # "missing: X"
        r'missing:\s*(\S+)',
        # "requires X"
        r'requires\s+(\S+)',
        # pkg-config errors - be more specific
        r"Package '([^']+)' not found",
        r'Package\s+\'([^\']+)\'\s+not found',
        # CMake specific: Could NOT find X
        r'Could NOT find (\w+)',
        # CMake find_package: "package configuration file provided by X"
        r'package configuration file provided by\s+"([^"]+)"',
        # CMake find_package: "Findxxx.cmake" pattern
        r'not providing\s+"Find([^"]+)\.cmake"',
        # CMake pkg_check_modules format: "- libname"
        r'^\s+-\s+(\S+)\s*$',
        # Missing header files from compiler output
        r'fatal error:\s+([A-Za-z0-9_\-/\.]+)\s*:\s*No such file or directory',
        # Explicit header/library not found messages
        r'\b([A-Za-z0-9_\-]+)\s+header\s+not\s+found\b',
        # "X not found on system, please install" pattern (like argagg)
        r'\b([A-Za-z0-9_\-]+)\s+(?:header\s+)?not\s+found\s+on\s+system',
        # "please install X"
        r'please\s+install\s+([A-Za-z0-9_\-]+)',
        # CMake "Found X: Y-NOTFOUND" pattern
        r'Found\s+([A-Za-z0-9_\-]+):\s+\S*-NOTFOUND',
        r'Found\s+([A-Za-z0-9_\-]+):\s+[A-Za-z0-9_\-]+-NOTFOUND',
    ]
    
    # Common false positives to skip
    false_positives = {
        'yes', 'no', 'found', 'the', 'a', 'an', 'is', 'are', 'was', 'were',
        'not', 'command', 'error', 'warning', 'file', 'directory', 'to', 
        'for', 'in', 'on', 'at', 'by', 'or', 'and', 'if', 'it', 'be',
        'this', 'that', 'with', 'from', 'but', 'have', 'has', 'had',
        'do', 'does', 'did', 'will', 'would', 'could', 'should', 'can',
        'git', 'cache', 'commit', 'version', 'id', 'via', 'cmake', 'make',
        'required', 'packages', 'were', 'following', 'stack', 'call',
        'program', 'system',  # Added to avoid false positives
    }
    
    # Patterns that indicate bad/internal variable names, not real packages
    bad_package_patterns = [
        r'.*_LIBRARY$',      # CMake internal like LIBSSH2A_LIBRARY
        r'.*_INCLUDE.*',     # CMake internal like FOO_INCLUDE_DIR
        r'.*_DIR$',          # CMake internal
        r'.*_PATH$',         # CMake internal
        r'.*_ROOT$',         # CMake internal
        r'^[A-Z_]+$',        # All caps with underscores = CMake variable
    ]
    
    for line in output.split('\n'):
        # Skip lines matching skip patterns
        should_skip = False
        for skip_pat in skip_patterns:
            if re.search(skip_pat, line, re.IGNORECASE):
                should_skip = True
                break
        if should_skip:
            continue
        
        for pattern in patterns:
            for match in re.finditer(pattern, line, re.IGNORECASE):
                dep_name = match.group(1).strip()
                # Normalize header paths like "argagg/argagg.hpp" -> "argagg"
                if '/' in dep_name and dep_name.endswith(('.h', '.hpp')):
                    dep_name = dep_name.split('/')[0]
                # Clean name
                dep_name = dep_name.strip("'\".,;:")
                # Skip false positives
                if dep_name.lower() in false_positives:
                    continue
                # Skip if too short
                if len(dep_name) < 2:
                    continue
                # Skip if contains problematic characters (but allow - and _)
                if any(c in dep_name for c in ['(', ')', '[', ']', '{', '}', '/']):
                    continue
                
                # Skip CMake internal variable names
                is_bad_pattern = False
                for bad_pat in bad_package_patterns:
                    if re.match(bad_pat, dep_name):
                        is_bad_pattern = True
                        break
                if is_bad_pattern:
                    continue
                
                # Use the new get_dependency_info for comprehensive info
                dep_info = get_dependency_info(dep_name, line)
                
                # Use fedora_package or name as key for deduplication
                key = (dep_info.fedora_package or dep_name).lower()
                if key not in seen_packages:
                    seen_packages.add(key)
                    dependencies.append(dep_info)

    return dependencies


# =============================================================================
# WORKER THREADS
# =============================================================================

class ExtractionWorker(QThread):
    """Worker thread for extracting tarballs."""
    
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, result/error
    
    def __init__(self, tarball_path: str, extract_dir: str):
        super().__init__()
        self.tarball_path = tarball_path
        self.extract_dir = extract_dir
    
    def run(self):
        try:
            self.progress.emit(f"Extracting {os.path.basename(self.tarball_path)}...")
            
            with tarfile.open(self.tarball_path, 'r:*') as tar:
                # Get the root directory name
                members = tar.getmembers()
                if not members:
                    self.finished.emit(False, "Empty archive")
                    return
                
                # Find common prefix (source directory)
                first_member = members[0].name
                if '/' in first_member:
                    source_subdir = first_member.split('/')[0]
                else:
                    source_subdir = first_member
                
                # Extract all files
                tar.extractall(self.extract_dir)
                
                source_dir = os.path.join(self.extract_dir, source_subdir)
                if os.path.isdir(source_dir):
                    self.finished.emit(True, source_dir)
                else:
                    # No subdirectory, files extracted directly
                    self.finished.emit(True, self.extract_dir)
                    
        except Exception as e:
            self.finished.emit(False, str(e))


class CommandWorker(QThread):
    """Worker thread for running shell commands."""
    
    output = pyqtSignal(str)
    error_output = pyqtSignal(str)
    finished = pyqtSignal(bool, int, str, str)  # success, returncode, stdout, stderr
    progress = pyqtSignal(int, int)  # current, total (for compilation progress)
    
    def __init__(self, command: List[str], cwd: str, env: Optional[Dict] = None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.env = env or os.environ.copy()
        self._cancelled = False
        self.process = None
    
    def run(self):
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self.env,
                bufsize=1
            )
            
            stdout_lines = []
            stderr_lines = []
            
            while True:
                if self._cancelled:
                    self.process.terminate()
                    self.process.wait()
                    self.finished.emit(False, -1, '', 'Cancelled by user')
                    return
                
                # Check if process has finished
                poll = self.process.poll()
                
                # Read available output (non-blocking check)
                if self.process.stdout:
                    line = self.process.stdout.readline()
                    if line:
                        stdout_lines.append(line)
                        self.output.emit(line.rstrip())
                        self._parse_progress(line)
                
                if self.process.stderr:
                    line = self.process.stderr.readline()
                    if line:
                        stderr_lines.append(line)
                        self.error_output.emit(line.rstrip())
                
                if poll is not None:
                    # Process finished - drain any remaining output from pipes
                    # Read remaining stdout
                    while True:
                        line = self.process.stdout.readline()
                        if not line:
                            break
                        stdout_lines.append(line)
                        self.output.emit(line.rstrip())
                    
                    # Read remaining stderr
                    while True:
                        line = self.process.stderr.readline()
                        if not line:
                            break
                        stderr_lines.append(line)
                        self.error_output.emit(line.rstrip())
                    
                    break
            
            stdout = ''.join(stdout_lines)
            stderr = ''.join(stderr_lines)
            success = self.process.returncode == 0
            
            self.finished.emit(success, self.process.returncode, stdout, stderr)
            
        except FileNotFoundError as e:
            # Command not found - provide helpful error message
            cmd_name = self.command[0] if self.command else "unknown"
            error_msg = f"Command not found: {cmd_name}\n\nThe required build tool is not installed.\n"
            if cmd_name == "cmake":
                error_msg += "Install with: sudo dnf install cmake"
            elif cmd_name == "meson":
                error_msg += "Install with: sudo dnf install meson ninja-build"
            elif cmd_name == "ninja":
                error_msg += "Install with: sudo dnf install ninja-build"
            self.error_output.emit(error_msg)
            self.finished.emit(False, -1, '', error_msg)
        except Exception as e:
            error_msg = f"Error running command: {str(e)}"
            self.error_output.emit(error_msg)
            self.finished.emit(False, -1, '', error_msg)
    
    def _parse_progress(self, line: str):
        """Try to extract compilation progress from output."""
        # CMake/Ninja style: [45/120]
        match = re.search(r'\[(\d+)/(\d+)\]', line)
        if match:
            current = int(match.group(1))
            total = int(match.group(2))
            self.progress.emit(current, total)
            return
        
        # GCC compilation
        if 'Compiling' in line or '.o' in line:
            # Can't determine total, just emit activity
            self.progress.emit(-1, -1)
    
    def cancel(self):
        """Cancel the running command."""
        self._cancelled = True
        if self.process:
            self.process.terminate()


# =============================================================================
# WIZARD PAGES
# =============================================================================

class WelcomePage(QWizardPage):
    """Welcome and introduction page."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Welcome to the Source Code Compilation Wizard")
        self.setSubTitle("This wizard will guide you through compiling and installing software from source.")
        
        layout = QVBoxLayout()
        
        # Introduction text
        intro = QLabel(
            "You've selected a source code archive to compile. This wizard will:\n\n"
            "1. Extract the source code from the archive\n"
            "2. Detect the build system used by the software\n"
            "3. Configure the build for your system\n"
            "4. Automatically resolve missing dependencies\n"
            "5. Compile the software (this may take a while)\n"
            "6. Install the software to your chosen location\n"
            "7. Integrate with your KDE desktop\n\n"
            "You can cancel at any time. If something goes wrong, the wizard will\n"
            "clean up and provide helpful error information.\n\n"
            "Click Next to begin."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        
        # Show selected tarball
        layout.addSpacing(20)
        file_group = QGroupBox("Selected Archive")
        file_layout = QVBoxLayout()
        self.file_label = QLabel()
        self.file_label.setWordWrap(True)
        file_layout.addWidget(self.file_label)
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def initializePage(self):
        """Called when page is shown."""
        self.file_label.setText(f"<b>{os.path.basename(self.state.tarball_path)}</b>\n\n"
                                f"Location: {self.state.tarball_path}")


class InstallLocationPage(QWizardPage):
    """Page for choosing installation location."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Choose Installation Location")
        self.setSubTitle("Select where you want the software to be installed.")
        
        layout = QVBoxLayout()
        
        # Radio buttons for location choice
        self.button_group = QButtonGroup(self)
        
        # User-local option
        self.user_local_radio = QRadioButton("User-local installation (recommended)")
        self.user_local_radio.setChecked(True)
        self.button_group.addButton(self.user_local_radio)
        layout.addWidget(self.user_local_radio)
        
        user_local_desc = QLabel(
            "   • Installs to ~/.local (your home directory)\n"
            "   • No administrator password required\n"
            "   • Only available to your user account\n"
            "   • Easy to remove without affecting system"
        )
        user_local_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(user_local_desc)
        
        layout.addSpacing(15)
        
        # System-wide option
        self.system_wide_radio = QRadioButton("System-wide installation")
        self.button_group.addButton(self.system_wide_radio)
        layout.addWidget(self.system_wide_radio)
        
        system_wide_desc = QLabel(
            "   • Installs to /usr/local\n"
            "   • Requires administrator (sudo) password\n"
            "   • Available to all users on this computer\n"
            "   • Standard location for manually compiled software"
        )
        system_wide_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(system_wide_desc)
        
        layout.addStretch()
        
        # Warning for system-wide
        self.warning_label = QLabel(
            "⚠️ System-wide installation modifies files outside your home directory. "
            "Make sure you trust the software source."
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: orange;")
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)
        
        self.system_wide_radio.toggled.connect(self.warning_label.setVisible)
        
        self.setLayout(layout)
    
    def validatePage(self):
        """Save choice to state."""
        if self.user_local_radio.isChecked():
            self.state.install_location = InstallLocation.USER_LOCAL
            self.state.prefix = os.path.expanduser("~/.local")
        else:
            self.state.install_location = InstallLocation.SYSTEM_WIDE
            self.state.prefix = "/usr/local"
        return True


class BuildSystemDetectionPage(QWizardPage):
    """Page for detecting and displaying build system."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Build System Detection")
        self.setSubTitle("Analyzing the source code to determine how to build it...")
        
        layout = QVBoxLayout()
        
        # Status area
        self.status_label = QLabel("Extracting source archive...")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        layout.addWidget(self.progress_bar)
        
        layout.addSpacing(20)
        
        # Detection result
        self.result_group = QGroupBox("Detection Result")
        result_layout = QVBoxLayout()
        
        self.detected_label = QLabel()
        self.detected_label.setWordWrap(True)
        result_layout.addWidget(self.detected_label)
        
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setStyleSheet("color: gray;")
        result_layout.addWidget(self.description_label)
        
        self.result_group.setLayout(result_layout)
        self.result_group.setVisible(False)
        layout.addWidget(self.result_group)
        
        # Force build system option (shown if detection fails)
        self.force_group = QGroupBox("Manual Build System Selection")
        force_layout = QVBoxLayout()
        
        force_label = QLabel(
            "No build system was automatically detected. You can try forcing "
            "a build system, but this may not work correctly."
        )
        force_label.setWordWrap(True)
        force_layout.addWidget(force_label)
        
        self.force_combo = QComboBox()
        self.force_combo.addItems([
            "Select a build system...",
            "GNU Autotools (./configure)",
            "CMake",
            "Meson",
            "Plain Makefile"
        ])
        self.force_combo.currentIndexChanged.connect(self.completeChanged.emit)
        force_layout.addWidget(self.force_combo)
        
        self.force_group.setLayout(force_layout)
        self.force_group.setVisible(False)
        layout.addWidget(self.force_group)
        
        layout.addStretch()
        self.setLayout(layout)
        
        # Workers
        self.extraction_worker = None
        self.build_system = None
        self._detection_complete = False
    
    def initializePage(self):
        """Start extraction and detection when page is shown."""
        self._detection_complete = False
        self.result_group.setVisible(False)
        self.force_group.setVisible(False)
        self.progress_bar.setRange(0, 0)
        
        # Create temp directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state.extract_dir = tempfile.mkdtemp(
            prefix=f"source-compile-{timestamp}-"
        )
        
        # Start extraction
        self.extraction_worker = ExtractionWorker(
            self.state.tarball_path,
            self.state.extract_dir
        )
        self.extraction_worker.progress.connect(self._on_extraction_progress)
        self.extraction_worker.finished.connect(self._on_extraction_finished)
        self.extraction_worker.start()
    
    def _on_extraction_progress(self, message: str):
        """Handle extraction progress."""
        self.status_label.setText(message)
    
    def _on_extraction_finished(self, success: bool, result: str):
        """Handle extraction completion."""
        if not success:
            self.status_label.setText(f"❌ Extraction failed: {result}")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            return
        
        self.state.source_dir = result
        self.status_label.setText("Detecting build system...")
        
        # Detect project name from directory
        self.state.project_name = os.path.basename(result)
        # Clean up version numbers from name
        self.state.project_name = re.sub(r'[-_]?\d+\..*$', '', self.state.project_name)
        
        # Detect build system
        self.build_system = detect_build_system(result, self.state)
        
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        
        if self.build_system:
            self.state.build_system_name = self.build_system.name
            self.status_label.setText("✅ Build system detected!")
            self.result_group.setVisible(True)
            self.detected_label.setText(f"<b>{self.build_system.name}</b>")
            
            descriptions = {
                "GNU Autotools": "Traditional Unix build system using ./configure && make. "
                                 "Well-established and widely supported.",
                "CMake": "Modern cross-platform build system. Generates native build files.",
                "Meson": "Fast, modern build system using Ninja backend.",
                "Plain Makefile": "Direct Makefile without configure script. "
                                  "May have limited configuration options."
            }
            self.description_label.setText(descriptions.get(self.build_system.name, ""))
            self._detection_complete = True
        else:
            self.status_label.setText("⚠️ No build system detected")
            self.force_group.setVisible(True)
            self._detection_complete = False
        
        self.completeChanged.emit()
    
    def isComplete(self):
        """Check if page is complete."""
        if self._detection_complete:
            return True
        # Allow proceeding if force selection made
        if self.force_combo.currentIndex() > 0:
            return True
        return False
    
    def validatePage(self):
        """Validate and handle force selection."""
        if not self._detection_complete and self.force_combo.currentIndex() > 0:
            # Force build system
            self.state.build_system_forced = True
            force_map = {
                1: AutotoolsBuildSystem,
                2: CMakeBuildSystem,
                3: MesonBuildSystem,
                4: PlainMakefileBuildSystem
            }
            bs_class = force_map.get(self.force_combo.currentIndex())
            if bs_class:
                self.build_system = bs_class(self.state.source_dir, self.state)
                self.state.build_system_name = self.build_system.name
        
        # Store build system in wizard for later pages
        wizard = self.wizard()
        if wizard:
            wizard.build_system = self.build_system
        
        return self.build_system is not None


class ConfigurationModePage(QWizardPage):
    """Page for selecting basic or advanced configuration mode."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Configuration Mode")
        self.setSubTitle("Choose how to configure the build options.")
        
        layout = QVBoxLayout()
        
        self.button_group = QButtonGroup(self)
        
        # Basic mode
        self.basic_radio = QRadioButton("Basic Mode (recommended)")
        self.basic_radio.setChecked(True)
        self.button_group.addButton(self.basic_radio)
        layout.addWidget(self.basic_radio)
        
        basic_desc = QLabel(
            "   • Uses default configuration options\n"
            "   • Suitable for most users\n"
            "   • Faster and simpler process"
        )
        basic_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(basic_desc)
        
        layout.addSpacing(15)
        
        # Advanced mode
        self.advanced_radio = QRadioButton("Advanced Mode")
        self.button_group.addButton(self.advanced_radio)
        layout.addWidget(self.advanced_radio)
        
        advanced_desc = QLabel(
            "   • Shows all available configuration options\n"
            "   • Allows enabling/disabling specific features\n"
            "   • For experienced users who need customization"
        )
        advanced_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(advanced_desc)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def validatePage(self):
        """Save choice to state."""
        if self.basic_radio.isChecked():
            self.state.config_mode = ConfigMode.BASIC
        else:
            self.state.config_mode = ConfigMode.ADVANCED
        return True
    
    def nextId(self):
        """Skip advanced options page if basic mode."""
        if self.basic_radio.isChecked():
            # Skip to dependency resolution (page 5)
            return 5
        return super().nextId()


class AdvancedConfigPage(QWizardPage):
    """Page for advanced configuration options."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Advanced Configuration Options")
        self.setSubTitle("Select which features to enable or disable.")
        
        layout = QVBoxLayout()
        
        # Status label
        self.status_label = QLabel("Loading configuration options...")
        layout.addWidget(self.status_label)
        
        # Scrollable options area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        self.options_widget = QWidget()
        self.options_layout = QVBoxLayout()
        self.options_widget.setLayout(self.options_layout)
        
        scroll.setWidget(self.options_widget)
        layout.addWidget(scroll)
        
        self.setLayout(layout)
        self.checkboxes = []
    
    def initializePage(self):
        """Load configuration options when page is shown."""
        # Clear existing options
        for cb in self.checkboxes:
            self.options_layout.removeWidget(cb)
            cb.deleteLater()
        self.checkboxes.clear()
        
        wizard = self.wizard()
        if not wizard or not hasattr(wizard, 'build_system'):
            self.status_label.setText("Error: Build system not available")
            return
        
        build_system = wizard.build_system
        
        # Get help output and parse options
        help_text = build_system.get_help_output()
        options = build_system.parse_config_options(help_text)
        
        if not options:
            self.status_label.setText(
                "No configurable options detected. Click Next to continue."
            )
            return
        
        self.status_label.setText(f"Found {len(options)} configuration options:")
        
        self.state.config_options = options
        
        for opt in options:
            cb = QCheckBox(opt.name)
            cb.setToolTip(opt.description)
            cb.setChecked(opt.default_enabled)
            self.options_layout.addWidget(cb)
            
            if opt.description:
                desc = QLabel(f"   {opt.description[:100]}...")
                desc.setStyleSheet("color: gray; font-size: 10px;")
                self.options_layout.addWidget(desc)
            
            self.checkboxes.append(cb)
        
        self.options_layout.addStretch()
    
    def validatePage(self):
        """Save selected options to state."""
        self.state.selected_options.clear()
        
        for i, cb in enumerate(self.checkboxes):
            if cb.isChecked():
                opt = self.state.config_options[i]
                self.state.selected_options.append(opt.name)
        
        return True


class DependencyResolutionPage(QWizardPage):
    """Page for running configure and resolving dependencies."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Dependency Resolution")
        self.setSubTitle("Checking for required dependencies...")
        
        layout = QVBoxLayout()
        
        # Status
        self.status_label = QLabel("Running configuration...")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,  0)
        layout.addWidget(self.progress_bar)
        
        # Fix progress label (for git versioning fixes)
        self.fix_progress_label = QLabel("")
        self.fix_progress_label.setStyleSheet("color: blue; font-style: italic;")
        self.fix_progress_label.setVisible(False)
        layout.addWidget(self.fix_progress_label)
        
        # Output display
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumHeight(150)
        self.output_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.output_text)
        
        # Git versioning fix group (hidden initially)
        self.git_fix_group = QGroupBox("Git Versioning Issue Detected")
        git_fix_layout = QVBoxLayout()
        
        self.git_fix_label = QLabel()
        self.git_fix_label.setWordWrap(True)
        git_fix_layout.addWidget(self.git_fix_label)
        
        self.git_issues_label = QLabel()
        self.git_issues_label.setWordWrap(True)
        self.git_issues_label.setStyleSheet("color: gray; font-size: 10px;")
        git_fix_layout.addWidget(self.git_issues_label)
        
        git_btn_layout = QHBoxLayout()
        self.auto_fix_btn = QPushButton("🔧 Apply Automatic Fix")
        self.auto_fix_btn.clicked.connect(self._apply_git_versioning_fix)
        self.auto_fix_btn.setStyleSheet("font-weight: bold;")
        git_btn_layout.addWidget(self.auto_fix_btn)
        
        self.skip_fix_btn = QPushButton("Skip (Show Manual Options)")
        self.skip_fix_btn.clicked.connect(self._show_manual_git_options)
        git_btn_layout.addWidget(self.skip_fix_btn)
        git_fix_layout.addLayout(git_btn_layout)
        
        self.git_fix_group.setLayout(git_fix_layout)
        self.git_fix_group.setVisible(False)
        layout.addWidget(self.git_fix_group)
        
        # Dependencies list
        self.deps_group = QGroupBox("Missing Dependencies")
        deps_layout = QVBoxLayout()
        
        # Explanation label
        self.deps_explanation = QLabel(
            "The following Fedora packages need to be installed before compilation can proceed:"
        )
        self.deps_explanation.setWordWrap(True)
        deps_layout.addWidget(self.deps_explanation)
        
        self.deps_scroll = QScrollArea()
        self.deps_scroll.setWidgetResizable(True)
        self.deps_widget = QWidget()
        self.deps_layout = QVBoxLayout()
        self.deps_widget.setLayout(self.deps_layout)
        self.deps_scroll.setWidget(self.deps_widget)
        deps_layout.addWidget(self.deps_scroll)
        
        # Sudo access section
        sudo_frame = QFrame()
        sudo_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        sudo_layout = QVBoxLayout()
        
        self.sudo_checkbox = QCheckBox("Allow wizard to install packages (requires sudo password)")
        self.sudo_checkbox.setChecked(False)
        self.sudo_checkbox.toggled.connect(self._on_sudo_toggled)
        sudo_layout.addWidget(self.sudo_checkbox)
        
        self.sudo_info_label = QLabel(
            "   If unchecked, you can copy the command below and run it manually in a terminal."
        )
        self.sudo_info_label.setStyleSheet("color: gray; font-size: 10px;")
        sudo_layout.addWidget(self.sudo_info_label)
        
        sudo_frame.setLayout(sudo_layout)
        deps_layout.addWidget(sudo_frame)
        
        # Manual command display
        self.manual_cmd_group = QGroupBox("Manual Installation Command")
        manual_cmd_layout = QVBoxLayout()
        
        self.manual_cmd_label = QLabel()
        self.manual_cmd_label.setWordWrap(True)
        self.manual_cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.manual_cmd_label.setStyleSheet("font-family: monospace; background-color: #2d2d2d; color: #f0f0f0; padding: 8px; border-radius: 4px;")
        manual_cmd_layout.addWidget(self.manual_cmd_label)
        
        copy_btn_layout = QHBoxLayout()
        self.copy_cmd_btn = QPushButton("📋 Copy Command")
        self.copy_cmd_btn.clicked.connect(self._copy_install_command)
        copy_btn_layout.addWidget(self.copy_cmd_btn)
        copy_btn_layout.addStretch()
        manual_cmd_layout.addLayout(copy_btn_layout)
        
        self.manual_cmd_group.setLayout(manual_cmd_layout)
        self.manual_cmd_group.setVisible(True)
        deps_layout.addWidget(self.manual_cmd_group)
        
        # Install button
        install_btn_layout = QHBoxLayout()
        self.install_deps_btn = QPushButton("🔧 Install Selected Packages")
        self.install_deps_btn.clicked.connect(self._install_dependencies)
        self.install_deps_btn.setEnabled(False)  # Disabled until sudo checkbox is checked
        self.install_deps_btn.setStyleSheet("font-weight: bold; padding: 8px 16px;")
        install_btn_layout.addWidget(self.install_deps_btn)
        
        self.retry_config_btn = QPushButton("🔄 Retry Configuration")
        self.retry_config_btn.clicked.connect(self._retry_configuration)
        self.retry_config_btn.setVisible(False)
        install_btn_layout.addWidget(self.retry_config_btn)
        
        install_btn_layout.addStretch()
        deps_layout.addLayout(install_btn_layout)
        
        self.deps_group.setLayout(deps_layout)
        self.deps_group.setVisible(False)
        layout.addWidget(self.deps_group)
        
        # Success indicator
        self.success_label = QLabel()
        self.success_label.setWordWrap(True)
        self.success_label.setVisible(False)
        layout.addWidget(self.success_label)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.worker = None
        self.dep_checkboxes = []
        self._configure_success = False
        self._git_fixer: Optional[GitVersioningFixer] = None
        self._last_output = ""
        self._git_fix_attempted = False
    
    def _on_sudo_toggled(self, checked: bool):
        """Handle sudo checkbox toggle."""
        self.install_deps_btn.setEnabled(checked and len(self.dep_checkboxes) > 0)
        if checked:
            self.manual_cmd_group.setVisible(False)
            self.install_deps_btn.setText("🔧 Install Selected Packages")
        else:
            self.manual_cmd_group.setVisible(True)
            self._update_manual_command()
    
    def _update_manual_command(self):
        """Update the manual installation command display."""
        packages = self._get_selected_packages()
        if packages:
            cmd = f"sudo dnf install {' '.join(packages)}"
            self.manual_cmd_label.setText(cmd)
        else:
            self.manual_cmd_label.setText("(No packages selected)")
    
    def _get_selected_packages(self) -> List[str]:
        """Get list of selected package names."""
        packages = []
        for i, cb in enumerate(self.dep_checkboxes):
            if cb.isChecked():
                dep = self.state.dependencies[i]
                if dep.fedora_package:
                    packages.append(dep.fedora_package)
        return packages
    
    def _copy_install_command(self):
        """Copy the installation command to clipboard."""
        packages = self._get_selected_packages()
        if packages:
            cmd = f"sudo dnf install {' '.join(packages)}"
            clipboard = QApplication.clipboard()
            clipboard.setText(cmd)
            
            # Visual feedback
            old_text = self.copy_cmd_btn.text()
            self.copy_cmd_btn.setText("✓ Copied!")
            QTimer.singleShot(2000, lambda: self.copy_cmd_btn.setText(old_text))
    
    def _retry_configuration(self):
        """Retry configuration after manual package installation."""
        self.deps_group.setVisible(False)
        self.output_text.clear()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Re-running configuration...")
        self._run_configure()
    
    def initializePage(self):
        """Run configure when page is shown."""
        self._configure_success = False
        self._git_fix_attempted = False
        self.deps_group.setVisible(False)
        self.git_fix_group.setVisible(False)
        self.success_label.setVisible(False)
        self.output_text.clear()
        self.progress_bar.setRange(0, 0)
        
        self._run_configure()
    
    def _run_configure(self, extra_cmake_args: List[str] = None):
        """Run the configure command."""
        wizard = self.wizard()
        if not wizard or not hasattr(wizard, 'build_system'):
            self.status_label.setText("Error: Build system not available")
            return
        
        build_system = wizard.build_system
        cmd = build_system.get_configure_command()
        
        if not cmd:
            # No configure needed (plain Makefile)
            self.status_label.setText("✅ No configuration required")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self._configure_success = True
            self.success_label.setText("✅ Ready to compile!")
            self.success_label.setVisible(True)
            self.completeChanged.emit()
            return
        
        # Add extra CMake args if provided (for git versioning fixes)
        if extra_cmake_args and 'cmake' in cmd[0].lower():
            cmd = cmd + extra_cmake_args
        
        self.status_label.setText(f"Running: {' '.join(cmd)}")
        
        self.worker = CommandWorker(cmd, self.state.source_dir)
        self.worker.output.connect(self._on_output)
        self.worker.error_output.connect(self._on_output)
        self.worker.finished.connect(self._on_configure_finished)
        self.worker.start()
    
    def _on_output(self, line: str):
        """Handle output from configure."""
        self.output_text.appendPlainText(line)
        # Auto-scroll
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )
    
    def _on_configure_finished(self, success: bool, returncode: int, 
                                stdout: str, stderr: str):
        """Handle configure completion."""
        self.state.full_stdout += stdout
        self.state.full_stderr += stderr
        self._last_output = stdout + stderr
        
        self.progress_bar.setRange(0, 1)
        
        if success:
            self.progress_bar.setValue(1)
            self.status_label.setText("✅ Configuration successful!")
            self.success_label.setText("✅ All dependencies satisfied. Ready to compile!")
            self.success_label.setStyleSheet("color: green;")
            self.success_label.setVisible(True)
            self._configure_success = True
            self.git_fix_group.setVisible(False)
            self.completeChanged.emit()
        else:
            self.progress_bar.setValue(0)
            
            # Show error output in the text area if not already shown
            full_output = stdout + stderr
            if full_output.strip() and self.output_text.toPlainText().strip() == "":
                self.output_text.setPlainText(full_output)
            
            # Check if this is a missing build tool (not a dependency issue)
            is_missing_tool = "Command not found" in full_output or "FileNotFoundError" in full_output
            
            # Check if this is a git versioning error using our improved detection
            is_git_version_error = is_git_versioning_error(full_output)
            
            if is_missing_tool:
                # Don't try to parse dependencies - the build tool itself is missing
                self.status_label.setText("❌ Build tool not installed")
                
                error_detail = "The required build tool is not installed.\n\n"
                if "cmake" in self.state.build_system_name.lower():
                    error_detail += "Install CMake with:\n  sudo dnf install cmake"
                elif "meson" in self.state.build_system_name.lower():
                    error_detail += "Install Meson with:\n  sudo dnf install meson ninja-build"
                else:
                    error_detail += "Please install the required build tools and try again."
                
                self.success_label.setText(error_detail)
                self.success_label.setStyleSheet("color: red;")
                self.success_label.setVisible(True)
            elif is_git_version_error and not self._git_fix_attempted:
                # This is a tarball that expects git version info - offer automatic fix
                self._handle_git_versioning_error(full_output)
            else:
                self.status_label.setText("⚠️ Configuration failed - checking for missing dependencies...")
                
                # Parse for missing dependencies
                deps = parse_configure_errors(full_output)
                
                if deps:
                    self._show_dependencies(deps)
                else:
                    self.status_label.setText(
                        "❌ Configuration failed. Unable to automatically determine missing dependencies."
                    )
                    error_msg = (
                        "The configuration step failed. You may need to manually install dependencies "
                        "or check the output above for error messages."
                    )
                    if self._git_fix_attempted:
                        error_msg += "\n\nNote: Git versioning fixes were applied but the build still failed. "
                        error_msg += "This may be a different issue."
                    self.success_label.setText(error_msg)
                    self.success_label.setStyleSheet("color: red;")
                    self.success_label.setVisible(True)
    
    def _handle_git_versioning_error(self, output: str):
        """Handle a detected git versioning error."""
        self.status_label.setText("⚠️ Git Versioning Issue Detected")
        
        # Create the fixer
        self._git_fixer = GitVersioningFixer(
            self.state.source_dir,
            self.state.tarball_path
        )
        
        # Detect specific issues
        issues = self._git_fixer.detect_issues(output)
        
        # Build explanation text
        explanation = (
            "This project's build system requires git version information, but you're "
            "building from a source tarball instead of a git clone.\n\n"
            "The wizard can automatically fix this by creating the necessary version files "
            "and/or initializing a minimal git repository."
        )
        self.git_fix_label.setText(explanation)
        
        # Show detected issues
        if issues:
            issue_text = "Detected issues:\n"
            for issue in issues:
                issue_text += f"  • {issue.description}\n"
                issue_text += f"    Fix: {issue.fix_description}\n"
            self.git_issues_label.setText(issue_text)
        else:
            self.git_issues_label.setText("Generic git versioning error detected.")
        
        self.git_fix_group.setVisible(True)
        self.success_label.setVisible(False)
    
    def _apply_git_versioning_fix(self):
        """Apply automatic git versioning fixes."""
        if not self._git_fixer:
            QMessageBox.warning(self, "Error", "Git versioning fixer not initialized")
            return
        
        self.auto_fix_btn.setEnabled(False)
        self.skip_fix_btn.setEnabled(False)
        self.status_label.setText("🔧 Applying git versioning fixes...")
        self.fix_progress_label.setVisible(True)
        self.fix_progress_label.setText("Starting fixes...")
        
        # Set up progress callback
        def on_progress(message: str):
            self.fix_progress_label.setText(message)
            self.output_text.appendPlainText(f"  → {message}")
            QApplication.processEvents()  # Keep UI responsive
        
        self._git_fixer.progress_callback = on_progress
        
        # Process events to show initial state
        QApplication.processEvents()
        
        # Apply fixes
        success, message = self._git_fixer.apply_fixes()
        
        self.fix_progress_label.setVisible(False)
        
        if success:
            self.output_text.appendPlainText(f"\n=== Git Versioning Fix ===\n{message}\n")
            self.status_label.setText("✅ Fixes applied! Re-running configuration...")
            self._git_fix_attempted = True
            self.git_fix_group.setVisible(False)
            
            # Get any extra CMake args the fixer suggests
            extra_args = self._git_fixer.get_cmake_extra_args()
            
            # Re-run configure with the fixes in place
            self.output_text.clear()
            self.progress_bar.setRange(0, 0)
            QApplication.processEvents()  # Show the cleared state
            self._run_configure(extra_args)
        else:
            self.auto_fix_btn.setEnabled(True)
            self.skip_fix_btn.setEnabled(True)
            self.status_label.setText("❌ Some fixes failed")
            self.output_text.appendPlainText(f"\n=== Git Versioning Fix ===\n{message}\n")
            QMessageBox.warning(
                self, "Partial Fix",
                f"Some fixes could not be applied:\n\n{message}\n\n"
                "You may need to apply manual fixes."
            )
    
    def _show_manual_git_options(self):
        """Show manual options for git versioning issues."""
        self.git_fix_group.setVisible(False)
        
        error_detail = (
            "Manual solutions for git versioning issues:\n\n"
            "1. Clone from git instead:\n"
            "   git clone <repository-url>\n"
            "   cd <project> && mkdir build && cd build\n"
            "   cmake .. && make\n\n"
            "2. Download a release tarball that includes version info\n"
            "   (often on the project's GitHub Releases page, not 'Download ZIP')\n\n"
            "3. Check if the project documents building from tarballs\n\n"
            "4. Manually create version files - check the project's cmake/ directory\n"
            "   for versioning.cmake or similar files to understand the format needed."
        )
        
        self.success_label.setText(error_detail)
        self.success_label.setStyleSheet("color: orange;")
        self.success_label.setVisible(True)
    
    def _show_dependencies(self, deps: List[DependencyInfo]):
        """Display detected dependencies."""
        # Clear existing
        for cb in self.dep_checkboxes:
            self.deps_layout.removeWidget(cb)
            cb.deleteLater()
        self.dep_checkboxes.clear()
        
        # Also clear any description labels
        while self.deps_layout.count() > 0:
            item = self.deps_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.state.dependencies = deps
        
        # Separate packaged vs unpackaged dependencies
        packaged_deps = []
        unpackaged_deps = []
        
        for dep in deps:
            if dep.not_in_repos or (dep.is_header_only and not dep.fedora_package):
                unpackaged_deps.append(dep)
            else:
                packaged_deps.append(dep)
        
        # Show packaged dependencies (can be installed via dnf)
        if packaged_deps:
            packaged_label = QLabel("<b>📦 Available via dnf:</b>")
            packaged_label.setStyleSheet("margin-top: 5px;")
            self.deps_layout.addWidget(packaged_label)
            
            for dep in packaged_deps:
                pkg_name = dep.fedora_package or f"{dep.name}-devel"
                
                dep_frame = QFrame()
                dep_frame.setFrameStyle(QFrame.Shape.NoFrame)
                dep_layout = QHBoxLayout()
                dep_layout.setContentsMargins(15, 2, 0, 2)
                
                cb = QCheckBox()
                cb.setChecked(dep.install_selected)
                cb.toggled.connect(self._on_dep_selection_changed)
                dep_layout.addWidget(cb)
                
                pkg_label = QLabel(f"<b>{pkg_name}</b>")
                pkg_label.setStyleSheet("font-size: 11px;")
                dep_layout.addWidget(pkg_label)
                
                if dep.description and dep.description != "Required by configure":
                    desc_label = QLabel(f"<i>({dep.description[:50]}...)</i>")
                    desc_label.setStyleSheet("color: gray; font-size: 10px;")
                    dep_layout.addWidget(desc_label)
                
                dep_layout.addStretch()
                dep_frame.setLayout(dep_layout)
                self.deps_layout.addWidget(dep_frame)
                self.dep_checkboxes.append(cb)
        
        # Show unpackaged dependencies (require manual installation)
        if unpackaged_deps:
            # Add separator
            if packaged_deps:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                separator.setStyleSheet("margin: 10px 0;")
                self.deps_layout.addWidget(separator)
            
            unpackaged_label = QLabel(
                "<b>⚠️ Not in Fedora repos (manual installation required):</b>"
            )
            unpackaged_label.setStyleSheet("margin-top: 5px; color: orange;")
            self.deps_layout.addWidget(unpackaged_label)
            
            for dep in unpackaged_deps:
                dep_frame = QFrame()
                dep_frame.setFrameStyle(QFrame.Shape.StyledPanel)
                dep_frame.setStyleSheet("background-color: #fff3cd; border-radius: 4px; padding: 5px; margin: 3px 0;")
                dep_layout = QVBoxLayout()
                dep_layout.setContentsMargins(10, 5, 10, 5)
                
                # Name and description
                name_label = QLabel(f"<b>{dep.name}</b>")
                if dep.is_header_only:
                    name_label.setText(f"<b>{dep.name}</b> <span style='color: blue;'>(header-only)</span>")
                dep_layout.addWidget(name_label)
                
                if dep.description:
                    desc_label = QLabel(dep.description)
                    desc_label.setStyleSheet("color: #664d03; font-size: 10px;")
                    desc_label.setWordWrap(True)
                    dep_layout.addWidget(desc_label)
                
                # Installation instructions
                if dep.manual_install_cmd:
                    install_label = QLabel("<b>Install with:</b>")
                    install_label.setStyleSheet("margin-top: 5px; font-size: 10px;")
                    dep_layout.addWidget(install_label)
                    
                    # Show abbreviated instructions
                    instructions = dep.manual_install_cmd
                    if len(instructions) > 300:
                        instructions = instructions[:300] + "..."
                    
                    cmd_label = QLabel(f"<pre style='background-color: #2d2d2d; color: #f0f0f0; padding: 5px; font-size: 9px;'>{instructions}</pre>")
                    cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    cmd_label.setWordWrap(True)
                    dep_layout.addWidget(cmd_label)
                
                # Copy button for quick install if available
                if dep.name.lower() in UNPACKAGED_DEPENDENCIES:
                    unpack_info = UNPACKAGED_DEPENDENCIES[dep.name.lower()]
                    if 'quick_install' in unpack_info:
                        btn_layout = QHBoxLayout()
                        install_btn = QPushButton("📋 Copy Quick Install Commands")
                        install_btn.setStyleSheet("font-size: 10px; padding: 3px 8px;")
                        
                        # Store the commands for the button
                        quick_cmds = unpack_info['quick_install']
                        full_cmd = ' && '.join(quick_cmds)
                        
                        install_btn.clicked.connect(
                            lambda checked, cmd=full_cmd, btn=install_btn: self._copy_manual_install(cmd, btn)
                        )
                        btn_layout.addWidget(install_btn)
                        btn_layout.addStretch()
                        dep_layout.addLayout(btn_layout)
                
                if dep.manual_install_url:
                    url_label = QLabel(f"<a href='{dep.manual_install_url}'>📎 GitHub Repository</a>")
                    url_label.setOpenExternalLinks(True)
                    url_label.setStyleSheet("font-size: 10px;")
                    dep_layout.addWidget(url_label)
                
                dep_frame.setLayout(dep_layout)
                self.deps_layout.addWidget(dep_frame)
                
                # Add a disabled checkbox for tracking (won't be installed via dnf)
                cb = QCheckBox()
                cb.setChecked(False)
                cb.setEnabled(False)  # Can't be auto-installed
                cb.setVisible(False)  # Hidden but tracked
                self.dep_checkboxes.append(cb)
        
        self.deps_layout.addStretch()
        
        # Update explanation based on what we found
        if unpackaged_deps and not packaged_deps:
            self.deps_explanation.setText(
                "⚠️ The missing dependencies are not available in Fedora repositories. "
                "You'll need to install them manually before continuing."
            )
            self.deps_explanation.setStyleSheet("color: orange;")
            # Show retry button since user needs to install manually then retry
            self.retry_config_btn.setVisible(True)
            self.retry_config_btn.setEnabled(True)
        elif unpackaged_deps and packaged_deps:
            self.deps_explanation.setText(
                "Some dependencies are available via dnf, others require manual installation. "
                "Install the manual dependencies first, then use dnf for the rest."
            )
            self.deps_explanation.setStyleSheet("")
        else:
            self.deps_explanation.setText(
                "The following Fedora packages need to be installed before compilation can proceed:"
            )
            self.deps_explanation.setStyleSheet("")
        
        # Update manual command and button state
        self._update_manual_command()
        self._on_sudo_toggled(self.sudo_checkbox.isChecked())
        
        # Show retry button for manual installation flow
        self.retry_config_btn.setVisible(True)
        
        self.deps_group.setVisible(True)
    
    def _copy_manual_install(self, cmd: str, btn: QPushButton):
        """Copy manual installation command to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(cmd)
        
        old_text = btn.text()
        btn.setText("✓ Copied!")
        QTimer.singleShot(2000, lambda: btn.setText(old_text))
    
    def _on_dep_selection_changed(self):
        """Handle dependency selection change."""
        self._update_manual_command()
        # Update install button state
        has_selection = any(cb.isChecked() for cb in self.dep_checkboxes)
        self.install_deps_btn.setEnabled(self.sudo_checkbox.isChecked() and has_selection)
    
    def _install_dependencies(self):
        """Install selected dependencies via dnf."""
        packages = self._get_selected_packages()
        
        if not packages:
            QMessageBox.warning(self, "No Selection", 
                               "Please select at least one package to install.")
            return
        
        # Confirm with user
        reply = QMessageBox.question(
            self, "Install Packages",
            f"The following packages will be installed:\n\n"
            f"{chr(10).join('  • ' + p for p in packages)}\n\n"
            "You will be prompted for your password in a separate window.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Run dnf install using pkexec for graphical sudo prompt
        self.install_deps_btn.setEnabled(False)
        self.sudo_checkbox.setEnabled(False)
        self.status_label.setText(f"Installing {len(packages)} package(s)...")
        self.progress_bar.setRange(0, 0)
        
        # Use pkexec for graphical authentication (works with KDE/Polkit)
        # pkexec will show a graphical password dialog
        cmd = ["pkexec", "dnf", "install", "-y"] + packages
        
        self.worker = CommandWorker(cmd, self.state.source_dir)
        self.worker.output.connect(self._on_output)
        self.worker.error_output.connect(self._on_output)
        self.worker.finished.connect(self._on_deps_installed)
        self.worker.start()
    
    def _on_deps_installed(self, success: bool, returncode: int,
                           stdout: str, stderr: str):
        """Handle dependency installation completion."""
        self.install_deps_btn.setEnabled(True)
        self.sudo_checkbox.setEnabled(True)
        self.progress_bar.setRange(0, 1)
        
        if success:
            self.progress_bar.setValue(1)
            self.status_label.setText("✅ Packages installed! Re-running configuration...")
            # Re-run configure
            self.deps_group.setVisible(False)
            self.output_text.clear()
            self.progress_bar.setRange(0, 0)
            self._run_configure()
        else:
            self.progress_bar.setValue(0)
            self.status_label.setText("❌ Failed to install packages")
            
            # Check for common error conditions
            full_output = (stdout + stderr).lower()
            
            if returncode == 126 or returncode == 127:
                # pkexec returns 126 for dismissed dialog, 127 for not authorized
                QMessageBox.warning(
                    self, "Authentication Cancelled",
                    "Package installation was cancelled.\n\n"
                    "You can try again or install packages manually using the command shown below."
                )
                # Show manual command option
                self.sudo_checkbox.setChecked(False)
            elif "not authorized" in full_output or "authorization" in full_output:
                QMessageBox.warning(
                    self, "Authorization Failed", 
                    "You are not authorized to install packages.\n\n"
                    "You can install packages manually using the command shown below."
                )
                self.sudo_checkbox.setChecked(False)
            elif "no package" in full_output or "not found" in full_output:
                QMessageBox.warning(
                    self, "Package Not Found",
                    f"Some packages could not be found in the repositories.\n\n"
                    "You may need to enable additional repositories or install packages manually."
                )
            else:
                QMessageBox.critical(
                    self, "Installation Failed",
                    f"Failed to install packages:\n\n{stderr[:500] if stderr else stdout[:500]}"
                )
    
    def isComplete(self):
        """Page is complete when configure succeeds."""
        return self._configure_success


class CompilationPage(QWizardPage):
    """Page for running the compilation."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Compiling Software")
        self.setSubTitle("Building the software from source code...")
        
        layout = QVBoxLayout()
        
        # Status
        self.status_label = QLabel("Starting compilation...")
        layout.addWidget(self.status_label)
        
        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)
        
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: gray;")
        layout.addWidget(self.progress_label)
        
        # Output
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.output_text)
        
        # Error handling buttons (hidden initially)
        self.error_group = QGroupBox("Compilation Failed")
        error_layout = QVBoxLayout()
        
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        error_layout.addWidget(self.error_label)
        
        btn_layout = QHBoxLayout()
        self.view_log_btn = QPushButton("View Full Log")
        self.view_log_btn.clicked.connect(self._view_full_log)
        btn_layout.addWidget(self.view_log_btn)
        
        self.retry_single_btn = QPushButton("Retry Single-Threaded")
        self.retry_single_btn.clicked.connect(self._retry_single_threaded)
        btn_layout.addWidget(self.retry_single_btn)
        
        error_layout.addLayout(btn_layout)
        
        self.llm_help = QLabel(
            "💡 <b>Tip:</b> For help understanding this error, consider using an AI assistant "
            "like Claude. Click 'View Full Log' to copy the error details."
        )
        self.llm_help.setWordWrap(True)
        error_layout.addWidget(self.llm_help)
        
        self.error_group.setLayout(error_layout)
        self.error_group.setVisible(False)
        layout.addWidget(self.error_group)
        
        self.setLayout(layout)
        
        self.worker = None
        self._compile_success = False
        self._jobs = 1
    
    def initializePage(self):
        self._compile_success = False
        self.error_group.setVisible(False)
        self.output_text.clear()
        self.progress_bar.setRange(0, 0)
        
        try:
            nproc = os.cpu_count() or 2
            self._jobs = max(1, nproc - 1)
        except:
            self._jobs = 2
        
        self.state.start_time = datetime.now()
        self._run_compilation(self._jobs)
    
    def _run_compilation(self, jobs: int):
        wizard = self.wizard()
        if not wizard or not hasattr(wizard, 'build_system'):
            self.status_label.setText("Error: Build system not available")
            return
        
        build_system = wizard.build_system
        cmd = build_system.get_build_command(jobs)
        
        cwd = self.state.source_dir
        if hasattr(build_system, 'build_dir') and os.path.exists(build_system.build_dir):
            cwd = build_system.build_dir
        
        self.status_label.setText(f"Running: {' '.join(cmd)} (using {jobs} parallel jobs)")
        
        self.worker = CommandWorker(cmd, cwd)
        self.worker.output.connect(self._on_output)
        self.worker.error_output.connect(self._on_error_output)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_compile_finished)
        self.worker.start()
    
    def _on_output(self, line: str):
        self.output_text.appendPlainText(line)
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )
    
    def _on_error_output(self, line: str):
        self.output_text.appendPlainText(f"[stderr] {line}")
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )
    
    def _on_progress(self, current: int, total: int):
        if current >= 0 and total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self.progress_label.setText(f"Compiled {current} of {total} files")
        else:
            self.progress_label.setText("Compiling...")
    
    def _on_compile_finished(self, success: bool, returncode: int, stdout: str, stderr: str):
        self.state.full_stdout += stdout
        self.state.full_stderr += stderr
        
        if success:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            elapsed = datetime.now() - self.state.start_time
            self.status_label.setText(
                f"✅ Compilation successful! (took {elapsed.seconds // 60}m {elapsed.seconds % 60}s)"
            )
            self._compile_success = True
            self.completeChanged.emit()
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.status_label.setText("❌ Compilation failed")
            self.error_label.setText(
                "The compilation process encountered an error. This could be due to:\n"
                "• Missing development headers\n"
                "• Incompatible compiler version\n"
                "• Source code issues\n"
                "• Parallel build race condition"
            )
            self.error_group.setVisible(True)
            self.state.current_stage = BuildStage.FAILED
            self.state.error_stage = "compilation"
            self.state.error_message = stderr[-2000:] if stderr else stdout[-2000:]
    
    def _view_full_log(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Compilation Log")
        dialog.setIcon(QMessageBox.Icon.Information)
        log_text = self.output_text.toPlainText()
        dialog.setDetailedText(log_text)
        dialog.setText("Full compilation log is shown below. You can copy this to share with an AI assistant for troubleshooting help.")
        copy_btn = dialog.addButton("Copy to Clipboard", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() == copy_btn:
            QApplication.clipboard().setText(log_text)
    
    def _retry_single_threaded(self):
        self.error_group.setVisible(False)
        self.output_text.clear()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Retrying with single-threaded build...")
        self._run_compilation(1)
    
    def isComplete(self):
        return self._compile_success


class TestingPage(QWizardPage):
    """Page for running tests (optional)."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Testing")
        self.setSubTitle("Running automated tests to verify the build...")
        
        layout = QVBoxLayout()
        
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        
        self.button_group = QButtonGroup(self)
        
        self.run_tests_radio = QRadioButton("Run tests (recommended)")
        self.run_tests_radio.setChecked(True)
        self.button_group.addButton(self.run_tests_radio)
        layout.addWidget(self.run_tests_radio)
        
        self.skip_tests_radio = QRadioButton("Skip tests")
        self.button_group.addButton(self.skip_tests_radio)
        layout.addWidget(self.skip_tests_radio)
        
        layout.addSpacing(10)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumHeight(200)
        self.output_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        self.output_text.setVisible(False)
        layout.addWidget(self.output_text)
        
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.worker = None
        self._test_cmd = None
        self._tests_complete = False
        self._no_tests = False
    
    def initializePage(self):
        self._tests_complete = False
        self._no_tests = False
        self.progress_bar.setVisible(False)
        self.output_text.setVisible(False)
        self.result_label.setVisible(False)
        
        wizard = self.wizard()
        if not wizard or not hasattr(wizard, 'build_system'):
            self._no_tests = True
            self.info_label.setText("No build system available.")
            return
        
        build_system = wizard.build_system
        self._test_cmd = build_system.get_test_command()
        
        if self._test_cmd:
            self.info_label.setText(
                "This software includes an automated test suite. Running tests helps "
                "verify that the software compiled correctly for your system.\n\n"
                f"Test command: {' '.join(self._test_cmd)}"
            )
            self.run_tests_radio.setEnabled(True)
        else:
            self._no_tests = True
            self.info_label.setText(
                "No automated tests were detected for this software. "
                "Click Next to continue with installation."
            )
            self.run_tests_radio.setEnabled(False)
            self.skip_tests_radio.setChecked(True)
        
        self.completeChanged.emit()
    
    def validatePage(self):
        if self._no_tests or self.skip_tests_radio.isChecked():
            self.state.run_tests = False
            return True
        if self._tests_complete:
            return True
        self.state.run_tests = True
        self._run_tests()
        return False
    
    def _run_tests(self):
        if not self._test_cmd:
            return
        
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.output_text.setVisible(True)
        self.output_text.clear()
        
        wizard = self.wizard()
        build_system = wizard.build_system
        
        cwd = self.state.source_dir
        if hasattr(build_system, 'build_dir') and os.path.exists(build_system.build_dir):
            cwd = build_system.build_dir
        
        self.worker = CommandWorker(self._test_cmd, cwd)
        self.worker.output.connect(lambda l: self.output_text.appendPlainText(l))
        self.worker.error_output.connect(lambda l: self.output_text.appendPlainText(l))
        self.worker.finished.connect(self._on_tests_finished)
        self.worker.start()
    
    def _on_tests_finished(self, success: bool, returncode: int, stdout: str, stderr: str):
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.result_label.setVisible(True)
        
        if success:
            self.result_label.setText("✅ All tests passed!")
            self.result_label.setStyleSheet("color: green;")
        else:
            self.result_label.setText(
                "⚠️ Some tests failed. This may or may not indicate a problem. "
                "You can continue with installation."
            )
            self.result_label.setStyleSheet("color: orange;")
        
        self._tests_complete = True
        self.completeChanged.emit()
        QTimer.singleShot(1000, self.wizard().next)
    
    def isComplete(self):
        return True


class InstallationPage(QWizardPage):
    """Page for installing the compiled software."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Installing Software")
        self.setSubTitle("Installing compiled files to your system...")
        
        layout = QVBoxLayout()
        
        self.status_label = QLabel("Preparing installation...")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)
        
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumHeight(150)
        self.output_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.output_text)
        
        self.files_group = QGroupBox("Installed Files")
        files_layout = QVBoxLayout()
        self.files_label = QLabel()
        self.files_label.setWordWrap(True)
        files_layout.addWidget(self.files_label)
        self.files_group.setLayout(files_layout)
        layout.addWidget(self.files_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def initializePage(self):
        self._install_success = False
        self.files_group.setVisible(False)
        self.output_text.clear()
        self.progress_bar.setRange(0, 0)
        self._run_installation()
    
    def _run_installation(self):
        wizard = self.wizard()
        if not wizard or not hasattr(wizard, 'build_system'):
            self.status_label.setText("Error: Build system not available")
            return
        
        build_system = wizard.build_system
        cmd = build_system.get_install_command()
        
        cwd = self.state.source_dir
        if hasattr(build_system, 'build_dir') and os.path.exists(build_system.build_dir):
            cwd = build_system.build_dir
        
        self.status_label.setText(f"Running: {' '.join(cmd)}")
        
        self.worker = CommandWorker(cmd, cwd)
        self.worker.output.connect(lambda l: self.output_text.appendPlainText(l))
        self.worker.error_output.connect(lambda l: self.output_text.appendPlainText(l))
        self.worker.finished.connect(self._on_install_finished)
        self.worker.start()
    
    def _on_install_finished(self, success: bool, returncode: int, stdout: str, stderr: str):
        self.state.full_stdout += stdout
        self.state.full_stderr += stderr
        
        if success:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self.status_label.setText("✅ Installation successful!")
            self._verify_installation()
            self._install_success = True
            self.completeChanged.emit()
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.status_label.setText("❌ Installation failed")
            self.state.current_stage = BuildStage.FAILED
            self.state.error_stage = "installation"
            self.state.error_message = stderr
    
    def _verify_installation(self):
        bin_dir = os.path.join(self.state.prefix, "bin")
        
        if not os.path.exists(bin_dir):
            self.files_label.setText("No executables found in bin directory.")
            self.files_group.setVisible(True)
            return
        
        executables = []
        project_name_lower = self.state.project_name.lower()
        
        for filename in os.listdir(bin_dir):
            filepath = os.path.join(bin_dir, filename)
            if os.path.isfile(filepath) and os.access(filepath, os.X_OK):
                try:
                    result = subprocess.run(['file', filepath], capture_output=True, text=True)
                    is_elf = 'ELF' in result.stdout
                except:
                    is_elf = False
                
                is_main = project_name_lower in filename.lower()
                self.state.installed_files.append(InstalledFile(
                    path=filepath, is_executable=True, is_elf=is_elf, is_main_binary=is_main
                ))
                if is_main:
                    self.state.main_executable = filepath
                executables.append(f"{'⭐ ' if is_main else ''}{filepath}")
        
        if not self.state.main_executable and self.state.installed_files:
            self.state.main_executable = self.state.installed_files[0].path
        
        if executables:
            self.files_label.setText("Found executables:\n" + "\n".join(executables[:10]))
            if len(executables) > 10:
                self.files_label.setText(self.files_label.text() + f"\n... and {len(executables) - 10} more")
        else:
            self.files_label.setText("No executables found.")
        
        self.files_group.setVisible(True)
    
    def isComplete(self):
        return self._install_success


class DesktopIntegrationPage(QWizardPage):
    """Page for creating desktop file and symlinks."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Desktop Integration")
        self.setSubTitle("Setting up desktop shortcuts and terminal access...")
        
        layout = QVBoxLayout()
        
        self.desktop_group = QGroupBox("Desktop Shortcut")
        desktop_layout = QFormLayout()
        
        self.create_desktop_cb = QCheckBox("Create desktop shortcut")
        self.create_desktop_cb.setChecked(True)
        self.create_desktop_cb.toggled.connect(self._toggle_desktop_options)
        desktop_layout.addRow(self.create_desktop_cb)
        
        self.app_name_edit = QLineEdit()
        desktop_layout.addRow("Application Name:", self.app_name_edit)
        
        self.categories_combo = QComboBox()
        self.categories_combo.addItems([
            "Utility", "Development", "Graphics", "Audio", "Video",
            "Network", "Office", "Game", "Education", "Science", "System"
        ])
        desktop_layout.addRow("Category:", self.categories_combo)
        
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Optional description")
        desktop_layout.addRow("Description:", self.comment_edit)
        
        icon_layout = QHBoxLayout()
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("Leave blank for default")
        icon_layout.addWidget(self.icon_edit)
        self.icon_browse_btn = QPushButton("Browse...")
        self.icon_browse_btn.clicked.connect(self._browse_icon)
        icon_layout.addWidget(self.icon_browse_btn)
        desktop_layout.addRow("Icon:", icon_layout)
        
        self.desktop_group.setLayout(desktop_layout)
        layout.addWidget(self.desktop_group)
        
        self.symlink_group = QGroupBox("Terminal Access")
        symlink_layout = QVBoxLayout()
        
        self.create_symlink_cb = QCheckBox("Create symlink in ~/.local/bin for terminal access")
        self.create_symlink_cb.setChecked(True)
        symlink_layout.addWidget(self.create_symlink_cb)
        
        self.symlink_info = QLabel()
        self.symlink_info.setStyleSheet("color: gray;")
        symlink_layout.addWidget(self.symlink_info)
        
        self.symlink_group.setLayout(symlink_layout)
        layout.addWidget(self.symlink_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def initializePage(self):
        self.app_name_edit.setText(self.state.project_name.title())
        
        if self.state.main_executable:
            exe_name = os.path.basename(self.state.main_executable)
            bin_dir = os.path.expanduser("~/.local/bin")
            
            if os.path.dirname(os.path.realpath(self.state.main_executable)) == os.path.realpath(bin_dir):
                self.symlink_info.setText(f"Executable is already in ~/.local/bin/{exe_name} (no symlink needed)")
                self.create_symlink_cb.setChecked(False)
                self.create_symlink_cb.setEnabled(False)
            else:
                self.symlink_info.setText(f"Will create: ~/.local/bin/{exe_name} → {self.state.main_executable}")
        else:
            self.symlink_info.setText("No executable found for symlink")
            self.create_symlink_cb.setChecked(False)
            self.create_symlink_cb.setEnabled(False)
        
        if self.state.main_executable:
            try:
                result = subprocess.run(['ldd', self.state.main_executable], capture_output=True, text=True)
                gui_libs = ['libgtk', 'libQt', 'libSDL', 'libX11', 'libwayland']
                self.state.is_gui_app = any(lib in result.stdout for lib in gui_libs)
            except:
                self.state.is_gui_app = False
        
        if not self.state.is_gui_app:
            self.create_desktop_cb.setChecked(False)
            self.desktop_group.setTitle("Desktop Shortcut (not recommended for CLI apps)")
    
    def _toggle_desktop_options(self, checked: bool):
        self.app_name_edit.setEnabled(checked)
        self.categories_combo.setEnabled(checked)
        self.comment_edit.setEnabled(checked)
        self.icon_edit.setEnabled(checked)
        self.icon_browse_btn.setEnabled(checked)
    
    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Icon", "", "Images (*.png *.svg *.xpm);;All Files (*)")
        if path:
            self.icon_edit.setText(path)
    
    def validatePage(self):
        self.state.desktop_app_name = self.app_name_edit.text()
        self.state.desktop_categories = self.categories_combo.currentText()
        self.state.desktop_comment = self.comment_edit.text()
        self.state.desktop_icon = self.icon_edit.text()
        
        if self.create_symlink_cb.isChecked() and self.state.main_executable:
            self._create_symlink()
        
        if self.create_desktop_cb.isChecked() and self.state.main_executable:
            self._create_desktop_file()
        
        return True
    
    def _create_symlink(self):
        bin_dir = os.path.expanduser("~/.local/bin")
        os.makedirs(bin_dir, exist_ok=True)
        
        exe_name = os.path.basename(self.state.main_executable)
        symlink_path = os.path.join(bin_dir, exe_name)
        
        if os.path.dirname(os.path.realpath(self.state.main_executable)) == os.path.realpath(bin_dir):
            self.state.created_symlink = f"{symlink_path} (already in PATH)"
            return
        
        if os.path.islink(symlink_path):
            os.remove(symlink_path)
        elif os.path.exists(symlink_path):
            QMessageBox.warning(self, "Symlink Warning", f"A file already exists at {symlink_path}. Symlink not created.")
            return
        
        try:
            os.symlink(self.state.main_executable, symlink_path)
            self.state.created_symlink = symlink_path
        except Exception as e:
            QMessageBox.warning(self, "Symlink Warning", f"Failed to create symlink: {e}")
    
    def _create_desktop_file(self):
        apps_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(apps_dir, exist_ok=True)
        
        desktop_name = self.state.project_name.lower().replace(' ', '-')
        desktop_path = os.path.join(apps_dir, f"{desktop_name}.desktop")
        
        icon = self.state.desktop_icon
        icon_for_desktop = desktop_name
        
        if icon and os.path.isfile(icon):
            icon_ext = os.path.splitext(icon)[1].lower()
            icon_name = f"{desktop_name}{icon_ext}"
            pixmaps_dir = os.path.expanduser("~/.local/share/pixmaps")
            os.makedirs(pixmaps_dir, exist_ok=True)
            icon_dest = os.path.join(pixmaps_dir, icon_name)
            
            try:
                shutil.copy2(icon, icon_dest)
                icon_for_desktop = icon_dest
                self.state.desktop_icon = icon_dest
            except Exception as e:
                icon_for_desktop = desktop_name
        elif icon:
            icon_for_desktop = icon
        
        content = f"""[Desktop Entry]
Type=Application
Name={self.state.desktop_app_name}
Exec={self.state.main_executable}
Icon={icon_for_desktop}
Categories={self.state.desktop_categories};
Comment={self.state.desktop_comment}
Terminal=false
"""
        
        try:
            with open(desktop_path, 'w') as f:
                f.write(content)
            os.chmod(desktop_path, 0o755)
            self.state.created_desktop_file = desktop_path
            subprocess.run(['kbuildsycoca6'], capture_output=True)
            subprocess.run(['update-desktop-database', apps_dir], capture_output=True)
        except Exception as e:
            QMessageBox.warning(self, "Desktop File Warning", f"Failed to create desktop file: {e}")


class SummaryPage(QWizardPage):
    """Final summary page."""
    
    def __init__(self, state: WizardState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setTitle("Installation Complete!")
        self.setSubTitle("The software has been successfully compiled and installed.")
        
        layout = QVBoxLayout()
        
        success_label = QLabel("✅ Installation completed successfully!")
        success_label.setStyleSheet("font-size: 14px; color: green;")
        layout.addWidget(success_label)
        
        layout.addSpacing(20)
        
        self.summary_group = QGroupBox("Summary")
        summary_layout = QFormLayout()
        
        self.project_label = QLabel()
        summary_layout.addRow("Project:", self.project_label)
        
        self.location_label = QLabel()
        summary_layout.addRow("Installation:", self.location_label)
        
        self.executable_label = QLabel()
        summary_layout.addRow("Main Executable:", self.executable_label)
        
        self.desktop_label = QLabel()
        summary_layout.addRow("Desktop File:", self.desktop_label)
        
        self.symlink_label = QLabel()
        summary_layout.addRow("Symlink:", self.symlink_label)
        
        self.time_label = QLabel()
        summary_layout.addRow("Compilation Time:", self.time_label)
        
        self.summary_group.setLayout(summary_layout)
        layout.addWidget(self.summary_group)
        
        self.log_group = QGroupBox("Installation Log")
        log_layout = QVBoxLayout()
        
        self.log_path_label = QLabel()
        self.log_path_label.setWordWrap(True)
        log_layout.addWidget(self.log_path_label)
        
        btn_layout = QHBoxLayout()
        self.view_log_btn = QPushButton("View Log")
        self.view_log_btn.clicked.connect(self._view_log)
        btn_layout.addWidget(self.view_log_btn)
        
        self.open_folder_btn = QPushButton("Open Log Folder")
        self.open_folder_btn.clicked.connect(self._open_log_folder)
        btn_layout.addWidget(self.open_folder_btn)
        
        log_layout.addLayout(btn_layout)
        self.log_group.setLayout(log_layout)
        layout.addWidget(self.log_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def initializePage(self):
        self.state.end_time = datetime.now()
        self.state.current_stage = BuildStage.COMPLETE
        
        self.project_label.setText(self.state.project_name)
        self.location_label.setText(self.state.prefix)
        self.executable_label.setText(self.state.main_executable or "Not found")
        self.desktop_label.setText(self.state.created_desktop_file or "Not created")
        self.symlink_label.setText(self.state.created_symlink or "Not created")
        
        if self.state.start_time and self.state.end_time:
            elapsed = self.state.end_time - self.state.start_time
            self.time_label.setText(f"{elapsed.seconds // 60}m {elapsed.seconds % 60}s")
        
        self._save_log()
    
    def _save_log(self):
        log_dir = os.path.expanduser("~/.local/share/source-compile-logs")
        os.makedirs(log_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"{self.state.project_name}-SUCCESS-{timestamp}.txt"
        self.state.log_file = os.path.join(log_dir, log_name)
        
        log_content = f"""Source Code Compilation Wizard - Installation Log
================================================

Project: {self.state.project_name}
Tarball: {self.state.tarball_path}
Date: {datetime.now().isoformat()}

Build System: {self.state.build_system_name}
Installation Location: {self.state.prefix}

Main Executable: {self.state.main_executable}
Desktop File: {self.state.created_desktop_file or 'Not created'}
Symlink: {self.state.created_symlink or 'Not created'}

Compilation Time: {self.time_label.text()}

=== STDOUT ===
{self.state.full_stdout[-10000:]}

=== STDERR ===
{self.state.full_stderr[-5000:]}
"""
        
        try:
            with open(self.state.log_file, 'w') as f:
                f.write(log_content)
            self.log_path_label.setText(f"Log saved to:\n{self.state.log_file}")
        except Exception as e:
            self.log_path_label.setText(f"Failed to save log: {e}")
    
    def _view_log(self):
        if self.state.log_file and os.path.exists(self.state.log_file):
            subprocess.run(['xdg-open', self.state.log_file])
    
    def _open_log_folder(self):
        log_dir = os.path.dirname(self.state.log_file)
        subprocess.run(['xdg-open', log_dir])


# =============================================================================
# MAIN WIZARD
# =============================================================================

class SourceCompileWizard(QWizard):
    """Main wizard for source code compilation."""
    
    def __init__(self, tarball_path: str, parent=None):
        super().__init__(parent)
        
        self.state = WizardState(tarball_path=tarball_path)
        self.build_system: Optional[BuildSystem] = None
        
        self.setWindowTitle("Source Code Compilation Wizard")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(700, 550)
        
        self.addPage(WelcomePage(self.state))
        self.addPage(InstallLocationPage(self.state))
        self.addPage(BuildSystemDetectionPage(self.state))
        self.addPage(ConfigurationModePage(self.state))
        self.addPage(AdvancedConfigPage(self.state))
        self.addPage(DependencyResolutionPage(self.state))
        self.addPage(CompilationPage(self.state))
        self.addPage(TestingPage(self.state))
        self.addPage(InstallationPage(self.state))
        self.addPage(DesktopIntegrationPage(self.state))
        self.addPage(SummaryPage(self.state))
        
        self.rejected.connect(self._on_cancel)
    
    def _on_cancel(self):
        reply = QMessageBox.question(
            self, "Cancel Compilation",
            "Are you sure you want to cancel? All progress will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._cleanup()
    
    def _cleanup(self):
        if self.state.extract_dir and os.path.exists(self.state.extract_dir):
            try:
                shutil.rmtree(self.state.extract_dir)
            except Exception as e:
                print(f"Warning: Failed to clean up {self.state.extract_dir}: {e}")
        self.state.current_stage = BuildStage.CANCELLED


def main():
    """Main entry point for the Source Compile Wizard."""
    if len(sys.argv) < 2:
        app = QApplication(sys.argv)
        tarball_path, _ = QFileDialog.getOpenFileName(
            None, "Select Source Archive", os.path.expanduser("~"),
            "Archives (*.tar.gz *.tar.xz *.tar.bz2 *.tgz *.txz);;All Files (*)"
        )
        if not tarball_path:
            print("No file selected. Exiting.")
            sys.exit(0)
    else:
        tarball_path = sys.argv[1]
        app = QApplication(sys.argv)
    
    if not os.path.isfile(tarball_path):
        QMessageBox.critical(None, "File Not Found", f"The specified file does not exist:\n{tarball_path}")
        sys.exit(1)
    
    valid_extensions = ('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.txz', '.tar')
    if not any(tarball_path.lower().endswith(ext) for ext in valid_extensions):
        reply = QMessageBox.question(
            None, "Unrecognized Format",
            f"The file doesn't appear to be a source archive:\n{tarball_path}\n\nContinue anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            sys.exit(0)
    
    wizard = SourceCompileWizard(tarball_path)
    wizard.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
