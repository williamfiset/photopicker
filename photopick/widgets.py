"""Qt pieces: the photo list model, background image loading, the grid and
filmstrip painting, the single-photo (loupe) view, and the shortcuts overlay."""

from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QRect, QRunnable,
    QSize, Qt, QThreadPool, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QListView, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from . import images

PICK_GREEN = QColor("#3cb44b")
SELECTED_BORDER = QColor("#f0c040")
CELL_BACKGROUND = QColor("#333333")
PLACEHOLDER = QColor("#444444")
BROKEN = QColor("#5a2a2a")
TEXT = QColor("#dddddd")
DIM_TEXT = QColor("#888888")

PathRole = Qt.ItemDataRole.UserRole + 1
PickedRole = Qt.ItemDataRole.UserRole + 2
ThumbRole = Qt.ItemDataRole.UserRole + 3  # QPixmap, "loading" or "broken"


# --- background loading -----------------------------------------------------


class _JobSignals(QObject):
    done = Signal(str, object)  # path, QImage or None if the file can't be decoded


class _Job(QRunnable):
    """Run `func(path, *args)` on a worker thread and report the QImage back."""

    def __init__(self, signals, func, path, *args):
        super().__init__()
        self.signals = signals
        self.func = func
        self.path = path
        self.args = args

    def run(self):
        try:
            image = self.func(self.path, *self.args)
        except Exception as e:
            print(f"photopick: cannot decode {self.path}: {e}")
            image = None
        self.signals.done.emit(self.path, image)


class ImageLoader(QObject):
    """Loads images on a thread pool and keeps the most recent ones in memory.

    Newer requests run first, so whatever is on screen right now wins over
    things that were requested while scrolling past.
    """

    loaded = Signal(str)  # path

    def __init__(self, func, func_args=(), threads=None, memory_limit=600):
        super().__init__()
        self.func = func
        self.func_args = func_args
        self.pool = QThreadPool()
        if threads:
            self.pool.setMaxThreadCount(threads)
        self.memory_limit = memory_limit
        self.pixmaps = OrderedDict()  # path -> QPixmap, least recently used first
        self.broken = set()
        self.pending = set()
        self._priority = 0
        self._signals = _JobSignals()
        self._signals.done.connect(self._on_done)

    def get(self, path):
        """Return a QPixmap if ready, "broken", or "loading" (and start loading)."""
        if path in self.pixmaps:
            self.pixmaps.move_to_end(path)
            return self.pixmaps[path]
        if path in self.broken:
            return "broken"
        self.request(path)
        return "loading"

    def request(self, path):
        if path in self.pixmaps or path in self.broken or path in self.pending:
            return
        self.pending.add(path)
        self._priority += 1
        self.pool.start(_Job(self._signals, self.func, path, *self.func_args), self._priority)

    def _on_done(self, path, image):
        self.pending.discard(path)
        if image is None or image.isNull():
            self.broken.add(path)
        else:
            self.pixmaps[path] = QPixmap.fromImage(image)
            while len(self.pixmaps) > self.memory_limit:
                self.pixmaps.popitem(last=False)
        self.loaded.emit(path)


# --- model ------------------------------------------------------------------


class PhotoModel(QAbstractListModel):
    """One row per photo. Shared by the grid and the filmstrip."""

    def __init__(self, photo_set, pick_store, thumbnails):
        super().__init__()
        self.photo_set = photo_set
        self.pick_store = pick_store
        self.thumbnails = thumbnails
        self._row_of_path = {}
        thumbnails.loaded.connect(self._on_thumbnail_loaded)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.photo_set.photos)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        photo = self.photo_set.photos[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return photo.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return photo.path
        if role == PathRole:
            return photo.path
        if role == PickedRole:
            return self.pick_store.is_picked(photo)
        if role == ThumbRole:
            return self.thumbnails.get(photo.path)
        return None

    def reset(self, photo_set):
        self.beginResetModel()
        self.photo_set = photo_set
        self._row_of_path = {}
        self.endResetModel()

    def rows_appended(self, first_new_row):
        """Call after photos were appended to the current set."""
        last = len(self.photo_set.photos) - 1
        if last >= first_new_row:
            self.beginInsertRows(QModelIndex(), first_new_row, last)
            self.endInsertRows()

    def row_changed(self, row):
        index = self.index(row)
        self.dataChanged.emit(index, index)

    def _on_thumbnail_loaded(self, path):
        if len(self._row_of_path) != len(self.photo_set.photos):
            self._row_of_path = {p.path: i for i, p in enumerate(self.photo_set.photos)}
        row = self._row_of_path.get(path)
        if row is not None:
            self.row_changed(row)


# --- painting helpers -------------------------------------------------------


def _fit(size, box):
    """The rect of `size` scaled to fit inside `box`, centered."""
    scaled = size.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
    x = box.x() + (box.width() - scaled.width()) // 2
    y = box.y() + (box.height() - scaled.height()) // 2
    return QRect(x, y, scaled.width(), scaled.height())


def draw_thumbnail(painter, box, thumb, name=""):
    """Draw a thumbnail, a loading placeholder, or a broken-image placeholder."""
    if isinstance(thumb, QPixmap):
        painter.drawPixmap(_fit(thumb.size() / thumb.devicePixelRatio(), box), thumb)
        return
    if thumb == "broken":
        painter.fillRect(box, BROKEN)
        painter.setPen(TEXT)
        label = "✕ can't read\n" + name if box.height() > 60 else "✕"
        painter.drawText(box.adjusted(4, 4, -4, -4),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWrapAnywhere, label)
    else:
        painter.fillRect(box, PLACEHOLDER)
        painter.setPen(DIM_TEXT)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "…")


def draw_pick_corner(painter, rect, size):
    """Green triangle with a check mark in the top-right corner of `rect`."""
    right, top = rect.right() + 1, rect.top()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(PICK_GREEN)
    painter.drawPolygon(QPolygon([QPoint(right - size, top), QPoint(right, top),
                                  QPoint(right, top + size)]))
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(9, size // 2))
    painter.setFont(font)
    check_box = QRect(right - size // 2 - 1, top + 1, size // 2, size // 2)
    painter.drawText(check_box, Qt.AlignmentFlag.AlignCenter, "✓")


# --- grid -------------------------------------------------------------------


class GridDelegate(QStyledItemDelegate):
    CELL = QSize(196, 172)
    CAPTION_HEIGHT = 20

    def sizeHint(self, option, index):
        return self.CELL

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cell = option.rect.adjusted(4, 4, -4, -4)
        painter.fillRect(cell, CELL_BACKGROUND)

        image_box = cell.adjusted(8, 8, -8, -8 - self.CAPTION_HEIGHT)
        draw_thumbnail(painter, image_box, index.data(ThumbRole), index.data())

        caption = QRect(cell.left() + 6, cell.bottom() - self.CAPTION_HEIGHT - 2,
                        cell.width() - 12, self.CAPTION_HEIGHT)
        painter.setPen(TEXT)
        name = painter.fontMetrics().elidedText(index.data(), Qt.TextElideMode.ElideMiddle,
                                                caption.width())
        painter.drawText(caption, Qt.AlignmentFlag.AlignCenter, name)

        if index.data(PickedRole):
            draw_pick_corner(painter, cell, 30)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(SELECTED_BORDER, 3))
            painter.drawRect(cell.adjusted(1, 1, -2, -2))
        painter.restore()


class GridView(QListView):
    """Lightroom-style thumbnail grid. Shows a hint when nothing is loaded."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(GridDelegate.CELL)
        self.setItemDelegate(GridDelegate(self))
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # all keys go to the main window
        self.setStyleSheet("QListView { background: #262626; border: none; }")
        self.empty_hint = ""

    def columns(self):
        """How many thumbnails fit in one row right now."""
        model = self.model()
        if model is None or model.rowCount() == 0:
            return 1
        first_top = self.visualRect(model.index(0)).top()
        count = 0
        while count < model.rowCount() and self.visualRect(model.index(count)).top() == first_top:
            count += 1
        return max(1, count)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.model() is not None and self.model().rowCount() == 0 and self.empty_hint:
            painter = QPainter(self.viewport())
            painter.setPen(DIM_TEXT)
            font = painter.font()
            font.setPointSize(15)
            painter.setFont(font)
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.empty_hint)


# --- filmstrip --------------------------------------------------------------


class FilmstripDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 58

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor("#4a4a4a") if selected else QColor("#2b2b2b"))
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top(), 4, rect.height()), SELECTED_BORDER)

        thumb_box = QRect(rect.left() + 10, rect.top() + 4, 74, rect.height() - 8)
        draw_thumbnail(painter, thumb_box, index.data(ThumbRole))

        text_left = thumb_box.right() + 10
        text_width = rect.right() - text_left - 6
        picked = index.data(PickedRole)

        painter.setPen(TEXT)
        name_rect = QRect(text_left, rect.top() + 8, text_width, 20)
        name = painter.fontMetrics().elidedText(index.data(), Qt.TextElideMode.ElideMiddle,
                                                text_width)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        state_rect = QRect(text_left, rect.top() + 30, text_width, 18)
        painter.setPen(PICK_GREEN if picked else DIM_TEXT)
        font = painter.font()
        font.setBold(picked)
        painter.setFont(font)
        painter.drawText(state_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "✓ pick" if picked else "–")
        painter.restore()


class FilmstripView(QListView):
    def __init__(self):
        super().__init__()
        self.setUniformItemSizes(True)
        self.setItemDelegate(FilmstripDelegate(self))
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListView { background: #2b2b2b; border: none; }")
        self.setMinimumWidth(180)


# --- single view ------------------------------------------------------------


class LoupeView(QWidget):
    """One photo, fit to the window. Double-click goes back to the grid."""

    double_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.image = None  # QPixmap, "loading" or "broken"
        self.name = ""
        self.picked = False
        self.is_full_size = False

    def show_photo(self, name, image, picked, is_full_size):
        self.name = name
        self.image = image
        self.picked = picked
        self.is_full_size = is_full_size
        self.update()

    def clear(self):
        self.show_photo("", None, False, False)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1a1a1a"))
        if self.image is None:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        box = self.rect().adjusted(16, 16, -16, -16)

        if isinstance(self.image, QPixmap):
            target = _fit(self.image.size() / self.image.devicePixelRatio(), box)
            painter.drawPixmap(target, self.image)
        else:
            target = _fit(QSize(3, 2), box)
            draw_thumbnail(painter, target, self.image, self.name)
            if self.image == "broken":
                painter.setPen(TEXT)
                painter.drawText(target.adjusted(0, 0, 0, -20),
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                                 "This file could not be decoded.")

        painter.setPen(TEXT)
        caption = self.name if self.is_full_size else f"{self.name}  (loading…)"
        painter.drawText(QRect(16, 0, self.width() - 32, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, caption)
        if self.picked:
            draw_pick_corner(painter, target, 56)


# --- shortcuts overlay ------------------------------------------------------


class ShortcutsOverlay(QWidget):
    """Translucent panel over the main area listing every shortcut.

    Built from the same table that creates the key bindings (see app.SHORTCUTS),
    so it can't go out of date.
    """

    def __init__(self, parent, rows):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("ShortcutsOverlay { background: rgba(0, 0, 0, 170); }")

        panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet("#panel { background: #303030; border: 1px solid #555;"
                            " border-radius: 10px; } QLabel { color: #ddd; }")
        grid = QGridLayout(panel)
        grid.setContentsMargins(28, 22, 28, 22)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(5)

        title = QLabel("Keyboard Shortcuts")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 5)
        title_font.setBold(True)
        title.setFont(title_font)
        grid.addWidget(title, 0, 0, 1, 2)

        row = 1
        for keys, description in rows:
            key_label = QLabel(keys)
            key_label.setStyleSheet("color: #f0c040; font-weight: bold;")
            key_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(key_label, row, 0)
            grid.addWidget(QLabel(description), row, 1)
            row += 1

        footer = QLabel("Press ? or Esc to close")
        footer.setStyleSheet("color: #888; padding-top: 8px;")
        grid.addWidget(footer, row, 0, 1, 2)

        layout = QVBoxLayout(self)
        layout.addWidget(panel, 0, Qt.AlignmentFlag.AlignCenter)
        parent.installEventFilter(self)  # to follow the parent's size
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def mousePressEvent(self, event):
        self.hide()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.setGeometry(self.parentWidget().rect())
            self.raise_()
            self.show()
