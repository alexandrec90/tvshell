# TODO — Tvshell

## Setup

- [ ] Confirm `python scripts/sync-devkit.py --list` shows a stamped `DEVKIT_VERSION`
- [ ] Set `DEVKIT_DIR` in CI so the drift check actually gates — a
      `--check` that prints "nothing to do (skipping)" is checking nothing
      (once `DEVKIT_VERSION` is stamped it fails instead of skipping; to check
      drift on a machine with no devkit clone, `pre-commit run devkit-drift`)
- [ ] Ship devkit's `agent/register-tvshell-1113` branch (workspace registration and
      `ports.toml` slot 11), left for the fix pass by `new-project.py`

## Known gaps

- [ ] **Most of the C# helper still has no test**: `tests/helper-untested.txt` is the
      exact list, and it only shrinks. The pure parts are testable once extracted from
      the Win32 calls and given a mode of the exe, as `TvDisplay.Pick` was: which window
      the controller should focus (`OnPadReport`'s priority), which screen a game launch
      chose (mode file vs process start time), the launcher's last-channel pick from
      `PVR.GetChannels`.
- [ ] The helper's tests write to the real `%LOCALAPPDATA%/TvShell/tvshell.log`, so a
      test that provokes an ERROR line puts one where the nightly health check reads.
      Give the exe a log-directory override for tests.
- [ ] `helper/TvShell.cs` is one 700-line file; the structure ratchet only scans Python.
      Split it (helper, launchers, add-on patches, Win32 interop) when it next grows.
- [ ] The add-on patches in the helper are string replacements that silently stop
      applying if an add-on update rewrites those lines. Log when a patch target is
      missing rather than only when a patch is applied.
- [ ] Steam settings this setup relies on are set by hand in Steam's UI and are not
      reproducible from here: Desktop Layout joysticks = None, screensaver off while
      playing, Steam Input enabled for the game tile.
- [ ] `game.cfg` parsing is duplicated in C# (`Game.Load`) and Python
      (`tiles.parse_cfg`); a test feeding both the same file would keep them agreeing.

## Archive

- [x] Replace the placeholder in `tvshell/` with something that does the job
- [x] Delete `tests/test_smoke.py` once real tests exist
- [x] `.env` — none needed: no stack, no ports, no credentials
- [x] The `helper` job in `pr-gate.yml` has not run yet: confirm on its first PR that
      the Windows runner finds `csc.exe` and the job is green. It reports; it is never
      made a required check (delivery is `direct`). Confirmed on `main`: 202 passed
