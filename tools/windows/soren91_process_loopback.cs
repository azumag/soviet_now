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
// Usage:
//   soren91_process_loopback.exe --pid <chrome pid> [--parent-pid <pid>]
//       [--expect-image chrome.exe] [--duration-sec N]
// stderr (one line each):
//   SOREN91_LOOPBACK_READY={"pid":..,"rate":48000,"channels":2,"format":"s16le"}
//   SOREN91_LOOPBACK_END={"reason":"..."}
// Exit codes: 0 normal end, 2 bad arguments / target mismatch, 3 activation
// failure, 4 capture failure.
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
            string expectImage = null;
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                string v = i + 1 < args.Length ? args[i + 1] : null;
                if (a == "--pid" && v != null) { int.TryParse(v, out pid); i++; }
                else if (a == "--parent-pid" && v != null) { int.TryParse(v, out parentPid); i++; }
                else if (a == "--duration-sec" && v != null) { int.TryParse(v, out durationSec); i++; }
                else if (a == "--expect-image" && v != null) { expectImage = v; i++; }
                else return Fail(2, "unknown argument " + a);
            }
            if (pid <= 0) return Fail(2, "--pid is required");
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

                Status("SOREN91_LOOPBACK_READY", "{\"pid\":" + pid + ",\"rate\":" + Rate + ",\"channels\":" + Channels + ",\"format\":\"s16le\"}");
                return Pump(client, capture, ev, pid, parentPid, durationSec);
            }
            finally
            {
                Marshal.FreeHGlobal(paramsPtr);
                Marshal.FreeHGlobal(propPtr);
                Marshal.FreeHGlobal(fmtPtr);
            }
        }

        static int Pump(IAudioClient client, IAudioCaptureClient capture, AutoResetEvent ev, int pid, int parentPid, int durationSec)
        {
            var output = Console.OpenStandardOutput();
            var clock = Stopwatch.StartNew();
            long framesWritten = 0;
            long lastLiveness = 0;
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
                    if (durationSec > 0 && ms >= durationSec * 1000L) return Fail(0, "duration reached");
                }
            }
            catch (IOException) { return Fail(0, "stdout closed"); }
            finally { try { client.Stop(); } catch { } }
        }
    }
}
