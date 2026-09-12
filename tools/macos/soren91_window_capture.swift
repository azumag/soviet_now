// Soren91 macOS window-targeted capture helper (ScreenCaptureKit).
//
// Fixes the structural flaw in the original avfoundation "whole-display
// capture + fixed-coordinate crop" design (see Issue #303): that approach
// records whatever is on-screen at a hard-coded position, so any other
// window placed there — including, in a real incident, the operator's own
// everyday browser window — gets streamed instead of the intended game
// window. ScreenCaptureKit instead binds the capture to a *specific window's
// content* (SCContentFilter(desktopIndependentWindow:)), which the compositor
// delivers correctly even if that window is occluded or positioned off the
// visible display — so the identity of what gets captured no longer depends
// on screen geometry or z-order at all.
//
// Target selection is intentionally fail-closed: the caller must supply the
// owning application's bundle identifier AND an EXACT window title. If zero
// or more than one window matches, this exits non-zero and writes no frame
// data — it never guesses.
//
// Protocol: on success, prints one JSON line to stderr
// (`{"ok":true,"windowID":<id>,...}`) once the stream has started, then
// writes raw, tightly-packed BGRA8888 frames of exactly `width x height` to
// stdout until SIGINT/SIGTERM — or until stdout's reader (ffmpeg's stdin)
// goes away first, in which case the next frame write fails with EPIPE
// (SIGPIPE is ignored, same as the audio-tap helper) and this exits 0 with
// the ok:true line left standing: a listener-first close is a normal end,
// not a crash. On failure, prints one JSON line to stderr
// (`{"ok":false,"error":"..."}`) and exits 1 without ever starting the
// stream or writing to stdout — callers must not treat "still running" as
// success on its own; they must observe the ok:true line first.
import AppKit
import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreVideo

struct HelperError: Error, CustomStringConvertible {
  let description: String
}

func emitStatus(_ payload: [String: Any]) {
  guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
  FileHandle.standardError.write(data)
  FileHandle.standardError.write("\n".data(using: .utf8)!)
}

func failClosed(_ message: String) -> Never {
  emitStatus(["ok": false, "error": message])
  exit(1)
}

struct Options {
  var bundleId: String?
  var title: String?
  var width: Int32 = 960
  var height: Int32 = 540
  var fps: Int32 = 30
}

func parseArgs(_ argv: [String]) -> Options {
  var options = Options()
  var index = 0
  while index < argv.count {
    let arg = argv[index]
    func nextValue() -> String {
      index += 1
      guard index < argv.count else { failClosed("\(arg) requires a value") }
      return argv[index]
    }
    switch arg {
    case "--bundle-id": options.bundleId = nextValue()
    case "--title": options.title = nextValue()
    case "--width":
      guard let value = Int32(nextValue()) else { failClosed("--width must be an integer") }
      options.width = value
    case "--height":
      guard let value = Int32(nextValue()) else { failClosed("--height must be an integer") }
      options.height = value
    case "--fps":
      guard let value = Int32(nextValue()) else { failClosed("--fps must be an integer") }
      options.fps = value
    default: failClosed("unknown argument: \(arg)")
    }
    index += 1
  }
  return options
}

final class FrameWriter: NSObject, SCStreamOutput, SCStreamDelegate {
  let expectedWidth: Int
  let expectedHeight: Int
  let stdoutHealth: StdoutHealth
  private var mismatchReported = false

  init(expectedWidth: Int, expectedHeight: Int, stdoutHealth: StdoutHealth) {
    self.expectedWidth = expectedWidth
    self.expectedHeight = expectedHeight
    self.stdoutHealth = stdoutHealth
  }

  // Frame sink (Issue #303): stdout feeds ffmpeg's stdin, the ONLY reader.
  // When the downstream goes away first (OCI listener closed -> ffmpeg
  // exited), the next write fails with EPIPE instead of killing this helper
  // with SIGPIPE (ignored in main). Record it and stop writing; the main
  // loop observes the flag and exits 0 — the same clean stop as the
  // audio-tap helper, so a listener-first close never wins the session's
  // exit race as a signal death.
  private func writeFrame(_ data: Data) {
    do {
      try FileHandle.standardOutput.write(contentsOf: data)
    } catch {
      stdoutHealth.markBroken()
    }
  }

  func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
    guard !stdoutHealth.isBroken() else { return }
    guard type == .screen, sampleBuffer.isValid else { return }
    guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

    let actualWidth = CVPixelBufferGetWidth(imageBuffer)
    let actualHeight = CVPixelBufferGetHeight(imageBuffer)
    if actualWidth != expectedWidth || actualHeight != expectedHeight {
      // Fail loudly rather than silently forwarding a wrongly-sized frame
      // that would desync the downstream rawvideo demuxer.
      if !mismatchReported {
        mismatchReported = true
        failClosed("captured frame size \(actualWidth)x\(actualHeight) != requested \(expectedWidth)x\(expectedHeight)")
      }
      return
    }

    CVPixelBufferLockBaseAddress(imageBuffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(imageBuffer, .readOnly) }
    guard let base = CVPixelBufferGetBaseAddress(imageBuffer) else { return }
    let bytesPerRow = CVPixelBufferGetBytesPerRow(imageBuffer)
    let rowBytes = expectedWidth * 4
    if bytesPerRow == rowBytes {
      writeFrame(Data(bytes: base, count: rowBytes * expectedHeight))
    } else {
      // CVPixelBuffer rows can be padded for alignment; strip the padding so
      // the output stream stays tightly packed rawvideo (what ffmpeg expects).
      var packed = Data(capacity: rowBytes * expectedHeight)
      for row in 0..<expectedHeight {
        packed.append(Data(bytes: base.advanced(by: row * bytesPerRow), count: rowBytes))
      }
      writeFrame(packed)
    }
  }

  func stream(_ stream: SCStream, didStopWithError error: Error) {
    failClosed("stream stopped unexpectedly: \(error)")
  }
}

// Shared flag between the stream-output queue (FrameWriter, above) and the
// main loop (below): set once a stdout frame write fails with EPIPE.
final class StdoutHealth: @unchecked Sendable {
  private let lock = NSLock()
  private var broken = false
  func markBroken() {
    lock.lock()
    defer { lock.unlock() }
    broken = true
  }
  func isBroken() -> Bool {
    lock.lock()
    defer { lock.unlock() }
    return broken
  }
}

@main
struct Soren91WindowCapture {
  static func main() async {
    // Bare CLI executables have no CGS/WindowServer connection until an
    // NSApplication exists (ScreenCaptureKit's window enumeration calls into
    // AppKit-adjacent CoreGraphics APIs that otherwise assert
    // `CGS_REQUIRE_INIT`). `.accessory` keeps this headless (no Dock icon, no
    // menu bar) while still establishing that connection. Must run before any
    // SCShareableContent / SCStream call.
    NSApplication.shared.setActivationPolicy(.accessory)

    let options = parseArgs(Array(CommandLine.arguments.dropFirst()))
    guard let bundleId = options.bundleId, !bundleId.isEmpty else {
      failClosed("--bundle-id is required")
    }
    guard let title = options.title, !title.isEmpty else {
      failClosed("--title is required (exact match, fail-closed if ambiguous)")
    }

    let content: SCShareableContent
    do {
      // onScreenWindowsOnly: false so a window that is occluded or positioned
      // off the visible display is still discoverable — that is the whole
      // point of moving off avfoundation's whole-display capture.
      content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    } catch {
      failClosed("SCShareableContent failed (check Screen Recording permission for this binary): \(error)")
    }

    let matches = content.windows.filter { window in
      window.owningApplication?.bundleIdentifier == bundleId && window.title == title
    }
    guard matches.count == 1 else {
      // Do not enumerate other windows or titles here. This helper normally
      // targets Google Chrome, and unrelated window titles can contain private
      // browsing/document context that must not be copied into logs.
      failClosed("expected exactly 1 matching capture window, found \(matches.count) (fail-closed)")
    }
    let window = matches[0]

    let filter = SCContentFilter(desktopIndependentWindow: window)
    let config = SCStreamConfiguration()
    config.width = Int(options.width)
    config.height = Int(options.height)
    config.scalesToFit = true
    config.showsCursor = false
    config.capturesAudio = false
    config.pixelFormat = kCVPixelFormatType_32BGRA
    config.minimumFrameInterval = CMTime(value: 1, timescale: options.fps)
    config.queueDepth = 5

    let stdoutHealth = StdoutHealth()
    let writer = FrameWriter(expectedWidth: Int(options.width), expectedHeight: Int(options.height), stdoutHealth: stdoutHealth)
    let stream = SCStream(filter: filter, configuration: config, delegate: writer)
    do {
      try stream.addStreamOutput(writer, type: .screen, sampleHandlerQueue: DispatchQueue(label: "soren91.capture"))
      try await stream.startCapture()
    } catch {
      failClosed("failed to start capture: \(error)")
    }

    emitStatus(["ok": true, "windowID": window.windowID, "width": options.width, "height": options.height, "fps": options.fps])

    // SIGPIPE is ignored (best-effort, same as the audio-tap helper): when
    // the downstream reader (ffmpeg's stdin) goes away first, the next frame
    // write fails with EPIPE instead of killing this helper with a SIGPIPE
    // signal. FrameWriter records that as stdoutHealth-broken and the loop
    // below exits 0 — the already-emitted ok:true line stands, so the
    // stderr status contract is unchanged: a listener-first close is a
    // normal end, not a crash.
    signal(SIGPIPE, SIG_IGN)
    signal(SIGINT) { _ in exit(0) }
    signal(SIGTERM) { _ in exit(0) }
    while true {
      try? await Task.sleep(nanoseconds: 100_000_000)
      // Downstream went away first (EPIPE on a frame write): stop the same
      // way as a signal stop and exit 0. Without this the helper would idle
      // until SIGTERM even though no reader remains.
      if stdoutHealth.isBroken() { exit(0) }
    }
  }
}
