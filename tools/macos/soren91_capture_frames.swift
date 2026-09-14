// Frame geometry and bounded latest-frame state for the Soren91 SCK helper.
// Foundation-only policy is exercised on Linux as well as macOS. Pixel
// normalization is also tested on macOS without recording the screen.
import Foundation

struct CaptureFrameError: Error, CustomStringConvertible {
  let description: String
  init(_ description: String) { self.description = description }
}

struct CaptureGeometry {
  let contentRect: CGRect // Output-buffer pixels, top-left origin (NOT screen coordinates).
  let contentScale: Double
  let scaleFactor: Double

  func validatedRect(bufferWidth: Int, bufferHeight: Int, logicalSize: CGSize) throws -> CGRect {
    let r = contentRect
    let values = [Double(r.origin.x), Double(r.origin.y), Double(r.size.width), Double(r.size.height),
                  contentScale, scaleFactor, Double(logicalSize.width), Double(logicalSize.height)]
    guard values.allSatisfy({ $0.isFinite }), bufferWidth > 0, bufferHeight > 0,
          r.size.width > 0, r.size.height > 0, logicalSize.width > 0, logicalSize.height > 0,
          contentScale > 0, contentScale <= 16, scaleFactor > 0, scaleFactor <= 8 else {
      throw CaptureFrameError("invalid-frame-geometry")
    }
    guard r.minX >= 0, r.minY >= 0,
          r.maxX <= CGFloat(bufferWidth), r.maxY <= CGFloat(bufferHeight) else {
      // Never clamp a clipped window and stretch its remainder to look valid.
      throw CaptureFrameError("content-rect-outside-buffer")
    }
    // Apple WWDC22/10155: crop contentRect, undo contentScale to recover
    // backing pixels, then undo scaleFactor to recover logical points.
    // A compositor scale/translation is repairable; a real window resize is
    // not, because the downstream canvas/chrome crop was calibrated once.
    let pointsPerPixel = 1 / (contentScale * scaleFactor)
    let nativeWidth = Double(r.width) * pointsPerPixel
    let nativeHeight = Double(r.height) * pointsPerPixel
    guard abs(nativeWidth - Double(logicalSize.width)) <= 2,
          abs(nativeHeight - Double(logicalSize.height)) <= 2 else {
      throw CaptureFrameError("logical-window-size-changed")
    }
    return r
  }
}

// One owned, normalized Data buffer, never an SCK IOSurface. The output queue
// may block on ffmpeg without retaining the capture pool or building a queue.
final class CaptureFrameStore: @unchecked Sendable {
  private let lock = NSLock()
  private var frame: Data?
  private var healthyAt: Double
  private var invalidSince: Double?
  private var reason = "awaiting-first-frame"
  let holdSeconds: Double

  init(now: Double, holdSeconds: Double = 10) {
    self.healthyAt = now
    self.holdSeconds = holdSeconds
  }

  func accept(_ data: Data, now: Double) {
    lock.lock(); defer { lock.unlock() }
    frame = data
    healthyAt = now
    invalidSince = nil
    reason = "no-frame-updates"
  }

  func reject(_ why: String, now: Double) {
    lock.lock(); defer { lock.unlock() }
    if invalidSince == nil { invalidSince = now }
    reason = why
  }

  func idle(now: Double) {
    lock.lock(); defer { lock.unlock() }
    // An idle frame has no new pixels. It may keep a known-good still image
    // alive, but must not rehabilitate an invalid frame or missing first frame.
    if frame != nil && invalidSince == nil { healthyAt = now }
  }

  func snapshot(now: Double) throws -> Data? {
    lock.lock(); defer { lock.unlock() }
    // invalidSince never slides forward on repeated rejected/idle callbacks.
    let since = min(healthyAt, invalidSince ?? healthyAt)
    guard now - since < holdSeconds else {
      throw CaptureFrameError("capture-hold-timeout:\(reason)")
    }
    return frame
  }
}

// Rawvideo carries no timestamps: account for elapsed ticks, including missed
// timer callbacks. Small scheduling delays repeat the latest frame; an output
// stall > 0.5s fails rather than silently accumulating A/V drift or a huge burst.
struct CaptureCadence {
  let fps: Int
  private var epoch: Double?
  private var emitted = 0

  init(fps: Int) { self.fps = fps }

  mutating func started(now: Double) {
    epoch = now
    emitted = 1
  }

  func due(now: Double) throws -> Int {
    guard let epoch else { return 1 }
    let elapsed = max(0, now - epoch)
    guard elapsed.isFinite, elapsed < Double(Int.max / max(1, fps)) else {
      throw CaptureFrameError("invalid-output-clock")
    }
    let due = max(0, Int(floor(elapsed * Double(fps))) + 1 - emitted)
    guard due <= max(1, fps / 2) else { throw CaptureFrameError("output-cadence-stalled") }
    return due
  }

  mutating func wroteFrame() { emitted += 1 }
  var isStarted: Bool { epoch != nil }
}

#if canImport(CoreImage)
import CoreImage
import CoreVideo

final class CaptureFrameNormalizer {
  private let context = CIContext(options: [.cacheIntermediates: false])
  private let colorSpace = CGColorSpaceCreateDeviceRGB()
  let width: Int
  let height: Int
  let logicalSize: CGSize

  init(width: Int, height: Int, logicalSize: CGSize) {
    self.width = width
    self.height = height
    self.logicalSize = logicalSize
  }

  func normalize(_ buffer: CVPixelBuffer, geometry: CaptureGeometry) throws -> Data {
    let bufferWidth = CVPixelBufferGetWidth(buffer)
    let bufferHeight = CVPixelBufferGetHeight(buffer)
    guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_32BGRA else {
      throw CaptureFrameError("unexpected-pixel-format")
    }
    let rect = try geometry.validatedRect(bufferWidth: bufferWidth, bufferHeight: bufferHeight,
                                          logicalSize: logicalSize)
    let rowBytes = width * 4
    // Exact identity: copy rows and strip alignment padding without a GPU
    // round trip or color conversion. Never hold an SCK-owned pixel buffer.
    if rect == CGRect(x: 0, y: 0, width: width, height: height),
       bufferWidth == width, bufferHeight == height {
      guard CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else {
        throw CaptureFrameError("pixel-buffer-lock-failed")
      }
      defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
      guard let base = CVPixelBufferGetBaseAddress(buffer) else {
        throw CaptureFrameError("missing-pixel-buffer-base")
      }
      let stride = CVPixelBufferGetBytesPerRow(buffer)
      guard stride >= rowBytes else { throw CaptureFrameError("invalid-pixel-stride") }
      var packed = Data(count: rowBytes * height)
      packed.withUnsafeMutableBytes { output in
        for row in 0..<height {
          output.baseAddress!.advanced(by: row * rowBytes)
            .copyMemory(from: base.advanced(by: row * stride), byteCount: rowBytes)
        }
      }
      return packed
    }
    // SCK metadata has a top-left origin; Core Image has a bottom-left origin.
    // Crop FIRST, then translate to zero and scale to the immutable rawvideo
    // canvas. Using screen coordinates here reintroduces the privacy bug.
    let ciRect = CGRect(x: rect.minX, y: CGFloat(bufferHeight) - rect.maxY,
                        width: rect.width, height: rect.height)
    let image = CIImage(cvPixelBuffer: buffer).cropped(to: ciRect).clampedToExtent()
      .transformed(by: CGAffineTransform(translationX: -ciRect.minX, y: -ciRect.minY))
      .transformed(by: CGAffineTransform(scaleX: CGFloat(width) / rect.width,
                                         y: CGFloat(height) / rect.height))
    var packed = Data(count: rowBytes * height)
    packed.withUnsafeMutableBytes { output in
      context.render(image, toBitmap: output.baseAddress!, rowBytes: rowBytes,
                     bounds: CGRect(x: 0, y: 0, width: width, height: height),
                     format: .BGRA8, colorSpace: colorSpace)
    }
    return packed
  }
}
#endif
