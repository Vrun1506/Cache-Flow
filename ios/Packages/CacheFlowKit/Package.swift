// swift-tools-version: 6.0
import PackageDescription

// Pure-Swift logic shared by the iPad app: board geometry, ink ownership, line
// segmentation, the v2 wire contract, the lesson engine and the mock. Nothing here imports UIKit or
// PencilKit, so `swift test` runs on the Mac with no simulator.
let package = Package(
    name: "CacheFlowKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "CacheFlowKit", targets: ["CacheFlowKit"]),
    ],
    targets: [
        .target(name: "CacheFlowKit"),
        .testTarget(name: "CacheFlowKitTests", dependencies: ["CacheFlowKit"], resources: [.copy("Fixtures")]),
    ]
)
