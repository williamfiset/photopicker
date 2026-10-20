"""The set of photos being culled, the saved pick state, and export.

No Qt in here, so it is easy to reason about and test on its own.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime

from .images import is_supported


@dataclass
class Photo:
    path: str  # absolute path
    name: str  # file name only
    size: int  # bytes, used to tell a re-used SD card path from the original photo


def _make_photo(path):
    path = os.path.abspath(path)
    return Photo(path=path, name=os.path.basename(path), size=os.path.getsize(path))


def _sort_key(path):
    return (os.path.dirname(path).lower(), os.path.basename(path).lower())


class PhotoSet:
    """An ordered list of photos, built from folders and/or individual files."""

    def __init__(self):
        self.photos = []
        self.sources = []  # ("folder", path) or ("files", count), in the order added
        self._known_paths = set()

    def __len__(self):
        return len(self.photos)

    def add_folder(self, folder):
        """Recursively add every supported image under `folder`. Returns count added."""
        folder = os.path.abspath(folder)
        found = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if is_supported(path):
                    found.append(path)
        added = self._add_paths(sorted(found, key=_sort_key))
        self.sources.append(("folder", folder))
        return added

    def add_files(self, paths):
        """Add specific files. Returns (added, skipped_unsupported)."""
        supported = []
        skipped = 0
        for path in paths:
            if os.path.isfile(path) and is_supported(path):
                supported.append(os.path.abspath(path))
            else:
                skipped += 1
        added = self._add_paths(sorted(supported, key=_sort_key))
        if added:
            self.sources.append(("files", added))
        return added, skipped

    def _add_paths(self, paths):
        added = 0
        for path in paths:
            if path in self._known_paths:
                continue
            try:
                photo = _make_photo(path)
            except OSError:
                continue  # vanished or unreadable; nothing useful to show
            self.photos.append(photo)
            self._known_paths.add(photo.path)
            added += 1
        return added

    def single_folder(self):
        """The folder path if the set came from exactly one folder, else None."""
        if len(self.sources) == 1 and self.sources[0][0] == "folder":
            return self.sources[0][1]
        return None

    def source_label(self):
        folder = self.single_folder()
        if folder:
            return folder
        return f"{len(self.photos)} files"


class PickStore:
    """Pick state saved as JSON, keyed by full file path.

    Each entry also remembers the file size, so if a formatted SD card later
    holds a different photo at the same path, it does not show up as picked.
    """

    def __init__(self, json_path):
        self.json_path = json_path
        self.picks = {}  # path -> size in bytes
        self._load()

    def _load(self):
        try:
            with open(self.json_path, encoding="utf-8") as f:
                self.picks = json.load(f).get("picks", {})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"photopick: could not read {self.json_path}: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
            tmp_path = self.json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "picks": self.picks}, f, indent=1, sort_keys=True)
            os.replace(tmp_path, self.json_path)  # atomic: never a half-written file
        except OSError as e:
            print(f"photopick: could not save picks to {self.json_path}: {e}")

    def is_picked(self, photo):
        return self.picks.get(photo.path) == photo.size

    def set_picked(self, photo, picked):
        if picked:
            self.picks[photo.path] = photo.size
        else:
            self.picks.pop(photo.path, None)
        self.save()

    def clear(self, photos):
        for photo in photos:
            self.picks.pop(photo.path, None)
        self.save()


# --- export -----------------------------------------------------------------


def default_export_name(photo_set, now=None):
    now = now or datetime.now()
    folder = photo_set.single_folder()
    label = os.path.basename(folder.rstrip(os.sep)) if folder else "files"
    return f"picks-{label or 'files'}-{now:%Y%m%d-%H%M}.txt"


def write_picks(out_path, picked_photos, full_paths):
    """Write one file name (or full path) per line, in set order."""
    with open(out_path, "w", encoding="utf-8") as f:
        for photo in picked_photos:
            f.write((photo.path if full_paths else photo.name) + "\n")
