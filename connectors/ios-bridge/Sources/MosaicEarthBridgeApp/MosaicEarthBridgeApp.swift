import SwiftUI
import MosaicEarthBridgeKit

@main
struct MosaicEarthBridgeApp: App {
    @State private var bearerToken: String = UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.string(forKey: "bearer_token") ?? ""
    @State private var cloudOrigin: String = UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.string(forKey: "cloud_origin") ?? "https://app.mosaicearth.app"

    var body: some Scene {
        WindowGroup {
            BridgeMainView(bearerToken: $bearerToken, cloudOrigin: $cloudOrigin)
                .preferredColorScheme(.dark)
                .onOpenURL { url in
                    guard url.scheme == "mosaicearth",
                          let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
                    if let tok = comps.queryItems?.first(where: { $0.name == "token" })?.value, !tok.isEmpty {
                        bearerToken = tok
                        UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.set(tok, forKey: "bearer_token")
                    }
                    if let org = comps.queryItems?.first(where: { $0.name == "origin" })?.value, !org.isEmpty {
                        cloudOrigin = org
                        UserDefaults(suiteName: BridgeConfiguration.appGroupIdentifier)?.set(org, forKey: "cloud_origin")
                    }
                }
        }
    }
}
