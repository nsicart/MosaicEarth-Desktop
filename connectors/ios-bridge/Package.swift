// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MosaicEarthBridge",
    platforms: [
        .iOS(.v16),
        .macOS(.v13)
    ],
    products: [
        .library(
            name: "MosaicEarthBridgeKit",
            targets: ["MosaicEarthBridgeKit"]
        )
    ],
    targets: [
        .target(
            name: "MosaicEarthBridgeKit",
            path: "Sources/MosaicEarthBridgeKit"
        ),
        .testTarget(
            name: "MosaicEarthBridgeKitTests",
            dependencies: ["MosaicEarthBridgeKit"],
            path: "Tests/MosaicEarthBridgeKitTests"
        )
    ]
)
