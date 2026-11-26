// TvShell - keeps Steam Big Picture on the TV (secondary display) and the desk display an
// ordinary desktop. One exe, several modes:
//   TvShell.exe              -> background helper (tray icon, hotkey Ctrl+Alt+T)
//   TvShell.exe kodi <what>  -> Steam tile: Kodi on the TV (tv, radio, cbcradio, gem)
//   TvShell.exe youtube      -> Steam tile: YouTube's TV interface on the TV
//   TvShell.exe backwhen-app -> Steam tile: BackWhenTV's Windows app (backwhentv-app\) on the TV
//   TvShell.exe launch       -> Steam tile: the game from game.cfg, started through its
//                               store launcher so Steam Input (controller) attaches. On the TV.
//   TvShell.exe launch-desk  -> desktop shortcut: same, but the game goes to the desk display.
// The game is optional and named only in game.cfg beside the exe, never in this source.
// Nothing here touches any program's memory. It moves windows and focus, holds back a physical
// key pressed while the TV has focus (the keyboard belongs to the desk), and sends keys only to a
// keyboard-only app on the TV (BackWhenTV's) while that app has focus (PadKeys).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class Cfg {
    public static readonly string Dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TvShell");
    public static readonly string ModeFile = Path.Combine(Dir, "game-screen.txt");  // "tv"/"desk", written at launch
    public static readonly string NextFile = Path.Combine(Dir, "next-screen.txt");  // handoff from launch-desk to launch
    public static readonly string LogFile = Path.Combine(Dir, "tvshell.log");
    public static readonly string OldLogFile = Path.Combine(Dir, "tvshell.old.log");
    // A crashed app's own log, copied here before its next start overwrites it.
    public static readonly string CrashDir = Path.Combine(Dir, "crashes");
    public const string KodiExe = @"C:\Program Files\Kodi\kodi.exe";
    public static readonly string KodiLog = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), @"Kodi\kodi.log");
    public const string ChromeExe = @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe";
    public static string SteamGameId = "";  // filled from steam-gameid.txt next to the exe
    public static string Role = "helper";   // which mode wrote a line: the helper and the launchers share the file
    static readonly int Pid = Process.GetCurrentProcess().Id;
    const long MaxLog = 4L * 1024 * 1024;
    static readonly object gate = new object();

    // One line per entry: `yyyy-MM-dd HH:mm:ss.fff LEVEL role[pid] message`. The level is what
    // the runtime health check (tvshell/health.py) reads: ERROR is a defect someone must fix,
    // WARN is worth knowing, INFO is the story. A multi-line message (a stack trace) is folded
    // onto its line with " | ".
    public static void Log(string m) { Write("INFO", m); }
    public static void Warn(string m) { Write("WARN", m); }
    public static void Error(string m) { Write("ERROR", m); }
    static void Write(string level, string m) {
        var line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + level.PadRight(5) + " " + Role + "[" + Pid + "] "
            + (m ?? "").Replace("\r", "").Replace("\n", " | ") + Environment.NewLine;
        // Another process appending at the same moment is the usual failure: retry, don't drop the line.
        lock (gate) {
            for (int i = 0; i < 5; i++) {
                try {
                    Directory.CreateDirectory(Dir);
                    var fi = new FileInfo(LogFile);
                    // Rotated, not deleted: the hours before a problem are the ones worth reading.
                    if (fi.Exists && fi.Length > MaxLog) { File.Delete(OldLogFile); File.Move(LogFile, OldLogFile); }
                    File.AppendAllText(LogFile, line);
                    return;
                } catch { Thread.Sleep(25); }
            }
        }
    }

    // An exit code as a person reads it. A crash is an NTSTATUS value (0xC0000005 is an access
    // violation); `taskkill /F` gives 1 and .NET's Process.Kill gives -1.
    public static bool IsCrash(int code) { return code != -1 && unchecked((uint)code) >= 0xC0000000; }
    public static string ExitText(int code) {
        uint u = unchecked((uint)code);
        if (!IsCrash(code)) return code.ToString();
        string what = u == 0xC0000005 ? " access violation" : u == 0xC0000409 ? " stack buffer overrun"
            : u == 0xC00000FD ? " stack overflow" : u == 0xC0000374 ? " heap corruption" : "";
        return "0x" + u.ToString("X8") + what + ", a crash";
    }

    // Copy a crashed app's log aside (the app overwrites it on its next start); keeps the last 10.
    public static string KeepCrashLog(string name, string source) {
        try {
            if (!File.Exists(source)) return "";
            Directory.CreateDirectory(CrashDir);
            var dest = Path.Combine(CrashDir, name + "-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            File.Copy(source, dest, true);
            foreach (var old in new DirectoryInfo(CrashDir).GetFiles(name + "-*.log").OrderByDescending(f => f.Name).Skip(10)) old.Delete();
            return dest;
        } catch (Exception ex) { Warn("could not keep the " + name + " crash log: " + ex.Message); return ""; }
    }

    // Opens a handle now, so ExitCode can still be read once the process is gone. False when
    // Windows refuses (an elevated process): the exit code is then unknown.
    public static bool Hold(Process p) {
        try { return p != null && p.Handle != IntPtr.Zero; } catch { return false; }
    }
    // Kodi's last log line, once it has saved its settings and the EPG and unloaded everything.
    // Must match KODI_EXITED in tvshell/health.py.
    public const string KodiExitedLine = "Exiting the application...";
    // Whether an app's log ends with `line`: the app got that far before it went.
    public static bool LogEndsWith(string path, string line) {
        try {
            using (var s = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) {
                s.Seek(Math.Max(0, s.Length - 4096), SeekOrigin.Begin);
                var tail = new StreamReader(s).ReadToEnd().TrimEnd();
                return tail.EndsWith(line, StringComparison.Ordinal);
            }
        } catch { return false; }
    }

    public static string ExitOf(Process p) {
        try { return p.HasExited ? "exit " + ExitText(p.ExitCode) : "still running"; } catch { return "exit code unknown"; }
    }
}

// How a TV app ended, from its exit code and its own log.
static class AppExit {
    public enum Kind { Closed = 0, CrashedAfterShutdown = 1, Crashed = 2 }
    // Kodi 21.3 crashes in its last teardown on most exits, after it has saved everything and
    // logged `exitedLine`: Kodi's defect, which nothing here can fix.
    public static Kind Judge(int code, string log, string exitedLine) {
        if (!Cfg.IsCrash(code)) return Kind.Closed;
        return log != null && exitedLine != null && Cfg.LogEndsWith(log, exitedLine) ? Kind.CrashedAfterShutdown : Kind.Crashed;
    }
    // `TvShell.exe app-exit <exit code> <log> <exited line>`: exits with the Kind, 254 on bad arguments.
    public static int Cli(string[] args) {
        int code;
        if (args.Length != 4 || !int.TryParse(args[1], out code)) return 254;
        return (int)Judge(code, args[2], args[3]);
    }
}

// The optional game, from game.cfg beside the exe (key=value lines). Absent file or missing
// `process` means no game: the game menu, hotkey and window handling stay inert.
static class Game {
    public static string Name = "", Process = "", Icon = "", Launcher = "", LauncherProcess = "", LaunchArgs = "";
    public static bool Configured { get { return Process.Length > 0; } }
    public static bool HasLauncher { get { return Launcher.Length > 0; } }

    public static void Load(string path) {
        if (!File.Exists(path)) return;
        foreach (var raw in File.ReadAllLines(path)) {
            var line = raw.Trim();
            int eq = line.IndexOf('=');
            if (line.Length == 0 || line.StartsWith("#") || eq <= 0) continue;
            var key = line.Substring(0, eq).Trim().ToLowerInvariant();
            var value = line.Substring(eq + 1).Trim();
            if (key == "name") Name = value;
            else if (key == "process") Process = value;
            else if (key == "icon") Icon = value;
            else if (key == "launcher") Launcher = value;
            else if (key == "launcher_process") LauncherProcess = value;
            else if (key == "launch_args") LaunchArgs = value;
        }
        Cfg.Log("game.cfg: " + (Configured ? "game process " + Process : "no game process set"));
    }

    public static System.Diagnostics.Process[] Running() {
        return Configured ? System.Diagnostics.Process.GetProcessesByName(Process) : new System.Diagnostics.Process[0];
    }
}

// Which display is the TV. tv.cfg beside the exe (`monitor=<EDID code>[,<code>...]`) names it by
// the monitor's identity, so an extra or swapped display can never be taken for the TV. Without
// tv.cfg the TV is the non-primary display only when there are exactly two; anything else means
// "no TV", and the helper leaves every window where it is rather than guess.
static class TvDisplay {
    public static string[] Wanted = new string[0];
    static string lastLogged;

    public static void Load(string path) {
        if (!File.Exists(path)) { Cfg.Log("tv.cfg: none, TV = the other display when exactly two"); return; }
        foreach (var raw in File.ReadAllLines(path)) {
            var line = raw.Trim();
            int eq = line.IndexOf('=');
            if (line.StartsWith("#") || eq <= 0) continue;
            if (line.Substring(0, eq).Trim().ToLowerInvariant() == "monitor")
                Wanted = line.Substring(eq + 1).Split(',').Select(s => s.Trim()).Where(s => s.Length > 0).ToArray();
        }
        Cfg.Log("tv.cfg: TV monitor " + (Wanted.Length > 0 ? string.Join(",", Wanted) : "(none set)"));
    }

    // Pure choice: the index of the TV among displays (ids[i] = monitor interface path, primary[i]),
    // or -1. A wanted code matches a whole '#'-separated segment of the id, case-insensitively, and
    // a primary display is never the TV.
    public static int Pick(string[] ids, bool[] primary, string[] wanted) {
        if (wanted.Length > 0) {
            for (int i = 0; i < ids.Length; i++) {
                if (primary[i]) continue;
                if (Matches(ids[i], wanted)) return i;
            }
            return -1;
        }
        if (ids.Length != 2) return -1;
        return primary[0] == primary[1] ? -1 : (primary[0] ? 1 : 0);
    }

    // Whether a monitor interface path carries one of the wanted codes as a whole segment.
    public static bool Matches(string id, string[] wanted) {
        var segs = (id ?? "").Split('#', '\\');
        return wanted.Any(w => segs.Any(s => string.Equals(s, w, StringComparison.OrdinalIgnoreCase)));
    }

    // Every display as the log names it: output, monitor path, bounds, and which is primary.
    public static string List() {
        try {
            return string.Join(" | ", Screen.AllScreens.Select(x => x.DeviceName + " " + N.MonitorId(x.DeviceName) + " " + x.Bounds + (x.Primary ? " primary" : "")));
        } catch (Exception ex) { return "unreadable: " + ex.Message; }
    }

    public static Screen Find() {
        var all = Screen.AllScreens;
        var ids = all.Select(s => N.MonitorId(s.DeviceName)).ToArray();
        int i = Pick(ids, all.Select(s => s.Primary).ToArray(), Wanted);
        var desc = i < 0 ? "not found among " + string.Join(" | ", ids) : all[i].DeviceName + " " + ids[i] + " " + all[i].Bounds;
        if (desc != lastLogged) {
            lastLogged = desc;
            if (i < 0) Cfg.Warn("TV display: " + desc + " (nothing is moved to the TV)"); else Cfg.Log("TV display: " + desc);
        }
        return i < 0 ? null : all[i];
    }

    // `TvShell.exe pick-tv <wanted,codes|-> <id>[*] ...` (a trailing * marks the primary):
    // exits with Pick's index, or 255 for none. Lets the tests exercise Pick without displays.
    public static int Cli(string[] args) {
        if (args.Length < 2) return 254;
        var wanted = args[1] == "-" ? new string[0] : args[1].Split(',');
        var rest = args.Skip(2).ToArray();
        int r = Pick(rest.Select(a => a.TrimEnd('*')).ToArray(), rest.Select(a => a.EndsWith("*")).ToArray(), wanted);
        return r < 0 ? 255 : r;
    }
}

// The desk display is the primary one: the taskbar, the sign-in screen and every new window open
// on the primary display, and the TV cannot be seen from the desk. Windows keeps one layout per
// set of monitors, and the TV is a different monitor to it depending on whether it was on at
// boot (it then reports its own identity rather than its adapter's), so a layout with the TV as
// primary can come back on any boot. The helper then makes the other display primary, with the
// TV to its right - but only when nothing is guessed: tv.cfg names the TV, exactly two displays
// are connected, exactly one of them is the TV, and the TV is the primary one.
static class DeskPrimary {
    static readonly List<DateTime> switched = new List<DateTime>();
    static bool gaveUp;
    const int MaxSwitches = 3;          // within Window; more than that and something else is
    const int WindowMinutes = 10;       // setting it back, which the helper does not fight

    // Pure choice: the index of the display to make primary, or -1 to leave the displays alone.
    public static int Plan(string[] ids, bool[] primary, string[] wanted) {
        if (wanted.Length == 0 || ids.Length != 2) return -1;
        bool tv0 = TvDisplay.Matches(ids[0], wanted), tv1 = TvDisplay.Matches(ids[1], wanted);
        if (tv0 == tv1) return -1;
        int tv = tv0 ? 0 : 1;
        return primary[tv] && !primary[1 - tv] ? 1 - tv : -1;
    }

    static int PlanNow(out Screen[] all, out string[] ids) {
        all = Screen.AllScreens;
        ids = all.Select(s => N.MonitorId(s.DeviceName)).ToArray();
        return Plan(ids, all.Select(s => s.Primary).ToArray(), TvDisplay.Wanted);
    }

    // Makes the desk display primary when Plan says so. 0: nothing to do, 2: switched, 1: could not.
    public static int Ensure() {
        Screen[] all; string[] ids;
        int desk = PlanNow(out all, out ids);
        if (desk < 0) { gaveUp = false; return 0; }
        switched.RemoveAll(t => (DateTime.Now - t).TotalMinutes > WindowMinutes);
        if (switched.Count >= MaxSwitches) {
            if (!gaveUp) Cfg.Error("the TV is the primary display again after the helper made the desk primary " + MaxSwitches + " times in " + WindowMinutes + " minutes; something else keeps setting it back, so the displays are left alone: " + TvDisplay.List());
            gaveUp = true;
            return 1;
        }
        switched.Add(DateTime.Now);
        Cfg.Warn("the TV is the primary display (" + TvDisplay.List() + ") -> making " + all[desk].DeviceName + " primary");
        string problem = Apply(all[desk].DeviceName, all[1 - desk].DeviceName);
        if (problem != null) { Cfg.Error("could not make the desk display primary: " + problem); return 1; }
        Cfg.Log("the desk display " + all[desk].DeviceName + " is primary, the TV is to its right");
        return 2;
    }

    // The layout as Windows' display-configuration API holds it: the primary display is the one
    // at 0,0, so the desk goes there and the TV to its right, in one change. null when it took.
    //
    // Not ChangeDisplaySettingsEx: Windows can start the plasma in a mode (852x480) that is not in
    // its own mode list, and that API then answers "bad mode" (-2) to any change that names the
    // TV, even one that moves nothing. SetDisplayConfig is asked to keep every mode as it is first,
    // and only when that is refused to pick the nearest ones (SDC_ALLOW_CHANGES); a TV whose
    // resolution changed that way is a WARN.
    //
    // The two arrays are read as bytes at the documented offsets rather than as structs:
    // DISPLAYCONFIG_MODE_INFO is 64 bytes - infoType at 0 (1 = a source, i.e. a desktop area),
    // id at 4, adapterId at 8, then for a source: width 16, height 20, position x 28, y 32.
    const int PathSize = 72, ModeSize = 64;
    static string Apply(string desk, string tv) {
        uint np, nm; byte[] paths, modes;
        string problem = Query(out np, out paths, out nm, out modes);
        if (problem != null) return problem;
        int deskAt = SourceMode(modes, nm, desk), tvAt = SourceMode(modes, nm, tv);
        if (deskAt < 0 || tvAt < 0) return "the display configuration does not list " + (deskAt < 0 ? desk : tv);
        int deskWidth = (int)BitConverter.ToUInt32(modes, deskAt + 16);
        string tvSize = Size(modes, tvAt);
        BitConverter.GetBytes(0).CopyTo(modes, deskAt + 28); BitConverter.GetBytes(0).CopyTo(modes, deskAt + 32);
        BitConverter.GetBytes(deskWidth).CopyTo(modes, tvAt + 28); BitConverter.GetBytes(0).CopyTo(modes, tvAt + 32);
        const uint UseSupplied = 0x20, ApplyNow = 0x80, SaveToDatabase = 0x200, AllowChanges = 0x400;
        int exact = N.SetDisplayConfig(np, paths, nm, modes, UseSupplied | ApplyNow | SaveToDatabase);
        if (exact != 0) {
            int r = N.SetDisplayConfig(np, paths, nm, modes, UseSupplied | ApplyNow | SaveToDatabase | AllowChanges);
            if (r != 0) return "SetDisplayConfig answered " + exact + ", then " + r + " when allowed to adjust the modes (0 is success)";
        }
        // Windows saying yes is not the display being there: read it back.
        problem = Query(out np, out paths, out nm, out modes);
        if (problem != null) return "the change was accepted but cannot be read back: " + problem;
        deskAt = SourceMode(modes, nm, desk); tvAt = SourceMode(modes, nm, tv);
        if (deskAt < 0 || !AtOrigin(modes, deskAt)) return "Windows accepted the change but " + desk + " is not at 0,0";
        if (tvAt >= 0 && Size(modes, tvAt) != tvSize)
            Cfg.Warn("Windows could not keep the mode of " + tv + " (SetDisplayConfig answered " + exact + "): it was " + tvSize + " and is now " + Size(modes, tvAt));
        return null;
    }
    static string Size(byte[] modes, int at) { return BitConverter.ToUInt32(modes, at + 16) + "x" + BitConverter.ToUInt32(modes, at + 20); }
    static bool AtOrigin(byte[] modes, int at) { return BitConverter.ToInt32(modes, at + 28) == 0 && BitConverter.ToInt32(modes, at + 32) == 0; }
    // Whether a display output is the primary one right now, asked of Windows afresh.
    static bool IsPrimary(string gdiName) {
        uint np, nm; byte[] paths, modes;
        if (Query(out np, out paths, out nm, out modes) != null) return false;
        int at = SourceMode(modes, nm, gdiName);
        return at >= 0 && AtOrigin(modes, at);
    }

    // `TvShell.exe desk-primary drill`: the fault itself, on this PC's real displays. Makes the
    // TV the primary display, as a bad boot does, and gives the running helper 15 seconds to
    // undo it. 0: the helper did. 1: it did not (the drill then puts the desk back itself).
    // 3, 4, 5: nothing was drilled - the TV is already primary, tv.cfg names no TV among exactly
    // two displays, or Windows refused the fault. Both displays flicker; run it from the deploy.
    static int Drill() {
        Screen[] all; string[] ids;
        if (PlanNow(out all, out ids) >= 0) { Console.WriteLine("the TV is already the primary display: nothing to drill"); return 3; }
        int tv = TvDisplay.Wanted.Length > 0 && all.Length == 2 ? TvDisplay.Pick(ids, all.Select(s => s.Primary).ToArray(), TvDisplay.Wanted) : -1;
        if (tv < 0) { Console.WriteLine("tv.cfg names no TV among exactly two displays: nothing to drill"); return 4; }
        string desk = all[1 - tv].DeviceName, tvName = all[tv].DeviceName;
        Cfg.Log("drill: making the TV (" + tvName + ") the primary display; the running helper has 15 s to undo it");
        string problem = Apply(tvName, desk);
        if (problem != null) { Cfg.Warn("drill: could not make the TV primary: " + problem); Console.WriteLine("could not make the TV primary: " + problem); return 5; }
        var started = DateTime.Now;
        while ((DateTime.Now - started).TotalSeconds < 15) {
            Thread.Sleep(250);
            if (!IsPrimary(desk)) continue;
            var took = (DateTime.Now - started).TotalSeconds.ToString("0.0");
            Cfg.Log("drill: the helper made the desk primary again after " + took + " s");
            Console.WriteLine("the helper made the desk primary again after " + took + " s");
            return 0;
        }
        problem = Apply(desk, tvName);
        Cfg.Error("drill: the helper did not make the desk display primary within 15 s; the drill put it back itself" + (problem == null ? "" : ", which failed: " + problem));
        Console.WriteLine("the helper did not make the desk primary within 15 s");
        return 1;
    }

    static string Query(out uint np, out byte[] paths, out uint nm, out byte[] modes) {
        paths = modes = new byte[0]; np = nm = 0;
        const uint OnlyActivePaths = 2;
        int r = N.GetDisplayConfigBufferSizes(OnlyActivePaths, out np, out nm);
        if (r != 0) return "GetDisplayConfigBufferSizes answered " + r;
        paths = new byte[np * PathSize]; modes = new byte[nm * ModeSize];
        r = N.QueryDisplayConfig(OnlyActivePaths, ref np, paths, ref nm, modes, IntPtr.Zero);
        return r == 0 ? null : "QueryDisplayConfig answered " + r;
    }

    // The byte offset of the source mode whose desktop area is the display output `gdiName`
    // (\\.\DISPLAY1), or -1. DISPLAYCONFIG_SOURCE_DEVICE_NAME is 84 bytes: type (1) at 0, size
    // at 4, adapterId at 8, id at 16, then the name as 32 UTF-16 characters at 20.
    static int SourceMode(byte[] modes, uint count, string gdiName) {
        for (int i = 0; i < count; i++) {
            int o = i * ModeSize;
            if (BitConverter.ToUInt32(modes, o) != 1) continue;
            var packet = new byte[84];
            BitConverter.GetBytes(1u).CopyTo(packet, 0); BitConverter.GetBytes(84u).CopyTo(packet, 4);
            Array.Copy(modes, o + 8, packet, 8, 8); Array.Copy(modes, o + 4, packet, 16, 4);
            if (N.DisplayConfigGetDeviceInfo(packet) != 0) continue;
            var name = Encoding.Unicode.GetString(packet, 20, 64);
            int end = name.IndexOf('\0');
            if (string.Equals(end < 0 ? name : name.Substring(0, end), gdiName, StringComparison.OrdinalIgnoreCase)) return o;
        }
        return -1;
    }

    // `TvShell.exe plan-primary <wanted,codes|-> <id>[*] ...` (a trailing * marks the primary):
    // exits with Plan's index, or 255 for "leave them alone". Lets the tests exercise Plan
    // without displays.
    public static int PlanCli(string[] args) {
        if (args.Length < 2) return 254;
        var wanted = args[1] == "-" ? new string[0] : args[1].Split(',');
        var rest = args.Skip(2).ToArray();
        int r = Plan(rest.Select(a => a.TrimEnd('*')).ToArray(), rest.Select(a => a.EndsWith("*")).ToArray(), wanted);
        return r < 0 ? 255 : r;
    }

    // `TvShell.exe desk-primary check`: this PC's displays, read only. Prints them and exits 0
    // when the desk is primary (or there is nothing to decide), 3 when the TV is.
    // `TvShell.exe desk-primary`: Ensure's answer (0 nothing to do, 2 switched, 1 could not).
    // `TvShell.exe desk-primary drill`: see Drill. Anything else exits 254.
    public static int Cli(string[] args) {
        if (args.Length == 2 && args[1] == "drill") return Drill();
        if (args.Length > 2 || (args.Length == 2 && args[1] != "check")) return 254;
        Console.WriteLine(TvDisplay.List());
        if (args.Length == 2) { Screen[] all; string[] ids; return PlanNow(out all, out ids) >= 0 ? 3 : 0; }
        return Ensure();
    }
}

// The controller's buttons as keys, for the BackWhenTV app, which only understands a
// keyboard. The helper already reads the controller, so it presses the keys itself, and only
// while that app's window has focus; the app's Steam tile keeps Steam's default layout, which
// sends no keys, so no press lands twice.
static class PadKeys {
    public const int Up = 1, Down = 2, Left = 4, Right = 8, A = 16, B = 32, Minus = 64;

    // The logical buttons held in a Switch Pro report, or -1 for a report it does not know.
    // Labels are Nintendo's: A is the right face button, B the bottom one.
    public static int Buttons(byte[] rep) {
        int b = 0;
        if (rep.Length >= 6 && rep[0] == 0x30) {   // full report: right, shared, left button bytes
            if ((rep[3] & 0x08) != 0) b |= A;
            if ((rep[3] & 0x04) != 0) b |= B;
            if ((rep[4] & 0x01) != 0) b |= Minus;
            if ((rep[5] & 0x02) != 0) b |= Up;
            if ((rep[5] & 0x01) != 0) b |= Down;
            if ((rep[5] & 0x08) != 0) b |= Left;
            if ((rep[5] & 0x04) != 0) b |= Right;
            return b;
        }
        if (rep.Length >= 4 && rep[0] == 0x3F) {   // simple report: face buttons, shared, hat 0-7 (8 = centred)
            if ((rep[1] & 0x02) != 0) b |= A;
            if ((rep[1] & 0x01) != 0) b |= B;
            if ((rep[2] & 0x01) != 0) b |= Minus;
            int hat = rep[3];
            if (hat == 7 || hat == 0 || hat == 1) b |= Up;
            if (hat == 1 || hat == 2 || hat == 3) b |= Right;
            if (hat == 3 || hat == 4 || hat == 5) b |= Down;
            if (hat == 5 || hat == 6 || hat == 7) b |= Left;
            return b;
        }
        return -1;
    }

    // The key presses for newly pressed buttons, each a virtual-key list pressed together:
    // the D-pad is the arrows, A is Enter, B is Esc, and Minus closes the app (Alt+F4).
    public static List<byte[]> Keys(int pressed) {
        var keys = new List<byte[]>();
        if ((pressed & Up) != 0) keys.Add(new byte[] { 0x26 });
        if ((pressed & Down) != 0) keys.Add(new byte[] { 0x28 });
        if ((pressed & Left) != 0) keys.Add(new byte[] { 0x25 });
        if ((pressed & Right) != 0) keys.Add(new byte[] { 0x27 });
        if ((pressed & A) != 0) keys.Add(new byte[] { 0x0D });
        if ((pressed & B) != 0) keys.Add(new byte[] { 0x1B });
        if ((pressed & Minus) != 0) keys.Add(new byte[] { 0x12, 0x73 });
        return keys;
    }

    public static void Press(byte[] combo) {
        foreach (var vk in combo) N.keybd_event(vk, 0, 0, UIntPtr.Zero);
        for (int i = combo.Length - 1; i >= 0; i--) N.keybd_event(combo[i], 0, 2 /*KEYEVENTF_KEYUP*/, UIntPtr.Zero);
    }

    // `TvShell.exe pad-keys <hex report>`: prints Buttons and the keys for them (as if all were
    // newly pressed), and exits 0, or 2 for a report it does not know. Lets the tests run without a pad.
    public static int Cli(string[] args) {
        if (args.Length < 2) return 254;
        var hex = args[1];
        var rep = new byte[hex.Length / 2];
        for (int i = 0; i < rep.Length; i++) rep[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
        int b = Buttons(rep);
        if (b < 0) { Console.WriteLine("unknown"); return 2; }
        Console.WriteLine(b + " " + string.Join(" ", Keys(b).Select(c => string.Join("+", c.Select(v => v.ToString("X2"))))));
        return 0;
    }
}

// The keyboard belongs to the desk display. A physical key pressed while focus is on the TV
// sends focus back to the desk, and the key itself is held back so it never reaches Kodi,
// YouTube or the game. Classify decides that per key, apart from where focus is.
static class DeskKeys {
    public const int Ignore = 0;   // not the person at the desk, or global: leave it and focus alone
    public const int Pass = 1;     // go back to the desk, and let the key through (a modifier)
    public const int Hold = 2;     // go back to the desk, and drop the key (it would type on the TV)

    public static int Classify(uint vk, uint flags) {
        // Injected keys come from software, notably Steam Input turning the controller into
        // keys for the app on the TV. Only a real keyboard means "the person is at the desk".
        if ((flags & 0x10 /*LLKHF_INJECTED*/) != 0 || (flags & 0x02 /*LLKHF_LOWER_IL_INJECTED*/) != 0) return Ignore;
        // Browser, volume and media keys act system-wide; holding one back would break the
        // volume keys while watching TV.
        if (vk >= 0xA6 && vk <= 0xB7) return Ignore;
        // Shift, Ctrl, Alt, Win, PrintScreen: dropping one would break the shortcut it starts.
        if ((vk >= 0x10 && vk <= 0x12) || (vk >= 0xA0 && vk <= 0xA5) || vk == 0x5B || vk == 0x5C || vk == 0x2C) return Pass;
        return Hold;
    }

    // `TvShell.exe desk-key <vk> <flags>` (decimal): exits with Classify's answer, 254 on bad
    // arguments. Lets the tests exercise Classify without a keyboard.
    public static int Cli(string[] args) {
        uint vk, flags;
        if (args.Length != 3 || !uint.TryParse(args[1], out vk) || !uint.TryParse(args[2], out flags)) return 254;
        return Classify(vk, flags);
    }
}

// Whether a Switch Pro controller report is a fresh press. Steam switches the controller between
// full reports (0x30) and simple ones (0x3F) as focus moves, and the two lay buttons out
// differently, so a report is only ever compared with the previous one of the same kind: a mode
// switch is a new baseline, not a press. Without that, every focus change Steam answered with a
// mode switch read as a press, and focus bounced between the desk and the TV.
// Steam's window in front of the game or TV app with nothing on it. Closing Steam's menu, or
// a second tile ending, leaves Big Picture focused and black over what was running; its
// "Resume" does not bring that back, and the controller is never taken from a focused Steam
// window (that would fight the menu while it is in use). So the screen is read: a Steam
// window that has shown nothing but black for a couple of seconds is not a menu in use.
static class BlankSteam {
    const int Cols = 32, Rows = 18, Black = 8;
    public const double Seconds = 2;

    // Pure: give the screen back to what is running on the TV?
    public static bool Plan(bool running, bool steamInFront, bool dark, double seconds) {
        return running && steamInFront && dark && seconds >= Seconds;
    }
    // Pure: every sampled pixel (0xRRGGBB) is black. No sample is not black: nothing was read.
    public static bool Dark(int[] rgb) {
        if (rgb.Length == 0) return false;
        foreach (int c in rgb)
            if (((c >> 16) & 255) > Black || ((c >> 8) & 255) > Black || (c & 255) > Black) return false;
        return true;
    }
    // A grid of pixels from the TV, without the bottom strip: Steam keeps its button hints there
    // on the black screen too.
    public static int[] Sample(Rectangle tv) {
        int w = tv.Width, h = tv.Height - tv.Height / 8;
        var px = new List<int>();
        try {
            using (var b = new Bitmap(w, h)) {
                using (var g = Graphics.FromImage(b)) g.CopyFromScreen(tv.X, tv.Y, 0, 0, b.Size);
                for (int y = 0; y < Rows; y++)
                    for (int x = 0; x < Cols; x++)
                        px.Add(b.GetPixel((2 * x + 1) * w / (2 * Cols), (2 * y + 1) * h / (2 * Rows)).ToArgb() & 0xFFFFFF);
            }
        } catch (Exception) { return new int[0]; }   // a locked or secure desktop cannot be read
        return px.ToArray();
    }
    // blank-steam <running 0|1> <steam in front 0|1> <seconds> <rrggbb>... -> 1 give it back, 0 leave it
    // blank-steam sample <x> <y> <w> <h> -> 0 with every pixel of that screen area black, 3 with
    // something on it, 4 when the screen could not be read
    public static int Cli(string[] args) {
        if (args.Length == 6 && args[1] == "sample") {
            int[] r;
            try { r = args.Skip(2).Select(int.Parse).ToArray(); } catch (Exception) { return 254; }
            var px = Sample(new Rectangle(r[0], r[1], r[2], r[3]));
            return px.Length == 0 ? 4 : Dark(px) ? 0 : 3;
        }
        if (args.Length < 4) return 254;
        double seconds;
        if (!double.TryParse(args[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out seconds)) return 254;
        int[] rgb;
        try { rgb = args.Skip(4).Select(a => Convert.ToInt32(a, 16)).ToArray(); } catch (Exception) { return 254; }
        return Plan(args[1] == "1", args[2] == "1", Dark(rgb), seconds) ? 1 : 0;
    }
}

// Focus on the TV but on a window another of ours covers (Kodi behind the game): every press
// goes to a window nobody can see. Hand focus to the one on top. Only between the game and the
// TV apps: never away from or onto Steam's windows, whose menu the Home button opens over them.
static class Uncovered {
    // Pure: should a press move focus from `fg` to `top`, the window showing on the TV?
    public static bool Plan(IntPtr top, IntPtr fg, IntPtr game, IntPtr app) {
        if (top == IntPtr.Zero || top == fg) return false;
        return (top == game || top == app) && (fg == game || fg == app);
    }
    // `TvShell.exe uncovered <top> <focused> <game> <app>`: exits 1 to focus the top window, 0 to
    // leave focus alone, 254 on bad arguments (0 is no window).
    public static int Cli(string[] args) {
        long[] w;
        try { w = args.Skip(1).Select(long.Parse).ToArray(); } catch (Exception) { return 254; }
        if (w.Length != 4) return 254;
        return Plan(new IntPtr(w[0]), new IntPtr(w[1]), new IntPtr(w[2]), new IntPtr(w[3])) ? 1 : 0;
    }
}

// What a controller press does to focus. The helper only ever takes focus from the desk display:
// a press with focus on the TV is the TV's own (at most Uncovered), and a press while no window
// has focus is one that lands mid-switch. Steam's overlay switching from the game to a TV app is
// such a switch: no window is focused for a moment, and taking focus for the game then left the
// game on top and Steam's menu in the TV app behind it, driven by the controller and out of sight.
// No window at all has an empty rectangle at (0,0), which is on the desk: hence `hasForeground`.
static class PadFocus {
    public enum Act { Leave = 0, Uncover = 1, TakeToTv = 2 }
    // Pure. `onTv` / `onDesk`: the focused window's centre is on that display.
    public static Act Plan(bool hasForeground, bool steamInFront, bool onTv, bool onDesk) {
        if (!hasForeground) return Act.Leave;
        if (steamInFront && !onDesk) return Act.Leave;
        if (onTv) return Act.Uncover;
        return onDesk ? Act.TakeToTv : Act.Leave;   // off both displays: not the desk's to give
    }
    // `TvShell.exe pad-focus <has foreground> <steam in front> <on tv> <on desk>`, each 0 or 1:
    // exits with the Act, 254 on bad arguments.
    public static int Cli(string[] args) {
        if (args.Length != 5) return 254;
        var b = new bool[4];
        for (int i = 0; i < 4; i++) {
            if (args[i + 1] == "1") b[i] = true;
            else if (args[i + 1] != "0") return 254;
        }
        return (int)Plan(b[0], b[1], b[2], b[3]);
    }

    // Which window a press that takes focus from the desk gives it to. The TV app when it is the
    // one last used on the TV: focus wanders to the desk while a TV app plays (the game's store
    // launcher pops up there, the taskbar takes it), and handing the next press to the game put
    // the game over Kodi, whose covered-video guard then stopped the channel being watched.
    // Otherwise the game when it is on the TV, then the TV app; a game on the desk keeps focus
    // where it is, and with neither, Big Picture.
    public enum Target { None = 0, Game = 1, App = 2, BigPicture = 3 }
    public static Target Pick(bool game, bool gameOnTv, bool app, bool appUsedLast) {
        if (app && appUsedLast) return Target.App;
        if (game && gameOnTv) return Target.Game;
        if (app) return Target.App;
        return game ? Target.None : Target.BigPicture;
    }
    // `TvShell.exe pad-target <game> <game on tv> <app> <app used last>`, each 0 or 1: exits with
    // the Target, 254 on bad arguments.
    public static int TargetCli(string[] args) {
        if (args.Length != 5) return 254;
        var b = new bool[4];
        for (int i = 0; i < 4; i++) {
            if (args[i + 1] == "1") b[i] = true;
            else if (args[i + 1] != "0") return 254;
        }
        return (int)Pick(b[0], b[1], b[2], b[3]);
    }

    // Pure: after focus moves to a window, was a TV app (not the game) the last of the two used?
    // Any other window (the desk, Steam, the store launcher) leaves the answer as it was.
    public static bool UsedLast(bool isGame, bool isApp, bool before) {
        if (isGame) return false;
        return isApp || before;
    }
    // `TvShell.exe pad-used-last <is game> <is app> <before>`, each 0 or 1: exits 1 when a TV app
    // was used last, 0 when not, 254 on bad arguments.
    public static int UsedLastCli(string[] args) {
        if (args.Length != 4) return 254;
        var b = new bool[3];
        for (int i = 0; i < 3; i++) {
            if (args[i + 1] == "1") b[i] = true;
            else if (args[i + 1] != "0") return 254;
        }
        return UsedLast(b[0], b[1], b[2]) ? 1 : 0;
    }
}

static class Pad {
    // Report id plus three button bytes, Home and Capture masked off (Home opens Steam's menu), or
    // null for a report that carries no buttons this reads.
    public static byte[] Buttons(byte[] rep) {
        byte[] b = null;
        if (rep.Length >= 12 && rep[0] == 0x30) b = new[] { rep[0], rep[3], rep[4], rep[5] };      // full: right, shared, left
        else if (rep.Length >= 4 && rep[0] == 0x3F) b = new[] { rep[0], rep[1], rep[2], rep[3] };  // simple: buttons, buttons, hat
        if (b != null) b[2] &= unchecked((byte)~0x30);
        return b;
    }

    // `prev` is the last report that had buttons (null before the first).
    public static bool Pressed(byte[] prev, byte[] rep) {
        var cur = Buttons(rep);
        if (cur == null) return false;
        // A deliberate stick push counts (12-bit sticks, centre ~2048), far beyond drift.
        if (cur[0] == 0x30) {
            int lx = rep[6] | ((rep[7] & 0xF) << 8), ly = (rep[7] >> 4) | (rep[8] << 4);
            int rx = rep[9] | ((rep[10] & 0xF) << 8), ry = (rep[10] >> 4) | (rep[11] << 4);
            if (Math.Abs(lx - 2048) > 900 || Math.Abs(ly - 2048) > 900 || Math.Abs(rx - 2048) > 900 || Math.Abs(ry - 2048) > 900) return true;
        }
        var old = prev == null ? null : Buttons(prev);
        if (old == null || old[0] != cur[0]) return false;
        if (cur[0] == 0x3F) {
            // byte 3 is the d-pad as a direction (0-7), 8 when centred: not a bit field.
            if ((cur[1] & ~old[1]) != 0 || (cur[2] & ~old[2]) != 0) return true;
            return cur[3] != old[3] && cur[3] != 8;
        }
        for (int i = 1; i < 4; i++) if ((cur[i] & ~old[i]) != 0) return true;
        return false;
    }

    // `TvShell.exe pad-press <prev hex|-> <report hex>`: exits 1 for a press, 0 for none, 254 on
    // bad arguments. Lets the tests exercise Pressed without a controller.
    public static int Cli(string[] args) {
        if (args.Length != 3) return 254;
        byte[] prev = null, rep;
        if (args[1] != "-" && (prev = Hex(args[1])) == null) return 254;
        if ((rep = Hex(args[2])) == null || rep.Length == 0) return 254;
        return Pressed(prev, rep) ? 1 : 0;
    }

    static byte[] Hex(string s) {
        if (s.Length % 2 != 0) return null;
        var b = new byte[s.Length / 2];
        for (int i = 0; i < b.Length; i++)
            if (!byte.TryParse(s.Substring(i * 2, 2), System.Globalization.NumberStyles.HexNumber, null, out b[i])) return null;
        return b;
    }
}

// Which windows may sit on the TV. Windows moves open windows around when the displays come back
// in a different layout, and an app (a browser, say) then saves that position and reopens on the
// TV, where the mouse - confined to the desk - cannot reach it. So an ordinary desk window found
// on the TV goes back to the desk. Only what the TV runs stays: Kodi, Steam (Big Picture and its
// menus), the YouTube browser, and the game and its store launcher.
static class TvSeat {
    static readonly string[] TvProcesses = { "kodi", "steam", "steamwebhelper", "gameoverlayui", "gameoverlayui64" };

    // `process` is the process name without ".exe"; `youTube` says the window is the YouTube
    // browser's, which is an ordinary chrome process told apart only by its profile.
    public static bool BelongsOnTv(string process, bool youTube, string game, string gameLauncher) {
        if (youTube) return true;
        var p = process ?? "";
        if (TvProcesses.Any(t => string.Equals(t, p, StringComparison.OrdinalIgnoreCase))) return true;
        return (game.Length > 0 && string.Equals(p, game, StringComparison.OrdinalIgnoreCase))
            || (gameLauncher.Length > 0 && string.Equals(p, gameLauncher, StringComparison.OrdinalIgnoreCase));
    }

    // The YouTube browser: chrome's main process (no --type= switch) on the TvShell profile. A
    // recorded PID alone is not enough: if the launcher is killed its PID file stays, and Windows
    // may give that PID to the person's own Chrome.
    public static bool IsYouTubeBrowser(string commandLine, string profile) {
        var cmd = commandLine ?? "";
        return profile.Length > 0 && cmd.IndexOf(profile, StringComparison.OrdinalIgnoreCase) >= 0
            && cmd.IndexOf("--type=", StringComparison.Ordinal) < 0;
    }

    // Where a window found on the TV goes on the desk: the same offset from the top-left corner,
    // its size kept but never larger than the desk, and wholly on the desk.
    public static Rectangle DeskRect(Rectangle win, Rectangle tv, Rectangle desk) {
        int w = Math.Min(win.Width, desk.Width), h = Math.Min(win.Height, desk.Height);
        int x = desk.X + Math.Max(0, Math.Min(win.X - tv.X, desk.Width - w));
        int y = desk.Y + Math.Max(0, Math.Min(win.Y - tv.Y, desk.Height - h));
        return new Rectangle(x, y, w, h);
    }

    // `TvShell.exe tv-seat belongs <process> <youtube 0|1> <game|-> <launcher|->`: exits 1 when
    // the window stays on the TV, 0 when it goes to the desk.
    // `TvShell.exe tv-seat youtube <command line> <profile>`: exits 1 for the YouTube browser.
    // `TvShell.exe tv-seat desk-rect <x,y,w,h win> <tv> <desk>`: prints x,y,w,h, exits 0.
    // All exit 254 on bad arguments. Lets the tests exercise these without windows or displays.
    public static int Cli(string[] args) {
        if (args.Length == 6 && args[1] == "belongs" && (args[3] == "0" || args[3] == "1"))
            return BelongsOnTv(args[2], args[3] == "1", args[4] == "-" ? "" : args[4], args[5] == "-" ? "" : args[5]) ? 1 : 0;
        if (args.Length == 4 && args[1] == "youtube") return IsYouTubeBrowser(args[2], args[3]) ? 1 : 0;
        if (args.Length == 5 && args[1] == "desk-rect") {
            Rectangle win, tv, desk;
            if (!ParseRect(args[2], out win) || !ParseRect(args[3], out tv) || !ParseRect(args[4], out desk)) return 254;
            var r = DeskRect(win, tv, desk);
            Console.WriteLine(r.X + "," + r.Y + "," + r.Width + "," + r.Height);
            return 0;
        }
        return 254;
    }
    static bool ParseRect(string s, out Rectangle r) {
        r = Rectangle.Empty;
        var p = s.Split(',');
        int x, y, w, h;
        if (p.Length != 4 || !int.TryParse(p[0], out x) || !int.TryParse(p[1], out y) || !int.TryParse(p[2], out w) || !int.TryParse(p[3], out h)) return false;
        r = new Rectangle(x, y, w, h);
        return true;
    }
}

static class N {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out int v, int size);
    public static bool IsCloaked(IntPtr h) { int c; return DwmGetWindowAttribute(h, 14 /*DWMWA_CLOAKED*/, out c, 4) == 0 && c != 0; }
    public static string ClassOf(IntPtr h) { var sb = new StringBuilder(256); GetClassName(h, sb, 256); return sb.ToString(); }
    [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr h, int id, uint mod, uint vk);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr h, int id);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
    public static Rectangle ClientScreenRect(IntPtr h) {
        RECT c; GetClientRect(h, out c); var p = new POINT();
        ClientToScreen(h, ref p);
        return new Rectangle(p.X, p.Y, c.R - c.L, c.B - c.T);
    }
    [DllImport("user32.dll")] public static extern bool ClipCursor(ref RECT r);
    [DllImport("user32.dll")] public static extern bool GetClipCursor(out RECT r);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr p);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll", SetLastError = true)] public static extern bool RegisterRawInputDevices(RAWINPUTDEVICE[] d, uint n, uint size);
    public delegate void WinEventProc(IntPtr hook, uint ev, IntPtr h, int obj, int child, uint thread, uint time);
    [DllImport("user32.dll")] public static extern IntPtr SetWinEventHook(uint min, uint max, IntPtr mod, WinEventProc fn, uint pid, uint thread, uint flags);
    [DllImport("user32.dll")] public static extern bool UnhookWinEvent(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetRawInputData(IntPtr h, uint cmd, IntPtr data, ref uint size, uint hdr);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern bool EnumDisplayDevices(string dev, uint i, ref DISPLAY_DEVICE dd, uint flags);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct DISPLAY_DEVICE {
        public int cb;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceString;
        public int StateFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceID;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceKey;
    }
    // The monitor's device interface path on a display output, e.g. \\?\DISPLAY#ABC1234#5&...#{...};
    // the second segment is the EDID's manufacturer+product code. "" when nothing is attached.
    public static string MonitorId(string output) {
        var dd = new DISPLAY_DEVICE(); dd.cb = Marshal.SizeOf(typeof(DISPLAY_DEVICE));
        return EnumDisplayDevices(output, 0, ref dd, 1 /*EDD_GET_DEVICE_INTERFACE_NAME*/) ? (dd.DeviceID ?? "") : "";
    }
    // Windows' display-configuration API; DeskPrimary reads the arrays as bytes.
    [DllImport("user32.dll")] public static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
    [DllImport("user32.dll")] public static extern int QueryDisplayConfig(uint flags, ref uint nPaths, byte[] paths, ref uint nModes, byte[] modes, IntPtr topology);
    [DllImport("user32.dll")] public static extern int SetDisplayConfig(uint nPaths, byte[] paths, uint nModes, byte[] modes, uint flags);
    [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(byte[] packet);
    [StructLayout(LayoutKind.Sequential)] public struct RAWINPUTDEVICE { public ushort UsagePage, Usage; public uint Flags; public IntPtr Target; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }

    public delegate IntPtr HookProc(int code, IntPtr w, IntPtr l);
    [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetWindowsHookEx(int id, HookProc fn, IntPtr mod, uint thread);
    [DllImport("user32.dll")] public static extern bool UnhookWindowsHookEx(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr h, int code, IntPtr w, IntPtr l);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetShellWindow();
    [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr h, bool altTab);
    [StructLayout(LayoutKind.Sequential)] public struct KBDLLHOOKSTRUCT { public uint Vk, Scan, Flags, Time; public IntPtr Extra; }

    // Give focus to a window even though we are a background process. True when it took:
    // Windows can refuse a background process, and a silent refusal leaves the controller
    // (or the keyboard) talking to the other screen.
    public static bool Focus(IntPtr h) {
        var fg = GetForegroundWindow();
        if (fg == h) return true;
        uint fgThread = GetWindowThreadProcessId(fg, IntPtr.Zero), me = GetCurrentThreadId();
        bool att = fgThread != 0 && fgThread != me && AttachThreadInput(me, fgThread, true);
        BringWindowToTop(h); SetForegroundWindow(h);
        if (att) AttachThreadInput(me, fgThread, false);
        if (Landed(h, GetForegroundWindow)) return true;
        SwitchToThisWindow(h, true);
        return Landed(h, GetForegroundWindow);
    }
    // A focus change completes asynchronously: mid-switch the foreground is no window at all, and
    // the target gets it some ms later. Reading it at once logged "refused (focus stays with no
    // window)" for switches that then succeeded, so wait while it is in flight (at most 300 ms).
    static bool Landed(IntPtr h, Func<IntPtr> foreground) {
        for (int i = 0; i < 15; i++) {
            var fg = foreground();
            if (fg == h) return true;
            if (fg != IntPtr.Zero && i >= 2) return false;   // settled on another window
            System.Threading.Thread.Sleep(20);
        }
        return foreground() == h;
    }
    // `TvShell.exe focus-landed <target> <foreground>...`: Landed against a scripted foreground, one
    // value per reading, the last one repeating (0 is no window). Exits 1 landed, 0 not, 254 on bad
    // arguments. Lets the tests exercise it without moving the focus.
    public static int LandedCli(string[] args) {
        long[] w;
        try { w = args.Skip(1).Select(long.Parse).ToArray(); } catch (Exception) { return 254; }
        if (w.Length < 2) return 254;
        int next = 1;
        return Landed(new IntPtr(w[0]), () => new IntPtr(w[Math.Min(next++, w.Length - 1)])) ? 1 : 0;
    }
    public const uint SWP_NOZORDER = 0x4, SWP_NOACTIVATE = 0x10, SWP_FRAMECHANGED = 0x20;

    public static List<IntPtr> WindowsOf(Func<uint, bool> pidOk, Func<string, bool> titleOk) {
        var res = new List<IntPtr>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (!pidOk(pid)) return true;
            var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
            if (titleOk(sb.ToString())) res.Add(h);
            return true;
        }, IntPtr.Zero);
        return res;
    }
    public static Rectangle Rect(IntPtr h) { RECT r; GetWindowRect(h, out r); return Rectangle.FromLTRB(r.L, r.T, r.R, r.B); }
    public static void Place(IntPtr h, Rectangle b, bool activate) {
        if (IsIconic(h)) ShowWindow(h, 9 /*SW_RESTORE*/);
        SetWindowPos(h, IntPtr.Zero, b.X, b.Y, b.Width, b.Height, SWP_NOZORDER | SWP_FRAMECHANGED | (activate ? 0 : SWP_NOACTIVATE));
        if (activate) SetForegroundWindow(h);
    }
}

static class Program {
    [STAThread]
    static int Main(string[] args) {
        N.SetProcessDpiAwarenessContext(new IntPtr(-4)); // per-monitor v2: real pixel coordinates
        string mode = args.Length > 0 ? args[0].ToLowerInvariant() : "";
        Cfg.Role = mode == "" ? "helper" : mode;
        AppDomain.CurrentDomain.UnhandledException += (s, e) => Cfg.Error("unhandled exception, exiting: " + e.ExceptionObject);
        try {
            var gid = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "steam-gameid.txt");
            if (File.Exists(gid)) Cfg.SteamGameId = File.ReadAllText(gid).Trim();
        } catch (Exception ex) { Cfg.Error("cannot read steam-gameid.txt: " + ex.Message); }
        Game.Load(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "game.cfg"));
        if (mode == "pick-tv") return TvDisplay.Cli(args);
        if (mode == "plan-primary") return DeskPrimary.PlanCli(args);
        if (mode == "blank-steam") return BlankSteam.Cli(args);
        if (mode == "pad-keys") return PadKeys.Cli(args);
        if (mode == "desk-key") return DeskKeys.Cli(args);
        if (mode == "close-store-launcher") return Launcher.CloseStoreLauncherCli();
        if (mode == "stop-kodi-video") return KodiLauncher.StopVideoCli(args);
        if (mode == "tile-launcher") return Launcher.TileLauncherCli(args);
        if (mode == "focus-landed") return N.LandedCli(args);
        if (mode == "uncovered") return Uncovered.Cli(args);
        if (mode == "app-exit") return AppExit.Cli(args);
        if (mode == "wait-out-kodi") return KodiLauncher.WaitOutCli(args);
        if (mode == "pad-press") return Pad.Cli(args);
        if (mode == "pad-focus") return PadFocus.Cli(args);
        if (mode == "pad-target") return PadFocus.TargetCli(args);
        if (mode == "pad-used-last") return PadFocus.UsedLastCli(args);
        if (mode == "tv-seat") return TvSeat.Cli(args);
        TvDisplay.Load(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "tv.cfg"));
        if (mode == "desk-primary") return DeskPrimary.Cli(args);
        if (mode == "launch") return Launcher.Run();
        if (mode == "kodi") return KodiLauncher.Run(args.Length > 1 ? args[1].ToLowerInvariant() : "tv");
        if (mode == "backwhen-app") return BackWhenApp.Run();
        var webApp = WebApp.ForMode(mode);
        if (webApp != null) return WebAppLauncher.Run(webApp);
        if (mode == "launch-desk") return Launcher.ViaSteam("desk");
        if (mode == "launch-tv") return Launcher.ViaSteam("tv");
        // Only no argument at all is the helper: a mistyped or not-yet-existing mode must not
        // start a second tray helper from wherever that exe happens to be.
        if (mode != "") { Cfg.Warn("unknown mode '" + mode + "', nothing started"); return 253; }
        bool created;
        using (var mtx = new Mutex(true, @"Local\TvShellHelper", out created)) {
            if (!created) { Cfg.Log("helper already running, this one exits"); return 0; }
            Application.EnableVisualStyles();
            // An exception on the UI thread is logged and the helper keeps running.
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += (s, e) => Cfg.Error("UI thread exception: " + e.Exception);
            Application.Run(new Helper());
            Cfg.Log("helper exited");
        }
        return 0;
    }
}

static class Launcher {
    // Desktop shortcut path: leave a note for the Steam-launched instance, then ask Steam to run it.
    public static int ViaSteam(string target) {
        Directory.CreateDirectory(Cfg.Dir);
        File.WriteAllText(Cfg.NextFile, target);
        if (Cfg.SteamGameId == "") { Cfg.Error("launch-" + target + ": no steam-gameid.txt, the desktop shortcut cannot start the game"); return 1; }
        Cfg.Log("launch-" + target + ": asking Steam to run tile " + Cfg.SteamGameId);
        Process.Start(new ProcessStartInfo("steam://rungameid/" + Cfg.SteamGameId) { UseShellExecute = true });
        return 0;
    }

    // Steam shortcut target. Steam hooks this process and follows it into the store launcher and
    // from there into the game, which is what gives the game Steam's virtual Xbox controller.
    public static int Run() {
        if (!Game.Configured || !Game.HasLauncher) { Cfg.Error("launch: game.cfg needs process and launcher"); return 1; }
        Directory.CreateDirectory(Cfg.Dir);
        string target = "tv";
        try {
            var fi = new FileInfo(Cfg.NextFile);
            if (fi.Exists) {
                if ((DateTime.Now - fi.LastWriteTime).TotalSeconds < 60) target = File.ReadAllText(Cfg.NextFile).Trim();
                fi.Delete();
            }
        } catch { }
        File.WriteAllText(Cfg.ModeFile, target);
        Cfg.Log("launch: target=" + target);

        if (Game.Running().Length > 0) {
            Cfg.Log("launch: game already running, only moving it");
            // Steam Input follows only the process tree of the tile it launched. A game started
            // at the desk has no tile launcher waiting on it, so the controller never reaches it.
            if (!TileLauncherRunning())
                Cfg.Warn("launch: the game was started outside its Steam tile, so the controller will not reach it -> quit it and start it from the tile");
            return 0;
        }
        // The store launcher must be (re)started from here, otherwise the game is spawned by an
        // instance Steam is not watching and the controller won't work.
        CloseStoreLauncher("closing running store launcher");
        StartLauncher();

        // Wait for the game to appear (the store launcher may need to log in or update), then
        // for it to exit, so Steam shows the game as running for the whole session. A
        // cold-started store launcher can ignore its launch arguments until it is ready, so
        // keep re-sending them (the running instance handles the request).
        Process game = null;
        for (int i = 0; i < 600 && game == null; i++) {   // up to 5 minutes
            var ps = Game.Running();
            if (ps.Length > 0) { game = ps[0]; break; }
            if (i >= 16 && i % 16 == 0) { Cfg.Log("launch: re-sending launch request"); StartLauncher(); }
            Thread.Sleep(500);
        }
        if (game == null) { Cfg.Warn("launch: game never started within 5 minutes"); return 2; }
        bool held = Cfg.Hold(game);
        Cfg.Log("launch: game pid " + game.Id + " started" + (held ? "" : " (exit code will be unknown: no handle)"));
        try { game.WaitForExit(); } catch (Exception ex) { Cfg.Warn("launch: cannot wait for the game: " + ex.Message); }
        // The game is not this project's code, so its crash is worth knowing, not a TvShell defect.
        var gameExit = Cfg.ExitOf(game);
        if (held && game.HasExited && Cfg.IsCrash(game.ExitCode)) Cfg.Warn("launch: game exited (" + gameExit + ")");
        else Cfg.Log("launch: game exited (" + gameExit + ")");
        // Steam counts the tile as running while anything it started is alive, and the store
        // launcher stays open after the game: left running, the tile is stuck on "Resume".
        if (!CloseStoreLauncher("closing store launcher so the tile ends"))
            Cfg.Error("launch: store launcher still running after 10 s, the tile will show Resume");
        return 0;
    }

    // Kills every process named launcher_process in game.cfg and waits up to 10 s for them to
    // go, killing any that starts meanwhile (the store launcher respawns its parts, and one
    // born after the first listing used to outlive it). True when none is left (or none is
    // configured).
    static bool CloseStoreLauncher(string why) {
        if (Game.LauncherProcess.Length == 0) return true;
        var running = Process.GetProcessesByName(Game.LauncherProcess);
        if (running.Length == 0) return true;
        Cfg.Log("launch: " + why + " (" + running.Length + " procs)");
        var killed = new HashSet<int>();
        for (int i = 0; i < 50; i++) {
            foreach (var p in running) {
                if (!killed.Add(p.Id)) continue;   // killed already, still on its way out
                if (i > 0) Cfg.Log("launch: store launcher pid " + p.Id + " started meanwhile, closing it too");
                try { p.Kill(); } catch (Exception ex) { Cfg.Warn("launch: cannot kill store launcher pid " + p.Id + ": " + ex.Message); }
            }
            Thread.Sleep(200);
            running = Process.GetProcessesByName(Game.LauncherProcess);
            if (running.Length == 0) return true;
        }
        Cfg.Warn("launch: store launcher left running: " + string.Join(", ", running.Select(p => "pid " + p.Id + (killed.Contains(p.Id) ? " (killed, not gone)" : " (just started)"))));
        return false;
    }

    // Another `TvShell.exe launch` still running: the one a tile started, waiting on the game.
    static bool TileLauncherRunning() {
        int me = Process.GetCurrentProcess().Id;
        try {
            using (var q = new System.Management.ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='TvShell.exe'"))
                foreach (System.Management.ManagementObject o in q.Get())
                    if (Convert.ToInt32(o["ProcessId"]) != me && IsGameLaunch(o["CommandLine"] as string)) return true;
        } catch (Exception ex) { Cfg.Warn("launch: cannot list TvShell processes: " + ex.Message); return true; }
        return false;
    }
    static bool IsGameLaunch(string cmd) {
        if (cmd == null) return false;
        var parts = cmd.Replace("\"", " ").Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
        for (int i = 0; i + 1 < parts.Length; i++)
            if (parts[i].EndsWith("TvShell.exe", StringComparison.OrdinalIgnoreCase)) return parts[i + 1].Equals("launch", StringComparison.OrdinalIgnoreCase);
        return false;
    }

    // `TvShell.exe tile-launcher`: TileLauncherRunning on this PC, exits 1 when one is running, else
    // 0. `TvShell.exe tile-launcher <command line>`: IsGameLaunch of that command line, same exits.
    public static int TileLauncherCli(string[] args) {
        return (args.Length > 1 ? IsGameLaunch(args[1]) : TileLauncherRunning()) ? 1 : 0;
    }

    // `TvShell.exe close-store-launcher`: runs CloseStoreLauncher, exits 0 when none is left.
    // Lets the tests exercise it against a stand-in launcher.
    public static int CloseStoreLauncherCli() { return CloseStoreLauncher("asked to close store launcher") ? 0 : 1; }

    static void StartLauncher() {
        try {
            Process.Start(new ProcessStartInfo(Game.Launcher, Game.LaunchArgs) {
                UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(Game.Launcher) });
        } catch (Exception ex) { Cfg.Error("launch: cannot start store launcher " + Game.Launcher + ": " + ex.Message); }
    }
}

// Steam tiles "Live TV", "Radio", "CBC Radio", "CBC Gem": start Kodi (as our child, so Steam's controller
// support follows it) and open the right section. If Kodi is already running, just switch section.
static class KodiLauncher {
    static readonly Dictionary<string, string> Sections = new Dictionary<string, string> {
        { "radio",    "ActivateWindow(Music,plugin://plugin.audio.radiobrowser/,return)" },
        { "cbcradio", "ActivateWindow(Music,plugin://plugin.audio.cbcradio/,return)" },
        { "gem",      "ActivateWindow(Videos,plugin://plugin.video.cbc/,return)" },
    };
    public static int Run(string target) {
        Process kodi = Process.GetProcessesByName("kodi").FirstOrDefault();
        bool started = false;
        if (kodi == null) {
            try {
                kodi = Process.Start(new ProcessStartInfo(Cfg.KodiExe) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(Cfg.KodiExe) });
            } catch (Exception ex) { Cfg.Error("kodi launcher: cannot start " + Cfg.KodiExe + ": " + ex.Message); return 1; }
            started = true;
        }
        Cfg.Log("kodi launcher: " + target + (started ? " (starting Kodi, pid " + kodi.Id + ")" : " (Kodi already running, pid " + kodi.Id + ")"));
        if (!WaitForKodi(60)) { Cfg.Error("kodi launcher: Kodi never answered on its JSON-RPC port 9090 within 60 s"); return 1; }
        if (target == "tv") {
            // Resume the last-watched channel. PVR channels load a few seconds after start, so retry.
            bool playing = false;
            for (int i = 0; i < 15 && !(playing = IsPlaying()); i++) {
                var ch = LastChannel();
                if (ch > 0) { Rpc("Player.Open", "{\"item\":{\"channelid\":" + ch + "}}"); Cfg.Log("kodi launcher: playing channel " + ch); Thread.Sleep(6000); }
                else Thread.Sleep(2000);
            }
            if (!playing) Cfg.Warn("kodi launcher: no channel started playing after 15 tries");
        } else if (Sections.ContainsKey(target)) {
            Builtin("PlayerControl(Stop)"); Thread.Sleep(500);
            Builtin(Sections[target]);
        } else Cfg.Warn("kodi launcher: unknown section '" + target + "'");
        // The helper is the one witness of how Kodi ends (crash log, ERROR); this is the tile's record.
        if (started) {
            if (WaitOut(kodi, Cfg.KodiLog, HungAfterExitSeconds)) Cfg.Log("kodi launcher: Kodi ended by the launcher, the tile ends");
            else Cfg.Log("kodi launcher: Kodi exited (" + Cfg.ExitOf(kodi) + "), the tile ends");
        }
        return 0;
    }

    // Kodi 21.3 can hang in its last teardown after logging that it has finished (once for 13 hours,
    // crashing at the end), and Steam counts the tile as running for as long as it lingers. So once
    // Kodi's log has ended with its exited line for `grace` seconds and the process is still there,
    // the launcher ends it: everything was saved before that line. True when the launcher ended it.
    const int HungAfterExitSeconds = 30;
    public static bool WaitOut(Process kodi, string log, int grace) {
        DateTime? exitedSince = null;
        try {
            while (!kodi.WaitForExit(1000)) {
                if (!Cfg.LogEndsWith(log, Cfg.KodiExitedLine)) { exitedSince = null; continue; }
                if (exitedSince == null) exitedSince = DateTime.Now;
                if ((DateTime.Now - exitedSince.Value).TotalSeconds < grace) continue;
                Cfg.Warn("kodi launcher: Kodi finished shutting down " + grace + " s ago but process " + kodi.Id + " is still running -> ending it");
                try { kodi.Kill(); } catch (Exception ex) { Cfg.Error("kodi launcher: cannot end hung Kodi pid " + kodi.Id + ": " + ex.Message); }
                if (!kodi.WaitForExit(10000)) Cfg.Error("kodi launcher: hung Kodi pid " + kodi.Id + " did not end within 10 s");
                return true;
            }
        } catch (Exception ex) { Cfg.Warn("kodi launcher: cannot wait for Kodi: " + ex.Message); }
        return false;
    }

    // `TvShell.exe wait-out-kodi <pid> <log> <grace seconds>`: runs WaitOut on that process, exits 2
    // when it ended it and 0 when the process went by itself. Lets the tests drive it with a
    // stand-in process and log.
    public static int WaitOutCli(string[] args) {
        if (args.Length < 4) { Cfg.Error("wait-out-kodi: usage wait-out-kodi <pid> <log> <grace seconds>"); return 1; }
        Process p;
        try { p = Process.GetProcessById(int.Parse(args[1])); } catch (Exception ex) { Cfg.Warn("wait-out-kodi: no process " + args[1] + ": " + ex.Message); return 0; }
        return WaitOut(p, args[2], int.Parse(args[3])) ? 2 : 0;
    }
    static bool WaitForKodi(int seconds) {
        for (int i = 0; i < seconds * 2; i++) {
            try { using (var c = new System.Net.Sockets.TcpClient("127.0.0.1", 9090)) { Thread.Sleep(3000); return true; } }
            catch { Thread.Sleep(500); }
        }
        return false;
    }
    static bool IsPlaying() { return Rpc("Player.GetActivePlayers", "{}").Contains("playerid"); }

    // Stops Kodi's video player, if one is playing; true when it did. Audio (radio) is left alone.
    internal static bool StopVideo() {
        var m = System.Text.RegularExpressions.Regex.Match(Rpc("Player.GetActivePlayers", "{}"),
            "\\{[^{}]*\"playerid\":(\\d+)[^{}]*\"type\":\"video\"[^{}]*\\}");
        if (!m.Success) return false;
        return Rpc("Player.Stop", "{\"playerid\":" + m.Groups[1].Value + "}").Contains("\"OK\"");
    }

    // `TvShell.exe stop-kodi-video <port>`: StopVideo against a Kodi on that local port, exits 1 when
    // it stopped a video, 0 when not, 254 on bad arguments. Lets the tests use a stand-in Kodi.
    public static int StopVideoCli(string[] args) {
        int port;
        if (args.Length != 2 || !int.TryParse(args[1], out port) || port < 1 || port > 65535) return 254;
        rpcPort = port;
        return StopVideo() ? 1 : 0;
    }

    // Most recently watched TV channel (or the first channel if none watched yet); 0 if channels aren't loaded.
    static int LastChannel() {
        var r = Rpc("PVR.GetChannels", "{\"channelgroupid\":\"alltv\",\"properties\":[\"lastplayed\"]}");
        int best = 0, first = 0; string bestTime = "";
        foreach (System.Text.RegularExpressions.Match m in System.Text.RegularExpressions.Regex.Matches(r,
                 "\"channelid\":(\\d+),\"label\":\"(?:[^\"\\\\]|\\\\.)*\",\"lastplayed\":\"([^\"]*)\"")) {
            int id = int.Parse(m.Groups[1].Value); if (first == 0) first = id;
            if (string.CompareOrdinal(m.Groups[2].Value, bestTime) > 0) { bestTime = m.Groups[2].Value; best = id; }
        }
        return best > 0 ? best : first;
    }

    // One JSON-RPC call to the local Kodi; returns the raw response ("" on failure).
    static int rpcPort = 9090;
    static string Rpc(string method, string prms) {
        try {
            using (var c = new System.Net.Sockets.TcpClient("127.0.0.1", rpcPort)) {
                var s = c.GetStream(); s.ReadTimeout = 5000;
                if (method != "Player.GetActivePlayers") Cfg.Log("kodi rpc: " + method);
                var req = Encoding.UTF8.GetBytes("{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"" + method + "\",\"params\":" + prms + "}");
                s.Write(req, 0, req.Length);
                var buf = new byte[1 << 20]; var sb = new StringBuilder(); int depth = 0; bool started = false;
                var until = DateTime.Now.AddSeconds(8);
                while (DateTime.Now < until) {
                    int n = s.Read(buf, 0, buf.Length); if (n <= 0) break;
                    var chunk = Encoding.UTF8.GetString(buf, 0, n); sb.Append(chunk);
                    foreach (var ch in chunk) { if (ch == '{') { depth++; started = true; } else if (ch == '}') depth--; }
                    if (started && depth == 0) {
                        if (sb.ToString().Contains("\"id\":7")) return sb.ToString();
                        sb.Clear(); started = false;   // a notification, keep reading
                    }
                }
                return sb.ToString();
            }
        } catch (Exception ex) { Cfg.Warn("kodi rpc " + method + " failed: " + ex.Message); return ""; }
    }
    // Kodi built-in command via its local EventServer (UDP 9777).
    static void Builtin(string cmd) {
        using (var u = new System.Net.Sockets.UdpClient()) {
            Func<int, byte[], byte[]> packet = (type, payload) => {
                var h = new byte[32];
                Encoding.ASCII.GetBytes("XBMC").CopyTo(h, 0); h[4] = 2; h[7] = (byte)type; h[11] = 1; h[15] = 1;
                h[16] = (byte)(payload.Length >> 8); h[17] = (byte)payload.Length; h[20] = 0x10; h[21] = 0x92;
                return h.Concat(payload).ToArray();
            };
            var helo = Encoding.UTF8.GetBytes("TvShell\0").Concat(new byte[11]).ToArray();
            var p = packet(1, helo); u.Send(p, p.Length, "127.0.0.1", 9777); Thread.Sleep(200);
            p = packet(10, new byte[] { 1 }.Concat(Encoding.UTF8.GetBytes(cmd + "\0")).ToArray()); u.Send(p, p.Length, "127.0.0.1", 9777);
        }
        Cfg.Log("kodi launcher: " + cmd);
    }
}

// A web app that runs in its own Chrome (separate profile, kiosk mode) on the TV.
class WebApp {
    public string Mode, Name, ProfileDir, Url, UserAgent;
    public string PidFile { get { return Path.Combine(Cfg.Dir, Mode + ".pid"); } }
    public string Profile { get { return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ProfileDir); } }

    public static readonly WebApp[] All = {
        // Steam tile "YouTube". YouTube only serves its TV ("leanback") interface to TV devices,
        // so identify as one.
        new WebApp { Mode = "youtube", Name = "YouTube", ProfileDir = "yt-profile", Url = "https://www.youtube.com/tv",
            UserAgent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Cobalt/25.lts.30.1034943-gold (unlike Gecko), Unknown_TV_Unknown_0/Unknown (Unknown, Unknown)" },
    };
    public static WebApp ForMode(string mode) { return All.FirstOrDefault(a => a.Mode == mode); }

    // The app's browser window, from the PID its launcher records. The PID counts only while its
    // process really is this app's browser (see TvSeat.IsYouTubeBrowser): a killed launcher leaves
    // the file behind, and Windows reuses PIDs.
    uint badPid = 0;
    uint Pid() {
        try {
            if (!File.Exists(PidFile)) return 0;
            uint pid = uint.Parse(File.ReadAllText(PidFile).Trim());
            if (WebAppLauncher.IsBrowser((int)pid, Profile)) return pid;
            if (pid != badPid) { badPid = pid; Cfg.Log(Mode + ".pid names process " + pid + ", which is not the " + Name + " browser -> ignored"); }
            return 0;
        } catch { return 0; }
    }
    public IntPtr Window() {
        uint pid = Pid();
        if (pid == 0) return IntPtr.Zero;
        var w = N.WindowsOf(p => p == pid, t => t.Length > 0);
        return w.Count > 0 ? w[0] : IntPtr.Zero;
    }
}

// Steam tile "BackWhenTV App": BackWhenTV's own Windows app, the portable exe the user put in
// backwhentv-app\ beside this one. It opens full-screen on the primary display; the helper moves it.
// The portable exe stays running as the parent of the app, so Steam's controller support follows it.
static class BackWhenApp {
    public const string ProcessName = "BackWhenTV";
    static string Folder { get { return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "backwhentv-app"); } }

    // The newest BackWhenTV-Portable-<version>.exe there, or null.
    public static string Exe() {
        if (!Directory.Exists(Folder)) return null;
        return Directory.GetFiles(Folder, "BackWhenTV-Portable-*.exe").OrderByDescending(f => File.GetLastWriteTimeUtc(f)).FirstOrDefault();
    }

    public static int Run() {
        var exe = Exe();
        if (exe == null) { Cfg.Log("backwhen-app launcher: no BackWhenTV-Portable-*.exe in " + Folder); return 1; }
        if (Process.GetProcessesByName(ProcessName).Length > 0) { Cfg.Log("backwhen-app launcher: already running"); return 0; }
        var p = Process.Start(new ProcessStartInfo(exe) { UseShellExecute = false, WorkingDirectory = Folder });
        Cfg.Log("backwhen-app launcher: started " + Path.GetFileName(exe));
        try { p.WaitForExit(); } catch { }
        Cfg.Log("backwhen-app launcher: closed");
        return 0;
    }

    static HashSet<uint> Pids() { return new HashSet<uint>(Process.GetProcessesByName(ProcessName).Select(p => (uint)p.Id)); }
    public static IntPtr Window() {
        var pids = Pids();
        if (pids.Count == 0) return IntPtr.Zero;
        var w = N.WindowsOf(pids.Contains, t => t.Length > 0);
        return w.Count > 0 ? w[0] : IntPtr.Zero;
    }
    public static bool HasFocus() {
        uint fg; N.GetWindowThreadProcessId(N.GetForegroundWindow(), out fg);
        return fg != 0 && Pids().Contains(fg);
    }
}

// Steam tiles for the web apps: Chrome as our child (so Steam's controller support follows it).
static class WebAppLauncher {
    public static int Run(WebApp app) {
        string tag = app.Mode + " launcher: ";
        var tv = TvDisplay.Find();
        if (tv == null) { Cfg.Warn(tag + "no TV display, not opening"); return 1; }
        var b = tv.Bounds;
        var profile = app.Profile;
        var ua = string.IsNullOrEmpty(app.UserAgent) ? "" : "--user-agent=\"" + app.UserAgent + "\" ";
        var args = string.Format("--user-data-dir=\"{0}\" --kiosk --window-position={1},{2} --window-size={3},{4} {5}" +
            "--no-first-run --no-default-browser-check --autoplay-policy=no-user-gesture-required --disable-features=Translate {6}",
            profile, b.X, b.Y, b.Width, b.Height, ua, app.Url);
        try { Process.Start(new ProcessStartInfo(Cfg.ChromeExe, args) { UseShellExecute = false }); }
        catch (Exception ex) { Cfg.Error(tag + "cannot start " + Cfg.ChromeExe + ": " + ex.Message); return 1; }
        // chrome.exe hands off to a new browser process and exits; find the real browser process
        // (the one using our profile without a --type= switch) and wait on that.
        Process browser = null;
        for (int i = 0; i < 40 && browser == null; i++) { Thread.Sleep(500); browser = FindBrowser(profile); }
        if (browser == null) { Cfg.Error(tag + "no Chrome browser process with the " + app.ProfileDir + " appeared within 20 s"); return 1; }
        Cfg.Hold(browser);
        Directory.CreateDirectory(Cfg.Dir); File.WriteAllText(app.PidFile, browser.Id.ToString());
        Cfg.Log(tag + "chrome browser pid " + browser.Id);
        try { browser.WaitForExit(); } catch (Exception ex) { Cfg.Warn(tag + "cannot wait for Chrome: " + ex.Message); }
        try { File.Delete(app.PidFile); } catch { }
        Cfg.Log(tag + "closed (" + Cfg.ExitOf(browser) + ")");
        return 0;
    }
    static Process FindBrowser(string profile) {
        try {
            using (var q = new System.Management.ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='chrome.exe'"))
                foreach (System.Management.ManagementObject o in q.Get())
                    if (TvSeat.IsYouTubeBrowser(o["CommandLine"] as string, profile))
                        return Process.GetProcessById(Convert.ToInt32(o["ProcessId"]));
        } catch (Exception ex) { Cfg.Warn("web app launcher: process query failed: " + ex.Message); }
        return null;
    }
    // Whether a PID is the browser on a web app's profile, by its command line. Cached per process
    // (PID and start time, so a reused PID is checked afresh) so the once-a-second checks do not
    // query WMI each time.
    static readonly Dictionary<int, KeyValuePair<DateTime, string>> commandLines = new Dictionary<int, KeyValuePair<DateTime, string>>();
    public static bool IsBrowser(int pid, string profile) {
        try {
            var start = Process.GetProcessById(pid).StartTime;
            KeyValuePair<DateTime, string> c;
            if (!commandLines.TryGetValue(pid, out c) || c.Key != start) {
                string cmd = null;
                using (var q = new System.Management.ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE ProcessId=" + pid))
                    foreach (System.Management.ManagementObject o in q.Get()) cmd = o["CommandLine"] as string;
                if (commandLines.Count > 200) commandLines.Clear();
                c = new KeyValuePair<DateTime, string>(start, cmd);
                commandLines[pid] = c;
            }
            return TvSeat.IsYouTubeBrowser(c.Value, profile);
        } catch { return false; }
    }
    // Whether a PID is any web app's browser.
    public static bool IsAnyBrowser(int pid) { return WebApp.All.Any(a => IsBrowser(pid, a.Profile)); }
}

class Helper : Form {
    NotifyIcon tray;
    System.Windows.Forms.Timer timer;
    ToolStripMenuItem miTv, miDesk, miBp, miMouse, miDeskWin;
    bool keepBp = true, lockMouse = true, keepDeskOffTv = true;
    System.Windows.Forms.Timer mouseTimer;
    int reclips = 0; DateTime reclipLogged = DateTime.Now;
    byte[] padReport = null; DateTime lastPadFocus = DateTime.MinValue;
    string gameScreen = "desk";
    int gamePid = -1;
    DateTime modeFileSeen = DateTime.MinValue;
    DateTime bpMissingSince = DateTime.MinValue, lastBpOpen = DateTime.MinValue;
    DateTime bpRestartAt = DateTime.MinValue, bpReopenAt = DateTime.MinValue, focusBpUntil = DateTime.MinValue;
    const int HOTKEY = 0x5456;

    public Helper() {
        ShowInTaskbar = false; FormBorderStyle = FormBorderStyle.None; Opacity = 0; Size = new Size(1, 1);
        var menu = new ContextMenuStrip();
        miTv = new ToolStripMenuItem("Game on TV", null, (s, e) => SetTarget("tv", true));
        miDesk = new ToolStripMenuItem("Game on desk display", null, (s, e) => SetTarget("desk", true));
        miBp = new ToolStripMenuItem("Keep Big Picture on TV", null, (s, e) => { keepBp = !keepBp; miBp.Checked = keepBp; }) { Checked = true };
        miMouse = new ToolStripMenuItem("Keep mouse on desk display", null, (s, e) => {
            lockMouse = !lockMouse; miMouse.Checked = lockMouse;
            if (!lockMouse) { N.RECT all = new N.RECT { L = -32000, T = -32000, R = 32000, B = 32000 }; N.ClipCursor(ref all); }
        }) { Checked = true };
        miDeskWin = new ToolStripMenuItem("Keep desk windows off the TV", null, (s, e) => {
            keepDeskOffTv = !keepDeskOffTv; miDeskWin.Checked = keepDeskOffTv; deskMoves.Clear();
        }) { Checked = true };
        if (Game.Configured) menu.Items.AddRange(new ToolStripItem[] { miTv, miDesk, new ToolStripSeparator() });
        menu.Items.AddRange(new ToolStripItem[] { miBp, miMouse, miDeskWin,
            new ToolStripMenuItem("Open log", null, (s, e) => { try { Process.Start("notepad.exe", Cfg.LogFile); } catch { } }),
            new ToolStripSeparator(), new ToolStripMenuItem("Exit", null, (s, e) => Close()) });
        Icon ico = SystemIcons.Application;
        try { ico = DrawTrayIcon(); } catch { }
        var tip = Game.Configured ? "TvShell (Ctrl+Alt+T: move the game)" : "TvShell";
        tray = new NotifyIcon { Icon = ico, Text = tip, ContextMenuStrip = menu, Visible = true };
        CreateHandle(); // the form is never shown, so create its window explicitly for the hotkey
        timer = new System.Windows.Forms.Timer { Interval = 1000 };
        timer.Tick += (s, e) => { try { Tick(); } catch (Exception ex) { Fault("tick", ex); } };
        timer.Start();
        mouseTimer = new System.Windows.Forms.Timer { Interval = 100 };
        mouseTimer.Tick += (s, e) => { try { KeepMouseOnDesk(); RememberDeskWindow(); } catch (Exception ex) { Fault("mouse", ex); } };
        mouseTimer.Start();
        keyHookProc = OnKey;   // held in a field: a collected delegate would crash the hook
        keyHook = N.SetWindowsHookEx(13 /*WH_KEYBOARD_LL*/, keyHookProc, N.GetModuleHandle(null), 0);
        if (keyHook == IntPtr.Zero) Cfg.Error("keyboard hook unavailable (error " + Marshal.GetLastWin32Error() + "): the keyboard will not return focus to the desk");
        // Every focus change, whoever caused it: without it, "the game lost focus" has no culprit.
        fgHookProc = OnForeground;
        fgHook = N.SetWinEventHook(3 /*EVENT_SYSTEM_FOREGROUND*/, 3, IntPtr.Zero, fgHookProc, 0, 0, 0x2 /*OUTOFCONTEXT|SKIPOWNPROCESS*/);
        if (fgHook == IntPtr.Zero) Cfg.Warn("focus-change hook unavailable: focus changes will not be logged");
        Microsoft.Win32.SystemEvents.DisplaySettingsChanged += OnDisplays;
        Microsoft.Win32.SystemEvents.PowerModeChanged += OnPower;
        Microsoft.Win32.SystemEvents.SessionSwitch += OnSession;
        string built = "?";
        try { built = File.GetLastWriteTime(Application.ExecutablePath).ToString("yyyy-MM-dd HH:mm"); } catch { }
        Cfg.Log("helper started: " + Application.ExecutablePath + " built " + built + "; displays " + DisplayList());
        EnsureAddonPatches();
    }

    // An exception from a timer: full stack the first time, then at most once a minute per
    // place, so a fault that repeats every tick cannot flood the log or hide everything else.
    readonly Dictionary<string, DateTime> faultLogged = new Dictionary<string, DateTime>();
    readonly Dictionary<string, int> faultCount = new Dictionary<string, int>();
    void Fault(string where, Exception ex) {
        int n; faultCount.TryGetValue(where, out n); faultCount[where] = ++n;
        DateTime last;
        if (faultLogged.TryGetValue(where, out last) && (DateTime.Now - last).TotalMinutes < 1) return;
        faultLogged[where] = DateTime.Now;
        Cfg.Error(where + " error (" + n + "x so far): " + ex);
    }

    static string DisplayList() { return TvDisplay.List(); }
    void OnDisplays(object s, EventArgs e) { Cfg.Log("displays changed: " + DisplayList()); }
    void OnPower(object s, Microsoft.Win32.PowerModeChangedEventArgs e) { Cfg.Log("power: " + e.Mode); }
    void OnSession(object s, Microsoft.Win32.SessionSwitchEventArgs e) { Cfg.Log("session: " + e.Reason); }

    IntPtr fgHook = IntPtr.Zero; N.WinEventProc fgHookProc;   // held in a field, like keyHookProc
    void OnForeground(IntPtr hook, uint ev, IntPtr h, int obj, int child, uint thread, uint time) {
        try { Cfg.Log("focus -> " + Describe(h)); } catch { }
        try {
            uint pid; N.GetWindowThreadProcessId(h, out pid);
            bool isApp = h != IntPtr.Zero && tvApps.Any(a => a.Running && a.Proc != null && a.Proc.Id == (int)pid);
            tvAppUsedLast = PadFocus.UsedLast(h != IntPtr.Zero && h == gameHwnd, isApp, tvAppUsedLast);
        } catch { }
    }
    bool tvAppUsedLast = false;   // a TV app, not the game, was the last of the two focused (PadFocus)

    // A window as the log names it: process, pid, screen. The title only for windows on the TV;
    // desk titles (documents, browser tabs) are the person's business, not the log's.
    string Describe(IntPtr h) {
        if (h == IntPtr.Zero) return "no window";
        uint pid; N.GetWindowThreadProcessId(h, out pid);
        string name = "?";
        try { using (var p = Process.GetProcessById((int)pid)) name = p.ProcessName; } catch { }
        var r = N.Rect(h); var c = new Point(r.X + r.Width / 2, r.Y + r.Height / 2);
        bool onTv = !tvBounds.IsEmpty && tvBounds.Contains(c);
        string where = onTv ? "tv" : Desk().Bounds.Contains(c) ? "desk" : "off-screen";
        string title = "";
        if (onTv) { var sb = new StringBuilder(256); N.GetWindowText(h, sb, 256); title = " \"" + sb + "\""; }
        return name + "[" + pid + "]" + title + " on " + where;
    }

    // The tray icon is drawn here, a plain TV set, so the helper ships no image file and never
    // borrows another program's icon (the game's own icon belongs to the game's tile only).
    static Icon DrawTrayIcon() {
        using (var bmp = new Bitmap(32, 32))
        using (var g = Graphics.FromImage(bmp)) {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using (var body = new SolidBrush(Color.FromArgb(235, 235, 235)))
            using (var screen = new SolidBrush(Color.FromArgb(40, 120, 220)))
            using (var stand = new Pen(Color.FromArgb(235, 235, 235), 3f)) {
                g.FillRectangle(body, 2, 5, 28, 19);
                g.FillRectangle(screen, 5, 8, 22, 13);
                g.DrawLine(stand, 16, 24, 16, 28);
                g.DrawLine(stand, 9, 29, 23, 29);
            }
            IntPtr h = bmp.GetHicon();
            // Clone so the icon owns its own copy; the HICON from GetHicon is never freed otherwise.
            using (var tmp = Icon.FromHandle(h)) {
                var ico = (Icon)tmp.Clone();
                N.DestroyIcon(h);
                return ico;
            }
        }
    }

    // Fixes to third-party Kodi add-ons, re-applied on every start because an add-on update
    // overwrites them. Each is independent: a missing add-on skips only its own patch.
    static void EnsureAddonPatches() {
        // SlyGuy's bandwidth cap only accepts whole Mbit/s; allow decimals (cap is 0.7 = Pluto's lowest stream).
        PatchAddon("SlyGuy decimal bandwidth cap", @"script.module.slyguy\resources\modules\slyguy\gui.py",
            "'max_bandwidth': settings.getInt('max_bandwidth', 0),",
            "'max_bandwidth': float(settings.get('max_bandwidth', 0) or 0),");
        // CBC's server stalls requests that identify as Python, so the CBC Radio add-on hangs forever.
        // Make it identify as a browser (and give up after 20 s instead of never).
        PatchAddon("CBC Radio browser identity", @"plugin.audio.cbcradio\now_playing.py",
            "response = urllib.request.urlopen(url)",
            "response = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20)");
    }

    static void PatchAddon(string name, string relative, string from, string to) {
        try {
            var f = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), @"Kodi\addons", relative);
            if (!File.Exists(f)) { Cfg.Log("add-on patch " + name + ": add-on not installed"); return; }
            var t = File.ReadAllText(f);
            if (t.Contains(from)) { File.WriteAllText(f, t.Replace(from, to)); Cfg.Log("add-on patch " + name + ": re-applied"); }
            else if (t.Contains(to)) Cfg.Log("add-on patch " + name + ": already in place");
            // Neither text: the add-on changed under the patch, which now does nothing.
            else Cfg.Error("add-on patch " + name + ": " + relative + " no longer contains the code it patches; update the patch");
        } catch (Exception ex) { Cfg.Error("add-on patch " + name + " failed: " + ex.Message); }
    }

    // Confine the cursor to the desk display. A focused game clips the cursor to its own window,
    // so re-apply whenever the clip rectangle is not the desk display.
    IntPtr gameHwnd = IntPtr.Zero;   // refreshed every second by Tick
    void KeepMouseOnDesk() {
        if (!lockMouse) return;
        // A game's controller mode can drive the real mouse (an on-screen cursor) inside its own
        // window, so while the game is on the TV and focused, let it manage the mouse.
        if (gameHwnd != IntPtr.Zero && gameScreen == "tv" && N.GetForegroundWindow() == gameHwnd) return;
        var d = Desk().Bounds;
        N.RECT cur; N.GetClipCursor(out cur);
        if (cur.L == d.Left && cur.T == d.Top && cur.R == d.Right && cur.B == d.Bottom) return;
        var r = new N.RECT { L = d.Left, T = d.Top, R = d.Right, B = d.Bottom };
        N.ClipCursor(ref r);
        reclips++;
        if ((DateTime.Now - reclipLogged).TotalMinutes >= 10) { Cfg.Log("mouse re-confined " + reclips + "x in last period"); reclips = 0; reclipLogged = DateTime.Now; }
    }

    // The TV and the desk are separate seats: the controller drives the TV, the keyboard and
    // mouse the desk. Windows has one focused window, so each side takes focus back when its
    // own input is used. The desk window to go back to is remembered here.
    IntPtr lastDeskWindow = IntPtr.Zero;
    Rectangle tvBounds = Rectangle.Empty;   // refreshed every second by Tick; empty = no TV
    IntPtr keyHook = IntPtr.Zero; N.HookProc keyHookProc;
    DateTime lastKeyReturn = DateTime.MinValue;

    void RememberDeskWindow() {
        var fg = N.GetForegroundWindow();
        if (fg != IntPtr.Zero && fg != Handle && FocusIsOnDesk()) lastDeskWindow = fg;
    }

    bool ForegroundOnTv() {
        if (tvBounds.IsEmpty) return false;
        var r = N.Rect(N.GetForegroundWindow());
        return tvBounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2));
    }

    // Low-level keyboard hook: runs before the key reaches any window, so a key pressed while
    // the TV has focus can be held back. It must return quickly, so the focus switch is queued.
    IntPtr OnKey(int code, IntPtr w, IntPtr l) {
        try {
            int msg = w.ToInt32();
            if (code >= 0 && (msg == 0x100 || msg == 0x104) /*WM_KEYDOWN, WM_SYSKEYDOWN*/ && ForegroundOnTv()) {
                var k = (N.KBDLLHOOKSTRUCT)Marshal.PtrToStructure(l, typeof(N.KBDLLHOOKSTRUCT));
                int act = DeskKeys.Classify(k.Vk, k.Flags);
                if (act != DeskKeys.Ignore) {
                    if ((DateTime.Now - lastKeyReturn).TotalMilliseconds > 300) { lastKeyReturn = DateTime.Now; BeginInvoke((Action)ReturnToDesk); }
                    if (act == DeskKeys.Hold) return new IntPtr(1);
                }
            }
        } catch { }
        return N.CallNextHookEx(keyHook, code, w, l);
    }

    void ReturnToDesk() {
        var target = lastDeskWindow;
        if (target == IntPtr.Zero || !N.IsWindow(target) || !N.IsWindowVisible(target)) target = N.GetShellWindow();
        if (target == IntPtr.Zero) return;
        if (N.Focus(target)) Cfg.Log("keyboard on desk -> focus back to the desk");
        else Cfg.Warn("keyboard on desk -> Windows refused the focus change (focus stays with " + Describe(N.GetForegroundWindow()) + ")");
    }

    // Controller press -> focus whatever is on the TV, because Steam only sends the controller
    // to the focused window.
    bool padSeen = false;
    void OnPadReport(byte[] rep) {
        if (!padSeen) { padSeen = true; Cfg.Log("first controller report: id 0x" + rep[0].ToString("X2") + ", " + rep.Length + " bytes"); }
        bool asKeys = PadToKeys(rep);
        if (Pad.Buttons(rep) == null) return;
        bool pressed = Pad.Pressed(padReport, rep);
        padReport = rep;   // kept current while the presses are keys, so no stale report reads as a press later
        if (asKeys) return;
        if (!pressed || (DateTime.Now - lastPadFocus).TotalSeconds < 1.5) return;
        // Already using something on the TV - leave it alone. A Steam window counts as the TV
        // unless it sits on the desk (the ordinary Steam client there must not keep the controller).
        // A press while no window has focus is mid-switch (PadFocus): leave it to whoever switches.
        var act = PadFocus.Plan(N.GetForegroundWindow() != IntPtr.Zero, SteamInFront(), FocusIsOnTv(), FocusIsOnDesk());
        if (act == PadFocus.Act.Leave) return;
        if (act == PadFocus.Act.Uncover) {
            // ... except a window covered by another of ours: focus the one on top (Uncovered).
            var tv = Tv(); if (tv == null) return;
            var centre = new N.POINT { X = tv.Bounds.X + tv.Bounds.Width / 2, Y = tv.Bounds.Y + tv.Bounds.Height / 2 };
            var top = N.GetAncestor(N.WindowFromPoint(centre), 2);
            var onGame = GameWindow();
            if (!Uncovered.Plan(top, N.GetForegroundWindow(), onGame, TvAppWindow())) return;
            lastPadFocus = DateTime.Now;
            var covering = top == onGame ? "game" : "TV app";
            if (N.Focus(top)) Cfg.Log("controller press -> focus was behind the " + covering + " -> focused it");
            else { lastPadFocus = DateTime.MinValue; Cfg.Warn("controller press -> focus is behind the " + covering + " and Windows refused to move it (focus stays with " + Describe(N.GetForegroundWindow()) + ")"); }
            return;
        }
        lastPadFocus = DateTime.Now;
        // Which window the press took focus from, and where it sat: switching from the game to
        // Live TV in Steam's overlay reaches this branch although the game had focus on the TV.
        var from = N.GetForegroundWindow();
        var fromText = Describe(from) + " at " + N.Rect(from);
        RememberDeskWindow();
        IntPtr target = IntPtr.Zero;
        var game = GameWindow();
        var kodi = TvAppWindow();
        var pick = PadFocus.Pick(game != IntPtr.Zero, gameScreen == "tv", kodi != IntPtr.Zero, tvAppUsedLast);
        if (pick == PadFocus.Target.Game) target = game;
        else if (pick == PadFocus.Target.App) target = kodi;
        else if (pick == PadFocus.Target.None) return;   // the game is on the desk display: leave focus alone
        else { var bp = BigPictureWindows(); if (bp.Count > 0) target = bp[0]; }
        if (target != IntPtr.Zero && N.GetForegroundWindow() != target) {
            var what = target == game ? "game" : target == kodi ? "TV app" : "Big Picture";
            if (N.Focus(target)) Cfg.Log("controller press -> focused " + what + " (focus was with " + fromText + ")");
            else { lastPadFocus = DateTime.MinValue; Cfg.Warn("controller press -> Windows refused to focus " + what + " (focus stays with " + Describe(N.GetForegroundWindow()) + ")"); }
        }
    }

    // While the keyboard-only BackWhenTV app has focus, newly pressed buttons become its keys.
    // True when the report was used that way.
    int padHeld = 0; bool padKeysLogged = false;
    bool PadToKeys(byte[] rep) {
        int now = PadKeys.Buttons(rep);
        if (now < 0) return false;
        int pressed = now & ~padHeld; padHeld = now;
        if (pressed == 0) return false;   // checking focus lists processes: only on a press
        if (!BackWhenApp.HasFocus()) return false;
        foreach (var combo in PadKeys.Keys(pressed)) PadKeys.Press(combo);
        if (!padKeysLogged) { padKeysLogged = true; Cfg.Log("controller -> keys for the BackWhenTV app"); }
        return true;
    }

    // True when a Steam window (Big Picture, its in-game menu) is the focused window.
    static bool SteamInFront() {
        uint pid; N.GetWindowThreadProcessId(N.GetForegroundWindow(), out pid);
        try { var n = Process.GetProcessById((int)pid).ProcessName.ToLowerInvariant(); return n == "steamwebhelper" || n == "steam" || n == "gameoverlayui" || n == "gameoverlayui64"; } catch { return false; }
    }

    // True when the focused window sits on the TV (Kodi, Big Picture, Steam's menu, a game on the TV).
    // The helper only ever pulls focus away from the desk display, never between things on the TV.
    static bool FocusIsOnTv() {
        var tv = Tv(); if (tv == null) return false;
        var r = N.Rect(N.GetForegroundWindow());
        return tv.Bounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2));
    }

    static List<IntPtr> BigPictureWindows() {
        var web = new HashSet<uint>(Process.GetProcessesByName("steamwebhelper").Select(p => (uint)p.Id));
        return N.WindowsOf(web.Contains, t => t == "Steam Big Picture Mode");
    }

    protected override void SetVisibleCore(bool v) { base.SetVisibleCore(false); }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        if (!N.RegisterHotKey(Handle, HOTKEY, 0x1 | 0x2 | 0x4000 /*Alt|Ctrl|NoRepeat*/, (uint)Keys.T)) Cfg.Warn("hotkey Ctrl+Alt+T unavailable (another program holds it)");
        // Gamepads + joysticks, delivered even when we are in the background (RIDEV_INPUTSINK).
        var devs = new[] {
            new N.RAWINPUTDEVICE { UsagePage = 1, Usage = 5, Flags = 0x100, Target = Handle },
            new N.RAWINPUTDEVICE { UsagePage = 1, Usage = 4, Flags = 0x100, Target = Handle } };
        if (!N.RegisterRawInputDevices(devs, 2, (uint)Marshal.SizeOf(typeof(N.RAWINPUTDEVICE))))
            Cfg.Error("raw input registration failed (error " + Marshal.GetLastWin32Error() + "): a controller press will not focus the TV");
    }
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x312 && m.WParam.ToInt32() == HOTKEY) SetTarget(gameScreen == "tv" ? "desk" : "tv", true);
        if (m.Msg == 0x00FF) {  // WM_INPUT
            try {
                uint hdr = (uint)(8 + 2 * IntPtr.Size), size = 0;
                N.GetRawInputData(m.LParam, 0x10000003, IntPtr.Zero, ref size, hdr);
                if (size > hdr + 8) {
                    IntPtr buf = Marshal.AllocHGlobal((int)size);
                    try {
                        if (N.GetRawInputData(m.LParam, 0x10000003, buf, ref size, hdr) == size && Marshal.ReadInt32(buf) == 2 /*RIM_TYPEHID*/) {
                            int each = Marshal.ReadInt32(buf, (int)hdr), count = Marshal.ReadInt32(buf, (int)hdr + 4);
                            for (int i = 0; i < count && each > 0; i++) {
                                var rep = new byte[each];
                                Marshal.Copy(IntPtr.Add(buf, (int)hdr + 8 + i * each), rep, 0, each);
                                OnPadReport(rep);
                            }
                        }
                    } finally { Marshal.FreeHGlobal(buf); }
                }
            } catch (Exception ex) { Fault("raw input", ex); }
        }
        base.WndProc(ref m);
    }
    protected override void OnFormClosed(FormClosedEventArgs e) {
        N.UnregisterHotKey(Handle, HOTKEY);
        if (keyHook != IntPtr.Zero) N.UnhookWindowsHookEx(keyHook);
        if (fgHook != IntPtr.Zero) N.UnhookWinEvent(fgHook);
        // SystemEvents are static: a handler left on them outlives the form.
        Microsoft.Win32.SystemEvents.DisplaySettingsChanged -= OnDisplays;
        Microsoft.Win32.SystemEvents.PowerModeChanged -= OnPower;
        Microsoft.Win32.SystemEvents.SessionSwitch -= OnSession;
        Cfg.Log("helper closing (Exit from the tray menu)");
        var all = new N.RECT { L = -32000, T = -32000, R = 32000, B = 32000 }; N.ClipCursor(ref all);
        tray.Visible = false; tray.Dispose(); base.OnFormClosed(e);
        Application.Exit();
    }

    static Screen Tv() { return TvDisplay.Find(); }
    static Screen Desk() { return Screen.PrimaryScreen; }

    void SetTarget(string t, bool activate) {
        gameScreen = t;
        miTv.Checked = t == "tv"; miDesk.Checked = t == "desk";
        Cfg.Log("game screen -> " + t);
        var h = GameWindow();
        if (h != IntPtr.Zero) MoveGame(h, activate);
    }

    static IntPtr KodiWindow() {
        var pids = new HashSet<uint>(Process.GetProcessesByName("kodi").Select(p => (uint)p.Id));
        if (pids.Count == 0) return IntPtr.Zero;
        var w = N.WindowsOf(pids.Contains, t => t == "Kodi");
        return w.Count > 0 ? w[0] : IntPtr.Zero;
    }

    // Kodi, else the BackWhenTV app, else the first web app (YouTube) that has a window.
    static IntPtr TvAppWindow() {
        var k = KodiWindow(); if (k != IntPtr.Zero) return k;
        var bw = BackWhenApp.Window(); if (bw != IntPtr.Zero) return bw;
        foreach (var a in WebApp.All) { var w = a.Window(); if (w != IntPtr.Zero) return w; }
        return IntPtr.Zero;
    }

    // Ordinary desk windows found on the TV go back to the desk (see TvSeat). Only top-level
    // app windows with a title bar: no tool windows, popups owned by another window (they follow
    // their owner), cloaked windows or the taskbar. A window Windows will not let us move (an
    // elevated one), or one that keeps going back, is left alone after a few tries.
    readonly Dictionary<IntPtr, int> deskMoves = new Dictionary<IntPtr, int>();
    void SendDeskWindowsHome(Screen tv) {
        if (!keepDeskOffTv) return;
        uint me = (uint)Process.GetCurrentProcess().Id;
        var desk = Desk().WorkingArea;
        var names = new Dictionary<uint, string>();
        var stray = new List<IntPtr>();
        N.EnumWindows((h, l) => {
            if (!N.IsWindowVisible(h) || N.IsIconic(h) || N.GetWindow(h, 4 /*GW_OWNER*/) != IntPtr.Zero) return true;
            int style = N.GetWindowLong(h, -16), ex = N.GetWindowLong(h, -20);
            if ((style & 0x00C00000 /*WS_CAPTION*/) != 0x00C00000 || (ex & 0x80 /*WS_EX_TOOLWINDOW*/) != 0) return true;
            var r = N.Rect(h);
            if (!tv.Bounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2))) return true;
            uint pid; N.GetWindowThreadProcessId(h, out pid);
            if (pid == me || N.IsCloaked(h)) return true;
            var cls = N.ClassOf(h);
            if (cls == "Shell_SecondaryTrayWnd" || cls == "Shell_TrayWnd" || cls == "Progman" || cls == "WorkerW") return true;
            string name;
            if (!names.TryGetValue(pid, out name)) {
                try { name = Process.GetProcessById((int)pid).ProcessName; } catch { name = ""; }
                names[pid] = name;
            }
            // A web app's browser by its own command line, not its PID file: the launcher writes
            // that file only once the window is already up.
            bool yt = string.Equals(name, "chrome", StringComparison.OrdinalIgnoreCase) && WebAppLauncher.IsAnyBrowser((int)pid);
            if (!TvSeat.BelongsOnTv(name, yt, Game.Process, Game.LauncherProcess)) stray.Add(h);
            return true;
        }, IntPtr.Zero);
        foreach (var dead in deskMoves.Keys.Where(h => !N.IsWindow(h)).ToList()) deskMoves.Remove(dead);
        foreach (var h in stray) {
            int tries; deskMoves.TryGetValue(h, out tries);
            if (tries >= 3) continue;
            deskMoves[h] = ++tries;
            var sb = new StringBuilder(256); N.GetWindowText(h, sb, 256);
            uint pid; N.GetWindowThreadProcessId(h, out pid);
            var what = names[pid] + " '" + sb + "'";
            // A maximized window stays on its monitor, so un-maximize, move, maximize again. Both
            // ShowWindow calls activate it; focus goes back to where it was, so the move never
            // takes the controller's window away from the TV.
            var fg = N.GetForegroundWindow();
            bool max = N.IsZoomed(h);
            if (max) N.ShowWindow(h, 9 /*SW_RESTORE*/);
            var to = TvSeat.DeskRect(N.Rect(h), tv.Bounds, desk);
            bool ok = N.SetWindowPos(h, IntPtr.Zero, to.X, to.Y, to.Width, to.Height, N.SWP_NOZORDER | N.SWP_NOACTIVATE);
            if (max) N.ShowWindow(h, 3 /*SW_MAXIMIZE*/);
            if (fg != h && fg != IntPtr.Zero && N.GetForegroundWindow() != fg) N.Focus(fg);
            var now = N.Rect(h);
            ok = ok && !tv.Bounds.Contains(new Point(now.X + now.Width / 2, now.Y + now.Height / 2));
            if (ok) Cfg.Log("desk window on the TV -> moved to the desk: " + what);
            else Cfg.Log("desk window on the TV -> Windows refused the move (an elevated window?): " + what);
            if (tries == 3) Cfg.Log("desk window keeps returning to the TV -> left there: " + what);
        }
    }

    static bool FocusIsOnDesk() {
        var r = N.Rect(N.GetForegroundWindow());
        return Desk().Bounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2));
    }

    // Apps that live on the TV (Kodi, BackWhenTV's app, the web apps): keep them there, focus them when
    // they start, never let Big Picture cover them, and bring Big Picture back when they close.
    // Proc is held from the moment its window appears, so its exit code can be read once it is
    // gone: that is what tells a crash from a close. CrashLog is the app's own log, kept on a crash;
    // ExitedLine is the last line the app logs once it has finished shutting down.
    // StopVideoWhenCovered: see KeepTvApp; CoveredSince and StopChecking are its state.
    class TvApp {
        public string Name, CrashLog, ExitedLine; public Func<IntPtr> Find; public bool Borderless, StopVideoWhenCovered;
        public bool Running, FocusPending, CoveredLogged; public Process Proc;
        public DateTime CoveredSince = DateTime.MinValue; public volatile bool StopChecking;
    }
    static readonly TimeSpan CoveredVideoLimit = TimeSpan.FromSeconds(60);
    readonly List<TvApp> tvApps = new List<TvApp> {
        // Kodi runs windowed (its own monitor switching is unreliable): strip the border and make its
        // drawing area exactly cover the TV, so it renders full-screen there.
        new TvApp { Name = "Kodi", Find = KodiWindow, Borderless = true, StopVideoWhenCovered = true, CrashLog = Cfg.KodiLog, ExitedLine = Cfg.KodiExitedLine },
        // BackWhenTV's app opens borderless full-screen on the primary display: same treatment.
        new TvApp { Name = "BackWhenTV app", Find = BackWhenApp.Window, Borderless = true },
    }.Concat(WebApp.All.Select(a => new TvApp { Name = a.Name, Find = a.Window, Borderless = false })).ToList();

    // How a TV app's window went away, logged once. A crash is an ERROR, with the app's log kept.
    // Kodi takes up to ten seconds to end after its window goes, and can crash in them, so an app
    // still running is waited for off this timer's thread: without that its log was lost to its
    // next start and nobody could say whether the crash came after it had shut down.
    void TvAppGone(TvApp a) {
        var p = a.Proc; a.Proc = null;
        if (p == null) { Cfg.Log(a.Name + " closed (process unknown)"); return; }
        Action ended = () => {
            string how; int code;
            try { how = Cfg.ExitOf(p); code = p.ExitCode; }
            catch (Exception ex) { how = "exit code unknown: " + ex.Message; code = 0; }
            p.Dispose();
            var kind = AppExit.Judge(code, a.CrashLog, a.ExitedLine);
            if (kind == AppExit.Kind.Closed) { Cfg.Log(a.Name + " closed (" + how + ")"); return; }
            var kept = a.CrashLog == null ? "" : Cfg.KeepCrashLog(a.Name.ToLowerInvariant(), a.CrashLog);
            if (kind == AppExit.Kind.CrashedAfterShutdown) {   // the app's defect: a WARN, not an ERROR
                Cfg.Warn(a.Name + " crashed after it finished shutting down (" + how + ")" + (kept != "" ? "; its log is kept at " + kept : ""));
                return;
            }
            Cfg.Error(a.Name + " crashed (" + how + ")" + (kept != "" ? "; its log is kept at " + kept : "")
                + "; the game " + (Game.Running().Length > 0 ? "was" : "was not") + " running");
        };
        try {
            if (!p.WaitForExit(2000)) {
                Cfg.Log(a.Name + " window gone, process " + p.Id + " still running");
                new Thread(() => { try { p.WaitForExit(); } catch { } ended(); }) { IsBackground = true }.Start();
                return;
            }
        } catch { }   // `ended` says what is not known
        ended();
    }
    // One TV app at a time, like a TV. A second one started from its tile closes the first:
    // left open, the first sits behind the second with its tile still "running", and the
    // controller (which goes to the first TV app it finds) never reaches the second. Apps
    // already open when the helper starts are left as they are.
    bool tvAppsSeen = false;
    void KeepTvApps(Screen tv) {
        var started = new List<TvApp>();
        foreach (var a in tvApps) { bool was = a.Running; KeepTvApp(a, tv); if (!was && a.Running) started.Add(a); }
        if (tvAppsSeen && started.Count == 1)
            foreach (var other in tvApps) {
                if (other == started[0] || !other.Running) continue;
                var h = other.Find();
                if (h == IntPtr.Zero) continue;
                N.PostMessage(h, 0x0010 /*WM_CLOSE*/, IntPtr.Zero, IntPtr.Zero);
                Cfg.Log(started[0].Name + " started -> closing " + other.Name + ", one TV app at a time");
            }
        tvAppsSeen = true;
    }
    void KeepTvApp(TvApp a, Screen tv) {
        var h = a.Find();
        if (h == IntPtr.Zero) {
            if (a.Running) {
                a.Running = false;
                TvAppGone(a);
                // Not while the game runs: Steam hides Big Picture then, and reopening it would
                // cover the game. The game's own exit restarts it.
                if (Game.Running().Length > 0) Cfg.Log(a.Name + " gone -> Big Picture left alone, the game is running");
                else if (TvAppWindow() == IntPtr.Zero) { bpRestartAt = DateTime.Now.AddSeconds(3); Cfg.Log(a.Name + " gone -> restarting Big Picture"); }
            }
            return;
        }
        if (!a.Running) {
            a.Running = true; focusBpUntil = DateTime.MinValue; bpRestartAt = DateTime.MinValue; bpReopenAt = DateTime.MinValue;
            a.FocusPending = true;
            uint pid; N.GetWindowThreadProcessId(h, out pid);
            try { a.Proc = Process.GetProcessById((int)pid); } catch { a.Proc = null; }
            bool held = Cfg.Hold(a.Proc);
            Cfg.Log(a.Name + " started (pid " + pid + (held ? "" : ", exit code will be unknown") + ")");
        }
        if (N.IsIconic(h)) N.ShowWindow(h, 9);
        if (a.Borderless) {
            int style = N.GetWindowLong(h, -16);
            if ((style & 0x00CF0000) != 0 || N.ClientScreenRect(h) != tv.Bounds) {
                N.SetWindowLong(h, -16, style & ~0x00CF0000);
                N.SetWindowPos(h, IntPtr.Zero, tv.Bounds.X, tv.Bounds.Y, tv.Bounds.Width, tv.Bounds.Height, N.SWP_NOZORDER | N.SWP_FRAMECHANGED | N.SWP_NOACTIVATE);
                Cfg.Log("placed " + a.Name + " on TV " + tv.Bounds);
            }
        } else {
            var r = N.Rect(h);
            if (!tv.Bounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2))) {
                N.Place(h, tv.Bounds, false); Cfg.Log("moved " + a.Name + " to TV " + tv.Bounds);
            }
        }
        // Just started: put it in front once (Steam can leave Big Picture over it).
        if (a.FocusPending) {
            a.FocusPending = false;
            if (!FocusIsOnDesk()) {
                if (N.Focus(h)) Cfg.Log("focused newly started " + a.Name);
                else Cfg.Warn("Windows refused to focus newly started " + a.Name + " (focus stays with " + Describe(N.GetForegroundWindow()) + ")");
            } else Cfg.Log(a.Name + " started while the desk has focus -> left unfocused");
        }
        // Nothing (e.g. Big Picture) may sit on top of it on the TV - except Steam's menu while you use it,
        // and the game: a game on the TV is what the controller is for, and raising the app over
        // it left the app holding focus behind the game, where no press reached anything visible.
        // Raised without taking focus: this only runs while focus is on the desk, and taking it
        // would send the desk keyboard to the TV. The next controller press focuses it.
        var centre = new N.POINT { X = tv.Bounds.X + tv.Bounds.Width / 2, Y = tv.Bounds.Y + tv.Bounds.Height / 2 };
        var onTop = N.GetAncestor(N.WindowFromPoint(centre), 2);
        if (onTop != h && (onTop == IntPtr.Zero || onTop != gameHwnd) && !SteamInFront() && !FocusIsOnTv()) {
            N.SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 /*NOSIZE|NOMOVE|NOACTIVATE, HWND_TOP*/);
            bool raised = N.GetAncestor(N.WindowFromPoint(centre), 2) == h;
            if (raised || !a.CoveredLogged) Cfg.Log(a.Name + " was covered -> " + (raised ? "brought to front" : "stays covered until the controller is used"));
            a.CoveredLogged = !raised;
        }
        // Kodi 21.3 playing video behind another window (the game, Big Picture) stops presenting
        // frames ("OutputPicture - timeout waiting for buffer" in its log) and minutes later crashes
        // in its video output. So video left covered for a minute is stopped; radio plays on. The
        // RPC runs off this timer's thread: it can wait seconds on a busy Kodi.
        if (a.StopVideoWhenCovered) {
            bool covered = N.GetAncestor(N.WindowFromPoint(centre), 2) != h;
            if (!covered) a.CoveredSince = DateTime.MinValue;
            else if (a.CoveredSince == DateTime.MinValue) a.CoveredSince = DateTime.Now;
            else if (DateTime.Now - a.CoveredSince >= CoveredVideoLimit && !a.StopChecking) {
                a.CoveredSince = DateTime.Now;   // still covered: checked again a minute later
                a.StopChecking = true;
                var app = a;
                ThreadPool.QueueUserWorkItem(_ => {
                    try {
                        if (KodiLauncher.StopVideo())
                            Cfg.Log(app.Name + " covered for " + (int)CoveredVideoLimit.TotalSeconds + " s while playing video -> stopped it, Kodi crashes rendering video it cannot show");
                    } catch (Exception ex) { Cfg.Warn("stopping covered " + app.Name + " video failed: " + ex.Message); }
                    finally { app.StopChecking = false; }
                });
            }
        }
    }

    static IntPtr GameWindow() {
        var pids = new HashSet<uint>(Game.Running().Select(p => (uint)p.Id));
        if (pids.Count == 0) return IntPtr.Zero;
        var w = N.WindowsOf(pids.Contains, t => t.Length > 0);
        return w.Count > 0 ? w[0] : IntPtr.Zero;
    }

    void MoveGame(IntPtr h, bool activate) {
        var scr = gameScreen == "tv" ? Tv() : Desk();
        if (scr == null) return;
        if (N.Rect(h) != scr.Bounds) { N.Place(h, scr.Bounds, activate); Cfg.Log("moved game to " + gameScreen + " " + scr.Bounds); }
        else if (activate) N.SetForegroundWindow(h);
    }

    // Steam's window focused and black over the game or TV app -> focus that instead (BlankSteam).
    DateTime blankSince = DateTime.MinValue;
    void RetakeFromBlankSteam(Screen tv) {
        var game = GameWindow();
        bool onTv = game != IntPtr.Zero && gameScreen == "tv";
        IntPtr target = onTv ? game : TvAppWindow();
        string what = onTv ? "game" : "TV app";
        bool running = target != IntPtr.Zero;
        bool front = running && SteamInFront() && FocusIsOnTv();
        bool dark = front && BlankSteam.Dark(BlankSteam.Sample(tv.Bounds));
        if (!dark) { blankSince = DateTime.MinValue; return; }
        if (blankSince == DateTime.MinValue) blankSince = DateTime.Now;
        if (!BlankSteam.Plan(running, front, dark, (DateTime.Now - blankSince).TotalSeconds)) return;
        blankSince = DateTime.MinValue;
        if (N.Focus(target)) Cfg.Log("Steam's window was black over the " + what + " -> focused the " + what);
        else Cfg.Warn("Steam's window is black over the " + what + " -> Windows refused to focus the " + what);
    }

    void Tick() {
        DeskPrimary.Ensure();  // before anything is placed: the TV as primary display means "no TV"
        var tv = Tv();
        tvBounds = tv == null ? Rectangle.Empty : tv.Bounds;

        // --- the game ---
        var procs = Game.Running();
        if (procs.Length == 0) {
            // Big Picture often comes back black (and with the wrong controller layout) after a
            // game closes, so restart it a few seconds after the game exits - unless Kodi or
            // YouTube is open on the TV: Big Picture would come up over it, and its tile's
            // "Resume" cannot bring it back. That app's own close restarts Big Picture.
            if (gamePid != -1) {
                Cfg.Log("game process " + gamePid + " gone");
                if (TvAppWindow() == IntPtr.Zero) { bpRestartAt = DateTime.Now.AddSeconds(3); Cfg.Log("game closed -> restarting Big Picture"); }
                else Cfg.Log("game closed -> Big Picture left alone, a TV app is open");
            }
            gamePid = -1; gameHwnd = IntPtr.Zero;
            if (bpRestartAt != DateTime.MinValue && DateTime.Now >= bpRestartAt) {
                bpRestartAt = DateTime.MinValue;
                Steam("close/bigpicture");
                bpReopenAt = DateTime.Now.AddSeconds(4);
            }
            if (bpReopenAt != DateTime.MinValue && DateTime.Now >= bpReopenAt) {
                bpReopenAt = DateTime.MinValue; lastBpOpen = DateTime.Now; focusBpUntil = DateTime.Now.AddSeconds(15);
                Steam("open/bigpicture");
            }
        }
        else {
            var fi = new FileInfo(Cfg.ModeFile);
            if (procs[0].Id != gamePid) {
                // New game session: use the launcher's choice if it is recent, else it was
                // started outside Steam, at the desk -> desk display.
                gamePid = procs[0].Id;
                // (Comparing to the process start time also keeps the right screen if this
                // helper is restarted mid-session.)
                string t = "desk";
                DateTime started = DateTime.Now;
                try { started = procs[0].StartTime; } catch { }
                if (fi.Exists && fi.LastWriteTime >= started.AddMinutes(-6)) t = File.ReadAllText(Cfg.ModeFile).Trim();
                modeFileSeen = fi.Exists ? fi.LastWriteTime : DateTime.MinValue;
                SetTarget(t, false);
            } else if (fi.Exists && fi.LastWriteTime != modeFileSeen) {
                // Launched again from Steam/desktop while already running: just move it.
                modeFileSeen = fi.LastWriteTime;
                SetTarget(File.ReadAllText(Cfg.ModeFile).Trim(), true);
            }
            var h = GameWindow();
            gameHwnd = h;
            if (h != IntPtr.Zero && !N.IsIconic(h)) MoveGame(h, false);
        }

        // --- Big Picture ---
        if (tv == null) return;
        RetakeFromBlankSteam(tv);
        KeepTvApps(tv);
        SendDeskWindowsHome(tv);
        var web = new HashSet<uint>(Process.GetProcessesByName("steamwebhelper").Select(p => (uint)p.Id));
        var bp = N.WindowsOf(web.Contains, t => t == "Steam Big Picture Mode");
        bool steamUp = Process.GetProcessesByName("steam").Length > 0;
        if (steamUp != steamSeen) { steamSeen = steamUp; Cfg.Log("Steam " + (steamUp ? "is running" : "is not running")); }
        if ((bp.Count > 0) != bpSeen) {
            bpSeen = bp.Count > 0;
            Cfg.Log("Big Picture window " + (bpSeen ? "present" : "gone") + (Game.Running().Length > 0 ? " (game running)" : ""));
        }
        if (bp.Count > 0) {
            bpMissingSince = DateTime.MinValue;
            foreach (var h in bp)
                if (N.Rect(h) != tv.Bounds) { N.Place(h, tv.Bounds, false); Cfg.Log("moved Big Picture to TV " + tv.Bounds); }
            bool tvAppRunning = TvAppWindow() != IntPtr.Zero || Game.Running().Length > 0;
            if (tvAppRunning) focusBpUntil = DateTime.MinValue;   // never pull Big Picture over a running game/TV
            // Not while the desk has focus: the keyboard there would follow it to the TV.
            if (focusBpUntil > DateTime.Now && N.GetForegroundWindow() != bp[0] && !FocusIsOnDesk()) {
                focusBpUntil = DateTime.MinValue;
                if (N.Focus(bp[0])) Cfg.Log("focused restarted Big Picture");
                else Cfg.Warn("Windows refused to focus restarted Big Picture (focus stays with " + Describe(N.GetForegroundWindow()) + ")");
            }
        } else if (TvAppWindow() != IntPtr.Zero || Game.Running().Length > 0) {
            // Steam hides Big Picture while a game runs - that's not "closed", so don't reopen it
            // (a reopened Big Picture would cover the game with a black screen).
            bpMissingSince = DateTime.MinValue;
        } else if (keepBp && steamUp) {
            if (bpMissingSince == DateTime.MinValue) bpMissingSince = DateTime.Now;
            if ((DateTime.Now - bpMissingSince).TotalSeconds > 10 && (DateTime.Now - lastBpOpen).TotalSeconds > 60) {
                lastBpOpen = DateTime.Now;
                Cfg.Log("Big Picture not open -> opening");
                Steam("open/bigpicture");
            }
        }
    }
    bool bpSeen = false, steamSeen = false;

    static void Steam(string command) {
        Cfg.Log("steam://" + command);
        try { Process.Start(new ProcessStartInfo("steam://" + command) { UseShellExecute = true }); }
        catch (Exception ex) { Cfg.Error("steam://" + command + " failed: " + ex.Message); }
    }
}
