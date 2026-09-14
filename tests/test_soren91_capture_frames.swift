import Foundation
#if canImport(CoreImage)
import CoreImage
import CoreVideo
#endif

@main
struct CaptureFramesTests {
  static var count = 0
  static func expect(_ value: Bool) { precondition(value) }
  static func check(_ name: String, _ body: () throws -> Void) rethrows {
    try body()
    count += 1
    print("ok \(count) - \(name)")
  }
  static func rejects(_ body: () throws -> Void, containing: String) {
    do { try body(); fatalError("expected \(containing)") }
    catch { precondition(String(describing: error).contains(containing), "unexpected: \(error)") }
  }
  static func geometry(_ rect: CGRect = CGRect(x: 0, y: 0, width: 1280, height: 807),
                       scale: Double = 1, factor: Double = 1) -> CaptureGeometry {
    CaptureGeometry(contentRect: rect, contentScale: scale, scaleFactor: factor)
  }
  static func validate(_ g: CaptureGeometry) throws -> CGRect {
    try g.validatedRect(bufferWidth: 1280, bufferHeight: 807,
                        logicalSize: CGSize(width: 1280, height: 807))
  }
  static func main() throws {
    try check("identity frame keeps the calibrated coordinate grid") {
      expect(try validate(geometry()) == CGRect(x: 0, y: 0, width: 1280, height: 807))
    }
    try check("translated and scaled Mission Control content is recoverable") {
      let rect = CGRect(x: 120, y: 90, width: 640, height: 403.5)
      expect(try validate(geometry(rect, scale: 0.5)) == rect)
    }
    try check("Retina backing scale is not applied twice to output pixels") {
      _ = try validate(geometry(scale: 0.5, factor: 2))
    }
    try check("Retina Mission Control transform restores logical geometry") {
      _ = try validate(geometry(CGRect(x: 40, y: 50, width: 640, height: 403.5), scale: 0.25, factor: 2))
    }
    check("real window resize is rejected, not stretched into a stale canvas crop") {
      rejects({ _ = try validate(geometry(CGRect(x: 0, y: 0, width: 1000, height: 700))) },
              containing: "logical-window-size-changed")
    }
    check("wrong scale metadata is rejected") {
      rejects({ _ = try validate(geometry(scale: 0.5)) }, containing: "logical-window-size-changed")
    }
    check("out-of-buffer content is rejected instead of clamped") {
      for r in [CGRect(x: 1, y: 0, width: 1280, height: 807),
                CGRect(x: 0, y: 1, width: 1280, height: 807),
                CGRect(x: -1, y: 0, width: 1280, height: 807),
                CGRect(x: 0, y: -1, width: 1280, height: 807)] {
        rejects({ _ = try validate(geometry(r)) }, containing: "content-rect-outside-buffer")
      }
    }
    check("zero, negative, NaN and infinite scales are rejected") {
      for v in [0, -1, Double.nan, Double.infinity] {
        rejects({ _ = try validate(geometry(scale: v)) }, containing: "invalid-frame-geometry")
        rejects({ _ = try validate(geometry(factor: v)) }, containing: "invalid-frame-geometry")
      }
    }
    check("empty, negative and non-finite rects are rejected") {
      for r in [CGRect.zero, CGRect(x: 0, y: 0, width: -2, height: 20),
                CGRect(x: CGFloat.nan, y: 0, width: 20, height: 20),
                CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 20)] {
        rejects({ _ = try validate(geometry(r)) }, containing: "invalid-frame-geometry")
      }
    }
    try check("fractional rect coordinates are preserved") {
      let r = CGRect(x: 0.25, y: 0.5, width: 640, height: 403.5)
      expect(try validate(geometry(r, scale: 0.5)) == r)
    }
    check("wrong logical-size calibration is rejected") {
      rejects({ _ = try geometry().validatedRect(bufferWidth: 1280, bufferHeight: 807,
                                                  logicalSize: CGSize(width: 0, height: 807)) },
              containing: "invalid-frame-geometry")
    }
    try check("no fabricated image before the first valid frame") {
      let store = CaptureFrameStore(now: 0)
      expect(try store.snapshot(now: 1) == nil)
    }
    check("missing first image expires") {
      let store = CaptureFrameStore(now: 0)
      store.idle(now: 9)
      rejects({ _ = try store.snapshot(now: 10) }, containing: "capture-hold-timeout")
    }
    try check("bad transition holds exactly the last valid bytes") {
      let store = CaptureFrameStore(now: 0)
      store.accept(Data([1, 2, 3, 4]), now: 0.1)
      store.reject("transition", now: 1)
      expect(try store.snapshot(now: 2) == Data([1, 2, 3, 4]))
    }
    try check("recovery replaces the old image immediately") {
      let store = CaptureFrameStore(now: 0)
      store.accept(Data([1]), now: 0)
      store.reject("transition", now: 1)
      store.accept(Data([2]), now: 2)
      expect(try store.snapshot(now: 3) == Data([2]))
    }
    check("repeated invalid and idle callbacks cannot extend the deadline") {
      let store = CaptureFrameStore(now: 0)
      store.accept(Data([1]), now: 0)
      store.reject("bad", now: 1)
      store.reject("bad-again", now: 9)
      store.idle(now: 10)
      rejects({ _ = try store.snapshot(now: 10) }, containing: "capture-hold-timeout")
    }
    try check("valid idle heartbeats preserve a known-good static frame") {
      let store = CaptureFrameStore(now: 0)
      store.accept(Data([1]), now: 0)
      for i in 1...60 { store.idle(now: Double(i)) }
      expect(try store.snapshot(now: 60.5) == Data([1]))
    }
    check("silence after a valid image is bounded") {
      let store = CaptureFrameStore(now: 0)
      store.accept(Data([1]), now: 0)
      rejects({ _ = try store.snapshot(now: 10) }, containing: "capture-hold-timeout")
    }
    try check("only the newest frame is stored, no capture backlog") {
      let store = CaptureFrameStore(now: 0)
      for i in 0..<1000 { store.accept(Data([UInt8(i % 256)]), now: Double(i) / 1000) }
      expect(try store.snapshot(now: 1) == Data([231]))
    }
    try check("audio startup delay does not create a catch-up video burst") {
      var cadence = CaptureCadence(fps: 30)
      expect(try cadence.due(now: 120) == 1)
      cadence.started(now: 120)
      expect(try cadence.due(now: 120) == 0)
      expect(try cadence.due(now: 120.034) == 1)
    }
    try check("30 frames for a second of regular output") {
      var cadence = CaptureCadence(fps: 30)
      cadence.started(now: 0)
      var total = 1
      for i in 1..<30 {
        let due = try cadence.due(now: Double(i) / 30 + 0.000001)
        precondition(due == 1)
        cadence.wroteFrame(); total += due
      }
      precondition(total == 30)
    }
    try check("missed timer callbacks repeat frames without shortening video time") {
      var cadence = CaptureCadence(fps: 30)
      cadence.started(now: 0)
      let due = try cadence.due(now: 0.101)
      precondition(due == 3)
      for _ in 0..<due { cadence.wroteFrame() }
      expect(try cadence.due(now: 0.101) == 0)
    }
    check("long output stall fails instead of making unbounded bursts or A/V drift") {
      var cadence = CaptureCadence(fps: 30)
      cadence.started(now: 0)
      rejects({ _ = try cadence.due(now: 1) }, containing: "output-cadence-stalled")
    }
    try check("early timer callbacks do not emit extra frames") {
      var cadence = CaptureCadence(fps: 30)
      cadence.started(now: 0)
      expect(try cadence.due(now: 0.001) == 0)
      expect(try cadence.due(now: 0.02) == 0)
    }
    #if canImport(CoreImage)
    try pixelTests()
    #else
    print("# macOS CoreImage pixel tests unavailable on this platform (not counted as passed)")
    #endif
    print("1..\(count)")
  }

  #if canImport(CoreImage)
  static func pixelBuffer(width: Int, height: Int, rect: CGRect) -> CVPixelBuffer {
    var buffer: CVPixelBuffer?
    precondition(CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
      [kCVPixelBufferBytesPerRowAlignmentKey: 64] as CFDictionary, &buffer) == kCVReturnSuccess)
    let b = buffer!
    CVPixelBufferLockBaseAddress(b, [])
    let p = CVPixelBufferGetBaseAddress(b)!.assumingMemoryBound(to: UInt8.self)
    let stride = CVPixelBufferGetBytesPerRow(b)
    for y in 0..<height {
      for x in 0..<width {
        let inside = rect.contains(CGPoint(x: Double(x) + 0.5, y: Double(y) + 0.5))
        // Red top, blue bottom; magenta surrounding pixels must never appear.
        let top = Double(y) < Double(rect.midY)
        let value: [UInt8] = !inside ? [255, 0, 255, 255] : (top ? [0, 0, 255, 255] : [255, 0, 0, 255])
        for c in 0..<4 { p[y * stride + x * 4 + c] = value[c] }
      }
    }
    CVPixelBufferUnlockBaseAddress(b, [])
    return b
  }
  static func assertCorners(_ data: Data, width: Int, height: Int) {
    precondition(data.count == width * height * 4)
    for (x, y) in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)] {
      let offset = (y * width + x) * 4
      let expected: [UInt8] = y == 0 ? [0, 0, 255, 255] : [255, 0, 0, 255]
      for c in 0..<4 {
        precondition(abs(Int(data[offset + c]) - Int(expected[c])) <= 2,
                     "pixel mismatch x=\(x), y=\(y), channel=\(c): \(data[offset+c]) != \(expected[c])")
      }
    }
  }
  static func pixelTests() throws {
    try check("BGRA identity strips padded rows without swapping channels") {
      let rect = CGRect(x: 0, y: 0, width: 4, height: 4)
      let normalizer = CaptureFrameNormalizer(width: 4, height: 4, logicalSize: rect.size)
      let output = try normalizer.normalize(pixelBuffer(width: 4, height: 4, rect: rect), geometry: geometry(rect))
      assertCorners(output, width: 4, height: 4)
    }
    try check("translated content crops top-left metadata without vertical inversion") {
      let rect = CGRect(x: 2, y: 1, width: 4, height: 4)
      let normalizer = CaptureFrameNormalizer(width: 4, height: 4, logicalSize: rect.size)
      let output = try normalizer.normalize(pixelBuffer(width: 8, height: 8, rect: rect), geometry: geometry(rect))
      assertCorners(output, width: 4, height: 4)
    }
    try check("scaled translated content excludes padding and restores fixed output") {
      let rect = CGRect(x: 2, y: 3, width: 4, height: 4)
      let normalizer = CaptureFrameNormalizer(width: 8, height: 8, logicalSize: CGSize(width: 8, height: 8))
      let output = try normalizer.normalize(pixelBuffer(width: 8, height: 8, rect: rect),
                                           geometry: geometry(rect, scale: 0.5))
      assertCorners(output, width: 8, height: 8)
    }
  }
  #endif
}
