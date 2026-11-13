# Tvshell

A TV media centre on a second display: Steam Big Picture runs full-screen on the TV,
the desk monitor stays an ordinary desktop, and a game controller drives the TV side.
Big Picture tiles launch Kodi (live TV, radio, CBC Gem), YouTube's TV interface and,
optionally, one game.

**The repository names no game.** The game is whatever the local `game.cfg` in the install
directory says (process, store launcher, launch arguments, tile name, icon); without that
file the game features are off. Keep it that way: no game, publisher or store name in
code, tests, docs or commit messages.

## Tech Stack

| Layer | Choice |
| --- | --- |
| The helper | C#, one file (`helper/TvShell.cs`), built by .NET Framework's `csc.exe` |
| Tooling | Python 3.12, standard library only (`tvshell/`) |
| Tests | pytest; the `local` tier (`scripts/e2e.py`) compiles the helper |
| Lint | ruff, mypy |

## Two places: the source and the install

The repository is the **source**. What runs is installed outside it, in
`tiles.INSTALL_DIR` (`~/TvShell`): Windows startup (HKCU `Run` value `TvShell`), every
Steam tile and the desktop shortcut point at `~/TvShell/TvShell.exe`, so a checkout can
move or be deleted without breaking the TV. `python -m tvshell.deploy` builds the helper
from `helper/` and swaps it in; a launcher still running from the old exe (a game or
Kodi in progress) keeps running.

The install directory also holds runtime state the helper owns: the YouTube Chrome
profile (`yt-profile/`), `steam-gameid.txt`, and its log under
`%LOCALAPPDATA%/TvShell/`.

## What the helper does, and why each part exists

`helper/TvShell.cs` is one exe in several modes — no argument is the tray helper, an
argument is a launcher a Steam tile runs. The non-obvious constraints:

- **A launcher must be the parent of what it starts.** Steam's controller translation
  (Steam Input) follows the process tree of the tile it launched, so the game goes
  through its store launcher *started by the TvShell launcher* (an already-running store
  launcher is closed first), and Kodi and Chrome are its children.
- **Steam only sends the controller to the focused window**, so a controller press
  focuses the app on the TV — but only when focus is on the desk display. Pulling focus
  between windows already on the TV fights Steam's own menu (Home button).
- **Steam hides Big Picture while a game runs**; that is not "closed". Reopening it then
  covers the game with a black screen.
- **Kodi cannot be placed on a monitor reliably**, so it runs windowed and the helper
  strips the border and fits the client area to the TV.
- **Chrome hands off to a new browser process and exits**; the YouTube launcher finds
  the real one by its profile path.

It also re-applies three patches to third-party Kodi add-ons on every start, because an
add-on update overwrites them (`EnsurePlutoRealAds`): SlyGuy's bandwidth cap accepting
decimals, Pluto's server-side ads, and a browser User-Agent for CBC Radio, whose server
stalls Python's default one.

## Commands

```bash
python -m tvshell.deploy                    # build helper/TvShell.cs and install it
python -m tvshell.steam_shortcuts --vdf <Steam userdata/<id>/config/shortcuts.vdf>   # Steam closed
python -m tvshell.kodi install <addon id>   # Kodi running; answers its prompts
python -m tvshell.slyguy set max_bandwidth 0.7   # Kodi closed
python scripts/e2e.py                       # the local tier: the helper compiles
```

## Tooling

### Scripts and the vendored harness

Both are covered by **`.claude/rules/engineering.md`** — script conventions (pure
importable functions, stdlib-only hooks, tests in the same change), the failure-artifact
rule, and how the `.devkit.toml` seam works. That file is vendored from
[devkit](https://github.com/alexandrec90/devkit) and drift-gated, so it is the
authority; this file does not repeat it.

### VS Code tasks

- A task lives in the workspace file and dispatches through devkit's
  `devkit_project.py`, never in `.vscode/tasks.json`; that file's comment says why.
- A script a task runs starts as `python <script>` under the machine's `python`, not
  this project's `.venv`, with no console. It imports only the standard library until
  it has handed itself to the `.venv`, as `scripts/run-tests.py` does, and it cannot
  rely on Ctrl+C: a task that serves until stopped has to watch for its parent going.
- Use `"type": "process"` so VS Code monitors the process directly — that is what
  makes the spinner stop and the exit-code icon appear reliably.
- Set `"close": false` in `presentation` so the terminal stays open for review.
- **Wrap with `notify-wrap.py`** for the completion toast; never call `notify.py`
  from inside a script. Notifications are a task-layer concern only.
- Label convention: `"Domain: Title Case Action"`, and **every task carries a
  `detail`** — that is the second line in the quick-pick, and the only place a
  one-click action can state its cost or blast radius.

### Failure artifacts (fix from a file, not from the terminal)

Any task or script whose failures an agent is expected to act on must persist the
failure to a **parseable artifact file** under `logs/`. Never rely on streamed
terminal output — it scrolls away and buries the signal. Keep the terminal to a
status line plus the artifact path; put everything needed to diagnose in the file.
Write the artifact on failure too, not just success, and overwrite per run.

## Testing

**`.claude/rules/engineering.md`** is the authority: tests ship in the same commit,
every testable unit of logic is covered, regression test first, targeted runs locally
and full runs in CI.

Add this project's specifics *below* — fixtures, isolation rules, markers, what to mock
and where — but do not restate the policy above. It is vendored and drift-gated; a copy
here is a fork that will disagree with it the first time either is edited.

- **No test touches the real Steam, Kodi or Windows.** Kodi's sockets are faked with a
  local TCP/UDP server on port 0 (`tests/test_kodi.py`); Steam's file and SlyGuy's
  database live in `tmp_path`; process listing and `taskkill` are monkeypatched.
- **`local` marks what needs this PC** — today, compiling the helper with `csc.exe`.
  `addopts` deselects it; `scripts/e2e.py` runs it.
- **The C# helper's logic has no unit tests**, only the compile check. It is
  window-and-focus code against live Win32 state; see `TODO.md`.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`. Cross-reference
this project's own scoped rules here, one line each.
