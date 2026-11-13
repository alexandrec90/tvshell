// TvShell - keeps Steam Big Picture on the TV (secondary display) and the desk display an
// ordinary desktop. One exe, several modes:
//   TvShell.exe              -> background helper (tray icon, hotkey Ctrl+Alt+T)
//   TvShell.exe kodi <what>  -> Steam tile: Kodi on the TV (tv, radio, cbcradio, gem)
//   TvShell.exe youtube      -> Steam tile: YouTube's TV interface on the TV
//   TvShell.exe launch       -> Steam tile: the game from game.cfg, started through its
//                               store launcher so Steam Input (controller) attaches. On the TV.
//   TvShell.exe launch-desk  -> desktop shortcut: same, but the game goes to the desk display.
// The game is optional and named only in game.cfg beside the exe, never in this source.
// Nothing here touches any program's memory or sends inputs; it only moves windows.
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
    public static readonly string YouTubePidFile = Path.Combine(Dir, "youtube.pid");
    public const string KodiExe = @"C:\Program Files\Kodi\kodi.exe";
    public const string ChromeExe = @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe";
    public static string SteamGameId = "";  // filled from steam-gameid.txt next to the exe
    public static void Log(string m) {
        try {
            Directory.CreateDirectory(Dir);
            var fi = new FileInfo(LogFile);
            if (fi.Exists && fi.Length > 512 * 1024) File.Delete(LogFile);
            File.AppendAllText(LogFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + m + Environment.NewLine);
        } catch { }
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

static class N {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
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
    [DllImport("user32.dll")] public static extern bool RegisterRawInputDevices(RAWINPUTDEVICE[] d, uint n, uint size);
    [DllImport("user32.dll")] public static extern uint GetRawInputData(IntPtr h, uint cmd, IntPtr data, ref uint size, uint hdr);
    [StructLayout(LayoutKind.Sequential)] public struct RAWINPUTDEVICE { public ushort UsagePage, Usage; public uint Flags; public IntPtr Target; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }

    // Give focus to a window even though we are a background process.
    public static void Focus(IntPtr h) {
        var fg = GetForegroundWindow();
        if (fg == h) return;
        uint fgThread = GetWindowThreadProcessId(fg, IntPtr.Zero), me = GetCurrentThreadId();
        bool att = fgThread != 0 && fgThread != me && AttachThreadInput(me, fgThread, true);
        BringWindowToTop(h); SetForegroundWindow(h);
        if (att) AttachThreadInput(me, fgThread, false);
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
        try {
            var gid = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "steam-gameid.txt");
            if (File.Exists(gid)) Cfg.SteamGameId = File.ReadAllText(gid).Trim();
        } catch { }
        Game.Load(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "game.cfg"));
        string mode = args.Length > 0 ? args[0].ToLowerInvariant() : "";
        if (mode == "launch") return Launcher.Run();
        if (mode == "kodi") return KodiLauncher.Run(args.Length > 1 ? args[1].ToLowerInvariant() : "tv");
        if (mode == "youtube") return YouTubeLauncher.Run();
        if (mode == "launch-desk") return Launcher.ViaSteam("desk");
        if (mode == "launch-tv") return Launcher.ViaSteam("tv");
        bool created;
        using (var mtx = new Mutex(true, @"Local\TvShellHelper", out created)) {
            if (!created) return 0;
            Application.EnableVisualStyles();
            Application.Run(new Helper());
        }
        return 0;
    }
}

static class Launcher {
    // Desktop shortcut path: leave a note for the Steam-launched instance, then ask Steam to run it.
    public static int ViaSteam(string target) {
        Directory.CreateDirectory(Cfg.Dir);
        File.WriteAllText(Cfg.NextFile, target);
        if (Cfg.SteamGameId == "") { Cfg.Log("launch-" + target + ": no steam-gameid.txt"); return 1; }
        Process.Start(new ProcessStartInfo("steam://rungameid/" + Cfg.SteamGameId) { UseShellExecute = true });
        return 0;
    }

    // Steam shortcut target. Steam hooks this process and follows it into the store launcher and
    // from there into the game, which is what gives the game Steam's virtual Xbox controller.
    public static int Run() {
        if (!Game.Configured || !Game.HasLauncher) { Cfg.Log("launch: game.cfg needs process and launcher"); return 1; }
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
            return 0;
        }
        // The store launcher must be (re)started from here, otherwise the game is spawned by an
        // instance Steam is not watching and the controller won't work.
        if (Game.LauncherProcess.Length > 0) {
            var running = Process.GetProcessesByName(Game.LauncherProcess);
            if (running.Length > 0) {
                Cfg.Log("launch: closing running store launcher (" + running.Length + " procs)");
                foreach (var p in running) { try { p.Kill(); } catch { } }
                for (int i = 0; i < 50 && Process.GetProcessesByName(Game.LauncherProcess).Length > 0; i++) Thread.Sleep(200);
            }
        }
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
        if (game == null) { Cfg.Log("launch: game never started"); return 2; }
        Cfg.Log("launch: game pid " + game.Id + " started");
        try { game.WaitForExit(); } catch { }
        Cfg.Log("launch: game exited");
        return 0;
    }

    static void StartLauncher() {
        try {
            Process.Start(new ProcessStartInfo(Game.Launcher, Game.LaunchArgs) {
                UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(Game.Launcher) });
        } catch (Exception ex) { Cfg.Log("launch: cannot start store launcher: " + ex.Message); }
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
            kodi = Process.Start(new ProcessStartInfo(Cfg.KodiExe) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(Cfg.KodiExe) });
            started = true;
        }
        Cfg.Log("kodi launcher: " + target + (started ? " (starting Kodi)" : " (Kodi already running)"));
        if (!WaitForKodi(60)) { Cfg.Log("kodi launcher: Kodi never became ready"); return 1; }
        if (target == "tv") {
            // Resume the last-watched channel. PVR channels load a few seconds after start, so retry.
            for (int i = 0; i < 15 && !IsPlaying(); i++) {
                var ch = LastChannel();
                if (ch > 0) { Rpc("Player.Open", "{\"item\":{\"channelid\":" + ch + "}}"); Cfg.Log("kodi launcher: playing channel " + ch); Thread.Sleep(6000); }
                else Thread.Sleep(2000);
            }
        } else if (Sections.ContainsKey(target)) {
            Builtin("PlayerControl(Stop)"); Thread.Sleep(500);
            Builtin(Sections[target]);
        }
        if (started) { try { kodi.WaitForExit(); } catch { } }
        return 0;
    }
    static bool WaitForKodi(int seconds) {
        for (int i = 0; i < seconds * 2; i++) {
            try { using (var c = new System.Net.Sockets.TcpClient("127.0.0.1", 9090)) { Thread.Sleep(3000); return true; } }
            catch { Thread.Sleep(500); }
        }
        return false;
    }
    static bool IsPlaying() { return Rpc("Player.GetActivePlayers", "{}").Contains("playerid"); }

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
    static string Rpc(string method, string prms) {
        try {
            using (var c = new System.Net.Sockets.TcpClient("127.0.0.1", 9090)) {
                var s = c.GetStream(); s.ReadTimeout = 5000;
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
        } catch { return ""; }
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

// Steam tile "YouTube": YouTube's TV interface in a separate Chrome profile, kiosk mode, on the TV.
static class YouTubeLauncher {
    public static int Run() {
        var tv = Screen.AllScreens.FirstOrDefault(s => !s.Primary);
        var b = tv != null ? tv.Bounds : Screen.PrimaryScreen.Bounds;
        var profile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "yt-profile");
        // YouTube only serves its TV ("leanback") interface to TV devices, so identify as one.
        const string ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Cobalt/25.lts.30.1034943-gold (unlike Gecko), Unknown_TV_Unknown_0/Unknown (Unknown, Unknown)";
        var args = string.Format("--user-data-dir=\"{0}\" --kiosk --window-position={1},{2} --window-size={3},{4} --user-agent=\"{5}\" " +
            "--no-first-run --no-default-browser-check --autoplay-policy=no-user-gesture-required --disable-features=Translate https://www.youtube.com/tv",
            profile, b.X, b.Y, b.Width, b.Height, ua);
        Process.Start(new ProcessStartInfo(Cfg.ChromeExe, args) { UseShellExecute = false });
        // chrome.exe hands off to a new browser process and exits; find the real browser process
        // (the one using our profile without a --type= switch) and wait on that.
        Process browser = null;
        for (int i = 0; i < 40 && browser == null; i++) { Thread.Sleep(500); browser = FindBrowser(profile); }
        if (browser == null) { Cfg.Log("youtube launcher: browser process not found"); return 1; }
        Directory.CreateDirectory(Cfg.Dir); File.WriteAllText(Cfg.YouTubePidFile, browser.Id.ToString());
        Cfg.Log("youtube launcher: chrome browser pid " + browser.Id);
        try { browser.WaitForExit(); } catch { }
        try { File.Delete(Cfg.YouTubePidFile); } catch { }
        Cfg.Log("youtube launcher: closed");
        return 0;
    }
    static Process FindBrowser(string profile) {
        try {
            using (var q = new System.Management.ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='chrome.exe'"))
                foreach (System.Management.ManagementObject o in q.Get()) {
                    var cmd = (o["CommandLine"] as string) ?? "";
                    if (cmd.IndexOf(profile, StringComparison.OrdinalIgnoreCase) >= 0 && cmd.IndexOf("--type=", StringComparison.Ordinal) < 0)
                        return Process.GetProcessById(Convert.ToInt32(o["ProcessId"]));
                }
        } catch { }
        return null;
    }
}

class Helper : Form {
    NotifyIcon tray;
    System.Windows.Forms.Timer timer;
    ToolStripMenuItem miTv, miDesk, miBp, miMouse;
    bool keepBp = true, lockMouse = true;
    System.Windows.Forms.Timer mouseTimer;
    int reclips = 0; DateTime reclipLogged = DateTime.Now;
    byte[] padButtons = null; DateTime lastPadFocus = DateTime.MinValue;
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
        if (Game.Configured) menu.Items.AddRange(new ToolStripItem[] { miTv, miDesk, new ToolStripSeparator() });
        menu.Items.AddRange(new ToolStripItem[] { miBp, miMouse,
            new ToolStripMenuItem("Open log", null, (s, e) => { try { Process.Start("notepad.exe", Cfg.LogFile); } catch { } }),
            new ToolStripSeparator(), new ToolStripMenuItem("Exit", null, (s, e) => Close()) });
        Icon ico = SystemIcons.Application;
        try { if (Game.Icon.Length > 0) ico = Icon.ExtractAssociatedIcon(Game.Icon); } catch { }
        var tip = Game.Configured ? "TvShell (Ctrl+Alt+T: move the game)" : "TvShell";
        tray = new NotifyIcon { Icon = ico, Text = tip, ContextMenuStrip = menu, Visible = true };
        CreateHandle(); // the form is never shown, so create its window explicitly for the hotkey
        timer = new System.Windows.Forms.Timer { Interval = 1000 };
        timer.Tick += (s, e) => { try { Tick(); } catch (Exception ex) { Cfg.Log("tick error: " + ex.Message); } };
        timer.Start();
        mouseTimer = new System.Windows.Forms.Timer { Interval = 100 };
        mouseTimer.Tick += (s, e) => { try { KeepMouseOnDesk(); } catch { } };
        mouseTimer.Start();
        Cfg.Log("helper started");
        EnsurePlutoRealAds();
    }

    // The SlyGuy Pluto add-on asks for streams without server-side ads, which makes Pluto fill
    // every ad break with its logo. Ask for real ads instead; re-applied after add-on updates.
    static void EnsurePlutoRealAds() {
        try {
            var f = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Kodi\addons\slyguy.pluto.tv.provider\resources\lib\plugin.py");
            if (!File.Exists(f)) return;
            var t = File.ReadAllText(f);
            if (t.Contains("'serverSideAds': 'false'")) {
                File.WriteAllText(f, t.Replace("'serverSideAds': 'false'", "'serverSideAds': 'true'"));
                Cfg.Log("Pluto add-on: re-enabled real ads");
            }
        } catch (Exception ex) { Cfg.Log("Pluto ads patch failed: " + ex.Message); }
        // SlyGuy's bandwidth cap only accepts whole Mbit/s; allow decimals (cap is 0.7 = Pluto's lowest stream).
        try {
            var g = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Kodi\addons\script.module.slyguy\resources\modules\slyguy\gui.py");
            if (!File.Exists(g)) return;
            var t = File.ReadAllText(g);
            const string from = "'max_bandwidth': settings.getInt('max_bandwidth', 0),";
            const string to = "'max_bandwidth': float(settings.get('max_bandwidth', 0) or 0),";
            if (t.Contains(from)) { File.WriteAllText(g, t.Replace(from, to)); Cfg.Log("SlyGuy: re-enabled decimal bandwidth cap"); }
        } catch (Exception ex) { Cfg.Log("SlyGuy bandwidth patch failed: " + ex.Message); }
        // CBC's server stalls requests that identify as Python, so the CBC Radio add-on hangs forever.
        // Make it identify as a browser (and give up after 20 s instead of never).
        try {
            var c = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Kodi\addons\plugin.audio.cbcradio\now_playing.py");
            if (!File.Exists(c)) return;
            var t = File.ReadAllText(c);
            const string from = "response = urllib.request.urlopen(url)";
            const string to = "response = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20)";
            if (t.Contains(from)) { File.WriteAllText(c, t.Replace(from, to)); Cfg.Log("CBC Radio: re-applied browser identity fix"); }
        } catch (Exception ex) { Cfg.Log("CBC Radio patch failed: " + ex.Message); }
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

    // Controller press -> focus whatever is on the TV, because Steam only sends the controller
    // to the focused window.
    bool padSeen = false;
    void OnPadReport(byte[] rep) {
        if (!padSeen) { padSeen = true; Cfg.Log("first controller report: id 0x" + rep[0].ToString("X2") + ", " + rep.Length + " bytes"); }
        byte[] btn = null;
        if (rep.Length >= 12 && rep[0] == 0x30) btn = new[] { rep[3], rep[4], rep[5] };      // Switch Pro full report
        else if (rep.Length >= 4 && rep[0] == 0x3F) btn = new[] { rep[1], rep[2], rep[3] };  // Switch Pro simple report
        if (btn == null) return;
        btn[1] &= unchecked((byte)~0x30);   // ignore Home (0x10) and Capture (0x20): Home opens Steam's menu
        bool pressed = false;
        if (padButtons != null && padButtons.Length == btn.Length)
            for (int i = 0; i < btn.Length; i++) if ((btn[i] & ~padButtons[i]) != 0) pressed = true;
        padButtons = btn;
        // A deliberate stick push also counts (12-bit sticks, centre ~2048). The threshold is far
        // beyond stick drift so a resting, drifting stick never steals focus.
        if (!pressed && rep[0] == 0x30) {
            int lx = rep[6] | ((rep[7] & 0xF) << 8), ly = (rep[7] >> 4) | (rep[8] << 4);
            int rx = rep[9] | ((rep[10] & 0xF) << 8), ry = (rep[10] >> 4) | (rep[11] << 4);
            if (Math.Abs(lx - 2048) > 900 || Math.Abs(ly - 2048) > 900 || Math.Abs(rx - 2048) > 900 || Math.Abs(ry - 2048) > 900) pressed = true;
        }
        if (!pressed || (DateTime.Now - lastPadFocus).TotalSeconds < 1.5) return;
        if (SteamInFront() || FocusIsOnTv()) return;   // already using something on the TV - leave it alone
        lastPadFocus = DateTime.Now;
        IntPtr target = IntPtr.Zero;
        var game = GameWindow();
        var kodi = TvAppWindow();
        if (game != IntPtr.Zero && gameScreen == "tv") target = game;
        else if (kodi != IntPtr.Zero) target = kodi;
        else if (game != IntPtr.Zero) return;   // the game is on the desk display: leave focus alone
        else { var bp = BigPictureWindows(); if (bp.Count > 0) target = bp[0]; }
        if (target != IntPtr.Zero && N.GetForegroundWindow() != target) {
            N.Focus(target);
            Cfg.Log("controller press -> focused " + (target == game ? "game" : target == kodi ? "TV app" : "Big Picture"));
        }
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
        if (!N.RegisterHotKey(Handle, HOTKEY, 0x1 | 0x2 | 0x4000 /*Alt|Ctrl|NoRepeat*/, (uint)Keys.T)) Cfg.Log("hotkey Ctrl+Alt+T unavailable");
        // Gamepads + joysticks, delivered even when we are in the background (RIDEV_INPUTSINK).
        var devs = new[] {
            new N.RAWINPUTDEVICE { UsagePage = 1, Usage = 5, Flags = 0x100, Target = Handle },
            new N.RAWINPUTDEVICE { UsagePage = 1, Usage = 4, Flags = 0x100, Target = Handle } };
        if (!N.RegisterRawInputDevices(devs, 2, (uint)Marshal.SizeOf(typeof(N.RAWINPUTDEVICE)))) Cfg.Log("raw input registration failed");
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
            } catch (Exception ex) { Cfg.Log("raw input error: " + ex.Message); }
        }
        base.WndProc(ref m);
    }
    protected override void OnFormClosed(FormClosedEventArgs e) {
        N.UnregisterHotKey(Handle, HOTKEY);
        var all = new N.RECT { L = -32000, T = -32000, R = 32000, B = 32000 }; N.ClipCursor(ref all);
        tray.Visible = false; tray.Dispose(); base.OnFormClosed(e);
        Application.Exit();
    }

    static Screen Tv() { return Screen.AllScreens.FirstOrDefault(s => !s.Primary); }
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

    // YouTube runs in its own Chrome (separate profile, kiosk); the launcher records its PID.
    static IntPtr YouTubeWindow() {
        try {
            if (!File.Exists(Cfg.YouTubePidFile)) return IntPtr.Zero;
            uint pid = uint.Parse(File.ReadAllText(Cfg.YouTubePidFile).Trim());
            var w = N.WindowsOf(p => p == pid, t => t.Length > 0);
            return w.Count > 0 ? w[0] : IntPtr.Zero;
        } catch { return IntPtr.Zero; }
    }
    static IntPtr TvAppWindow() { var k = KodiWindow(); return k != IntPtr.Zero ? k : YouTubeWindow(); }

    static bool FocusIsOnDesk() {
        var r = N.Rect(N.GetForegroundWindow());
        return Desk().Bounds.Contains(new Point(r.X + r.Width / 2, r.Y + r.Height / 2));
    }

    // Apps that live on the TV (Kodi, YouTube): keep them there, focus them when they start, never let
    // Big Picture cover them, and bring Big Picture back when they close.
    class TvApp { public string Name; public Func<IntPtr> Find; public bool Borderless; public bool Running, FocusPending; }
    readonly List<TvApp> tvApps = new List<TvApp> {
        // Kodi runs windowed (its own monitor switching is unreliable): strip the border and make its
        // drawing area exactly cover the TV, so it renders full-screen there.
        new TvApp { Name = "Kodi", Find = KodiWindow, Borderless = true },
        new TvApp { Name = "YouTube", Find = YouTubeWindow, Borderless = false },
    };
    void KeepTvApps(Screen tv) { foreach (var a in tvApps) KeepTvApp(a, tv); }
    void KeepTvApp(TvApp a, Screen tv) {
        var h = a.Find();
        if (h == IntPtr.Zero) {
            if (a.Running) {
                a.Running = false;
                if (TvAppWindow() == IntPtr.Zero) { bpRestartAt = DateTime.Now.AddSeconds(3); Cfg.Log(a.Name + " closed -> restarting Big Picture"); }
            }
            return;
        }
        if (!a.Running) {
            a.Running = true; focusBpUntil = DateTime.MinValue; bpRestartAt = DateTime.MinValue; bpReopenAt = DateTime.MinValue;
            a.FocusPending = true; Cfg.Log(a.Name + " started");
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
            if (!FocusIsOnDesk()) { N.Focus(h); Cfg.Log("focused newly started " + a.Name); }
        }
        // Nothing (e.g. Big Picture) may sit on top of it on the TV - except Steam's menu while you use it.
        var centre = new N.POINT { X = tv.Bounds.X + tv.Bounds.Width / 2, Y = tv.Bounds.Y + tv.Bounds.Height / 2 };
        if (N.GetAncestor(N.WindowFromPoint(centre), 2) != h && !SteamInFront() && !FocusIsOnTv()) {
            N.SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 /*NOSIZE|NOMOVE|NOACTIVATE, HWND_TOP*/);
            if (N.GetAncestor(N.WindowFromPoint(centre), 2) != h) N.Focus(h);
            Cfg.Log(a.Name + " was covered -> brought to front");
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

    void Tick() {
        var tv = Tv();

        // --- the game ---
        var procs = Game.Running();
        if (procs.Length == 0) {
            // Big Picture often comes back black (and with the wrong controller layout) after a
            // game closes, so restart it a few seconds after the game exits.
            if (gamePid != -1) { bpRestartAt = DateTime.Now.AddSeconds(3); Cfg.Log("game closed -> restarting Big Picture"); }
            gamePid = -1; gameHwnd = IntPtr.Zero;
            if (bpRestartAt != DateTime.MinValue && DateTime.Now >= bpRestartAt) {
                bpRestartAt = DateTime.MinValue;
                Process.Start(new ProcessStartInfo("steam://close/bigpicture") { UseShellExecute = true });
                bpReopenAt = DateTime.Now.AddSeconds(4);
            }
            if (bpReopenAt != DateTime.MinValue && DateTime.Now >= bpReopenAt) {
                bpReopenAt = DateTime.MinValue; lastBpOpen = DateTime.Now; focusBpUntil = DateTime.Now.AddSeconds(15);
                Process.Start(new ProcessStartInfo("steam://open/bigpicture") { UseShellExecute = true });
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
        KeepTvApps(tv);
        var web = new HashSet<uint>(Process.GetProcessesByName("steamwebhelper").Select(p => (uint)p.Id));
        var bp = N.WindowsOf(web.Contains, t => t == "Steam Big Picture Mode");
        if (bp.Count > 0) {
            bpMissingSince = DateTime.MinValue;
            foreach (var h in bp)
                if (N.Rect(h) != tv.Bounds) { N.Place(h, tv.Bounds, false); Cfg.Log("moved Big Picture to TV " + tv.Bounds); }
            bool tvAppRunning = TvAppWindow() != IntPtr.Zero || Game.Running().Length > 0;
            if (tvAppRunning) focusBpUntil = DateTime.MinValue;   // never pull Big Picture over a running game/TV
            if (focusBpUntil > DateTime.Now && N.GetForegroundWindow() != bp[0]) {
                N.Focus(bp[0]); focusBpUntil = DateTime.MinValue; Cfg.Log("focused restarted Big Picture");
            }
        } else if (TvAppWindow() != IntPtr.Zero || Game.Running().Length > 0) {
            // Steam hides Big Picture while a game runs - that's not "closed", so don't reopen it
            // (a reopened Big Picture would cover the game with a black screen).
            bpMissingSince = DateTime.MinValue;
        } else if (keepBp && Process.GetProcessesByName("steam").Length > 0) {
            if (bpMissingSince == DateTime.MinValue) bpMissingSince = DateTime.Now;
            if ((DateTime.Now - bpMissingSince).TotalSeconds > 10 && (DateTime.Now - lastBpOpen).TotalSeconds > 60) {
                lastBpOpen = DateTime.Now;
                Cfg.Log("Big Picture not open -> opening");
                Process.Start(new ProcessStartInfo("steam://open/bigpicture") { UseShellExecute = true });
            }
        }
    }
}
