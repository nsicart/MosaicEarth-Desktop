import XCTest
@testable import MosaicEarthBridgeKit

final class BridgeClientTests: XCTestCase {
    func testSHA256HexKnownVector() {
        let digest = BridgeClient.sha256Hex(Data("mosaicearth-ios-bridge".utf8))
        XCTAssertEqual(digest.count, 64)
        XCTAssertEqual(digest, "c509122763a28cfe014d51bfae1793001a2a65ded0f8b89cdd7a76ada354a647")
        let abc = BridgeClient.sha256Hex(Data("abc".utf8))
        XCTAssertEqual(abc, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    func testChunkRangesFourMiB() {
        let total = 9_000_000
        let ranges = BridgeClient.chunkRanges(totalSize: total)
        XCTAssertEqual(ranges.count, 3)
        XCTAssertEqual(ranges[0], 0..<(4 * 1024 * 1024))
        XCTAssertEqual(ranges[1], (4 * 1024 * 1024)..<(8 * 1024 * 1024))
        XCTAssertEqual(ranges[2], (8 * 1024 * 1024)..<total)
    }

    func testEndpointBuilderNormalizesSlashes() {
        let cfg = BridgeConfiguration(origin: URL(string: "https://app.mosaicearth.app/")!, bearerToken: "tok_123")
        let client = BridgeClient(config: cfg)
        XCTAssertEqual(
            client.endpoint("/api/v2/bridge/quick-upload").absoluteString,
            "https://app.mosaicearth.app/api/v2/bridge/quick-upload"
        )
    }
}
