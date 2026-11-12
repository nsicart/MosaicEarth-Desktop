import CryptoKit
import Foundation

public struct BridgeConfiguration: Codable, Equatable, Sendable {
    public var origin: URL
    public var bearerToken: String

    public static let appGroupIdentifier = "group.app.mosaicearth.bridge"
    public static let chunkSizeBytes = 4 * 1024 * 1024 // 4 MiB resumable SHA-256 blocks

    public init(origin: URL = URL(string: "https://app.mosaicearth.app")!, bearerToken: String = "") {
        self.origin = origin
        self.bearerToken = bearerToken
    }
}

public struct BridgeDevicePairSession: Codable, Equatable, Sendable {
    public let deviceCode: String
    public let userCode: String
    public let verificationUri: String
    public let verificationUriComplete: String
    public let expiresIn: Int
    public let interval: Int

    enum CodingKeys: String, CodingKey {
        case deviceCode = "device_code"
        case userCode = "user_code"
        case verificationUri = "verification_uri"
        case verificationUriComplete = "verification_uri_complete"
        case expiresIn = "expires_in"
        case interval
    }
}

public struct BridgeSurveyTarget: Codable, Identifiable, Equatable, Sendable {
    public let id: Int
    public let name: String
    public let parentProjectId: Int?
    public let captureDate: String?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case parentProjectId = "parent_project_id"
        case captureDate = "capture_date"
    }
}

public struct BridgeDeliveryLinks: Codable, Equatable, Sendable {
    public let projectId: Int
    public let assetId: Int
    public let visibility: String
    public let viewerUrl: String
    public let shareUrl: String
    public let embedUrl: String
    public let embedIframe: String

    enum CodingKeys: String, CodingKey {
        case projectId = "project_id"
        case assetId = "asset_id"
        case visibility
        case viewerUrl = "viewer_url"
        case shareUrl = "share_url"
        case embedUrl = "embed_url"
        case embedIframe = "embed_iframe"
    }
}

public enum BridgeClientError: LocalizedError {
    case unauthorized
    case authorizationPending
    case serverError(status: Int, message: String)
    case emptySelection

    public var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "Sign in or pair this iPhone/iPad with MosaicEarth first."
        case .authorizationPending:
            return "Waiting for approval on MosaicEarth Bridge..."
        case let .serverError(status, message):
            return "HTTP \(status): \(message)"
        case .emptySelection:
            return "Select at least one 3D scan, 360° panorama, or drone video."
        }
    }
}

public final class BridgeClient: @unchecked Sendable {
    public var config: BridgeConfiguration
    private let session: URLSession

    public init(config: BridgeConfiguration = BridgeConfiguration(), session: URLSession = .shared) {
        self.config = config
        self.session = session
    }

    public static func sha256Hex(_ data: Data) -> String {
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    public static func chunkRanges(totalSize: Int, startOffset: Int = 0, chunkSize: Int = BridgeConfiguration.chunkSizeBytes) -> [Range<Int>] {
        guard totalSize > startOffset, chunkSize > 0 else { return [] }
        var ranges: [Range<Int>] = []
        var offset = max(0, startOffset)
        while offset < totalSize {
            let end = min(totalSize, offset + chunkSize)
            ranges.append(offset..<end)
            offset = end
        }
        return ranges
    }

    public func endpoint(_ path: String) -> URL {
        let trimmedBase = config.origin.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let cleanPath = path.hasPrefix("/") ? path : ("/" + path)
        return URL(string: trimmedBase + cleanPath)!
    }

    private func authorizedRequest(_ path: String, method: String = "GET", contentType: String? = nil) -> URLRequest {
        var req = URLRequest(url: endpoint(path))
        req.httpMethod = method
        if !config.bearerToken.isEmpty {
            req.setValue("Bearer \(config.bearerToken)", forHTTPHeaderField: "Authorization")
        }
        if let ct = contentType {
            req.setValue(ct, forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    public func startDevicePairing(deviceName: String = "iPhone / iPad") async throws -> BridgeDevicePairSession {
        var req = authorizedRequest("/api/v2/bridge/device/start", method: "POST", contentType: "application/json")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["device_name": deviceName])
        let (data, resp) = try await session.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 500
        guard status == 200 else {
            throw BridgeClientError.serverError(status: status, message: String(data: data, encoding: .utf8) ?? "Pairing failed")
        }
        return try JSONDecoder().decode(BridgeDevicePairSession.self, from: data)
    }

    public func pollDevicePairing(deviceCode: String) async throws -> String {
        var req = authorizedRequest("/api/v2/bridge/device/poll", method: "POST", contentType: "application/json")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["device_code": deviceCode])
        let (data, resp) = try await session.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 500
        if status == 428 {
            throw BridgeClientError.authorizationPending
        }
        guard status == 200 else {
            throw BridgeClientError.serverError(status: status, message: String(data: data, encoding: .utf8) ?? "Pairing expired")
        }
        let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        guard let tok = obj["access_token"] as? String, !tok.isEmpty else {
            throw BridgeClientError.unauthorized
        }
        config.bearerToken = tok
        return tok
    }

    public func listProjects() async throws -> [BridgeSurveyTarget] {
        let req = authorizedRequest("/api/v2/bridge/projects")
        let (data, resp) = try await session.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 500
        if status == 401 { throw BridgeClientError.unauthorized }
        guard status == 200 else {
            throw BridgeClientError.serverError(status: status, message: String(data: data, encoding: .utf8) ?? "")
        }
        struct Wrapper: Decodable { let projects: [BridgeSurveyTarget] }
        return try JSONDecoder().decode(Wrapper.self, from: data).projects
    }

    public func createSurvey(name: String, captureDate: String = "") async throws -> BridgeSurveyTarget {
        var req = authorizedRequest("/api/v2/bridge/projects", method: "POST", contentType: "application/json")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["name": name, "capture_date": captureDate])
        let (data, resp) = try await session.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 500
        if status == 401 { throw BridgeClientError.unauthorized }
        guard status == 200 else {
            throw BridgeClientError.serverError(status: status, message: String(data: data, encoding: .utf8) ?? "")
        }
        struct Wrapper: Decodable { let project: BridgeSurveyTarget }
        return try JSONDecoder().decode(Wrapper.self, from: data).project
    }

    public func uploadFilesChunked(
        fileURLs: [URL],
        projectId: Int = 0,
        title: String = "",
        captureDate: String = "",
        latitude: Double? = nil,
        longitude: Double? = nil,
        visibility: String = "",
        onProgress: (@Sendable (Double) -> Void)? = nil
    ) async throws -> BridgeDeliveryLinks {
        guard !fileURLs.isEmpty else { throw BridgeClientError.emptySelection }
        let effectiveTitle = title.isEmpty ? fileURLs[0].deletingPathExtension().lastPathComponent : title
        let targetPid: Int
        if projectId > 0 {
            targetPid = projectId
        } else {
            targetPid = try await createSurvey(name: effectiveTitle, captureDate: captureDate).id
        }

        let uploadId = UUID().uuidString.lowercased()
        var filesMeta: [[String: Any]] = []
        var totalBytes = 0
        for url in fileURLs {
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            let sz = (attrs[.size] as? NSNumber)?.intValue ?? 0
            filesMeta.append(["name": url.lastPathComponent, "size": sz])
            totalBytes += sz
        }

        var details: [String: Any] = [
            "title": effectiveTitle,
            "capture_date": captureDate,
            "capture_method": "MosaicEarth Bridge for iOS (SwiftUI)"
        ]
        if let lat = latitude { details["lat"] = lat }
        if let lon = longitude { details["lon"] = lon }

        var startReq = authorizedRequest("/api/v2/tasks/\(targetPid)/asset-uploads/\(uploadId)", method: "PUT", contentType: "application/json")
        startReq.httpBody = try JSONSerialization.data(withJSONObject: [
            "origin": "imported",
            "files": filesMeta,
            "details": details
        ])
        let (startData, startResp) = try await session.data(for: startReq)
        let startStatus = (startResp as? HTTPURLResponse)?.statusCode ?? 500
        if startStatus == 401 { throw BridgeClientError.unauthorized }
        guard (200..<300).contains(startStatus) else {
            throw BridgeClientError.serverError(status: startStatus, message: String(data: startData, encoding: .utf8) ?? "")
        }

        var sentBytes = 0
        for (idx, fileURL) in fileURLs.enumerated() {
            let handle = try FileHandle(forReadingFrom: fileURL)
            defer { try? handle.close() }
            let attrs = try FileManager.default.attributesOfItem(atPath: fileURL.path)
            let fileSize = (attrs[.size] as? NSNumber)?.intValue ?? 0
            for range in Self.chunkRanges(totalSize: fileSize) {
                try handle.seek(toOffset: UInt64(range.lowerBound))
                let chunk = try handle.read(upToCount: range.count) ?? Data()
                let digest = Self.sha256Hex(chunk)
                var blkReq = authorizedRequest(
                    "/api/v2/tasks/\(targetPid)/asset-uploads/\(uploadId)/files/\(idx)?offset=\(range.lowerBound)",
                    method: "PUT",
                    contentType: "application/octet-stream"
                )
                blkReq.setValue(digest, forHTTPHeaderField: "X-Content-SHA256")
                blkReq.httpBody = chunk
                let (blkData, blkResp) = try await session.data(for: blkReq)
                let blkStatus = (blkResp as? HTTPURLResponse)?.statusCode ?? 500
                guard (200..<300).contains(blkStatus) else {
                    throw BridgeClientError.serverError(status: blkStatus, message: String(data: blkData, encoding: .utf8) ?? "")
                }
                sentBytes += chunk.count
                onProgress?(Double(sentBytes) / Double(max(1, totalBytes)))
            }
        }

        let finReq = authorizedRequest("/api/v2/tasks/\(targetPid)/asset-uploads/\(uploadId)/finish", method: "POST")
        let (finData, finResp) = try await session.data(for: finReq)
        let finStatus = (finResp as? HTTPURLResponse)?.statusCode ?? 500
        guard (200..<300).contains(finStatus) else {
            throw BridgeClientError.serverError(status: finStatus, message: String(data: finData, encoding: .utf8) ?? "")
        }
        let finObj = (try? JSONSerialization.jsonObject(with: finData) as? [String: Any]) ?? [:]
        let aid = (finObj["asset_id"] as? Int) ?? ((finObj["asset_ids"] as? [Int])?.first ?? 0)

        var linkReq = authorizedRequest("/api/v2/bridge/share-links", method: "POST", contentType: "application/json")
        linkReq.httpBody = try JSONSerialization.data(withJSONObject: [
            "project_id": targetPid,
            "asset_id": aid,
            "visibility": visibility
        ])
        let (linkData, linkResp) = try await session.data(for: linkReq)
        let linkStatus = (linkResp as? HTTPURLResponse)?.statusCode ?? 500
        guard (200..<300).contains(linkStatus) else {
            throw BridgeClientError.serverError(status: linkStatus, message: String(data: linkData, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(BridgeDeliveryLinks.self, from: linkData)
    }
}
