"""Image decoding, RAW preview extraction, and the on-disk thumbnail cache.

Everything in here is safe to call from a worker thread: it only uses Pillow,
rawpy and QImage (never QPixmap).
"""

import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtGui import QImage

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC files will just show as broken
    pillow_heif = None

try:
    import rawpy
except ImportError:  # RAW files will fall back to Pillow (works for some DNGs)
    rawpy = None


RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}
PLAIN_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | PLAIN_EXTENSIONS

THUMBNAIL_SIZE = 320  # long edge, in pixels, of cached thumbnails
EXIF_ORIENTATION = 0x0112


def is_supported(path):
    """True if the file looks like an image we can show (by extension).

    Names starting with "." are skipped: cameras and macOS leave "._DSC0001.ARW"
    metadata files on SD cards that are not real images.
    """
    name = os.path.basename(path)
    if name.startswith("."):
        return False
    return os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS


def is_raw(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


# --- decoding ---------------------------------------------------------------


def open_image(path, min_size=None):
    """Open any supported file as an upright Pillow image.

    For RAW files this is the camera's embedded JPEG preview, not a demosaic.
    `min_size` lets JPEG decoding skip work when we only need a small image.
    """
    if is_raw(path) and rawpy is not None:
        try:
            return _open_raw_preview(path, min_size)
        except Exception:
            pass  # fall through and let Pillow try (it can read some DNGs)

    img = Image.open(path)
    if min_size and img.format == "JPEG":
        img.draft("RGB", (min_size, min_size))
    return ImageOps.exif_transpose(img)


def _open_raw_preview(path, min_size):
    with rawpy.imread(path) as raw:
        thumb = raw.extract_thumb()
        flip = raw.sizes.flip

    if thumb.format == rawpy.ThumbFormat.JPEG:
        img = Image.open(io.BytesIO(thumb.data))
        if min_size:
            img.draft("RGB", (min_size, min_size))
        # Some cameras put the orientation inside the preview's own EXIF;
        # if so, trust it. Otherwise use the orientation from the RAW file.
        if img.getexif().get(EXIF_ORIENTATION, 1) != 1:
            return ImageOps.exif_transpose(img)
    else:
        img = Image.fromarray(thumb.data)

    # LibRaw flip codes: 3 = 180 degrees, 5 = 90 CCW, 6 = 90 CW
    if flip == 3:
        img = img.transpose(Image.Transpose.ROTATE_180)
    elif flip == 5:
        img = img.transpose(Image.Transpose.ROTATE_90)
    elif flip == 6:
        img = img.transpose(Image.Transpose.ROTATE_270)
    return img


def to_qimage(img):
    """Convert a Pillow image to a QImage that owns its own pixel data."""
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()  # detach from `data`, which Python will free


def load_preview(path, max_side):
    """Decode a photo for the single (loupe) view, scaled to fit max_side."""
    img = open_image(path, min_size=max_side)
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return to_qimage(img)


# --- thumbnail cache --------------------------------------------------------


def load_thumbnail(path, cache_dir):
    """Return a thumbnail QImage, using (and filling) the disk cache.

    The cache key is path + file size + modification time, so an edited or
    replaced file gets a fresh thumbnail.
    """
    stat = os.stat(path)
    key = hashlib.sha1(f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()).hexdigest()
    cache_file = Path(cache_dir) / key[:2] / f"{key}.jpg"

    if cache_file.exists():
        try:
            with Image.open(cache_file) as cached:
                return to_qimage(cached)
        except Exception:
            pass  # corrupt cache entry: regenerate it below

    img = open_image(path, min_size=THUMBNAIL_SIZE)
    img.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
    img = img.convert("RGB")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = cache_file.with_name(f"{key}.{os.getpid()}-{threading.get_ident()}.tmp")
    img.save(tmp_file, "JPEG", quality=85)
    os.replace(tmp_file, cache_file)  # atomic, so a crash never leaves half a file
    return to_qimage(img)
