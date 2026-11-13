// moonlight-secure-screen.cs
//
// The "Moonlight Pi secure screens" Windows service. The moonlight-pi-setup PC
// setup (moonlight-pc-setup.ps1) compiles it with the C# compiler that comes
// with Windows (.NET Framework 4), installs it in Program Files, and Windows
// runs it from the moment it starts, so it also works on the sign-in screen
// after a restart, before anyone has signed in.
//
// It tells your Moonlight Pi when Windows shows its lock screen, its sign-in
// screen or a UAC prompt. While it does, and the Pi is streaming to this PC,
// the Pi adds an Xbox copy of each controller that isn't an Xbox controller
// (Windows only lets Xbox controllers use those screens), and removes the
// copies when the screen goes away.
//
// Windows shows those screens with two programs: LogonUI.exe (lock and sign-in
// screens) and consent.exe (UAC prompts). Windows tells this service when one
// starts and when it ends, so it doesn't keep looking for them (it does look
// every 15 seconds too, in case Windows missed telling it).
//
// It sends a few bytes over UDP to the Pi (port 3241): at once when a screen
// comes up or goes away, every second while one is up, and every 5 seconds
// otherwise (so the Pi's menu can show this PC is set up). It sends them to
// each Pi it knows, and as a broadcast on the home network, which also finds a
// Pi whose address has changed. It never listens for connections, so nothing
// changes in the Windows firewall.
//
// Its settings (the Pis it knows) are in
// %ProgramData%\moonlight-pi-setup\secure-screen.conf, and it keeps a short log
// of what changed in secure-screen.log there.
//
// For testing: moonlight-secure-screen.exe --console [--data FOLDER], from an
// administrator prompt (otherwise Windows won't tell it about those screens).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace MoonlightPiSetup
{
    public class SecureScreenAgent : ServiceBase
    {
        public const string Name = "MoonlightPiSecureScreens";
        const int Port = 3241;
        const int UpEveryMs = 1000;         // messages while a screen is up
        const int IdleEveryMs = 5000;       // and otherwise
        const int CheckEveryMs = 15000;     // looks for the programs itself
        const int HelloEveryMs = 60000;     // asks which Pis are listening (their addresses)
        const long LogMax = 256 * 1024;

        [DllImport("kernel32.dll")]
        static extern uint WTSGetActiveConsoleSessionId();

        [DllImport("kernel32.dll")]
        static extern bool ProcessIdToSessionId(uint processId, out uint sessionId);

        class Pi
        {
            public string Name = "", Address = "", Tailscale = "";
        }

        class Tracked
        {
            public Process Process;
            public uint Session;
        }

        readonly string dataDir, configPath, logPath;
        readonly AutoResetEvent wake = new AutoResetEvent(false);
        readonly Dictionary<int, Tracked> tracked = new Dictionary<int, Tracked>();
        readonly object gate = new object();
        volatile bool stopping;
        volatile bool watcherStopped;
        bool watchFailed;
        bool console;
        Thread worker;
        ManagementEventWatcher watcher;
        UdpClient udp;
        List<Pi> pis = new List<Pi>();

        public SecureScreenAgent(string dataDir)
        {
            ServiceName = Name;
            CanStop = true;
            CanShutdown = true;
            CanHandleSessionChangeEvent = true;
            this.dataDir = dataDir;
            configPath = Path.Combine(dataDir, "secure-screen.conf");
            logPath = Path.Combine(dataDir, "secure-screen.log");
        }

        static int Main(string[] args)
        {
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                                      "moonlight-pi-setup");
            bool console = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--console") console = true;
                else if (args[i] == "--data" && i + 1 < args.Length) dir = args[++i];
            }
            var agent = new SecureScreenAgent(dir);
            if (!console)
            {
                ServiceBase.Run(agent);
                return 0;
            }
            agent.console = true;
            Console.CancelKeyPress += (s, e) => { e.Cancel = true; agent.stopping = true; agent.wake.Set(); };
            agent.Run();
            return 0;
        }

        protected override void OnStart(string[] args)
        {
            worker = new Thread(Run);
            worker.IsBackground = true;
            worker.Name = "secure-screens";
            worker.Start();
        }

        protected override void OnStop()
        {
            stopping = true;
            wake.Set();
            if (worker != null) worker.Join(5000);
        }

        protected override void OnShutdown()
        {
            OnStop();
        }

        // Locking, unlocking, signing in and out: look again straight away.
        protected override void OnSessionChange(SessionChangeDescription change)
        {
            wake.Set();
        }

        // -------------------------------------------------------------------
        // Log and settings
        // -------------------------------------------------------------------
        void Log(string text)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text;
            if (console) Console.WriteLine(line);
            try
            {
                File.AppendAllText(logPath, line + Environment.NewLine);
                if (new FileInfo(logPath).Length > LogMax)
                {
                    var lines = File.ReadAllLines(logPath);
                    File.WriteAllLines(logPath, lines.Skip(Math.Max(0, lines.Length - 1000)).ToArray());
                }
            }
            catch (Exception) { }
        }

        static readonly Regex NameRe = new Regex("^[A-Za-z0-9._-]{1,63}$");
        static readonly Regex Ipv4Re = new Regex(@"^\d{1,3}(\.\d{1,3}){3}$");

        // One Pi per line: name, home address, Tailscale address (or -).
        List<Pi> ReadPis()
        {
            var list = new List<Pi>();
            try
            {
                foreach (string raw in File.ReadAllLines(configPath))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    string[] w = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                    if (w.Length < 2 || !NameRe.IsMatch(w[0]) || !Ipv4Re.IsMatch(w[1])) continue;
                    var pi = new Pi { Name = w[0], Address = w[1] };
                    if (w.Length > 2 && Ipv4Re.IsMatch(w[2])) pi.Tailscale = w[2];
                    list.Add(pi);
                }
            }
            catch (Exception) { }
            return list;
        }

        void SavePis()
        {
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("# The Moonlight Pis this PC tells about its sign-in and UAC screens:");
                sb.AppendLine("# name, home address, Tailscale address (- if none). Kept up to date by the service.");
                foreach (var pi in pis)
                    sb.AppendLine(pi.Name + " " + pi.Address + " " + (pi.Tailscale.Length > 0 ? pi.Tailscale : "-"));
                string tmp = configPath + ".tmp";
                File.WriteAllText(tmp, sb.ToString());
                if (File.Exists(configPath)) File.Replace(tmp, configPath, null);
                else File.Move(tmp, configPath);
            }
            catch (Exception e)
            {
                Log("Couldn't save the Pis' addresses: " + e.Message);
            }
        }

        // -------------------------------------------------------------------
        // Is a sign-in, lock or UAC screen up? One of the programs that show
        // them, running in the session on the PC's screen (the one streamed).
        // -------------------------------------------------------------------
        bool StartWatching()
        {
            try
            {
                // (WqlEventQuery only accepts SELECT *.)
                watcher = new ManagementEventWatcher(new WqlEventQuery(
                    "SELECT * FROM Win32_ProcessStartTrace " +
                    "WHERE ProcessName='LogonUI.exe' OR ProcessName='consent.exe'"));
                watcher.EventArrived += (s, e) =>
                {
                    try { Track(Convert.ToInt32(e.NewEvent["ProcessID"])); }
                    catch (Exception) { }
                    wake.Set();
                };
                watcher.Stopped += (s, e) => { watcherStopped = true; wake.Set(); };
                watcherStopped = false;
                watcher.Start();
                if (watchFailed) Log("Windows says when programs start again");
                watchFailed = false;
                return true;
            }
            catch (Exception e)
            {
                if (watcher != null) watcher.Dispose();
                watcher = null;
                if (!watchFailed)
                    Log("Windows can't say when programs start (" + e.Message.Trim() + "), so it looks for them every second");
                watchFailed = true;
                return false;
            }
        }

        // Tracks one of the programs. Windows says when it ends, so the Pi hears at once.
        void Track(int pid)
        {
            uint session;
            lock (gate)
            {
                if (tracked.ContainsKey(pid)) return;
            }
            if (!ProcessIdToSessionId((uint)pid, out session)) return;
            Process p = null;
            try
            {
                p = Process.GetProcessById(pid);
                p.EnableRaisingEvents = true;
                p.Exited += (s, e) => wake.Set();
            }
            catch (Exception)
            {
                if (p != null) p.Dispose();
                return;
            }
            lock (gate)
            {
                if (tracked.ContainsKey(pid)) p.Dispose();
                else tracked[pid] = new Tracked { Process = p, Session = session };
            }
        }

        // Looks for the programs (slower: it lists every process).
        void FindPrograms()
        {
            foreach (var p in Process.GetProcesses())
            {
                string name = "";
                try { name = p.ProcessName; } catch (Exception) { }
                if (name.Equals("LogonUI", StringComparison.OrdinalIgnoreCase) ||
                    name.Equals("consent", StringComparison.OrdinalIgnoreCase))
                    Track(p.Id);
                p.Dispose();
            }
        }

        bool SecureScreenUp()
        {
            uint console = WTSGetActiveConsoleSessionId();
            // While Windows switches the screen to another session (switching
            // user), no session has it for a moment: any sign-in screen counts.
            bool switching = console == 0xFFFFFFFF;
            bool up = false;
            lock (gate)
            {
                foreach (int pid in tracked.Keys.ToList())
                {
                    var t = tracked[pid];
                    bool gone = true;
                    try { gone = t.Process.HasExited; } catch (Exception) { }
                    if (gone)
                    {
                        t.Process.Dispose();
                        tracked.Remove(pid);
                    }
                    else if (t.Session == console || switching)
                    {
                        up = true;
                    }
                }
            }
            return up;
        }

        // -------------------------------------------------------------------
        // Telling the Pi
        // -------------------------------------------------------------------
        // The home network's broadcast addresses: networks with a default
        // gateway (so not virtual ones), and not Tailscale.
        static List<string> BroadcastAddresses()
        {
            var list = new List<string>();
            try
            {
                foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (nic.OperationalStatus != OperationalStatus.Up || nic.Description.Contains("Tailscale")) continue;
                    var props = nic.GetIPProperties();
                    if (!props.GatewayAddresses.Any(g => g.Address.AddressFamily == AddressFamily.InterNetwork &&
                                                         !g.Address.Equals(IPAddress.Any))) continue;
                    foreach (var u in props.UnicastAddresses)
                    {
                        if (u.Address.AddressFamily != AddressFamily.InterNetwork || u.IPv4Mask == null) continue;
                        byte[] ip = u.Address.GetAddressBytes(), mask = u.IPv4Mask.GetAddressBytes();
                        var b = new byte[4];
                        for (int i = 0; i < 4; i++) b[i] = (byte)(ip[i] | (mask[i] ^ 255));
                        string s = new IPAddress(b).ToString();
                        if (!list.Contains(s)) list.Add(s);
                    }
                }
            }
            catch (Exception) { }
            return list;
        }

        void Send(string text, IEnumerable<string> addresses)
        {
            byte[] bytes = Encoding.ASCII.GetBytes(text);
            foreach (string a in addresses)
            {
                try { udp.Send(bytes, bytes.Length, new IPEndPoint(IPAddress.Parse(a), Port)); }
                catch (Exception) { }
            }
        }

        static readonly Regex AnswerRe = new Regex(
            @"^moonlight-pi-setup pi 1(?: name=(?<name>[A-Za-z0-9._-]{1,63}))?(?: tailscale=(?<ts>100\.[0-9.]{3,11}))?\s*$");

        // Reads the Pis' answers to "hello", and remembers any new Pi or address.
        // Returns whether anything changed.
        bool ReadAnswers()
        {
            bool changed = false;
            try
            {
                while (udp.Available > 0)
                {
                    var from = new IPEndPoint(IPAddress.Any, 0);
                    byte[] data = udp.Receive(ref from);
                    var m = AnswerRe.Match(Encoding.ASCII.GetString(data));
                    if (!m.Success || from.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                    string address = from.Address.ToString(), name = m.Groups["name"].Value, ts = m.Groups["ts"].Value;
                    var pi = pis.FirstOrDefault(p => p.Address == address || p.Tailscale == address ||
                                                     (name.Length > 0 && p.Name == name));
                    if (pi == null)
                    {
                        pis.Add(new Pi { Name = name.Length > 0 ? name : address, Address = address, Tailscale = ts });
                        Log("Found " + (name.Length > 0 ? name : "a Pi") + " at " + address);
                        changed = true;
                        continue;
                    }
                    if (address != pi.Tailscale && address != pi.Address)
                    {
                        Log(pi.Name + " is now at " + address + " (was " + pi.Address + ")");
                        pi.Address = address;
                        changed = true;
                    }
                    if (ts.Length > 0 && ts != pi.Tailscale)
                    {
                        pi.Tailscale = ts;
                        changed = true;
                    }
                }
            }
            catch (Exception) { }
            if (changed) SavePis();
            return changed;
        }

        // -------------------------------------------------------------------
        void Run()
        {
            try { Directory.CreateDirectory(dataDir); } catch (Exception) { }
            Log("Started");
            string message = "moonlight-pi-setup secure-screen 1 secure={0} host=" +
                             Regex.Replace(Environment.MachineName, "[^A-Za-z0-9._-]", "");
            udp = new UdpClient(0);
            udp.EnableBroadcast = true;
            // Otherwise Windows makes the next read fail when an address sent to
            // has nothing listening (SIO_UDP_CONNRESET off).
            try { udp.Client.IOControl(unchecked((int)0x9800000C), new byte[4], null); } catch (Exception) { }

            bool watching = StartWatching();
            bool? secure = null;                // what the Pi was last told
            DateTime never = DateTime.MinValue;
            DateTime lastSent = never, lastCheck = never, lastHello = never, lastNetwork = never, lastWatch = DateTime.UtcNow;
            var broadcast = new List<string>();
            List<string> targets = null;
            while (!stopping)
            {
                var now = DateTime.UtcNow;
                if (watching && watcherStopped)
                {
                    watching = false;
                    Log("Windows stopped saying when programs start: looking for them every second");
                }
                if (!watching && (now - lastWatch).TotalSeconds >= 60)
                {
                    lastWatch = now;
                    if (watcher != null) watcher.Dispose();
                    watching = StartWatching();
                }
                if (!watching || (now - lastCheck).TotalMilliseconds >= CheckEveryMs)
                {
                    lastCheck = now;
                    FindPrograms();
                }
                bool up = SecureScreenUp();

                if ((now - lastNetwork).TotalSeconds >= 60)
                {
                    lastNetwork = now;
                    broadcast = BroadcastAddresses();
                    pis = ReadPis();
                    targets = null;
                }
                if (ReadAnswers()) targets = null;
                if (targets == null)
                    targets = pis.SelectMany(p => new[] { p.Address, p.Tailscale }).Where(a => a.Length > 0)
                                 .Concat(broadcast).Distinct().ToList();
                if ((now - lastHello).TotalMilliseconds >= HelloEveryMs)
                {
                    lastHello = now;
                    Send("moonlight-pi-setup hello 1", targets);
                }

                if (up != secure || (now - lastSent).TotalMilliseconds >= (up ? UpEveryMs : IdleEveryMs) - 100)
                {
                    if (up != secure && (secure != null || up))
                        Log(up ? "Sign-in or UAC screen up" : "Sign-in or UAC screen gone");
                    secure = up;
                    lastSent = now;
                    Send(string.Format(message, up ? 1 : 0), targets);
                }
                // Wait for a program to start or end, or for the next message to be due.
                wake.WaitOne(up || !watching ? UpEveryMs : IdleEveryMs);
            }
            if (secure == true)
                Send(string.Format(message, 0), targets);
            if (watcher != null)
            {
                try { watcher.Stop(); } catch (Exception) { }
                watcher.Dispose();
            }
            udp.Close();
            Log("Stopped");
        }
    }
}
