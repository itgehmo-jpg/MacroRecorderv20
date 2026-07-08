# UO Macro Helper — Build via GitHub Actions

You don't need Python installed locally. GitHub Actions builds the `.exe` for you
on a Windows runner every time you push.

## Setup (one-time)

1. Create a new GitHub repository (or use an existing one).
2. Upload these three items to the repo, keeping the folder structure:
   - `macro_recorder.py`
   - `requirements.txt`
   - `.github/workflows/build.yml`
3. Commit and push to the `main` branch.

## Getting the .exe

1. Go to your repo's **Actions** tab.
2. Click the latest **Build Windows EXE** run (it starts automatically on push,
   or click **Run workflow** to trigger it manually).
3. Wait for the run to finish (usually 2–4 minutes).
4. Scroll to the bottom of the run page → **Artifacts** → download
   `UO_Macro_Helper-windows-exe`.
5. Unzip it — inside is `UO_Macro_Helper.exe`.

## Updating the app

Whenever you edit `macro_recorder.py`, just commit and push the change.
The workflow re-runs automatically and produces a fresh `.exe`.

## Notes

- The build runs on `windows-latest` so `pywin32` and `win32gui`/`win32con`/etc.
  are available and compiled correctly for Windows.
- `--collect-all pynput` bundles pynput's backend modules that PyInstaller
  sometimes misses.
- The `--hidden-import` flags cover the win32 modules used for window
  targeting, message posting, and DPI awareness.
- If you add new imports to `macro_recorder.py` that PyInstaller doesn't
  auto-detect, add matching `--hidden-import` flags in `build.yml`.
