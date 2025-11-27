#!/usr/bin/env python3
"""
Source Code Compilation Uninstaller
A PyQt6 wizard for uninstalling software previously installed via the Source Compile Wizard.
"""

import sys
import os
import re
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List

from PyQt6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QTextEdit, QProgressBar,
    QPushButton, QCheckBox, QMessageBox, QGroupBox, QFormLayout,
    QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class InstalledApp:
    """Information about an installed application parsed from log file."""
    name: str
    log_file: str
    install_date: datetime
    prefix: str
    main_executable: str
    desktop_file: str
    symlink: str
    icon_files: List[str]
    all_installed_files: List[str]
    build_system: str
    
    @property
    def display_name(self) -> str:
        return f"{self.name} (installed {self.install_date.strftime('%Y-%m-%d %H:%M')})"


# =============================================================================
# LOG PARSER
# =============================================================================

def parse_installation_log(log_path: str) -> Optional[InstalledApp]:
    """Parse an installation log file to extract installed file information."""
    try:
        with open(log_path, 'r', errors='ignore') as f:
            content = f.read()
    except Exception:
        return None
    
    # Only parse SUCCESS logs
    if '-SUCCESS-' not in os.path.basename(log_path):
        return None
    
    # Extract fields
    name = ""
    prefix = ""
    main_executable = ""
    desktop_file = ""
    symlink = ""
    build_system = ""
    installed_files = []
    install_date = None
    
    # Parse project name
    match = re.search(r'^Project:\s*(.+)$', content, re.MULTILINE)
    if match:
        name = match.group(1).strip()
    
    # Parse date
    match = re.search(r'^Date:\s*(.+)$', content, re.MULTILINE)
    if match:
        try:
            install_date = datetime.fromisoformat(match.group(1).strip())
        except:
            pass
    
    # Fallback: parse date from filename
    if not install_date:
        filename = os.path.basename(log_path)
        match = re.search(r'(\d{8}_\d{6})', filename)
        if match:
            try:
                install_date = datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
            except:
                install_date = datetime.now()
    
    # Parse prefix
    match = re.search(r'^Installation Location:\s*(.+)$', content, re.MULTILINE)
    if match:
        prefix = match.group(1).strip()
    
    # Parse build system
    match = re.search(r'^Build System:\s*(.+)$', content, re.MULTILINE)
    if match:
        build_system = match.group(1).strip()
    
    # Parse main executable
    match = re.search(r'^Main Executable:\s*(.+)$', content, re.MULTILINE)
    if match:
        main_executable = match.group(1).strip()
    
    # Parse desktop file
    match = re.search(r'^Desktop File:\s*(.+)$', content, re.MULTILINE)
    if match:
        val = match.group(1).strip()
        if val.lower() not in ['not created', 'none', '']:
            desktop_file = val
    
    # Parse symlink
    match = re.search(r'^Symlink:\s*(.+)$', content, re.MULTILINE)
    if match:
        val = match.group(1).strip()
        if val.lower() not in ['not created', 'none', ''] and 'already in PATH' not in val:
            symlink = val
    
    # Parse installed files section
    in_files_section = False
    for line in content.split('\n'):
        if line.strip() == 'Installed Files:':
            in_files_section = True
            continue
        if in_files_section:
            if line.strip() == '' or line.startswith('Main Executable:'):
                in_files_section = False
                continue
            filepath = line.strip()
            if filepath and os.path.exists(filepath):
                installed_files.append(filepath)
    
    # Find icon files
    icon_files = []
    icon_dirs = [
        os.path.expanduser('~/.local/share/pixmaps'),
        os.path.expanduser('~/.local/share/icons/hicolor/scalable/apps'),
        os.path.expanduser('~/.local/share/icons/hicolor/48x48/apps'),
    ]
    if name:
        name_lower = name.lower().replace(' ', '-')
        for icon_dir in icon_dirs:
            if os.path.isdir(icon_dir):
                for ext in ['.svg', '.png', '.xpm', '.ico']:
                    icon_path = os.path.join(icon_dir, f"{name_lower}{ext}")
                    if os.path.exists(icon_path):
                        icon_files.append(icon_path)
    
    if not name:
        return None
    
    return InstalledApp(
        name=name,
        log_file=log_path,
        install_date=install_date or datetime.now(),
        prefix=prefix,
        main_executable=main_executable,
        desktop_file=desktop_file,
        symlink=symlink,
        icon_files=icon_files,
        all_installed_files=installed_files,
        build_system=build_system
    )


def scan_for_installations() -> List[InstalledApp]:
    """Scan log directory for installed applications."""
    log_dir = os.path.expanduser('~/.local/share/source-compile-logs')
    
    if not os.path.isdir(log_dir):
        return []
    
    apps = []
    for filename in os.listdir(log_dir):
        if filename.endswith('.txt') and '-SUCCESS-' in filename:
            log_path = os.path.join(log_dir, filename)
            app = parse_installation_log(log_path)
            if app:
                apps.append(app)
    
    # Sort by install date, newest first
    apps.sort(key=lambda a: a.install_date, reverse=True)
    return apps


# =============================================================================
# UNINSTALL WORKER
# =============================================================================

class UninstallWorker(QThread):
    """Worker thread for uninstalling an application."""
    
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, app: InstalledApp, delete_log: bool = False):
        super().__init__()
        self.app = app
        self.delete_log = delete_log
        self.files_removed = []
        self.files_failed = []
    
    def run(self):
        try:
            # Remove desktop file
            if self.app.desktop_file and os.path.exists(self.app.desktop_file):
                self.progress.emit(f"Removing desktop file: {self.app.desktop_file}")
                try:
                    os.remove(self.app.desktop_file)
                    self.files_removed.append(self.app.desktop_file)
                except Exception as e:
                    self.files_failed.append((self.app.desktop_file, str(e)))
            
            # Remove symlink
            if self.app.symlink and os.path.islink(self.app.symlink):
                self.progress.emit(f"Removing symlink: {self.app.symlink}")
                try:
                    os.remove(self.app.symlink)
                    self.files_removed.append(self.app.symlink)
                except Exception as e:
                    self.files_failed.append((self.app.symlink, str(e)))
            
            # Remove icon files
            for icon_path in self.app.icon_files:
                if os.path.exists(icon_path):
                    self.progress.emit(f"Removing icon: {icon_path}")
                    try:
                        os.remove(icon_path)
                        self.files_removed.append(icon_path)
                    except Exception as e:
                        self.files_failed.append((icon_path, str(e)))
            
            # Remove main executable
            if self.app.main_executable and os.path.exists(self.app.main_executable):
                self.progress.emit(f"Removing executable: {self.app.main_executable}")
                try:
                    os.remove(self.app.main_executable)
                    self.files_removed.append(self.app.main_executable)
                except Exception as e:
                    self.files_failed.append((self.app.main_executable, str(e)))
            
            # Remove other installed files from log
            for filepath in self.app.all_installed_files:
                if filepath == self.app.main_executable:
                    continue  # Already handled
                if os.path.exists(filepath):
                    self.progress.emit(f"Removing: {filepath}")
                    try:
                        if os.path.isdir(filepath):
                            shutil.rmtree(filepath)
                        else:
                            os.remove(filepath)
                        self.files_removed.append(filepath)
                    except Exception as e:
                        self.files_failed.append((filepath, str(e)))
            
            # Try to remove app's share directory if it exists and is empty
            if self.app.prefix:
                share_dir = os.path.join(self.app.prefix, 'share', self.app.name.lower())
                if os.path.isdir(share_dir):
                    try:
                        os.rmdir(share_dir)  # Only removes if empty
                        self.files_removed.append(share_dir)
                    except:
                        pass  # Not empty or other error, skip
            
            # Remove log file if requested
            if self.delete_log and os.path.exists(self.app.log_file):
                self.progress.emit(f"Removing log file: {self.app.log_file}")
                try:
                    os.remove(self.app.log_file)
                    self.files_removed.append(self.app.log_file)
                except Exception as e:
                    self.files_failed.append((self.app.log_file, str(e)))
            
            # Refresh KDE menu
            self.progress.emit("Refreshing desktop menu...")
            subprocess.run(['kbuildsycoca6'], capture_output=True)
            
            # Build result message
            msg = f"Removed {len(self.files_removed)} files."
            if self.files_failed:
                msg += f"\n\nFailed to remove {len(self.files_failed)} files:"
                for path, err in self.files_failed[:5]:
                    msg += f"\n  {path}: {err}"
                if len(self.files_failed) > 5:
                    msg += f"\n  ... and {len(self.files_failed) - 5} more"
            
            self.finished.emit(len(self.files_failed) == 0, msg)
            
        except Exception as e:
            self.finished.emit(False, f"Uninstall failed: {str(e)}")


# =============================================================================
# WIZARD PAGES
# =============================================================================

class SelectAppPage(QWizardPage):
    """Page for selecting which application to uninstall."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Select Application to Uninstall")
        self.setSubTitle("Choose an application that was installed via the Source Compile Wizard.")
        
        layout = QVBoxLayout()
        
        # App list
        self.app_list = QListWidget()
        self.app_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.app_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.app_list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.app_list)
        
        # Refresh button
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._refresh_list)
        layout.addWidget(refresh_btn)
        
        # No apps message
        self.no_apps_label = QLabel(
            "No applications found.\n\n"
            "Applications installed via the Source Compile Wizard will appear here."
        )
        self.no_apps_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_apps_label.setStyleSheet("color: gray;")
        self.no_apps_label.setVisible(False)
        layout.addWidget(self.no_apps_label)
        
        self.setLayout(layout)
        self.apps: List[InstalledApp] = []
        self.selected_app: Optional[InstalledApp] = None
    
    def initializePage(self):
        self._refresh_list()
    
    def _refresh_list(self):
        self.app_list.clear()
        self.apps = scan_for_installations()
        
        if not self.apps:
            self.app_list.setVisible(False)
            self.no_apps_label.setVisible(True)
        else:
            self.app_list.setVisible(True)
            self.no_apps_label.setVisible(False)
            
            for app in self.apps:
                item = QListWidgetItem(app.display_name)
                item.setData(Qt.ItemDataRole.UserRole, app)
                self.app_list.addItem(item)
        
        self.selected_app = None
        self.completeChanged.emit()
    
    def _on_selection_changed(self):
        items = self.app_list.selectedItems()
        if items:
            self.selected_app = items[0].data(Qt.ItemDataRole.UserRole)
        else:
            self.selected_app = None
        self.completeChanged.emit()
    
    def _on_double_click(self, item):
        """Double-click to proceed to next page."""
        self.selected_app = item.data(Qt.ItemDataRole.UserRole)
        if self.selected_app:
            self.wizard().next()
    
    def isComplete(self):
        return self.selected_app is not None
    
    def validatePage(self):
        if self.selected_app:
            wizard = self.wizard()
            wizard.selected_app = self.selected_app
        return self.selected_app is not None


class ConfirmPage(QWizardPage):
    """Page showing what will be removed and confirming uninstall."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Confirm Uninstallation")
        self.setSubTitle("Review what will be removed.")
        
        layout = QVBoxLayout()
        
        # App info
        self.info_group = QGroupBox("Application Details")
        info_layout = QFormLayout()
        
        self.name_label = QLabel()
        info_layout.addRow("Name:", self.name_label)
        
        self.date_label = QLabel()
        info_layout.addRow("Installed:", self.date_label)
        
        self.prefix_label = QLabel()
        info_layout.addRow("Location:", self.prefix_label)
        
        self.build_label = QLabel()
        info_layout.addRow("Build System:", self.build_label)
        
        self.info_group.setLayout(info_layout)
        layout.addWidget(self.info_group)
        
        # Files to remove
        self.files_group = QGroupBox("Files to Remove")
        files_layout = QVBoxLayout()
        
        self.files_text = QTextEdit()
        self.files_text.setReadOnly(True)
        self.files_text.setMaximumHeight(200)
        self.files_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        files_layout.addWidget(self.files_text)
        
        self.files_group.setLayout(files_layout)
        layout.addWidget(self.files_group)
        
        # Options
        self.delete_log_cb = QCheckBox("Also delete installation log file")
        self.delete_log_cb.setChecked(False)
        layout.addWidget(self.delete_log_cb)
        
        # Warning
        warning = QLabel("⚠️ This action cannot be undone.")
        warning.setStyleSheet("color: orange;")
        layout.addWidget(warning)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def initializePage(self):
        wizard = self.wizard()
        app = wizard.selected_app
        
        if not app:
            return
        
        self.name_label.setText(app.name)
        self.date_label.setText(app.install_date.strftime('%Y-%m-%d %H:%M:%S'))
        self.prefix_label.setText(app.prefix or "Unknown")
        self.build_label.setText(app.build_system or "Unknown")
        
        # Build file list
        files = []
        
        if app.main_executable and os.path.exists(app.main_executable):
            files.append(f"[Executable] {app.main_executable}")
        
        if app.desktop_file and os.path.exists(app.desktop_file):
            files.append(f"[Desktop] {app.desktop_file}")
        
        if app.symlink and os.path.islink(app.symlink):
            files.append(f"[Symlink] {app.symlink}")
        
        for icon in app.icon_files:
            if os.path.exists(icon):
                files.append(f"[Icon] {icon}")
        
        for filepath in app.all_installed_files:
            if filepath != app.main_executable and os.path.exists(filepath):
                files.append(filepath)
        
        if files:
            self.files_text.setPlainText('\n'.join(files))
        else:
            self.files_text.setPlainText("No files found to remove.\n\n"
                                         "The application may have already been removed manually.")


class UninstallPage(QWizardPage):
    """Page for performing the uninstallation."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Uninstalling")
        self.setSubTitle("Removing application files...")
        
        layout = QVBoxLayout()
        
        self.status_label = QLabel("Starting uninstallation...")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        layout.addWidget(self.progress_bar)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.log_text)
        
        self.setLayout(layout)
        
        self.worker = None
        self._complete = False
    
    def initializePage(self):
        self._complete = False
        self.log_text.clear()
        self.progress_bar.setRange(0, 0)
        
        wizard = self.wizard()
        app = wizard.selected_app
        
        # Get delete log preference from previous page
        confirm_page = wizard.page(1)
        delete_log = confirm_page.delete_log_cb.isChecked()
        
        self.worker = UninstallWorker(app, delete_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()
    
    def _on_progress(self, message: str):
        self.log_text.append(message)
        self.status_label.setText(message)
    
    def _on_finished(self, success: bool, message: str):
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        
        if success:
            self.status_label.setText("✅ Uninstallation complete!")
            self.log_text.append(f"\n{message}")
        else:
            self.status_label.setText("⚠️ Uninstallation completed with errors")
            self.log_text.append(f"\n{message}")
        
        self._complete = True
        self.completeChanged.emit()
    
    def isComplete(self):
        return self._complete


class SummaryPage(QWizardPage):
    """Final summary page."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Uninstallation Complete")
        self.setSubTitle("The application has been removed from your system.")
        
        layout = QVBoxLayout()
        
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        
        layout.addStretch()
        
        self.setLayout(layout)
    
    def initializePage(self):
        wizard = self.wizard()
        app = wizard.selected_app
        
        self.summary_label.setText(
            f"✅ <b>{app.name}</b> has been successfully uninstalled.\n\n"
            f"The following were removed:\n"
            f"• Executable files\n"
            f"• Desktop menu entry\n"
            f"• Icons\n"
            f"• Symlinks\n\n"
            f"Your system has been cleaned up and the desktop menu refreshed."
        )


# =============================================================================
# MAIN WIZARD
# =============================================================================

class UninstallWizard(QWizard):
    """Main wizard for uninstalling compiled applications."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.selected_app: Optional[InstalledApp] = None
        
        self.setWindowTitle("Source Compile Uninstaller")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(600, 500)
        
        self.addPage(SelectAppPage())
        self.addPage(ConfirmPage())
        self.addPage(UninstallPage())
        self.addPage(SummaryPage())


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Source Compile Uninstaller")
    
    wizard = UninstallWizard()
    wizard.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
