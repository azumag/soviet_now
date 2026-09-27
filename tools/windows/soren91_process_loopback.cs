// Soren91 Windows process-scoped audio loopback helper.
//
// Captures ONLY the audio rendered by one process tree (the Chrome that the
// cdp-host spawned itself) through the Windows 10 2004+ ApplicationLoopback
// API (VAD\Process_Loopback + PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE)
// and writes s16le / 48000 Hz / stereo PCM to stdout. System audio, the
// operator's everyday Chrome and every other process are never captured.
//
// The output is paced to wall clock: when the target renders nothing (Chrome
// emits no packets while silent) the helper pads silence so the downstream
// ffmpeg audio timeline keeps moving in real time.
//
// --sink-endpoint "<render endpoint friendly name>" --sink-allowed-dir <dir>
// keeps the game off this PC's speakers. Muting the source is not an option:
// process loopback captures after the session mute/volume, so a muted Chrome
// is captured as silence. Instead the target's per-app default render
// endpoint (the Settings "App volume and device preferences" entry) is set
// to an endpoint nobody listens to, e.g. an unused virtual cable. Process
// loopback captures the process regardless of the endpoint it renders to.
// Windows persists that preference by executable path, so it is only set
// when the target executable lives under <dir> (a dedicated Chrome for
// Testing), never for the operator's Chrome. Every 250ms an active session of
// the target tree on another endpoint is routed; one still active there 2s
// after routing fails the helper closed. The preference sticks to the
// executable path, so later sessions start on the sink directly.
//
// Usage:
//   soren91_process_loopback.exe --pid <chrome pid> [--parent-pid <pid>]
//       [--expect-image chrome.exe] [--duration-sec N]
//       [--sink-endpoint <name> --sink-allowed-dir <dir>]
// stderr (one line each):
//   SOREN91_LOOPBACK_SINK={"endpoint":"<name>"}
//   SOREN91_LOOPBACK_READY={"pid":..,"rate":48000,"channels":2,"format":"s16le","sink":..}
//   SOREN91_LOOPBACK_END={"reason":"..."}
// Exit codes: 0 normal end, 2 bad arguments / target mismatch, 3 activation
// or sink routing failure, 4 capture failure / audio leaking to another endpoint.
//
// Build (no SDK needed; csc ships with .NET Framework 4.x on Windows 10):
//   tools/soren91_windows_helpers_build.ps1
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace Soren91
{
    [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioClient
    {
        [PreserveSig] int Initialize(int shareMode, uint streamFlags, long hnsBufferDuration, long hnsPeriodicity, IntPtr pFormat, IntPtr audioSessionGuid);
        [PreserveSig] int GetBufferSize(out uint numBufferFrames);
        [PreserveSig] int GetStreamLatency(out long latency);
        [PreserveSig] int GetCurrentPadding(out uint numPaddingFrames);
        [PreserveSig] int IsFormatSupported(int shareMode, IntPtr pFormat, out IntPtr closestMatch);
        [PreserveSig] int GetMixFormat(out IntPtr deviceFormat);
        [PreserveSig] int GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
        [PreserveSig] int Start();
        [PreserveSig] int Stop();
        [PreserveSig] int Reset();
        [PreserveSig] int SetEventHandle(IntPtr eventHandle);
        [PreserveSig] int GetService([MarshalAs(UnmanagedType.LPStruct)] Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
    }

    [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioCaptureClient
    {
        [PreserveSig] int GetBuffer(out IntPtr data, out uint numFramesToRead, out uint flags, out ulong devicePosition, out ulong qpcPosition);
        [PreserveSig] int ReleaseBuffer(uint numFramesRead);
        [PreserveSig] int GetNextPacketSize(out uint numFramesInNextPacket);
    }

    [ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceAsyncOperation
    {
        void GetActivateResult([MarshalAs(UnmanagedType.Error)] out int activateResult, [MarshalAs(UnmanagedType.IUnknown)] out object activatedInterface);
    }

    [ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceCompletionHandler
    {
        void ActivateCompleted(IActivateAudioInterfaceAsyncOperation activateOperation);
    }

    // Marker interface: ActivateAudioInterfaceAsync rejects a completion
    // handler that does not answer QueryInterface(IAgileObject) with
    // E_ILLEGAL_METHOD_CALL (0x8000000E).
    [ComImport, Guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAgileObject { }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorCom { }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
    }

    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceCollection
    {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int Item(uint index, out IMMDevice device);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        [PreserveSig] int Activate([MarshalAs(UnmanagedType.LPStruct)] Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROPERTYKEY { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr ptr; }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int GetAt(uint index, out PROPERTYKEY key);
        [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT value);
    }

    [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioSessionManager2
    {
        [PreserveSig] int GetAudioSessionControl(IntPtr sessionGuid, uint flags, out IntPtr control);
        [PreserveSig] int GetSimpleAudioVolume(IntPtr sessionGuid, uint flags, out IntPtr volume);
        [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessions);
    }

    [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioSessionEnumerator
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetSession(int index, [MarshalAs(UnmanagedType.IUnknown)] out object session);
    }

    [ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioSessionControl2
    {
        [PreserveSig] int GetState(out int state);
        [PreserveSig] int GetDisplayName(out IntPtr name);
        [PreserveSig] int SetDisplayName(IntPtr name, IntPtr eventContext);
        [PreserveSig] int GetIconPath(out IntPtr path);
        [PreserveSig] int SetIconPath(IntPtr path, IntPtr eventContext);
        [PreserveSig] int GetGroupingParam(out Guid grouping);
        [PreserveSig] int SetGroupingParam(IntPtr grouping, IntPtr eventContext);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr client);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr client);
        [PreserveSig] int GetSessionIdentifier(out IntPtr id);
        [PreserveSig] int GetSessionInstanceIdentifier(out IntPtr id);
        [PreserveSig] int GetProcessId(out uint pid);
    }

    // Routes one process tree's audio to a render endpoint nobody listens to
    // (per-app default endpoint), and reports sessions that leak elsewhere.
    static class SinkRouter
    {
        const int eRender = 0;
        const int eConsole = 0, eMultimedia = 1;
        const int DEVICE_STATE_ACTIVE = 1;
        const int CLSCTX_ALL = 23;
        const int AudioSessionStateActive = 1;
        const uint TH32CS_SNAPPROCESS = 0x2;
        const string MMDEVAPI_PREFIX = "\\\\?\\SWD#MMDEVAPI#";
        const string DEVINTERFACE_AUDIO_RENDER = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}";
        static readonly Guid IID_IAudioSessionManager2 = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
        static readonly PROPERTYKEY PKEY_Device_FriendlyName = new PROPERTYKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
        // Windows.Media.Internal.AudioPolicyConfig (undocumented; the same
        // interface the Settings app and EarTrumpet use). The IID changed in
        // build 21390; the vtable layout did not: IUnknown(3) + IInspectable(3)
        // + 19 other methods, then Set/GetPersistedDefaultAudioEndpoint.
        static readonly Guid IID_AudioPolicyConfigFactoryDownlevel = new Guid("2a59116d-6c4f-45e0-a74f-707e3fef9258");
        static readonly Guid IID_AudioPolicyConfigFactory21H2 = new Guid("ab3d4648-e242-459f-b02f-541c70306324");
        const int SlotSetPersisted = 25, SlotGetPersisted = 26;

        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SetPersistedFn(IntPtr self, uint pid, int flow, int role, IntPtr deviceId);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int GetPersistedFn(IntPtr self, uint pid, int flow, int role, out IntPtr deviceId);

        [DllImport("combase.dll")] static extern int RoGetActivationFactory(IntPtr classId, [In] ref Guid iid, out IntPtr factory);
        [DllImport("combase.dll", CharSet = CharSet.Unicode)] static extern int WindowsCreateString(string s, int length, out IntPtr hstring);
        [DllImport("combase.dll")] static extern int WindowsDeleteString(IntPtr hstring);
        [DllImport("combase.dll", CharSet = CharSet.Unicode)] static extern IntPtr WindowsGetStringRawBuffer(IntPtr hstring, out uint length);
        [DllImport("ole32.dll")] static extern int PropVariantClear(ref PROPVARIANT pv);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct PROCESSENTRY32W
        {
            public uint dwSize, cntUsage, th32ProcessID;
            public IntPtr th32DefaultHeapID;
            public uint th32ModuleID, cntThreads, th32ParentProcessID;
            public int pcPriClassBase;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
        }

        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32FirstW(IntPtr snap, ref PROCESSENTRY32W entry);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32NextW(IntPtr snap, ref PROCESSENTRY32W entry);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        public static bool UnderDir(string file, string dir)
        {
            if (file == null) return false;
            string full = Path.GetFullPath(file);
            string root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            return full.StartsWith(root, StringComparison.OrdinalIgnoreCase);
        }

        static IMMDevice[] ActiveRenderEndpoints()
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDeviceCollection devices;
            int hr = enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, out devices);
            if (hr != 0) throw new Exception("EnumAudioEndpoints hr=0x" + hr.ToString("X8"));
            uint count;
            devices.GetCount(out count);
            var list = new System.Collections.Generic.List<IMMDevice>();
            for (uint d = 0; d < count; d++)
            {
                IMMDevice device;
                if (devices.Item(d, out device) == 0) list.Add(device);
            }
            return list.ToArray();
        }

        static string FriendlyName(IMMDevice device)
        {
            IPropertyStore store;
            if (device.OpenPropertyStore(0, out store) != 0) return null;
            var key = PKEY_Device_FriendlyName;
            PROPVARIANT value;
            if (store.GetValue(ref key, out value) != 0) return null;
            try { return value.vt == 31 ? Marshal.PtrToStringUni(value.ptr) : null; } // VT_LPWSTR
            finally { PropVariantClear(ref value); }
        }

        // Endpoint id ("{0.0.0.00000000}.{guid}") of the one active render
        // endpoint with this exact friendly name.
        public static string ResolveEndpoint(string friendlyName)
        {
            string found = null;
            foreach (var device in ActiveRenderEndpoints())
            {
                if (!string.Equals(FriendlyName(device), friendlyName, StringComparison.Ordinal)) continue;
                if (found != null) throw new Exception("more than one render endpoint is named " + friendlyName);
                string id;
                if (device.GetId(out id) != 0) throw new Exception("IMMDevice.GetId failed");
                found = id;
            }
            if (found == null) throw new Exception("no active render endpoint named " + friendlyName);
            return found;
        }

        static IntPtr PolicyFactory()
        {
            IntPtr classId;
            const string name = "Windows.Media.Internal.AudioPolicyConfig";
            int hr = WindowsCreateString(name, name.Length, out classId);
            if (hr != 0) throw new Exception("WindowsCreateString hr=0x" + hr.ToString("X8"));
            try
            {
                IntPtr factory;
                Guid iid = Environment.OSVersion.Version.Build >= 21390 ? IID_AudioPolicyConfigFactory21H2 : IID_AudioPolicyConfigFactoryDownlevel;
                hr = RoGetActivationFactory(classId, ref iid, out factory);
                if (hr != 0) throw new Exception("RoGetActivationFactory(AudioPolicyConfig) hr=0x" + hr.ToString("X8"));
                return factory;
            }
            finally { WindowsDeleteString(classId); }
        }

        static T Slot<T>(IntPtr iface, int slot)
        {
            IntPtr vtable = Marshal.ReadIntPtr(iface);
            return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(vtable, slot * IntPtr.Size), typeof(T));
        }

        // Sets the per-app default render endpoint (console + multimedia) of
        // the executable behind pid and reads it back. pid must own an audio
        // session (Chrome's audio service process, not the browser process;
        // the latter is rejected with E_INVALIDARG). Open streams move over.
        static void Route(uint pid, string endpointId)
        {
            IntPtr factory = PolicyFactory();
            try
            {
                var set = Slot<SetPersistedFn>(factory, SlotSetPersisted);
                var get = Slot<GetPersistedFn>(factory, SlotGetPersisted);
                string full = MMDEVAPI_PREFIX + endpointId + DEVINTERFACE_AUDIO_RENDER;
                IntPtr hs;
                int hr = WindowsCreateString(full, full.Length, out hs);
                if (hr != 0) throw new Exception("WindowsCreateString hr=0x" + hr.ToString("X8"));
                try
                {
                    foreach (int role in new[] { eConsole, eMultimedia })
                    {
                        hr = set(factory, pid, eRender, role, hs);
                        if (hr != 0) throw new Exception("SetPersistedDefaultAudioEndpoint hr=0x" + hr.ToString("X8"));
                        IntPtr back;
                        hr = get(factory, pid, eRender, role, out back);
                        if (hr != 0) throw new Exception("GetPersistedDefaultAudioEndpoint hr=0x" + hr.ToString("X8"));
                        uint len;
                        string actual = back == IntPtr.Zero ? "" : Marshal.PtrToStringUni(WindowsGetStringRawBuffer(back, out len), (int)len);
                        WindowsDeleteString(back);
                        if (actual.IndexOf(endpointId, StringComparison.OrdinalIgnoreCase) < 0)
                            throw new Exception("per-app endpoint did not stick (role " + role + ")");
                    }
                }
                finally { WindowsDeleteString(hs); }
            }
            finally { Marshal.Release(factory); }
        }

        // Descendants of root (inclusive). Parent PIDs can be recycled, so a
        // child only counts when it was created after its parent.
        static System.Collections.Generic.HashSet<uint> Tree(uint root)
        {
            var parents = new System.Collections.Generic.Dictionary<uint, uint>();
            IntPtr snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if (snap == IntPtr.Zero || snap == new IntPtr(-1)) return new System.Collections.Generic.HashSet<uint> { root };
            try
            {
                var e = new PROCESSENTRY32W { dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32W)) };
                for (bool ok = Process32FirstW(snap, ref e); ok; ok = Process32NextW(snap, ref e))
                    parents[e.th32ProcessID] = e.th32ParentProcessID;
            }
            finally { CloseHandle(snap); }
            var tree = new System.Collections.Generic.HashSet<uint> { root };
            for (bool grew = true; grew;)
            {
                grew = false;
                foreach (var kv in parents)
                    if (!tree.Contains(kv.Key) && tree.Contains(kv.Value) && StartedAfter(kv.Key, kv.Value)) { tree.Add(kv.Key); grew = true; }
            }
            return tree;
        }

        static bool StartedAfter(uint child, uint parent)
        {
            try
            {
                using (var c = Process.GetProcessById((int)child))
                using (var p = Process.GetProcessById((int)parent))
                    return c.StartTime >= p.StartTime;
            }
            catch { return false; }
        }

        // Routes every session of the tree found on another endpoint to
        // endpointId. Returns the friendly name of an endpoint where a session
        // is still actively rendering graceMs after its process was routed
        // (the game is audible on this PC), or null.
        public static string Enforce(uint root, string endpointId, System.Collections.Generic.Dictionary<uint, long> routedAt, long nowMs, long graceMs)
        {
            var tree = Tree(root);
            foreach (var device in ActiveRenderEndpoints())
            {
                string id;
                if (device.GetId(out id) != 0 || string.Equals(id, endpointId, StringComparison.OrdinalIgnoreCase)) continue;
                object mgrObj;
                if (device.Activate(IID_IAudioSessionManager2, CLSCTX_ALL, IntPtr.Zero, out mgrObj) != 0) continue;
                IAudioSessionEnumerator sessions;
                if (((IAudioSessionManager2)mgrObj).GetSessionEnumerator(out sessions) != 0) continue;
                int n;
                sessions.GetCount(out n);
                for (int i = 0; i < n; i++)
                {
                    object s;
                    if (sessions.GetSession(i, out s) != 0) continue;
                    var control = (IAudioSessionControl2)s;
                    uint pid; int state;
                    if (control.GetProcessId(out pid) != 0 || !tree.Contains(pid)) continue;
                    if (control.GetState(out state) != 0 || state != AudioSessionStateActive) continue;
                    long since;
                    if (!routedAt.TryGetValue(pid, out since))
                    {
                        Route(pid, endpointId);
                        routedAt[pid] = nowMs;
                    }
                    else if (nowMs - since > graceMs) return FriendlyName(device) ?? id;
                }
            }
            return null;
        }
    }

    [ComVisible(true)]
    sealed class ActivationHandler : IActivateAudioInterfaceCompletionHandler, IAgileObject
    {
        public readonly ManualResetEvent Done = new ManualResetEvent(false);
        public int Result = -1;
        public object Client;
        public void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op)
        {
            try
            {
                int hr; object iface;
                op.GetActivateResult(out hr, out iface);
                Result = hr;
                Client = iface;
            }
            catch (Exception e) { Result = Marshal.GetHRForException(e); }
            finally { Done.Set(); }
        }
    }

    static class Program
    {
        const int AUDCLNT_SHAREMODE_SHARED = 0;
        const uint AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
        const uint AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000;
        const uint AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000;
        const uint AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000;
        const uint AUDCLNT_BUFFERFLAGS_SILENT = 0x2;
        const int AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1;
        const int PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0;
        const ushort VT_BLOB = 65;
        const string VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback";
        const int Rate = 48000;
        const int Channels = 2;
        const int BlockAlign = 4; // s16le stereo

        [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
        static extern void ActivateAudioInterfaceAsync(
            [MarshalAs(UnmanagedType.LPWStr)] string deviceInterfacePath,
            [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
            IntPtr activationParams,
            IActivateAudioInterfaceCompletionHandler completionHandler,
            out IActivateAudioInterfaceAsyncOperation activationOperation);

        static readonly Guid IID_IAudioClient = new Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2");
        static readonly Guid IID_IAudioCaptureClient = new Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317");

        static void Status(string key, string json)
        {
            try { Console.Error.WriteLine(key + "=" + json); Console.Error.Flush(); } catch { }
        }

        static int Fail(int code, string reason)
        {
            Status("SOREN91_LOOPBACK_END", "{\"reason\":\"" + reason.Replace("\\", "\\\\").Replace("\"", "'") + "\",\"code\":" + code + "}");
            return code;
        }

        static bool Alive(int pid)
        {
            try { using (var p = Process.GetProcessById(pid)) return !p.HasExited; }
            catch { return false; }
        }

        [MTAThread]
        static int Main(string[] args)
        {
            int pid = 0, parentPid = 0, durationSec = 0;
            string expectImage = null, sinkName = null, sinkDir = null;
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                string v = i + 1 < args.Length ? args[i + 1] : null;
                if (a == "--pid" && v != null) { int.TryParse(v, out pid); i++; }
                else if (a == "--parent-pid" && v != null) { int.TryParse(v, out parentPid); i++; }
                else if (a == "--duration-sec" && v != null) { int.TryParse(v, out durationSec); i++; }
                else if (a == "--expect-image" && v != null) { expectImage = v; i++; }
                else if (a == "--sink-endpoint" && v != null) { sinkName = v; i++; }
                else if (a == "--sink-allowed-dir" && v != null) { sinkDir = v; i++; }
                else return Fail(2, "unknown argument " + a);
            }
            if (pid <= 0) return Fail(2, "--pid is required");
            if ((sinkName == null) != (sinkDir == null)) return Fail(2, "--sink-endpoint and --sink-allowed-dir go together");
            string sinkId = null;
            if (sinkName != null)
            {
                string image = null;
                try { using (var p = Process.GetProcessById(pid)) image = p.MainModule.FileName; }
                catch (Exception e) { return Fail(2, "cannot inspect target pid: " + e.Message); }
                // The preference persists for the executable path: never set it
                // for anything but the dedicated Chrome.
                if (!SinkRouter.UnderDir(image, sinkDir))
                    return Fail(2, "refusing to route: target is not under --sink-allowed-dir");
                try { sinkId = SinkRouter.ResolveEndpoint(sinkName); }
                catch (Exception e) { return Fail(3, "sink endpoint: " + e.Message); }
                Status("SOREN91_LOOPBACK_SINK", "{\"endpoint\":\"" + sinkName.Replace("\\", "\\\\").Replace("\"", "'") + "\"}");
            }
            if (expectImage != null)
            {
                // Fail closed unless the target is the expected executable.
                string actual = null;
                try { using (var p = Process.GetProcessById(pid)) actual = Path.GetFileName(p.MainModule.FileName); }
                catch (Exception e) { return Fail(2, "cannot inspect target pid: " + e.Message); }
                if (!string.Equals(actual, expectImage, StringComparison.OrdinalIgnoreCase))
                    return Fail(2, "target image mismatch: " + actual);
            }

            IntPtr paramsPtr = Marshal.AllocHGlobal(12);
            IntPtr propPtr = Marshal.AllocHGlobal(24);
            IntPtr fmtPtr = Marshal.AllocHGlobal(18);
            try
            {
                Marshal.WriteInt32(paramsPtr, 0, AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK);
                Marshal.WriteInt32(paramsPtr, 4, pid);
                Marshal.WriteInt32(paramsPtr, 8, PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE);
                for (int o = 0; o < 24; o += 4) Marshal.WriteInt32(propPtr, o, 0);
                Marshal.WriteInt16(propPtr, 0, (short)VT_BLOB);
                Marshal.WriteInt32(propPtr, 8, 12);
                Marshal.WriteIntPtr(propPtr, 16, paramsPtr);

                var handler = new ActivationHandler();
                IActivateAudioInterfaceAsyncOperation op;
                try { ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, IID_IAudioClient, propPtr, handler, out op); }
                catch (Exception e) { return Fail(3, "ActivateAudioInterfaceAsync failed: " + e.Message); }
                if (!handler.Done.WaitOne(10000)) return Fail(3, "activation timed out");
                if (handler.Result != 0 || handler.Client == null) return Fail(3, "activation hr=0x" + handler.Result.ToString("X8"));
                var client = (IAudioClient)handler.Client;

                Marshal.WriteInt16(fmtPtr, 0, 1);            // WAVE_FORMAT_PCM
                Marshal.WriteInt16(fmtPtr, 2, (short)Channels);
                Marshal.WriteInt32(fmtPtr, 4, Rate);
                Marshal.WriteInt32(fmtPtr, 8, Rate * BlockAlign);
                Marshal.WriteInt16(fmtPtr, 12, (short)BlockAlign);
                Marshal.WriteInt16(fmtPtr, 14, 16);
                Marshal.WriteInt16(fmtPtr, 16, 0);
                int hr = client.Initialize(AUDCLNT_SHAREMODE_SHARED,
                    AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                    2000000, 0, fmtPtr, IntPtr.Zero);
                if (hr != 0) return Fail(3, "IAudioClient.Initialize hr=0x" + hr.ToString("X8"));
                var ev = new AutoResetEvent(false);
                hr = client.SetEventHandle(ev.SafeWaitHandle.DangerousGetHandle());
                if (hr != 0) return Fail(3, "SetEventHandle hr=0x" + hr.ToString("X8"));
                object svc;
                hr = client.GetService(IID_IAudioCaptureClient, out svc);
                if (hr != 0 || svc == null) return Fail(3, "GetService(IAudioCaptureClient) hr=0x" + hr.ToString("X8"));
                var capture = (IAudioCaptureClient)svc;
                hr = client.Start();
                if (hr != 0) return Fail(3, "Start hr=0x" + hr.ToString("X8"));

                Status("SOREN91_LOOPBACK_READY", "{\"pid\":" + pid + ",\"rate\":" + Rate + ",\"channels\":" + Channels + ",\"format\":\"s16le\",\"sink\":" + (sinkId != null ? "true" : "false") + "}");
                return Pump(client, capture, ev, pid, parentPid, durationSec, sinkId);
            }
            finally
            {
                Marshal.FreeHGlobal(paramsPtr);
                Marshal.FreeHGlobal(propPtr);
                Marshal.FreeHGlobal(fmtPtr);
            }
        }

        static volatile string SinkFailure;

        // Session enumeration and routing take tens of ms, longer than the
        // capture loop may stall without overflowing its buffer, so they run
        // on their own thread and only report a failure back to the pump.
        static void StartSinkWatch(uint pid, string sinkId, Stopwatch clock)
        {
            var thread = new Thread(() =>
            {
                var routedAt = new System.Collections.Generic.Dictionary<uint, long>();
                try
                {
                    for (;;)
                    {
                        string leak = SinkRouter.Enforce(pid, sinkId, routedAt, clock.ElapsedMilliseconds, 2000);
                        if (leak != null) { SinkFailure = "audio leaking to endpoint " + leak; return; }
                        Thread.Sleep(250);
                    }
                }
                catch (Exception e) { SinkFailure = "sink routing failed: " + e.Message; }
            });
            thread.IsBackground = true;
            thread.SetApartmentState(ApartmentState.MTA);
            thread.Start();
        }

        static int Pump(IAudioClient client, IAudioCaptureClient capture, AutoResetEvent ev, int pid, int parentPid, int durationSec, string sinkId)
        {
            var output = Console.OpenStandardOutput();
            var clock = Stopwatch.StartNew();
            long framesWritten = 0;
            long lastLiveness = 0;
            if (sinkId != null) StartSinkWatch((uint)pid, sinkId, clock);
            byte[] buffer = new byte[Rate * BlockAlign]; // 1s scratch
            // Keep up to 100ms of slack before padding silence, and pad only up
            // to 20ms behind wall clock so late real packets are not dropped.
            long padThreshold = Rate / 10;
            long padTarget = Rate / 50;
            try
            {
                for (;;)
                {
                    ev.WaitOne(10);
                    uint packet;
                    int hr = capture.GetNextPacketSize(out packet);
                    if (hr != 0) return Fail(4, "GetNextPacketSize hr=0x" + hr.ToString("X8"));
                    while (packet > 0)
                    {
                        IntPtr data; uint frames, flags; ulong devPos, qpcPos;
                        hr = capture.GetBuffer(out data, out frames, out flags, out devPos, out qpcPos);
                        if (hr != 0) return Fail(4, "GetBuffer hr=0x" + hr.ToString("X8"));
                        int bytes = (int)frames * BlockAlign;
                        if (bytes > buffer.Length) buffer = new byte[bytes];
                        if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0 || data == IntPtr.Zero) Array.Clear(buffer, 0, bytes);
                        else Marshal.Copy(data, buffer, 0, bytes);
                        capture.ReleaseBuffer(frames);
                        output.Write(buffer, 0, bytes);
                        framesWritten += frames;
                        hr = capture.GetNextPacketSize(out packet);
                        if (hr != 0) return Fail(4, "GetNextPacketSize hr=0x" + hr.ToString("X8"));
                    }
                    long expected = clock.ElapsedTicks * Rate / Stopwatch.Frequency;
                    if (expected - framesWritten > padThreshold)
                    {
                        long pad = expected - framesWritten - padTarget;
                        while (pad > 0)
                        {
                            int chunk = (int)Math.Min(pad, Rate);
                            Array.Clear(buffer, 0, chunk * BlockAlign);
                            output.Write(buffer, 0, chunk * BlockAlign);
                            framesWritten += chunk;
                            pad -= chunk;
                        }
                    }
                    output.Flush();
                    long ms = clock.ElapsedMilliseconds;
                    if (ms - lastLiveness >= 1000)
                    {
                        lastLiveness = ms;
                        if (!Alive(pid)) return Fail(0, "target exited");
                        if (parentPid > 0 && !Alive(parentPid)) return Fail(0, "parent exited");
                    }
                    string sinkFailure = SinkFailure;
                    if (sinkFailure != null) return Fail(4, sinkFailure);
                    if (durationSec > 0 && ms >= durationSec * 1000L) return Fail(0, "duration reached");
                }
            }
            catch (IOException) { return Fail(0, "stdout closed"); }
            finally { try { client.Stop(); } catch { } }
        }
    }
}
