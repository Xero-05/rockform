// swift-tools-version: 6.0
import PackageDescription

var targets: [Target] = [
    .target(name: "CaptureCore"),
    .executableTarget(name: "CaptureCheck", dependencies: ["CaptureCore"]),
    .testTarget(name: "CaptureCoreTests", dependencies: ["CaptureCore"])
]
var products: [Product] = []
#if !os(Linux) || ROCKFORM_IOS
targets.append(.target(name: "RockformCapture", dependencies: ["CaptureCore"]))
products.append(.library(name: "RockformCapture", targets: ["RockformCapture"]))
#endif
// xtool evaluates the manifest on Linux. Use the environment flag for the iOS build;
// ordinary `swift test` can still exercise the platform-independent export code.
import Foundation
if ProcessInfo.processInfo.environment["ROCKFORM_IOS"] == "1" && !targets.contains(where: { $0.name == "RockformCapture" }) {
    targets.append(.target(name: "RockformCapture", dependencies: ["CaptureCore"]))
    products.append(.library(name: "RockformCapture", targets: ["RockformCapture"]))
}
let package = Package(
    name: "RockformCapture", platforms: [.iOS(.v17)], products: products,
    targets: targets, swiftLanguageModes: [.v5]
)
