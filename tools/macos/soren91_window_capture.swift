// Soren91 macOS window-targeted capture helper (ScreenCaptureKit).
//
// Captures ONE exact bundle-id/title match via desktopIndependentWindow.
// Never fall back to a display, another window, or fixed screen coordinates.
// Raw, tightly-packed BGRA frames of width x height go to stdout; JSON status
// goes to stderr. The first status is ok:true after startCapture succeeds.
// Listener-first EPIPE exits 0; capture/geometry/output faults exit non-zero.
//
// Mission Control can change the content's position/scale inside a fixed-size
// pixel buffer. Normalize per-frame SCK metadata BEFORE the caller's fixed
// canvas crop. Rejected/no-update frames repeat the last good image at a
// monotonic cadence, bounded by a 10-second health deadline.
import AppKit
import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreVideo
import Darwin

func emitStatus(_ payload: [String: Any]) {
  guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
  FileHandle.standardError.write(data)
  FileHandle.standardError.write("\n".data(using: .utf8)!)
}

func failClosed(_ message: String) -> Never {
  emitStatus(["ok": false, "error": message])
  exit(1)
}

func monotonicNow() -> Double { ProcessInfo.processInfo.systemUptime }

struct Options {
  var bundleId: String?
  var title: String?
  var width: Int32 = 960
  var height: Int32 = 540
  var fps: Int32 = 30
  var diagnostics = ProcessInfo.processInfo.environment["SOREN91_CAPTURE_DIAGNOSTICS"] == "1"
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
    case "--diagnostics": options.diagnostics = true
    default: failClosed("unknown argument: \(arg)")
    }
    index += 1
  }
  guard (2...4096).contains(options.width), (2...4096).contains(options.height),
        (1...120).contains(options.fps) else {
    failClosed("width/height must be 2..4096 and fps must be 1..120")
  }
  return options
}

final class StdoutHealth: @unchecked Sendable {
  private let lock = NSLock()
  private var broken = false
  private var lastWrite: Double?
  private var ready = false
  func markBroken() { lock.lock(); defer { lock.unlock() }; broken = true }
  func markReady() { lock.lock(); defer { lock.unlock() }; ready = true }
  func isReady() -> Bool { lock.lock(); defer { lock.unlock() }; return ready }
  func isBroken() -> Bool { lock.lock(); defer { lock.unlock() }; return broken }
  func wrote(now: Double) { lock.lock(); defer { lock.unlock() }; lastWrite = now }
  func stalled(now: Double) -> Bool {
    lock.lock(); defer { lock.unlock() }
    // The host may wait for an audio tap BEFORE connecting ffmpeg. The very
    // first frame write is allowed to wait for that existing startup contract.
    guard let lastWrite else { return false }
    return now - lastWrite > 2
  }
}

final class FrameWriter: NSObject, SCStreamOutput, SCStreamDelegate {
  let store: CaptureFrameStore
  let stdoutHealth: StdoutHealth
  private let normalizer: CaptureFrameNormalizer
  private let fps: Int
  private let diagnostics: Bool
  private var lastDiagnostic = -Double.infinity // capture queue only
  private var cadence: CaptureCadence // output queue only
  private var timer: DispatchSourceTimer?

  init(options: Options, logicalSize: CGSize, stdoutHealth: StdoutHealth) {
    self.stdoutHealth = stdoutHealth
    self.store = CaptureFrameStore(now: monotonicNow())
    self.normalizer = CaptureFrameNormalizer(width: Int(options.width), height: Int(options.height),
                                             logicalSize: logicalSize)
    self.fps = Int(options.fps)
    self.cadence = CaptureCadence(fps: Int(options.fps))
    self.diagnostics = options.diagnostics
  }

  func startOutput() {
    stdoutHealth.markReady() // readiness JSON has already been written
    let timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "soren91.output"))
    timer.schedule(deadline: .now(), repeating: .nanoseconds(1_000_000_000 / fps),
                   leeway: .milliseconds(1))
    timer.setEventHandler { [weak self] in self?.outputTick() }
    self.timer = timer
    timer.resume()
  }

  private func outputTick() {
    guard !stdoutHealth.isBroken() else { return }
    do {
      guard let frame = try store.snapshot(now: monotonicNow()) else { return }
      let due = try cadence.due(now: monotonicNow())
      for _ in 0..<due {
        guard writeFrame(frame) else { return }
        let now = monotonicNow()
        if !cadence.isStarted { cadence.started(now: now) }
        else { cadence.wroteFrame() }
        stdoutHealth.wrote(now: now)
      }
    } catch { failClosed(String(describing: error)) }
  }

  // One writer, complete frames only. Capture callbacks never wait on this
  // pipe. EINTR retries; EPIPE remains a clean listener-first end, not a crash.
  private func writeFrame(_ data: Data) -> Bool {
    data.withUnsafeBytes { bytes in
      var offset = 0
      while offset < bytes.count {
        let n = Darwin.write(STDOUT_FILENO, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
        if n > 0 { offset += n; continue }
        if n < 0 && errno == EINTR { continue }
        if n < 0 && errno == EPIPE { stdoutHealth.markBroken(); return false }
        failClosed("stdout-write-failed:\(errno)")
      }
      return true
    }
  }

  private func record(_ reason: String, now: Double, geometry: CaptureGeometry? = nil, status: Int? = nil) {
    guard diagnostics, stdoutHealth.isReady(), now - lastDiagnostic >= 1 else { return }
    lastDiagnostic = now
    var payload: [String: Any] = ["event": "capture-frame", "reason": reason,
                                  "bufferWidth": normalizer.width, "bufferHeight": normalizer.height]
    if let status { payload["status"] = status }
    if let g = geometry,
       [Double(g.contentRect.origin.x), Double(g.contentRect.origin.y),
        Double(g.contentRect.size.width), Double(g.contentRect.size.height),
        g.contentScale, g.scaleFactor].allSatisfy({ $0.isFinite }) {
      payload["contentRect"] = [Double(g.contentRect.origin.x), Double(g.contentRect.origin.y),
                                Double(g.contentRect.size.width), Double(g.contentRect.size.height)]
      payload["contentScale"] = g.contentScale
      payload["scaleFactor"] = g.scaleFactor
    }
    // Numeric geometry only. No titles, URLs, pixels or unrelated windows.
    emitStatus(payload)
  }

  func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
    guard type == .screen, !stdoutHealth.isBroken() else { return }
    let now = monotonicNow()
    guard sampleBuffer.isValid,
          let array = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false)
            as? [[SCStreamFrameInfo: Any]], let attachment = array.first,
          let rawStatus = (attachment[.status] as? NSNumber)?.intValue,
          let status = SCFrameStatus(rawValue: rawStatus) else {
      store.reject("missing-frame-status", now: now)
      record("missing-frame-status", now: now)
      return
    }
    if status == .idle {
      store.idle(now: now)
      record("idle", now: now, status: rawStatus)
      return
    }
    guard status == .complete else {
      store.reject("incomplete-frame-\(rawStatus)", now: now)
      record("incomplete-frame", now: now, status: rawStatus)
      return
    }
    guard let rectDict = attachment[.contentRect] as? [String: Any],
          let rect = CGRect(dictionaryRepresentation: rectDict as CFDictionary),
          let contentScale = attachment[.contentScale] as? NSNumber,
          let scaleFactor = attachment[.scaleFactor] as? NSNumber,
          let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
      store.reject("missing-frame-metadata-or-pixels", now: now)
      record("missing-frame-metadata-or-pixels", now: now, status: rawStatus)
      return
    }
    let actualWidth = CVPixelBufferGetWidth(imageBuffer)
    let actualHeight = CVPixelBufferGetHeight(imageBuffer)
    guard actualWidth == normalizer.width, actualHeight == normalizer.height else {
      failClosed("captured frame size \(actualWidth)x\(actualHeight) != requested \(normalizer.width)x\(normalizer.height)")
    }
    let geometry = CaptureGeometry(contentRect: rect, contentScale: contentScale.doubleValue,
                                   scaleFactor: scaleFactor.doubleValue)
    do {
      let frame = try autoreleasepool { try normalizer.normalize(imageBuffer, geometry: geometry) }
      store.accept(frame, now: monotonicNow())
      record("normalized", now: now, geometry: geometry, status: rawStatus)
    } catch {
      let reason = String(describing: error)
      store.reject(reason, now: now)
      record(reason, now: now, geometry: geometry, status: rawStatus)
    }
  }

  func stream(_ stream: SCStream, didStopWithError error: Error) {
    failClosed("stream stopped unexpectedly: \(error)")
  }
}

@main
struct Soren91WindowCapture {
  static func main() async {
    // Establish a WindowServer connection without a Dock icon or menu bar.
    NSApplication.shared.setActivationPolicy(.accessory)
    let options = parseArgs(Array(CommandLine.arguments.dropFirst()))
    guard let bundleId = options.bundleId, !bundleId.isEmpty else { failClosed("--bundle-id is required") }
    guard let title = options.title, !title.isEmpty else {
      failClosed("--title is required (exact match, fail-closed if ambiguous)")
    }
    let content: SCShareableContent
    do {
      content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    } catch {
      failClosed("SCShareableContent failed (check Screen Recording permission for this binary): \(error)")
    }
    let matches = content.windows.filter { window in
      window.owningApplication?.bundleIdentifier == bundleId && window.title == title
    }
    guard matches.count == 1 else {
      // Do not enumerate titles of other windows (private browser/document data).
      failClosed("expected exactly 1 matching capture window, found \(matches.count) (fail-closed)")
    }
    let window = matches[0]
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let config = SCStreamConfiguration()
    config.width = Int(options.width)
    config.height = Int(options.height)
    config.scalesToFit = true
    config.showsCursor = false
    // Capture ONLY the window content: without these, ScreenCaptureKit adds the
    // window's drop shadow / clip padding, which shows up as an extra border
    // ("枠") around the game and scales the content inside the requested size.
    config.ignoreShadowsSingleWindow = true
    config.ignoreGlobalClipSingleWindow = true
    config.capturesAudio = false
    config.pixelFormat = kCVPixelFormatType_32BGRA
    config.minimumFrameInterval = CMTime(value: 1, timescale: options.fps)
    config.queueDepth = 5

    // Install before any producer can write, including the first output tick.
    signal(SIGPIPE, SIG_IGN)
    signal(SIGINT) { _ in exit(0) }
    signal(SIGTERM) { _ in exit(0) }
    let stdoutHealth = StdoutHealth()
    let writer = FrameWriter(options: options, logicalSize: window.frame.size, stdoutHealth: stdoutHealth)
    let stream = SCStream(filter: filter, configuration: config, delegate: writer)
    do {
      try stream.addStreamOutput(writer, type: .screen, sampleHandlerQueue: DispatchQueue(label: "soren91.capture"))
      try await stream.startCapture()
    } catch { failClosed("failed to start capture: \(error)") }
    emitStatus(["ok": true, "windowID": window.windowID, "width": options.width, "height": options.height, "fps": options.fps])
    writer.startOutput()
    while true {
      try? await Task.sleep(nanoseconds: 100_000_000)
      if stdoutHealth.isBroken() { exit(0) }
      // Separate from the writer queue so a blocked pipe cannot disable the
      // health deadline. No image has ever been valid -> no fabricated frame.
      do { _ = try writer.store.snapshot(now: monotonicNow()) }
      catch { failClosed(String(describing: error)) }
      if stdoutHealth.stalled(now: monotonicNow()) { failClosed("stdout-stalled") }
    }
  }
}
