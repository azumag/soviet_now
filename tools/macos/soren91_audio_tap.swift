// Soren91 macOS Chrome-scoped audio tap helper (Core Audio Process Tap).
//
// Captures ONLY the audio of explicitly listed PIDs — the descendant Chrome
// processes of the Playwright-driven automation Chrome (see
// tools/soren91_macos_audio.mjs `collectDescendantChromePids`). There is
// deliberately NO bundle-ID fallback: tapping `com.google.Chrome` as a whole
// would also capture (and, via CATapMutedWhenTapped, physically mute) the
// operator's everyday Chrome windows. Zero matching/translatable PIDs is a
// fail-closed error, never a widened tap.
//
// Pipeline: CATapDescription(stereoMixdownOfProcesses:) with
// muteBehavior=CATapMutedWhenTapped + privateTap -> AudioHardwareCreateProcessTap
// -> private aggregate device (tap list only) -> AudioDeviceCreateIOProcIDWithBlock
// -> float32 stereo frames converted to s16le 48kHz 2ch interleaved PCM on stdout.
//
// Protocol: on success, prints one JSON line to stderr
// (`{"ok":true,"tapUID":..., "format":..., "pids":[...],
// "muteBehaviorRequested":2,"muteBehaviorVerified":2}`) once the IOProc is
// running, then writes raw s16le frames to stdout until --seconds elapses,
// SIGINT/SIGTERM, or stdout closes (the downstream reader — ffmpeg's fd 3 —
// going away first surfaces as an EPIPE write failure, which tears down
// IOProc -> aggregate -> tap and exits 0, exactly like the window-capture
// helper's clean stop: a listener-first close is a normal end, not a crash).
// On failure, prints one JSON line to stderr
// (`{"ok":false,"error":"..."}`) and exits 1 — it never crashes, never
// writes partial PCM, and never taps anything but the given PIDs.
//
// NOTE (physical mute): CATapMutedWhenTapped mutes the tapped processes'
// physical output ONLY while this helper's IOProc keeps reading. Stopping
// (SIGTERM/SIGINT/--seconds expiry) tears down IOProc -> aggregate -> tap,
// which releases the mute. Objective physical-mute verification (listening)
// has NOT been performed; only the API-level muteBehavior round-trip is
// verified in code (muteBehaviorVerified must read back as 2, else
// fail-closed).
import CoreAudio
import Darwin
import Foundation

func emitStatus(_ payload: [String: Any]) {
  guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
  FileHandle.standardError.write(data)
  FileHandle.standardError.write("\n".data(using: .utf8)!)
}

func failClosed(_ message: String) -> Never {
  emitStatus(["ok": false, "error": message])
  exit(1)
}

struct TapOptions {
  var pids: [Int32] = []
  var seconds: Double? = nil // nil = run until SIGTERM/SIGINT
  var listProcesses = false
}

func parseTapArgs(_ argv: [String]) -> TapOptions {
  var options = TapOptions()
  var index = 0
  while index < argv.count {
    let arg = argv[index]
    if arg == "--list-processes" {
      options.listProcesses = true
      index += 1
    } else if arg == "--pid" {
      guard index + 1 < argv.count, let pid = Int32(argv[index + 1]), pid > 0 else {
        failClosed("usage: soren91_audio_tap --pid N [--pid N ...] [--seconds N]")
      }
      options.pids.append(pid)
      index += 2
    } else if arg.hasPrefix("--pid=") {
      guard let pid = Int32(arg.dropFirst("--pid=".count)), pid > 0 else {
        failClosed("usage: soren91_audio_tap --pid N [--pid N ...] [--seconds N]")
      }
      options.pids.append(pid)
      index += 1
    } else if arg == "--seconds" {
      guard index + 1 < argv.count, let seconds = Double(argv[index + 1]), seconds > 0 else {
        failClosed("--seconds requires a positive value")
      }
      options.seconds = seconds
      index += 2
    } else {
      failClosed("unknown argument: \(arg)")
    }
  }
  return options
}

// Translates a UNIX pid to the Core Audio process object for
// CATapDescription(stereoMixdownOfProcesses:). Returns nil when the pid has
// no audio process object (exited, or never made sound).
func processObject(forPID pid: Int32) -> AudioObjectID? {
  var addr = AudioObjectPropertyAddress(
    mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
  var qualifier = pid
  var objID = AudioObjectID(0)
  var size = UInt32(MemoryLayout<AudioObjectID>.size)
  let status = withUnsafePointer(to: &qualifier) { qptr in
    AudioObjectGetPropertyData(
      AudioObjectID(kAudioObjectSystemObject), &addr,
      UInt32(MemoryLayout<Int32>.size), qptr,
      &size, &objID)
  }
  guard status == 0, objID != 0 else { return nil }
  return objID
}

// Debug helper: lists audio process objects as pid + bundleID ONLY. Window
// titles are never read here (privacy: no title enumeration).
func listAudioProcesses() -> [[String: Any]] {
  var addr = AudioObjectPropertyAddress(
    mSelector: kAudioHardwarePropertyProcessObjectList,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
  var size: UInt32 = 0
  guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == 0,
    size > 0
  else { return [] }
  let count = Int(size) / MemoryLayout<AudioObjectID>.size
  let ids = UnsafeMutablePointer<AudioObjectID>.allocate(capacity: count)
  defer { ids.deallocate() }
  guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, ids) == 0
  else { return [] }
  var out: [[String: Any]] = []
  for i in 0..<count {
    let oid = ids[i]
    var pid: Int32 = -1
    var psize = UInt32(MemoryLayout<Int32>.size)
    var paddr = AudioObjectPropertyAddress(
      mSelector: kAudioProcessPropertyPID,
      mScope: kAudioObjectPropertyScopeGlobal,
      mElement: kAudioObjectPropertyElementMain)
    _ = AudioObjectGetPropertyData(oid, &paddr, 0, nil, &psize, &pid)
    var bundleID: Any = NSNull()
    var baddr = AudioObjectPropertyAddress(
      mSelector: kAudioProcessPropertyBundleID,
      mScope: kAudioObjectPropertyScopeGlobal,
      mElement: kAudioObjectPropertyElementMain)
    var bsize: UInt32 = 0
    if AudioObjectGetPropertyDataSize(oid, &baddr, 0, nil, &bsize) == 0, bsize > 0 {
      let bbuf = UnsafeMutableRawPointer.allocate(byteCount: Int(bsize), alignment: 8)
      defer { bbuf.deallocate() }
      if AudioObjectGetPropertyData(oid, &baddr, 0, nil, &bsize, bbuf) == 0 {
        bundleID = (bbuf.load(as: CFString.self) as String) as Any
      }
    }
    out.append(["pid": pid, "bundleID": bundleID])
  }
  return out
}

func tapString(_ tapID: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
  var addr = AudioObjectPropertyAddress(
    mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
  var size: UInt32 = 0
  guard AudioObjectGetPropertyDataSize(tapID, &addr, 0, nil, &size) == 0, size > 0 else { return nil }
  let buf = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 8)
  defer { buf.deallocate() }
  guard AudioObjectGetPropertyData(tapID, &addr, 0, nil, &size, buf) == 0 else { return nil }
  return (buf.load(as: CFString.self) as String)
}

// API-level round-trip of the tap description: proves the created tap really
// carries muteBehavior=CATapMutedWhenTapped (rawValue 2). The caller
// fail-closes unless this reads back as 2.
func verifiedMuteBehavior(_ tapID: AudioObjectID) -> Int {
  var addr = AudioObjectPropertyAddress(
    mSelector: kAudioTapPropertyDescription, mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
  var size: UInt32 = 0
  guard AudioObjectGetPropertyDataSize(tapID, &addr, 0, nil, &size) == 0, size == 8 else { return -1 }
  let buf = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 8)
  defer { buf.deallocate() }
  guard AudioObjectGetPropertyData(tapID, &addr, 0, nil, &size, buf) == 0 else { return -1 }
  let obj: AnyObject = buf.load(as: AnyObject.self)
  guard let desc = obj as? CATapDescription else { return -1 }
  return desc.muteBehavior.rawValue
}

// Shared mutable state touched from the realtime IOProc block. All access is
// lock-guarded; blocking stdout writes from the audio thread are acceptable
// for this PoC (same trade-off as the validated spike).
final class TapState: @unchecked Sendable {
  let lock = NSLock()
  var bytes: Int = 0
  var callbacks: Int = 0
  var stdoutError = false
  func didLoseStdout() -> Bool {
    lock.lock()
    defer { lock.unlock() }
    return stdoutError
  }
}

@main
struct Soren91AudioTap {
  static func main() {
    let options = parseTapArgs(Array(CommandLine.arguments.dropFirst()))

    if options.listProcesses {
      emitStatus(["ok": true, "processes": listAudioProcesses()])
      exit(0)
    }

    guard !options.pids.isEmpty else {
      failClosed("no --pid given (fail-closed: refusing to tap without explicit PIDs)")
    }

    // PID -> Core Audio process object. PIDs with no audio process object
    // (already exited, or silent so far) cannot be tapped; with zero
    // translatable PIDs we refuse to run rather than tap nothing (or worse,
    // widen the scope).
    var objIDs: [AudioObjectID] = []
    var translated: [Int] = []
    var missing: [Int] = []
    for pid in options.pids {
      if let obj = processObject(forPID: pid) {
        objIDs.append(obj)
        translated.append(Int(pid))
      } else {
        missing.append(Int(pid))
      }
    }
    guard !objIDs.isEmpty else {
      failClosed(
        "no given PID has an audio process object (fail-closed): missing=\(missing)")
    }

    // PID-scoped tap only. bundleIDs and isProcessRestoreEnabled are
    // deliberately left unset: either would widen the tap to processes that
    // were never explicitly listed (e.g. the operator's everyday Chrome).
    let desc = CATapDescription(stereoMixdownOfProcesses: objIDs)
    desc.muteBehavior = CATapMuteBehavior(rawValue: 2)!
    desc.isPrivate = true
    let tapName = "soren91-audio-tap-\(Int(Date().timeIntervalSince1970))"
    desc.name = tapName
    desc.uuid = UUID()

    var tapID = AudioObjectID(0)
    var status = AudioHardwareCreateProcessTap(desc, &tapID)
    guard status == 0 else {
      failClosed("AudioHardwareCreateProcessTap failed st=\(status)")
    }

    guard let tapUID = tapString(tapID, kAudioTapPropertyUID) else {
      AudioHardwareDestroyProcessTap(tapID)
      failClosed("cannot read kAudioTapPropertyUID")
    }

    var fasbd = AudioObjectPropertyAddress(
      mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal,
      mElement: kAudioObjectPropertyElementMain)
    var tapASBD = AudioStreamBasicDescription()
    var fsize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    status = AudioObjectGetPropertyData(tapID, &fasbd, 0, nil, &fsize, &tapASBD)
    guard status == 0 else {
      AudioHardwareDestroyProcessTap(tapID)
      failClosed("cannot read kAudioTapPropertyFormat st=\(status)")
    }

    let muteVerified = verifiedMuteBehavior(tapID)
    guard muteVerified == 2 else {
      AudioHardwareDestroyProcessTap(tapID)
      failClosed(
        "tap muteBehavior did not read back as CATapMutedWhenTapped (fail-closed): got \(muteVerified)"
      )
    }

    // Contract with ffmpeg (`-f s16le -ar 48000 -ac 2 -i pipe:3`): the tap
    // must deliver float32 stereo at 48kHz. Anything else fail-closes here
    // instead of emitting mislabeled PCM.
    guard tapASBD.mFormatID == kAudioFormatLinearPCM,
      tapASBD.mChannelsPerFrame == 2,
      tapASBD.mBitsPerChannel == 32,
      (tapASBD.mFormatFlags & UInt32(kAudioFormatFlagIsFloat)) != 0,
      tapASBD.mSampleRate == 48000
    else {
      AudioHardwareDestroyProcessTap(tapID)
      failClosed(
        "unexpected tap format (need f32 stereo @48k): ch=\(tapASBD.mChannelsPerFrame) bits=\(tapASBD.mBitsPerChannel) rate=\(tapASBD.mSampleRate) flags=\(tapASBD.mFormatFlags)"
      )
    }
    let isNonInterleaved = (tapASBD.mFormatFlags & UInt32(kAudioFormatFlagIsNonInterleaved)) != 0

    let aggUID = "soren91-audio-tap-agg-\(UUID().uuidString)"
    let composition: CFDictionary = [
      kAudioAggregateDeviceNameKey as CFString: tapName as CFString,
      kAudioAggregateDeviceUIDKey as CFString: aggUID as CFString,
      kAudioAggregateDeviceIsPrivateKey as CFString: 1 as CFNumber,
      kAudioAggregateDeviceTapListKey as CFString: [
        [kAudioSubTapUIDKey as CFString: tapUID as CFString]
      ] as CFArray,
    ] as CFDictionary
    var aggID = AudioObjectID(0)
    status = AudioHardwareCreateAggregateDevice(composition, &aggID)
    guard status == 0 else {
      AudioHardwareDestroyProcessTap(tapID)
      failClosed("AudioHardwareCreateAggregateDevice failed st=\(status)")
    }

    let state = TapState()
    let stdoutHandle = FileHandle.standardOutput
    var ioID: AudioDeviceIOProcID?
    status = AudioDeviceCreateIOProcIDWithBlock(&ioID, aggID, nil, {
      (
        _ now: UnsafePointer<AudioTimeStamp>, inputData: UnsafePointer<AudioBufferList>,
        _ inputTime: UnsafePointer<AudioTimeStamp>, _ outputData: UnsafeMutablePointer<AudioBufferList>,
        _ outputTime: UnsafePointer<AudioTimeStamp>
      ) in
      let abl = inputData.pointee
      let bufferCount = Int(abl.mNumberBuffers)
      var pcm = Data()
      withUnsafePointer(to: abl.mBuffers) { ptr in
        ptr.withMemoryRebound(to: AudioBuffer.self, capacity: bufferCount) { bufs in
          if bufferCount == 1 {
            // Interleaved float32 stereo: clamp and quantize straight to s16le.
            let buf = bufs[0]
            guard let raw = buf.mData else { return }
            let frames = Int(buf.mDataByteSize) / 4
            let floats = raw.assumingMemoryBound(to: Float.self)
            pcm.reserveCapacity(frames * 2)
            for s in 0..<frames {
              var v = floats[s]
              if v > 1.0 { v = 1.0 } else if v < -1.0 { v = -1.0 }
              var le = Int16(v * 32767.0).littleEndian
              withUnsafeBytes(of: &le) { pcm.append(contentsOf: $0) }
            }
          } else if bufferCount >= 2 {
            // Non-interleaved: interleave the first two channels.
            let b0 = bufs[0]
            let b1 = bufs[1]
            guard let r0 = b0.mData, let r1 = b1.mData else { return }
            let frames = min(Int(b0.mDataByteSize), Int(b1.mDataByteSize)) / 4
            let f0 = r0.assumingMemoryBound(to: Float.self)
            let f1 = r1.assumingMemoryBound(to: Float.self)
            pcm.reserveCapacity(frames * 4)
            for s in 0..<frames {
              for f in [f0[s], f1[s]] {
                var v = f
                if v > 1.0 { v = 1.0 } else if v < -1.0 { v = -1.0 }
                var le = Int16(v * 32767.0).littleEndian
                withUnsafeBytes(of: &le) { pcm.append(contentsOf: $0) }
              }
            }
          }
        }
      }
      state.lock.lock()
      defer { state.lock.unlock() }
      state.callbacks += 1
      guard !pcm.isEmpty, !state.stdoutError else { return }
      do {
        try stdoutHandle.write(contentsOf: pcm)
        state.bytes += pcm.count
      } catch {
        state.stdoutError = true
      }
    })
    guard status == 0, ioID != nil else {
      AudioHardwareDestroyAggregateDevice(aggID)
      AudioHardwareDestroyProcessTap(tapID)
      failClosed("AudioDeviceCreateIOProcIDWithBlock failed st=\(status)")
    }

    status = AudioDeviceStart(aggID, ioID)
    guard status == 0 else {
      AudioDeviceDestroyIOProcID(aggID, ioID!)
      AudioHardwareDestroyAggregateDevice(aggID)
      AudioHardwareDestroyProcessTap(tapID)
      failClosed("AudioDeviceStart failed st=\(status)")
    }

    emitStatus([
      "ok": true,
      "tapUID": tapUID,
      "format": ["sampleRate": 48000, "channels": 2, "bitsPerChannel": 16, "encoding": "s16le"],
      "pids": translated,
      "missingPids": missing,
      "interleaved": !isNonInterleaved,
      "muteBehaviorRequested": 2,
      "muteBehaviorVerified": muteVerified,
    ])

    // SIGTERM/SIGINT: stop reading (which also releases the physical mute),
    // then tear down IOProc -> aggregate -> tap. Always runs: the mute must
    // never outlive this process.
    // SIGPIPE is ignored (best-effort): when the downstream reader (ffmpeg's
    // fd 3) goes away first, the next stdout write fails with EPIPE instead
    // of killing this helper with a SIGPIPE signal. The IOProc records that
    // as stdoutError, the main loop below observes it and performs the same
    // orderly teardown, exiting 0 — so a listener-first close never wins the
    // session's exit race as a signal death.
    signal(SIGPIPE, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)
    final class StopFlag: @unchecked Sendable { var stop = false }
    let flag = StopFlag()
    let srcTerm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: DispatchQueue.global())
    srcTerm.setEventHandler { flag.stop = true }
    srcTerm.resume()
    let srcInt = DispatchSource.makeSignalSource(signal: SIGINT, queue: DispatchQueue.global())
    srcInt.setEventHandler { flag.stop = true }
    srcInt.resume()

    let deadline = options.seconds.map { Date().addingTimeInterval($0) }
    while !flag.stop {
      if let deadline, Date() >= deadline { break }
      // Downstream went away first (EPIPE on a stdout write): stop the same
      // way as a signal stop — teardown below releases the physical mute —
      // and exit 0. Without this the helper would idle until SIGTERM even
      // though no reader remains.
      if state.didLoseStdout() { break }
      Thread.sleep(forTimeInterval: 0.1)
    }

    AudioDeviceStop(aggID, ioID)
    AudioDeviceDestroyIOProcID(aggID, ioID!)
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
    state.lock.lock()
    let bytes = state.bytes
    let callbacks = state.callbacks
    state.lock.unlock()
    let pipeBroken = state.didLoseStdout()
    emitStatus([
      "done": true, "bytes": bytes, "callbacks": callbacks, "terminatedEarly": flag.stop,
      "stdoutError": pipeBroken,
    ])
    exit(0)
  }
}
