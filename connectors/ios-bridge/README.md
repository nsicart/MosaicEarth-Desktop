# MosaicEarth Bridge for iOS (`0.4.0`)

Send **Apple LiDAR `.USDZ` scans**, **3D meshes (`.glb`, `.obj`)**, **Gaussian Splats (`.ply`, `.splat`, `.spz`)**, **360° equirectangular panoramas**, **Drone Geo-Videos (`.mp4` + `.srt`/`.gpx`)**, and **GeoTIFFs** directly from **Scaniverse, Polycam, 3d Scanner App, DJI Fly, Insta360, Photos, and iOS Files** straight to **MosaicEarth 3D** — with instant **3D Viewer**, **Client Delivery Portal**, and **`<iframe>` embed** links.

---

## Option A — Instant Mobile Web App (PWA, 0 € · Recommended Today)

1. Open **`https://app.mosaicearth.app/bridge`** in **Safari** on your iPhone or iPad.
2. Sign in to your MosaicEarth account.
3. Tap **Share → Add to Home Screen** to install **ME Bridge** as a standalone full-screen app.
4. Pick any `.usdz`, `.glb`, `.ply`, 360° `.jpg`, or drone `.mp4` + `.srt` — uploads use **resumable `4 MiB` `SHA-256` verified blocks** and automatically convert Apple `.USDZ` scans to web-ready PBR `.GLB` + 3D Tiles on the server.

---

## Option B — Official iOS Share Sheet Action (`Send_to_MosaicEarth.shortcut`)

Adds **"Send to MosaicEarth"** directly inside the native iOS *Share Sheet* across Scaniverse, Polycam, Files, and Photos without needing App Store / TestFlight:

1. Visit **`https://app.mosaicearth.app/bridge`** on your iPhone/iPad and tap **🔑 Get Shortcut Token** (copies your revocable 90-day Bearer token).
2. Open **`Shortcut/Send_to_MosaicEarth.shortcut`** included in this ZIP on your iPhone/iPad to add it to Apple **Shortcuts**.
3. Paste your token when prompted during setup.
4. From any iOS app (*Scaniverse → Share → Export USDZ → Send to MosaicEarth*), tap the Shortcut: it uploads the capture to `/api/v2/bridge/quick-upload`, copies the **Share / Client Portal URL** to your clipboard, and opens the interactive 3D model!

---

## Option C — Open-Source Native SwiftUI App + Share Extension (Xcode / TestFlight)

This directory contains a complete Swift 5.9+ package (`MosaicEarthBridgeKit`) and an **XcodeGen** specification (`project.yml`) for building the native iOS 16+ app (`MosaicEarthBridge`) and system Share Sheet extension (`MosaicEarthShareExtension`):

```bash
cd connectors/ios-bridge
swift test
brew install xcodegen
xcodegen generate
open MosaicEarthBridge.xcodeproj
```

Configure your Apple Developer Team in Xcode under *Signing & Capabilities* (App Group `group.app.mosaicearth.bridge`) to deploy to your personal iPhone/iPad or publish to TestFlight.
