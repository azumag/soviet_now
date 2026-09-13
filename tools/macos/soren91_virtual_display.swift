// Soren91 macOS offscreen helper: virtual display holder (Issue #303).
//
// Places the Soren91 Chrome window on a VIRTUAL display so it never appears
// on any physical screen, while the existing ScreenCaptureKit window-targeted
// capture (tools/macos/soren91_window_capture.swift, unchanged) keeps working
// — ScreenCaptureKit binds to a window by identity, so it captures the window
// even when that window lives entirely on a virtual (headless) display.
//
// Private API note: `CGVirtualDisplay` is a non-public CoreGraphics class, so
// this file uses runtime class lookup (`NSClassFromString`) plus `@objc`
// protocol dispatch and KVC — no private headers are needed. If the classes
// or selectors are absent (future macOS), this exits 1 with a JSON error
// (fail-soft); it never crashes on a missing selector.
//
// Constraints (measured on macOS 26.6.1 / M4, kept as code comments so future
// ports do not regress them):
// - `CGVirtualDisplayMode`'s initializer is
//   `initWithWidth:height:refreshRate:` where refreshRate is a Double. Passing
//   a UInt32 makes `applySettings:` return NO.
// - The descriptor's dispatch queue must be set via `setDispatchQueue:`
//   directly (KVC `setValue:forKey:` on `queue` does not stick).
// - Only ONE virtual display per process; `destroy` is ineffective — the
//   display is released only when this process exits. This holder therefore
//   stays alive until SIGTERM/SIGINT. Do NOT make it long-lived and reuse it
//   for several displays; restart the process to recreate.
// - Fixed vendorID/productID/serialNum so macOS remembers window placement.
// - A bare CLI executable has no WindowServer connection until an
//   NSApplication exists; `.accessory` keeps this headless (no Dock icon).
//
// Protocol (stderr, single JSON line; stdout is never used):
// - Normal mode: `{"ok":true,"displayID":N,"bounds":{x,y,width,height}}`,
//   then survives until SIGTERM/SIGINT (exit 0).
// - `--list` mode: `{"ok":true,"displays":[{"id":N,"bounds":{...}}, ...]}` for
//   every online display, then exits 0 without creating anything.
// - Failure: `{"ok":false,"error":"..."}` and exit 1.
//
// Safety: this helper never captures pixels and never enumerates window
// titles — only display IDs and bounds (`CGGetOnlineDisplayList`).
import AppKit
import CoreGraphics
import Foundation
import ObjectiveC

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

// MARK: - Private CGVirtualDisplay access via the ObjC runtime
//
// Ground-truth selector spellings and scalar types come from dumping the
// classes' method lists on the target Mac (see `method_getTypeEncoding`
// output — e.g. widths are UInt32 `I`, refreshRate is Double `d`). We call
// through `method_getImplementation` + typed `@convention(c)` function
// pointers: plain `@objc`-protocol casts do NOT work here because these
// classes never formally adopt such a protocol (the cast yields nil), and
// `perform(_:with:)` cannot pass scalar (non-object) arguments. Plain
// properties additionally work through KVC (`setValue:forKey:`) — including
// `_displayInfo`, which has no bulk setter but is KVC-accessible via its
// ivar; only the dispatch queue needs a direct `setDispatchQueue:` call.

func virtualClass(_ name: String, as what: String) -> AnyClass {
  guard let cls = NSClassFromString(name) else {
    failClosed("\(what) class \(name) is unavailable on this macOS version")
  }
  return cls
}

func implementation(of cls: AnyClass, _ selector: Selector, what: String) -> IMP {
  guard let method = class_getInstanceMethod(cls, selector) else {
    failClosed("\(what) does not implement \(NSStringFromSelector(selector)) on this macOS version")
  }
  return method_getImplementation(method)
}

func allocate(_ cls: AnyClass, what: String) -> Unmanaged<AnyObject> {
  // Returns the alloc'd instance as +1 Unmanaged WITHOUT claiming it in ARC:
  // per ObjC convention the matching init call below consumes this +1 and
  // its own +1 return is claimed with takeRetainedValue() at the call site.
  // (Claiming both — e.g. takeRetainedValue() on the alloc temp AND on the
  // init result — over-releases when init returns self, and traps. Measured.)
  guard let allocated = (cls as? NSObject.Type)?.perform(NSSelectorFromString("alloc")) else {
    failClosed("\(what) alloc failed on this macOS version")
  }
  return allocated
}

// MARK: - CLI

struct HolderOptions {
  var width: Int = 1920
  var height: Int = 1080
  var fps: Double = 60
  var name: String = "SOREN91-VD"
  var listOnly: Bool = false
}

func printUsageAndFail() -> Never {
  emitStatus(["ok": false, "error": "usage: soren91_virtual_display [--width N] [--height N] [--fps N] [--name NAME] [--list]"])
  exit(2)
}

func parseHolderArgs(_ argv: [String]) -> HolderOptions {
  var options = HolderOptions()
  var index = 0
  while index < argv.count {
    let arg = argv[index]
    func nextValue() -> String {
      index += 1
      guard index < argv.count else { printUsageAndFail() }
      return argv[index]
    }
    switch arg {
    case "--width":
      guard let value = Int(nextValue()), value >= 640, value <= 4096 else {
        failClosed("--width must be an integer 640..4096")
      }
      options.width = value
    case "--height":
      guard let value = Int(nextValue()), value >= 360, value <= 2160 else {
        failClosed("--height must be an integer 360..2160")
      }
      options.height = value
    case "--fps":
      guard let value = Double(nextValue()), value >= 30, value <= 120 else {
        failClosed("--fps must be a number 30..120")
      }
      options.fps = value
    case "--name":
      let value = nextValue()
      if value.isEmpty { failClosed("--name must not be empty") }
      options.name = value
    case "--list":
      options.listOnly = true
    case "--help", "-h":
      FileHandle.standardError.write("usage: soren91_virtual_display [--width N] [--height N] [--fps N] [--name NAME] [--list]\n".data(using: .utf8)!)
      exit(0)
    default:
      emitStatus(["ok": false, "error": "unknown argument: \(arg)"])
      exit(2)
    }
    index += 1
  }
  return options
}

// MARK: - Public display enumeration (no private API, no pixels)

func onlineDisplaysPayload() -> [[String: Any]] {
  var count: UInt32 = 0
  guard CGGetOnlineDisplayList(0, nil, &count) == .success else { return [] }
  var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
  var fetched: UInt32 = 0
  guard CGGetOnlineDisplayList(count, &ids, &fetched) == .success else { return [] }
  return ids.prefix(Int(fetched)).map { id in
    let bounds = CGDisplayBounds(id)
    return [
      "id": id,
      "bounds": [
        "x": Int(bounds.origin.x),
        "y": Int(bounds.origin.y),
        "width": Int(bounds.size.width),
        "height": Int(bounds.size.height),
      ] as [String: Any],
    ] as [String: Any]
  }
}

// Fixed identifiers so macOS remembers window placement on the virtual
// display across holder restarts.
let fixedVendorID: UInt32 = 0x736F // "so"
let fixedProductID: UInt32 = 0x3931 // "91"
let fixedSerialNum: UInt32 = 30391

// Holder-lifetime retains for the private display objects (see main()).
var retainedObjects: [AnyObject] = []

@main
struct Soren91VirtualDisplay {
  static func main() {
    NSApplication.shared.setActivationPolicy(.accessory)

    let options = parseHolderArgs(Array(CommandLine.arguments.dropFirst()))

    if options.listOnly {
      emitStatus(["ok": true, "displays": onlineDisplaysPayload()])
      exit(0)
    }

    // Leak detection: a previous holder that did not exit leaves its virtual
    // display online (destroy is ineffective; only process exit releases it).
    // Our fixed vendor/product/serial make it recognizable via public
    // CoreGraphics queries.
    do {
      var count: UInt32 = 0
      if CGGetOnlineDisplayList(0, nil, &count) == .success, count > 0 {
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        var fetched: UInt32 = 0
        if CGGetOnlineDisplayList(count, &ids, &fetched) == .success {
          for id in ids.prefix(Int(fetched)) {
            if CGDisplayVendorNumber(id) == fixedVendorID,
               CGDisplayModelNumber(id) == fixedProductID,
               CGDisplaySerialNumber(id) == fixedSerialNum
            {
              let bounds = CGDisplayBounds(id)
              failClosed(
                "a SOREN91 virtual display (id \(id), bounds \(Int(bounds.origin.x)),\(Int(bounds.origin.y)) \(Int(bounds.size.width))x\(Int(bounds.size.height))) from a previous run is still online — " +
                "terminate the leaked soren91_virtual_display holder process (only process exit releases a CGVirtualDisplay) and retry"
              )
            }
          }
        }
      }
    }

    let descriptorClass: AnyClass = virtualClass("CGVirtualDisplayDescriptor", as: "virtual display descriptor")
    let modeClass: AnyClass = virtualClass("CGVirtualDisplayMode", as: "virtual display mode")
    let settingsClass: AnyClass = virtualClass("CGVirtualDisplaySettings", as: "virtual display settings")
    let displayClass: AnyClass = virtualClass("CGVirtualDisplay", as: "virtual display")

    typealias VoidU32Fn = @convention(c) (AnyObject, Selector, UInt32) -> Void
    typealias VoidObjFn = @convention(c) (AnyObject, Selector, AnyObject) -> Void
    typealias VoidSizeFn = @convention(c) (AnyObject, Selector, CGSize) -> Void

    // Descriptor. Scalar/object properties go through KVC (the setters for
    // vendor/product/serial write into the internal `_displayInfo` dict —
    // verified by reading it back), EXCEPT the dispatch queue, for which KVC
    // does not stick (measured): `setDispatchQueue:`/`setQueue:` are called
    // directly. `_displayInfo` must be a non-nil (possibly empty) dict before
    // initWithDescriptor:, which inserts it into a 5-entry dictionary and
    // throws NSInvalidArgumentException on nil (measured via disassembly).
    // Order matters: install the empty dict FIRST — the scalar setters below
    // populate it (DisplayVendorID/DisplayProductID/DisplaySerialNumber).
    guard let descriptor = (descriptorClass as? NSObject.Type)?.init() as? NSObject else {
      failClosed("CGVirtualDisplayDescriptor cannot be instantiated on this macOS version")
    }
    descriptor.setValue(NSMutableDictionary(), forKey: "displayInfo")
    descriptor.setValue(options.name, forKey: "name")
    descriptor.setValue(NSNumber(value: fixedSerialNum), forKey: "serialNum")
    descriptor.setValue(NSNumber(value: fixedVendorID), forKey: "vendorID")
    descriptor.setValue(NSNumber(value: fixedProductID), forKey: "productID")
    descriptor.setValue(NSNumber(value: options.width), forKey: "maxPixelsWide")
    descriptor.setValue(NSNumber(value: options.height), forKey: "maxPixelsHigh")
    // KVC does not stick for the queue (measured); call setDispatchQueue:.
    // Set BOTH setQueue: and setDispatchQueue:: initWithDescriptor: reads the
    // `queue` property when building its internal dictionary, and a nil entry
    // throws NSInvalidArgumentException (measured).
    let vdQueue = DispatchQueue(label: "soren91.virtualdisplay")
    for queueSetter in ["setDispatchQueue:", "setQueue:"] {
      let queueSelector = NSSelectorFromString(queueSetter)
      let setQueue = unsafeBitCast(implementation(of: descriptorClass, queueSelector, what: "CGVirtualDisplayDescriptor"), to: VoidObjFn.self)
      setQueue(descriptor, queueSelector, vdQueue)
    }
    // Physical size hint for a 16:9 panel (matches DeskPad's usage); the
    // captured mode size itself comes from CGVirtualDisplayMode below.
    let sizeSelector = NSSelectorFromString("setSizeInMillimeters:")
    let setSize = unsafeBitCast(implementation(of: descriptorClass, sizeSelector, what: "CGVirtualDisplayDescriptor"), to: VoidSizeFn.self)
    setSize(descriptor, sizeSelector, CGSize(width: 477, height: 268))

    // Mode: initWithWidth:height:refreshRate: takes (UInt32, UInt32, Double) —
    // passing refreshRate as UInt32 makes applySettings: return NO (measured).
    typealias ModeInitFn = @convention(c) (AnyObject, Selector, UInt32, UInt32, Double) -> Unmanaged<AnyObject>
    let modeInitSelector = NSSelectorFromString("initWithWidth:height:refreshRate:")
    let modeInit = unsafeBitCast(implementation(of: modeClass, modeInitSelector, what: "CGVirtualDisplayMode"), to: ModeInitFn.self)
    let mode = modeInit(allocate(modeClass, what: "CGVirtualDisplayMode").takeUnretainedValue(), modeInitSelector, UInt32(options.width), UInt32(options.height), options.fps).takeRetainedValue()

    // Settings.
    guard let settings: AnyObject = (settingsClass as? NSObject.Type)?.init() else {
      failClosed("CGVirtualDisplaySettings cannot be instantiated on this macOS version")
    }
    let hiDPISelector = NSSelectorFromString("setHiDPI:")
    let setHiDPI = unsafeBitCast(implementation(of: settingsClass, hiDPISelector, what: "CGVirtualDisplaySettings"), to: VoidU32Fn.self)
    setHiDPI(settings, hiDPISelector, 0)
    let modesSelector = NSSelectorFromString("setModes:")
    let setModes = unsafeBitCast(implementation(of: settingsClass, modesSelector, what: "CGVirtualDisplaySettings"), to: VoidObjFn.self)
    setModes(settings, modesSelector, [mode] as NSArray)

    // Display: initWithDescriptor: + applySettings: (returns ObjC BOOL).
    typealias DisplayInitFn = @convention(c) (AnyObject, Selector, AnyObject) -> Unmanaged<AnyObject>
    typealias ApplySettingsFn = @convention(c) (AnyObject, Selector, AnyObject) -> Bool
    let displayInitSelector = NSSelectorFromString("initWithDescriptor:")
    let displayInit = unsafeBitCast(implementation(of: displayClass, displayInitSelector, what: "CGVirtualDisplay"), to: DisplayInitFn.self)
    let display = displayInit(allocate(displayClass, what: "CGVirtualDisplay").takeUnretainedValue(), displayInitSelector, descriptor).takeRetainedValue()
    let applySelector = NSSelectorFromString("applySettings:")
    let applySettings = unsafeBitCast(implementation(of: displayClass, applySelector, what: "CGVirtualDisplay"), to: ApplySettingsFn.self)
    guard applySettings(display, applySelector, settings) else {
      failClosed("CGVirtualDisplay applySettings: returned NO (mode \(options.width)x\(options.height)@\(options.fps))")
    }
    typealias DisplayIDFn = @convention(c) (AnyObject, Selector) -> UInt32
    let displayIDSelector = NSSelectorFromString("displayID")
    let getDisplayID = unsafeBitCast(implementation(of: displayClass, displayIDSelector, what: "CGVirtualDisplay"), to: DisplayIDFn.self)
    let displayID = getDisplayID(display, displayIDSelector)
    guard displayID != 0 else {
      failClosed("CGVirtualDisplay applySettings: succeeded but displayID is 0")
    }
    // Retain display/mode/settings/descriptor/queue for the holder's
    // lifetime: only process exit releases a CGVirtualDisplay, so nothing
    // here may be deallocated while we survive in the RunLoop below.
    retainedObjects = [display, mode, settings, descriptor, vdQueue]

    // Report the REAL bounds (never assume origin/size from the request).
    let bounds = CGDisplayBounds(displayID)
    emitStatus([
      "ok": true,
      "displayID": displayID,
      "bounds": [
        "x": Int(bounds.origin.x),
        "y": Int(bounds.origin.y),
        "width": Int(bounds.size.width),
        "height": Int(bounds.size.height),
      ] as [String: Any],
    ])

    // One process holds exactly one virtual display; only process exit
    // releases it, so survive here until SIGTERM/SIGINT.
    signal(SIGINT) { _ in exit(0) }
    signal(SIGTERM) { _ in exit(0) }
    RunLoop.main.run()
  }
}
