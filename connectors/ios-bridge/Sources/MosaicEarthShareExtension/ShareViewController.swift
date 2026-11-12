#if canImport(UIKit)
import UIKit
import SwiftUI
import UniformTypeIdentifiers
import MosaicEarthBridgeKit

public final class ShareViewController: UIViewController {
    public override func viewDidLoad() {
        super.viewDidLoad()
        let host = UIHostingController(rootView: ShareSheetExtensionView(
            extensionContext: extensionContext
        ))
        addChild(host)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
        host.didMove(toParent: self)
    }
}

struct ShareSheetExtensionView: View {
    let extensionContext: NSExtensionContext?
    @State private var status = "Preparing shared capture..."
    @State private var progress = 0.0
    @State private var finishedLinks: BridgeDeliveryLinks?

    var body: some View {
        VStack(spacing: 16) {
            Text("Send to MosaicEarth 3D")
                .font(.headline)
            ProgressView(value: progress)
            Text(status)
                .font(.caption)
                .multilineTextAlignment(.center)
            if let links = finishedLinks {
                Button("Copy Share Link & Close") {
                    UIPasteboard.general.string = links.shareUrl
                    extensionContext?.completeRequest(returningItems: nil)
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button("Cancel") {
                    extensionContext?.cancelRequest(withError: NSError(domain: "app.mosaicearth.bridge", code: 0))
                }
            }
        }
        .padding(24)
        .task { await runShareUpload() }
    }

    private func runShareUpload() async {
        let defaults = UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)
        let token = defaults?.string(forKey: "bearer_token") ?? ""
        let originStr = defaults?.string(forKey: "cloud_origin") ?? "https://app.mosaicearth.app"
        guard !token.isEmpty, let originURL = URL(string: originStr) else {
            status = "Open MosaicEarth Bridge once to pair your account."
            return
        }
        var copiedURLs: [URL] = []
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)

        for item in items {
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.item.identifier) {
                    if let localCopy = await copyProviderFile(provider, to: tempDir) {
                        copiedURLs.append(localCopy)
                    }
                }
            }
        }
        guard !copiedURLs.isEmpty else {
            status = "No compatible 3D, photo, or video files found in Share Sheet."
            return
        }
        let client = BridgeClient(config: BridgeConfiguration(origin: originURL, bearerToken: token))
        do {
            status = "Uploading \(copiedURLs.count) file(s) via SHA-256 chunks..."
            let links = try await client.uploadFilesChunked(fileURLs: copiedURLs) { pct in
                Task { @MainActor in progress = pct }
            }
            finishedLinks = links
            UIPasteboard.general.string = links.shareUrl
            status = "Uploaded & copied link to clipboard!"
        } catch {
            status = error.localizedDescription
        }
    }

    private func copyProviderFile(_ provider: NSItemProvider, to tempDir: URL) async -> URL? {
        await withCheckedContinuation { cont in
            provider.loadFileRepresentation(forTypeIdentifier: UTType.item.identifier) { url, _ in
                guard let src = url else {
                    cont.resume(returning: nil)
                    return
                }
                let dst = tempDir.appendingPathComponent(src.lastPathComponent)
                try? FileManager.default.removeItem(at: dst)
                do {
                    try FileManager.default.copyItem(at: src, to: dst)
                    cont.resume(returning: dst)
                } catch {
                    cont.resume(returning: nil)
                }
            }
        }
    }
}
#endif
