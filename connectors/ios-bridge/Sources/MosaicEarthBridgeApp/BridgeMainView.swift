import SwiftUI
import UniformTypeIdentifiers
import MosaicEarthBridgeKit

struct BridgeMainView: View {
    @Binding var bearerToken: String
    @Binding var cloudOrigin: String

    @State private var showingImporter = false
    @State private var selectedURLs: [URL] = []
    @State private var titleText: String = ""
    @State private var progress: Double = 0.0
    @State private var statusMessage: String = "Ready to bridge LiDAR .USDZ, 360° Panos & Geo-Videos."
    @State private var deliveryLinks: BridgeDeliveryLinks?
    @State private var pairSession: BridgeDevicePairSession?
    @State private var isUploading = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    connectionSection
                    uploadSection
                    if let links = deliveryLinks {
                        resultSection(links)
                    }
                }
                .padding()
            }
            .navigationTitle("MosaicEarth Bridge")
            .fileImporter(
                isPresented: $showingImporter,
                allowedContentTypes: [.usdz, .item, .image, .movie],
                allowsMultipleSelection: true
            ) { result in
                if case let .success(urls) = result {
                    selectedURLs = urls
                    statusMessage = "\(urls.count) file(s) ready for 4 MiB SHA-256 upload."
                }
            }
        }
    }

    private var connectionSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("1. Cloud Account Pairing")
                .font(.headline)
                .foregroundStyle(Color(red: 0.6, green: 0.8, blue: 0.0))
            TextField("https://app.mosaicearth.app", text: $cloudOrigin)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)
            SecureField("Paste Shortcut / Device Token", text: $bearerToken)
                .textFieldStyle(.roundedBorder)
                .onChange(of: bearerToken) { newVal in
                    UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.set(newVal, forKey: "bearer_token")
                }
            if let ps = pairSession {
                Text("Enter code **\(ps.userCode)** at \(ps.verificationUri)")
                    .font(.footnote)
            }
            Button("Pair via 8-Char Device Code") {
                Task { await startPairing() }
            }
            .buttonStyle(.bordered)
        }
        .padding()
        .background(Color.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private var uploadSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("2. Send Scaniverse / Polycam / DJI Capture")
                .font(.headline)
                .foregroundStyle(Color(red: 0.6, green: 0.8, blue: 0.0))
            Button {
                showingImporter = true
            } label: {
                Label(selectedURLs.isEmpty ? "Choose .USDZ, 3D Model, 360° Photo or Video" : "\(selectedURLs.count) file(s) selected", systemImage: "cube.transparent")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)

            TextField("Survey / Deliverable Title (optional)", text: $titleText)
                .textFieldStyle(.roundedBorder)

            if isUploading {
                ProgressView(value: progress)
            }
            Text(statusMessage)
                .font(.caption)
                .foregroundStyle(.secondary)

            Button {
                Task { await performUpload() }
            } label: {
                Text(isUploading ? "Uploading..." : "Upload to MosaicEarth 3D")
                    .fontWeight(.bold)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(Color(red: 0.6, green: 0.8, blue: 0.0))
            .disabled(isUploading || selectedURLs.isEmpty)
        }
        .padding()
        .background(Color.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func resultSection(_ links: BridgeDeliveryLinks) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("✓ Published to MosaicEarth 3D")
                .font(.headline)
                .foregroundStyle(Color(red: 0.6, green: 0.8, blue: 0.0))
            if let url = URL(string: links.viewerUrl) {
                Link("Open Interactive 3D Viewer ↗", destination: url)
                    .fontWeight(.bold)
            }
            HStack {
                Button("Copy Share Link") {
                    #if canImport(UIKit)
                    UIPasteboard.general.string = links.shareUrl
                    #endif
                    statusMessage = "Copied Share / Client Portal URL!"
                }
                .buttonStyle(.bordered)
                Button("Copy <iframe>") {
                    #if canImport(UIKit)
                    UIPasteboard.general.string = links.embedIframe
                    #endif
                    statusMessage = "Copied <iframe> embed code!"
                }
                .buttonStyle(.bordered)
            }
        }
        .padding()
        .background(Color.white.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func startPairing() async {
        guard let originURL = URL(string: cloudOrigin) else { return }
        let client = BridgeClient(config: BridgeConfiguration(origin: originURL, bearerToken: bearerToken))
        do {
            let ps = try await client.startDevicePairing(deviceName: "iPhone (SwiftUI Bridge)")
            pairSession = ps
            for _ in 0..<60 {
                try await Task.sleep(nanoseconds: UInt64(max(2, ps.interval)) * 1_000_000_000)
                if let tok = try? await client.pollDevicePairing(deviceCode: ps.deviceCode) {
                    bearerToken = tok
                    UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.set(tok, forKey: "bearer_token")
                    pairSession = nil
                    statusMessage = "Paired with MosaicEarth!"
                    break
                }
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func performUpload() async {
        guard let originURL = URL(string: cloudOrigin) else { return }
        isUploading = true
        progress = 0.02
        defer { isUploading = false }
        let client = BridgeClient(config: BridgeConfiguration(origin: originURL, bearerToken: bearerToken))
        var accessed: [URL] = []
        for u in selectedURLs where u.startAccessingSecurityScopedResource() {
            accessed.append(u)
        }
        defer { accessed.forEach { $0.stopAccessingSecurityScopedResource() } }
        do {
            let links = try await client.uploadFilesChunked(
                fileURLs: selectedURLs,
                title: titleText,
                onProgress: { pct in
                    Task { @MainActor in progress = pct }
                }
            )
            deliveryLinks = links
            statusMessage = "Asset #\(links.assetId) uploaded!"
        } catch {
            statusMessage = error.localizedDescription
        }
    }
}
