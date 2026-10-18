// Soren91 Windows window audit helper (no pixels are ever read).
//
// Collects the process tree rooted at --root-pid (Toolhelp32 snapshot) and
// lists every VISIBLE top-level window owned by a process in that tree via
// EnumWindows + IsWindowVisible. The cdp-host runs Chrome with
// --headless=new, so the expected answer is zero windows; any visible window
// is a privacy violation the host must fail closed on.
//
// Output (stdout, one JSON line; window titles are deliberately omitted):
//   {"rootPid":123,"treePids":[123,456],"visible":[{"pid":456,"className":"Chrome_WidgetWin_1",
//     "rect":[l,t,r,b],"cloaked":false,"minimized":false}]}
// With --all-chrome it also reports the number of visible top-level windows
// owned by ANY chrome.exe (used by the E2E check that the operator's own
// Chrome is untouched), again without titles or pixels.
// Exit codes: 0 audited, 2 bad arguments / root not running.
//
// Build: tools/soren91_windows_helpers_build.ps1.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace Soren91
{
    static class WindowAudit
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct PROCESSENTRY32
        {
            public uint dwSize; public uint cntUsage; public uint th32ProcessID; public IntPtr th32DefaultHeapID;
            public uint th32ModuleID; public uint cntThreads; public uint th32ParentProcessID; public int pcPriClassBase;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
        }
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
        delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32FirstW(IntPtr snap, ref PROCESSENTRY32 entry);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32NextW(IntPtr snap, ref PROCESSENTRY32 entry);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hWnd);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassNameW(IntPtr hWnd, StringBuilder sb, int max);
        [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr hWnd, int attr, out int value, int size);

        const uint TH32CS_SNAPPROCESS = 0x2;
        const int DWMWA_CLOAKED = 14;

        static List<PROCESSENTRY32> Snapshot()
        {
            var list = new List<PROCESSENTRY32>();
            IntPtr snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if (snap == IntPtr.Zero || snap == new IntPtr(-1)) return list;
            try
            {
                var e = new PROCESSENTRY32 { dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32)) };
                if (Process32FirstW(snap, ref e))
                {
                    do { list.Add(e); e.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32)); } while (Process32NextW(snap, ref e));
                }
            }
            finally { CloseHandle(snap); }
            return list;
        }

        static string Esc(string s) { return (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\""); }

        static int Main(string[] args)
        {
            int root = 0; bool allChrome = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--root-pid" && i + 1 < args.Length) { int.TryParse(args[++i], out root); }
                else if (args[i] == "--all-chrome") allChrome = true;
                else { Console.Error.WriteLine("unknown argument " + args[i]); return 2; }
            }
            if (root <= 0) { Console.Error.WriteLine("--root-pid is required"); return 2; }
            var procs = Snapshot();
            var tree = new HashSet<uint>();
            bool rootAlive = false;
            foreach (var p in procs) if (p.th32ProcessID == (uint)root) rootAlive = true;
            if (!rootAlive) { Console.Error.WriteLine("root pid not running"); return 2; }
            tree.Add((uint)root);
            // Fixed-point expansion over parent PIDs. Windows can reuse a dead
            // parent's PID, which may over-include an unrelated process; that
            // only ever adds windows to the report (fail-closed), never hides one.
            bool grew = true;
            while (grew)
            {
                grew = false;
                foreach (var p in procs)
                {
                    if (!tree.Contains(p.th32ProcessID) && tree.Contains(p.th32ParentProcessID) && p.th32ProcessID != p.th32ParentProcessID)
                    { tree.Add(p.th32ProcessID); grew = true; }
                }
            }
            var chromePids = new HashSet<uint>();
            foreach (var p in procs) if (string.Equals(p.szExeFile, "chrome.exe", StringComparison.OrdinalIgnoreCase)) chromePids.Add(p.th32ProcessID);

            var visible = new StringBuilder();
            int visibleCount = 0, otherChromeVisible = 0;
            EnumWindows((h, l) =>
            {
                if (!IsWindowVisible(h)) return true;
                uint pid; GetWindowThreadProcessId(h, out pid);
                if (tree.Contains(pid))
                {
                    RECT r; GetWindowRect(h, out r);
                    var cls = new StringBuilder(256); GetClassNameW(h, cls, 256);
                    int cloaked = 0; DwmGetWindowAttribute(h, DWMWA_CLOAKED, out cloaked, 4);
                    if (visibleCount > 0) visible.Append(',');
                    visible.Append("{\"pid\":" + pid + ",\"className\":\"" + Esc(cls.ToString()) + "\",\"rect\":[" + r.Left + "," + r.Top + "," + r.Right + "," + r.Bottom + "],\"cloaked\":" + (cloaked != 0 ? "true" : "false") + ",\"minimized\":" + (IsIconic(h) ? "true" : "false") + "}");
                    visibleCount++;
                }
                else if (allChrome && chromePids.Contains(pid)) otherChromeVisible++;
                return true;
            }, IntPtr.Zero);

            var sb = new StringBuilder();
            sb.Append("{\"rootPid\":" + root + ",\"treePids\":[");
            bool first = true;
            foreach (var pid in tree) { if (!first) sb.Append(','); sb.Append(pid); first = false; }
            sb.Append("],\"visible\":[" + visible + "]");
            if (allChrome) sb.Append(",\"otherChromeVisibleWindows\":" + otherChromeVisible);
            sb.Append("}");
            Console.WriteLine(sb.ToString());
            return 0;
        }
    }
}
