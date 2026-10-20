"""The set of photos being culled, the pick state, and the picks file.

No Qt in here, so it is easy to reason about and test on its own.
"""

import os
from dataclasses import dataclass
from datetime import datetime

from .images import is_supported


@dataclass
class Photo:
    path: str  # absolute path
    name: str  # file name only


def _make_photo(path):
    path = os.path.abspath(path)
    return Photo(path=path, name=os.path.basename(path))


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
            photo = _make_photo(path)
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
    """Which photos are picked, by full path.

    If a picks file is given (an album's picks.txt), it is read at startup and
    rewritten after every change, so quitting never loses anything and the next
    session picks up where this one stopped. Without one, picks only live in
    memory until exported.
    """

    def __init__(self, picks_file=None):
        self.picks_file = picks_file
        self.picked = set()
        if picks_file and os.path.exists(picks_file):
            self.picked = set(read_picks(picks_file))

    def is_picked(self, photo):
        return photo.path in self.picked

    def set_picked(self, photo, picked, photos):
        if picked:
            self.picked.add(photo.path)
        else:
            self.picked.discard(photo.path)
        self.save(photos)

    def clear(self, photos):
        self.picked -= {p.path for p in photos}
        self.save(photos)

    def ordered_paths(self, photos):
        """Picked paths in set order, then any picks not in the current set
        (e.g. from a card that isn't mounted right now), so none are dropped."""
        in_set = [p.path for p in photos if p.path in self.picked]
        others = sorted(self.picked - set(in_set))
        return in_set + others

    def save(self, photos):
        if not self.picks_file:
            return
        try:
            tmp_path = self.picks_file + ".tmp"
            write_picks(tmp_path, self.ordered_paths(photos))
            os.replace(tmp_path, self.picks_file)  # atomic: never a half-written file
        except OSError as e:
            print(f"photopick: could not save picks to {self.picks_file}: {e}")


# --- export -----------------------------------------------------------------


def default_export_name(photo_set, now=None):
    now = now or datetime.now()
    folder = photo_set.single_folder()
    label = os.path.basename(folder.rstrip(os.sep)) if folder else "files"
    return f"picks-{label or 'files'}-{now:%Y%m%d-%H%M}.txt"


def write_picks(out_path, paths):
    """Write one full path per line."""
    with open(out_path, "w", encoding="utf-8") as f:
        for path in paths:
            f.write(path + "\n")


def read_picks(picks_file):
    with open(picks_file, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]
