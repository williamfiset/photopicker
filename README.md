# PhotoPick

A small desktop tool for culling photos off an SD card, in the spirit of
Lightroom's Library module: browse a set of photos, flag the keepers with the
keyboard, and export a plain text list of the chosen file names.

It never copies, moves or modifies your photos. The exported `.txt` file is the
only output.

## Install

You need Python 3.10 or newer (on macOS, `brew install python` works).

```sh
cd photopicker
python3 -m venv ~/.venvs/photopick         # anywhere is fine; outside Google Drive/Dropbox is best
source ~/.venvs/photopick/bin/activate     # Windows: %USERPROFILE%\.venvs\photopick\Scripts\activate
pip install -r requirements.txt
```

This installs PySide6 (Qt), Pillow, pillow-heif and rawpy, and adds a
`photopick` command to the venv.

## Run

```sh
photopick                                   # start empty, then press ⌘O / Ctrl+O
photopick /Volumes/SDCARD/DCIM              # a folder, scanned recursively
photopick a.jpg b.ARW ~/Desktop/c.jpg       # only these files
photopick ~/shoot1 ~/shoot2/DSC0001.ARW     # mix folders and files
photopick /Volumes/SDCARD/DCIM --picks ~/album/picks.txt   # save picks as you go
python -m photopick /path/to/folder         # same thing without the command
```

### Picks files (`--picks`)

With `--picks FILE`, PhotoPick reads the picks already in `FILE` (if it exists)
and rewrites it after every P, X or Space. You can quit at any point and run the
same command later to carry on where you stopped. Picks that aren't in the
loaded set, for example from a card that isn't inserted, are kept in the file.

Without `--picks`, picks only live in memory until you export them (⌘E).

Inside the app you can also:

- **File > Open Folder…** (⌘O) or **Open Files…** (⇧⌘O) to start a new set.
  The files dialog allows multi-select from anywhere.
- **File > Add Folder…** / **Add Files…** to add to the current set without
  losing what's loaded.
- Drag a folder or a group of files onto the window to add them to the set.

When loading files, anything that isn't a supported image is skipped and the
status bar says how many were skipped. Hidden files (like the `._DSC0001.ARW`
files macOS leaves on SD cards) are ignored.

## Culling

- **Grid view** (G): thumbnails, the selected photo has a yellow border, picks
  have a green check mark in the corner.
- **Single view** (E or Return): one photo, fit to the window. Clicking a
  thumbnail in the grid opens it here; double-click or Esc goes back.
- **Filmstrip** on the left: always visible, shows every photo's thumbnail,
  name and pick state. Click to jump.
- **Status bar**: `photo 37 of 412 - 18 picked`, the source (folder path or
  `412 files`), the **advance after flag** toggle, and a `? shortcuts` hint.

By default P and X don't move to the next photo. Turn on **advance after flag**
in the status bar (or the View menu) to make them auto-advance.

## Keyboard shortcuts

Press `?` or F1 in the app to see this list (Help > Keyboard Shortcuts too).
On Windows and Linux, ⌘ is Ctrl, ⌥ is Alt and ⇧ is Shift.

| Keys | Action |
|------|--------|
| ← | Previous photo |
| → | Next photo |
| ↑ | Up one row (grid) / previous photo (single view) |
| ↓ | Down one row (grid) / next photo (single view) |
| Home | First photo |
| End | Last photo |
| P | Mark pick |
| X | Mark skip (clear the pick) |
| Space | Toggle pick on / off |
| G | Grid view |
| E / Return / Enter | Single view of the selected photo |
| Esc | Back to grid / close the shortcuts panel |
| ? / F1 | Show / hide keyboard shortcuts |
| ⌘O | Open a folder |
| ⇧⌘O | Open a list of files |
| ⌥⌘O | Add a folder to the current set |
| ⌥⇧⌘O | Add files to the current set |
| ⌘E | Export the picks list |

## Export

⌘E (File > Export Picks…) opens a save dialog and writes the picks as full
paths, one per line, in the same order as the set. The default name is
`picks-<folder-name>-YYYYMMDD-HHMM.txt`, or `picks-files-…` when the set came
from a file list. This is the same format as a `--picks` file.

## Supported formats

JPG/JPEG, PNG, HEIC/HEIF, and RAW: ARW, CR2, CR3, NEF, DNG, RAF, ORF (plus
RW2, PEF and SRW). For RAW files PhotoPick shows the JPEG preview the camera
embedded in the file, which is fast and good enough for culling. EXIF
orientation is honored, so portrait shots display upright. Files that can't be
decoded show a placeholder with their name.

## Where things are stored

- **Picks**: only in the `--picks` file you pass (or the file you export to).
  There is no global pick history, so each album starts clean.
  **File > Clear All Picks…** clears the picks for the photos currently loaded.
- **Thumbnail cache**: the user cache folder (macOS:
  `~/Library/Caches/PhotoPick/thumbnails`), never on the SD card. Entries are
  keyed by path + size + modification time. You can delete the folder at any time.

No network access, accounts or telemetry.

## Files

- `photopick/app.py`: main window, the shortcut table, menus, loading, export
- `photopick/widgets.py`: list model, background loader, grid, filmstrip,
  single view, shortcuts overlay
- `photopick/images.py`: decoding, RAW previews, orientation, thumbnail cache
- `photopick/photoset.py`: the photo set, pick state and picks file
