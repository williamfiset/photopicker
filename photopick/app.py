"""PhotoPick main window: loading photos, keyboard culling, and export."""

import os
import sys

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel,
    QMainWindow, QMessageBox, QRadioButton, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import images
from .photoset import (
    PhotoSet, PickStore, default_export_name, write_picks,
)
from .widgets import (
    FilmstripView, GridView, ImageLoader, LoupeView, PhotoModel, ShortcutsOverlay,
)

# Every keyboard shortcut lives here. The key bindings AND the "?" overlay are
# both generated from this table, so the overlay can never drift out of date.
# "Ctrl" means Cmd on macOS.
#   (keys, what it does, MainWindow method)
SHORTCUTS = [
    (["Left"], "Previous photo", "previous_photo"),
    (["Right"], "Next photo", "next_photo"),
    (["Up"], "Up one row (grid) / previous photo (single view)", "row_up"),
    (["Down"], "Down one row (grid) / next photo (single view)", "row_down"),
    (["Home"], "First photo", "first_photo"),
    (["End"], "Last photo", "last_photo"),
    (["P"], "Mark pick", "mark_pick"),
    (["X"], "Mark skip (clear the pick)", "mark_skip"),
    (["Space"], "Toggle pick on / off", "toggle_pick"),
    (["G"], "Grid view", "show_grid"),
    (["E", "Return", "Enter"], "Single view of the selected photo", "show_single"),
    (["Escape"], "Back to grid / close this panel", "escape"),
    (["?", "F1"], "Show / hide keyboard shortcuts", "toggle_shortcuts"),
    (["Ctrl+O"], "Open a folder", "open_folder"),
    (["Ctrl+Shift+O"], "Open a list of files", "open_files"),
    (["Ctrl+Alt+O"], "Add a folder to the current set", "add_folder"),
    (["Ctrl+Alt+Shift+O"], "Add files to the current set", "add_files"),
    (["Ctrl+E"], "Export the picks list", "export_picks"),
]

IMAGE_FILTER = "Images ({});;All files (*)".format(
    " ".join(f"*{ext} *{ext.upper()}" for ext in sorted(images.SUPPORTED_EXTENSIONS))
)


# Friendlier names than macOS's glyphs (it shows Home as ↖ and Escape as ⎋).
KEY_NAMES = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "Home": "Home",
             "End": "End", "Escape": "Esc", "Return": "Return", "Enter": "Enter"}


def shortcut_text(keys):
    """Keys as shown to the user, e.g. "⌘O" on macOS or "Ctrl+O" elsewhere."""
    return " / ".join(KEY_NAMES.get(k) or QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText)
                      for k in keys)


class ExportDialog(QDialog):
    """Ask whether to write bare file names or full paths."""

    def __init__(self, parent, pick_count):
        super().__init__(parent)
        self.setWindowTitle("Export picks")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Export {pick_count} picked photo(s) as a text file, one per line:"))
        self.paths_button = QRadioButton("Full paths  (/Volumes/…/DSC01234.ARW)")
        self.names_button = QRadioButton("File names only  (DSC01234.ARW)")
        layout.addWidget(self.paths_button)
        layout.addWidget(self.names_button)
        self.paths_button.setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Choose file…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def full_paths(self):
        return self.paths_button.isChecked()


class MainWindow(QMainWindow):
    def __init__(self, pick_store, thumbnail_dir):
        super().__init__()
        self.settings = QSettings("PhotoPick", "PhotoPick")
        self.pick_store = pick_store
        self.photo_set = PhotoSet()

        self.thumbnails = ImageLoader(images.load_thumbnail, (thumbnail_dir,))
        self.previews = ImageLoader(images.load_preview, (self._preview_size(),),
                                    threads=2, memory_limit=8)
        self.previews.loaded.connect(self._on_preview_loaded)
        self.thumbnails.loaded.connect(self._on_thumbnail_loaded)

        self.model = PhotoModel(self.photo_set, pick_store, self.thumbnails)
        self._build_views()
        self._build_status_bar()
        self._build_actions_and_menus()

        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1400, 900)
        self._update_status()

    # --- setup --------------------------------------------------------------

    def _preview_size(self):
        """Long edge for single-view images: enough for this screen, no more."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return 2560
        size = screen.size() * screen.devicePixelRatio()
        return max(1600, min(4096, max(size.width(), size.height())))

    def _build_views(self):
        self.grid = GridView()
        self.grid.setModel(self.model)
        self.grid.empty_hint = (
            f"Open a folder ({shortcut_text(['Ctrl+O'])}) or files "
            f"({shortcut_text(['Ctrl+Shift+O'])}),\nor drop them here.")
        self.selection = self.grid.selectionModel()
        self.selection.currentChanged.connect(self._on_current_changed)
        self.grid.clicked.connect(lambda index: self.show_single())

        self.filmstrip = FilmstripView()
        self.filmstrip.setModel(self.model)
        self.filmstrip.setSelectionModel(self.selection)  # both views share one selection

        self.loupe = LoupeView()
        self.loupe.double_clicked.connect(self.show_grid)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.loupe)

        splitter = QSplitter()
        splitter.addWidget(self.filmstrip)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1140])

        # Plain container so the overlay can sit on top of everything.
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        overlay_rows = [(shortcut_text(keys), description) for keys, description, _ in SHORTCUTS]
        self.overlay = ShortcutsOverlay(container, overlay_rows)

    def _build_status_bar(self):
        bar = self.statusBar()
        self.position_label = QLabel()
        self.source_label = QLabel()
        self.source_label.setStyleSheet("color: #999; padding-left: 16px;")
        bar.addPermanentWidget(self.position_label)
        bar.addPermanentWidget(self.source_label, 1)

        self.advance_checkbox = QCheckBox("advance after flag")
        self.advance_checkbox.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.advance_checkbox.setChecked(self.settings.value("advance_after_flag", False, type=bool))
        self.advance_checkbox.toggled.connect(
            lambda on: self.settings.setValue("advance_after_flag", on))
        bar.addPermanentWidget(self.advance_checkbox)

        hint = QLabel('<a href="#" style="color: #f0c040; text-decoration: none;">? shortcuts</a>')
        hint.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hint.linkActivated.connect(lambda _: self.toggle_shortcuts())
        bar.addPermanentWidget(hint)

    def _build_actions_and_menus(self):
        self.actions_by_method = {}
        for keys, description, method in SHORTCUTS:
            action = QAction(description, self)
            sequences = [QKeySequence(k) for k in keys]
            # "?" arrives as Shift+? on most keyboards; bind both so it always works.
            sequences += [QKeySequence("Shift+" + k) for k in keys if len(k) == 1 and not k.isalnum()]
            action.setShortcuts(sequences)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(getattr(self, method))
            self.addAction(action)  # active even when not in any menu
            self.actions_by_method[method] = action

        file_menu = self.menuBar().addMenu("&File")
        for method, title in [("open_folder", "Open Folder…"), ("open_files", "Open Files…"),
                              ("add_folder", "Add Folder…"), ("add_files", "Add Files…")]:
            file_menu.addAction(self._menu_action(method, title))
        file_menu.addSeparator()
        file_menu.addAction(self._menu_action("export_picks", "Export Picks…"))
        clear_action = file_menu.addAction("Clear All Picks…")
        clear_action.triggered.connect(self.clear_all_picks)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Quit")
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self._menu_action("show_grid", "Grid"))
        view_menu.addAction(self._menu_action("show_single", "Single Photo"))
        view_menu.addSeparator()
        advance_action = view_menu.addAction("Advance After Flag")
        advance_action.setCheckable(True)
        advance_action.setChecked(self.advance_checkbox.isChecked())
        advance_action.toggled.connect(self.advance_checkbox.setChecked)
        self.advance_checkbox.toggled.connect(advance_action.setChecked)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._menu_action("toggle_shortcuts", "Keyboard Shortcuts"))

    def _menu_action(self, method, title):
        """A menu entry for a SHORTCUTS action.

        Shortcuts with a modifier (Cmd+O) go on the menu item itself. Bare keys
        (G, ?) are only shown in the title: native macOS menus handle bare-key
        shortcuts unreliably, and the window-level action already handles them.
        """
        action = self.actions_by_method[method]
        keys = action.shortcuts()
        if all(k[0].keyboardModifiers() != Qt.KeyboardModifier.NoModifier for k in keys):
            action.setText(title)
            return action
        menu_action = QAction(f"{title}    ({keys[0].toString(QKeySequence.SequenceFormat.NativeText)})", self)
        menu_action.triggered.connect(action.trigger)
        return menu_action

    # --- loading photos -----------------------------------------------------

    def load(self, paths, replace):
        """Load folders and/or files, replacing the current set or adding to it."""
        if replace:
            self.photo_set = PhotoSet()
            self.model.reset(self.photo_set)
            self.loupe.clear()
        first_new_row = len(self.photo_set)

        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        added = 0
        skipped = 0
        for folder in folders:
            added += self.photo_set.add_folder(folder)
        if files:
            added_files, skipped = self.photo_set.add_files(files)
            added += added_files

        self.model.rows_appended(first_new_row)
        if self.current_row() < 0 and len(self.photo_set):
            self.go_to(0)

        message = f"Added {added} photo(s)"
        if skipped:
            message += f" - skipped {skipped} file(s) that aren't supported images"
        self.statusBar().showMessage(message, 15000)
        self.setWindowTitle(f"PhotoPick - {self.photo_set.source_label()}")
        self._update_status()

    def _ask_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of photos",
                                                  self.settings.value("last_dir", os.path.expanduser("~")))
        if folder:
            self.settings.setValue("last_dir", folder)
        return folder

    def _ask_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose photos",
                                                self.settings.value("last_dir", os.path.expanduser("~")),
                                                IMAGE_FILTER)
        if files:
            self.settings.setValue("last_dir", os.path.dirname(files[0]))
        return files

    def open_folder(self):
        folder = self._ask_folder()
        if folder:
            self.load([folder], replace=True)

    def open_files(self):
        files = self._ask_files()
        if files:
            self.load(files, replace=True)

    def add_folder(self):
        folder = self._ask_folder()
        if folder:
            self.load([folder], replace=False)

    def add_files(self):
        files = self._ask_files()
        if files:
            self.load(files, replace=False)

    def dragEnterEvent(self, event):
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.load(paths, replace=False)

    # --- navigation ---------------------------------------------------------

    def current_row(self):
        index = self.selection.currentIndex()
        return index.row() if index.isValid() else -1

    def current_photo(self):
        row = self.current_row()
        return self.photo_set.photos[row] if row >= 0 else None

    def go_to(self, row):
        if not len(self.photo_set):
            return
        row = max(0, min(row, len(self.photo_set) - 1))
        index = self.model.index(row)
        self.selection.setCurrentIndex(index, self.selection.SelectionFlag.ClearAndSelect)
        self.grid.scrollTo(index)
        self.filmstrip.scrollTo(index)

    def previous_photo(self):
        self.go_to(self.current_row() - 1)

    def next_photo(self):
        self.go_to(self.current_row() + 1)

    def first_photo(self):
        self.go_to(0)

    def last_photo(self):
        self.go_to(len(self.photo_set) - 1)

    def row_up(self):
        if self.in_single_view():
            self.previous_photo()
        elif self.current_row() - self.grid.columns() >= 0:
            self.go_to(self.current_row() - self.grid.columns())

    def row_down(self):
        if self.in_single_view():
            self.next_photo()
        elif self.current_row() + self.grid.columns() < len(self.photo_set):
            self.go_to(self.current_row() + self.grid.columns())

    def _on_current_changed(self, current, previous):
        self._update_status()
        if self.in_single_view():
            self._update_loupe()

    # --- views --------------------------------------------------------------

    def in_single_view(self):
        return self.stack.currentWidget() is self.loupe

    def show_grid(self):
        self.stack.setCurrentWidget(self.grid)
        if self.current_row() >= 0:
            self.grid.scrollTo(self.model.index(self.current_row()))

    def show_single(self):
        if not len(self.photo_set):
            return
        if self.current_row() < 0:
            self.go_to(0)
        self.stack.setCurrentWidget(self.loupe)
        self._update_loupe()

    def escape(self):
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.show_grid()

    def toggle_shortcuts(self):
        self.overlay.toggle()

    def _update_loupe(self):
        photo = self.current_photo()
        if photo is None:
            self.loupe.clear()
            return
        row = self.current_row()
        # Preload the neighbours first; the current photo is requested last so
        # it gets the highest priority.
        for neighbour in (row + 1, row - 1):
            if 0 <= neighbour < len(self.photo_set):
                self.previews.request(self.photo_set.photos[neighbour].path)
        image = self.previews.get(photo.path)
        is_full_size = image != "loading"
        if image == "loading":
            image = self.thumbnails.get(photo.path)  # blurry stand-in until ready
        self.loupe.show_photo(photo.name, image, self.pick_store.is_picked(photo), is_full_size)

    def _on_preview_loaded(self, path):
        photo = self.current_photo()
        if self.in_single_view() and photo and photo.path == path:
            self._update_loupe()

    def _on_thumbnail_loaded(self, path):
        photo = self.current_photo()
        if self.in_single_view() and photo and photo.path == path and not self.loupe.is_full_size:
            self._update_loupe()

    # --- picking ------------------------------------------------------------

    def _set_pick(self, picked, advance):
        photo = self.current_photo()
        if photo is None:
            return
        self.pick_store.set_picked(photo, picked)
        self.model.row_changed(self.current_row())
        self._update_status()
        if self.in_single_view():
            self._update_loupe()
        if advance and self.advance_checkbox.isChecked():
            self.next_photo()

    def mark_pick(self):
        self._set_pick(True, advance=True)

    def mark_skip(self):
        self._set_pick(False, advance=True)

    def toggle_pick(self):
        photo = self.current_photo()
        if photo is not None:
            self._set_pick(not self.pick_store.is_picked(photo), advance=False)

    def picked_photos(self):
        return [p for p in self.photo_set.photos if self.pick_store.is_picked(p)]

    def clear_all_picks(self):
        count = len(self.picked_photos())
        if not count:
            return
        answer = QMessageBox.question(self, "Clear all picks",
                                      f"Clear all {count} pick(s) in the current set?")
        if answer == QMessageBox.StandardButton.Yes:
            self.pick_store.clear(self.photo_set.photos)
            self.model.dataChanged.emit(self.model.index(0), self.model.index(len(self.photo_set) - 1))
            self._update_status()
            if self.in_single_view():
                self._update_loupe()

    # --- export -------------------------------------------------------------

    def export_picks(self):
        picked = self.picked_photos()
        if not picked:
            QMessageBox.information(self, "Export picks", "No photos are picked yet.")
            return

        dialog = ExportDialog(self, len(picked))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        start_dir = self.settings.value("export_dir", os.path.expanduser("~"))
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export picks", os.path.join(start_dir, default_export_name(self.photo_set)),
            "Text files (*.txt)")
        if not out_path:
            return
        try:
            write_picks(out_path, picked, dialog.full_paths())
        except OSError as e:
            QMessageBox.warning(self, "Export picks", f"Could not write the file:\n{e}")
            return
        self.settings.setValue("export_dir", os.path.dirname(out_path))
        self.statusBar().showMessage(f"Exported {len(picked)} pick(s) to {out_path}", 15000)

    # --- status bar ---------------------------------------------------------

    def _update_status(self):
        total = len(self.photo_set)
        if total == 0:
            self.position_label.setText("No photos loaded")
            self.source_label.setText("")
            return
        picked = len(self.picked_photos())
        self.position_label.setText(f"photo {self.current_row() + 1} of {total} - {picked} picked")
        self.source_label.setText(self.photo_set.source_label())


def dark_palette():
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#2b2b2b"), (QPalette.ColorRole.WindowText, "#dddddd"),
        (QPalette.ColorRole.Base, "#232323"), (QPalette.ColorRole.AlternateBase, "#2b2b2b"),
        (QPalette.ColorRole.Text, "#dddddd"), (QPalette.ColorRole.Button, "#353535"),
        (QPalette.ColorRole.ButtonText, "#dddddd"), (QPalette.ColorRole.Highlight, "#5a7fb0"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#333333"),
        (QPalette.ColorRole.ToolTipText, "#dddddd"), (QPalette.ColorRole.Link, "#f0c040"),
    ]:
        palette.setColor(role, QColor(color))
    return palette


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoPick")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())

    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    cache_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    window = MainWindow(PickStore(os.path.join(data_dir, "picks.json")),
                        os.path.join(cache_dir, "thumbnails"))
    window.show()

    paths = app.arguments()[1:]
    if paths:
        window.load(paths, replace=True)
    return app.exec()
