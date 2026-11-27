# Tvshell

A TV media centre on a second display: Steam Big Picture runs full-screen on the TV,
the desk monitor stays an ordinary desktop, and a game controller drives the TV side.
Big Picture tiles launch Kodi (live TV, radio, CBC Gem), YouTube's TV interface and,
optionally, BackWhenTV's Windows app and one game. The app's tile appears once its portable
exe is in `backwhentv-app/` in the install directory; the repository does not download it.
BackWhenTV's web player was tried and dropped: it streams raw tape rips from the Internet
Archive that the archive serves slower than they play, so its channels never start. Its
tile is in `tiles.RETIRED_TILES`, which `steam_shortcuts` removes.

**The repository names no game.** The game is whatever the local `game.cfg` in the install
directory says (process, store launcher, launch arguments, tile name, icon); without that
file the game features are off. Keep it that way: no game, publisher or store name in
code, tests, docs or commit messages.

## Tech Stack

| Layer | Choice |
| --- | --- |
| The helper | C#, one file (`helper/TvShell.cs`), built by .NET Framework's `csc.exe` |
| Tooling | Python 3.12, standard library only (`tvshell/`) |
| Tests | pytest; the `ci_e2e` tier (`scripts/e2e.py --ci`, on a Windows runner) compiles and runs the helper |
| Lint | ruff, mypy |

## Two places: the source and the install

The repository is the **source**. What runs is installed outside it, in
`tiles.INSTALL_DIR` (`~/TvShell`): Windows startup (HKCU `Run` value `TvShell`), every
Steam tile and the desktop shortcut point at `~/TvShell/TvShell.exe`, so a checkout can
move or be deleted without breaking the TV. `python -m tvshell.deploy` builds the helper
from `helper/` and swaps it in; a launcher still running from the old exe (a game or
Kodi in progress) keeps running. A deploy from a branch puts back a helper without what
other branches merged, so the scheduled `scripts/e2e.py` installs the merged one afterwards.

The install directory also holds runtime state the helper owns: the YouTube Chrome
profile (`yt-profile/`), `steam-gameid.txt`, and its log under
`%LOCALAPPDATA%/TvShell/`.

**Which display is the TV** comes from `tv.cfg` there: `monitor=<EDID code>`, the
segment after `DISPLAY#` in the monitor's device path (the log prints every display's
path when the TV is not found). Without it the TV is the non-primary display only
when there are exactly two. No match means no TV, and the helper moves nothing. It
never guesses, because after a bad boot the driver can bring the displays up in a
different layout.

**The desk is the primary display, and the helper keeps it so** (`DeskPrimary`). The
plasma reports its own identity when it is on at boot and its adapter's when it is not,
Windows keeps a separate layout for each, and one of them had the TV as primary: taskbar
and sign-in on a screen nobody at the desk can see. When the display `tv.cfg` names is
the primary one of exactly two, the helper makes the other primary with the TV to its
right. That is the one display setting it changes, and only then: with no `tv.cfg`, or
any other number of displays, it leaves them alone. It uses `SetDisplayConfig`, because
`ChangeDisplaySettingsEx` refuses any change naming the TV while Windows has it in a
mode that is not in its mode list (852x480, the default it picked for the plasma).

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
- **The TV and the desk are separate seats.** Windows has one focused window, so each
  side takes it back when its own input is used: a controller press focuses the TV, a
  physical key focuses the last desk window (a low-level keyboard hook holds that first
  key back so it never types on the TV), and the mouse is confined to the desk. Keys
  Steam Input injects for the controller never count. Nothing automatic (a TV app
  covered, Big Picture restarted) takes focus while the desk has it. Steam switches the
  controller's report format as focus moves, so a press is only ever read against the
  previous report of the same format; across formats, every focus change looked like one.
- **One TV app at a time.** A TV app started from its tile closes the other one;
  otherwise the first hides the second and the controller never reaches it.
- **Steam counts a tile as running while anything it started is alive**, and then its
  "Resume" does nothing useful. So the game launcher closes the store launcher when the
  game exits, and Big Picture is not restarted over a TV app still open on the TV.
- **Steam hides Big Picture while a game runs**; that is not "closed". Reopening it then
  covers the game with a black screen.
- **Steam's window can end up focused and black over the game or TV app**: after its menu
  is closed, or when a second running tile ends. Its "Resume" does not bring the app
  back, and a controller press is never taken from a focused Steam window (that would
  fight the menu in use), so there was no way out from the couch. The helper reads the
  TV's pixels (`BlankSteam`): a Steam window showing only black for two seconds is not
  a menu in use, and focus goes back to what is running.
- **Kodi cannot be placed on a monitor reliably**, so it runs windowed and the helper
  strips the border and fits the client area to the TV.
- **Kodi plays on behind the game, and that can crash it.** Kodi 21.3 playing video behind
  another window (the game, Big Picture) stops presenting frames ("OutputPicture - timeout
  waiting for buffer") and sometimes crashes in its video output minutes later. The helper
  can stop covered video after a minute over JSON-RPC (`StopVideoWhenCovered`), but the
  owner turned that off for Kodi: a programme is listened to behind the game, and stopping
  it cut that off. Each such crash shows up in the health check; do not turn the guard
  back on without asking.
- **The BackWhenTV app reads only the keyboard**, and Steam Input's keyboard bindings did
  not reach its web player's window either, so the helper turns controller presses into
  keys itself (`PadKeys`), only while the app has focus. Its tile keeps Steam's default
  (key-less) layout so no press lands twice.
- **Chrome hands off to a new browser process and exits**; the web-app launcher
  (`WebApp`, today YouTube only) finds the real one by its profile path. Everywhere else the YouTube browser is also told
  apart from the person's own Chrome by that profile on its command line, never by
  `youtube.pid` alone: a killed launcher leaves the file behind and Windows reuses PIDs.
- **Only the TV's own windows stay on the TV.** When the displays come back in a
  different layout Windows moves open windows around, and an app that lands on the TV
  saves that position and reopens there, out of reach of the confined mouse. So any
  other titled top-level window found on the TV goes back to the desk (`TvSeat`); a
  tray toggle turns that off, and a window that refuses the move or keeps returning
  is left alone after three tries.

- **The desk monitor sleeps while the TV plays** (`DeskBlank`). A playing app holds
  "display required", so Windows' monitor timeout never fires. Who holds it cannot be read
  without admin (the execution state shows only the caller's own requests), so a hold is
  inferred: the timeout has passed by 30 s with no keyboard, mouse or controller input and
  Windows still reports the displays on. The desk monitor then goes to DDC/CI standby and
  wakes at the next input; the display layout is never touched. **Open question:** once,
  the Dell U2412M left in standby for three hours reported itself on after the wake but
  showed no picture until its power button was pressed. Short reproductions all recovered,
  so the cause is unknown; if it recurs, replace standby with brightness 0 and a black
  window, which never changes the monitor's power state.

It also re-applies two patches to third-party Kodi add-ons on every start, because an
add-on update overwrites them (`EnsureAddonPatches`): SlyGuy's bandwidth cap accepting
decimals, and a browser User-Agent for CBC Radio, whose server stalls Python's default
one. A patch whose target code has changed logs an ERROR, since it then does nothing.

## Logging and the runtime health check

The helper and every launcher write one shared log, `%LOCALAPPDATA%/TvShell/tvshell.log`,
rotated to `tvshell.old.log` at 4 MB. Each line is `time LEVEL role[pid] message`, and
the level is a contract: **ERROR means a defect someone has to fix**, WARN is worth
knowing, INFO is the story. Every focus change is logged with the process that took it,
so a "lost focus" has a culprit; every TV app's exit is logged with its exit code; a
crash is an ERROR, and Kodi's own log is copied to `crashes/` before its next start
overwrites it.

**The trace is the evidence behind the story** (`trace.log` beside it, 32 MB, rotated to
`trace.old.log`). Each line is written when something changes: what the controller holds
(`pad`, Home included), every press with what the helper decided and why (`press`, never
silent), every focus change with how late its event came (`focus`), what the TV shows
(`tv`: focus, the window on top, whether the picture is black, every window over the TV
front first), and UDP endpoint owners (`udp`). Presses and TV changes save a half-size
screenshot to `tv-shots/` (the newest 1500), and the trace line names the file. Look at
the screenshots: Steam's menu is drawn inside the window under it, so window data says
Kodi is on top while the TV shows Steam's menu. **Diagnose from the trace and the
screenshots before changing the helper**; a fix from tvshell.log alone is a guess.

**Marks.** Minus and Plus held together for two seconds on the controller, or "Mark a
problem on the TV now" in the tray, copies a screenshot, the last screenshots and the tails
of the trace, this log and Kodi's to `marks/<time>/`, which nothing prunes, and logs an
ERROR naming that folder. A taken mark plays Windows' `tada.wav` and shows a green banner
for four seconds (`Cue`): on the TV, or on the desk for the tray. Start a fix there.

`tvshell/health.py` reads all of that, plus the Windows Application log's crash events,
and the `local` tier runs it nightly (`tests/test_health.py`), one test per kind of
problem: a crash, an ERROR line, Windows refusing most focus changes, the helper not
running. That is how a problem on the TV reaches the fix pass with nobody reporting it.
A Kodi crash after Kodi's own last log line ("Exiting the application...") is a WARN and
a note in the artifact, not a crash: Kodi 21.3 corrupts its heap in its final teardown on
most exits, after saving everything, and only Kodi can fix that.
The evidence is in `logs/runtime-health.json`; `python -m tvshell.health` runs it by hand.
The window is the last 24 hours, cut at the installed helper's build time, so a deployed
fix clears what came before it. Log an ERROR only for what a fixer should act on, or the
tier stays red over noise.

## Commands

```bash
python -m tvshell.deploy                    # build the helper, install it at once, test and check it meanwhile
python -m tvshell.deploy --no-drill         # the same without the display drill (someone is watching the TV)
python -m tvshell.verify                    # is the TV side right, right now? read-only; --drill adds the drill
python -m tvshell.steam_shortcuts --vdf <Steam userdata/<id>/config/shortcuts.vdf>   # Steam closed
python -m tvshell.kodi install <addon id>   # Kodi running; answers its prompts
python -m tvshell.slyguy set max_bandwidth 0.7   # Kodi closed
python -m tvshell.health                    # the TV's last 24 h: crashes, errors, focus
python scripts/e2e.py                       # the local tier: the TV is healthy, the desk is primary
python scripts/e2e.py --ci                  # the CI tier: the helper compiles and its tests pass
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
- **`local` marks what needs this PC**: the runtime health check against the real logs
  and event log, and the installed helper's read of the real displays. `addopts`
  deselects it; `scripts/e2e.py` runs it.
- **`ci_e2e` marks what needs only Windows and `csc.exe`**: compiling the helper and every
  test that runs it, which a `windows-latest` runner has. `addopts` deselects it;
  `scripts/e2e.py --ci` runs it, in the `E2E` job of `.github/workflows/pr-gate.yml` on
  the default branch.
- **A helper decision is tested through a mode of the exe.** The decision is a pure
  static function (`TvDisplay.Pick`, `DeskPrimary.Plan`), a mode runs it from the command
  line (`pick-tv`, `plan-primary`), and a test passes cases to the built exe through the
  `exe` fixture and reads the exit code. `tests/conftest.py` marks every such test
  `helper` and `ci_e2e`.
- **No test holds a fix back; every deploy says what it broke.** The owner's rule: a
  fix goes onto the TV at once, and testing and repairing happen beside it. So
  `python -m tvshell.deploy` installs as soon as the helper compiles, with every
  `helper` test running against that binary in the background, and delivery is
  `direct` in `.devkit.toml` (merge first, checks after). Do not add a step that
  refuses an install or a merge on a red test. After the install the deploy runs
  `tvshell/verify.py` against the installed helper: it is running, the desk is the
  primary display, no ERROR since it started, and the **display drill**, which makes
  the TV primary for real and requires the running helper to restore the exact layout.
  A red is exit 1 with the new helper in place, and the next thing to fix. The `helper`
  job in `.github/workflows/pr-gate.yml` runs the same tests on a Windows runner for a
  pull request; on the default branch the `E2E` job runs them, with the rest of `ci_e2e`.
- **Every helper function has a test or is on a list that only shrinks.**
  `tests/test_helper_coverage.py` reads `helper/TvShell.cs` in the plain suite: each
  method and mode must be named in the `COVERS` tuple of a test module that runs the
  exe, or be in `tests/helper-untested.txt`, whose length is pinned in that test. A new
  function is never added to the list; it comes with a test. A `COVERS` entry is a
  claim the check cannot judge, so make it true.
- **A fix for something seen on the TV is not done until it has run against the real
  fault.** The pure tests for `DeskPrimary` were green while Windows refused the change
  on the actual plasma. Reproduce the fault on this PC, run the fix, and read the state
  back with something other than the code under test.
- **The helper's tests write to the real log** (`%LOCALAPPDATA%/TvShell/tvshell.log`):
  a mode run from a test logs as its mode name. Read `role[pid]` before taking a line
  for the TV's own.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`. Cross-reference
this project's own scoped rules here, one line each.
