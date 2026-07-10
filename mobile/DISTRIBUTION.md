# Test builds — Android + iOS (for your lead to test)

Same Flutter app, built for two platforms. The backend host URL is **baked at build time**
so the tester installs and it just connects — no manual setup.

## Point the apps at the host

When the host is ready, build with its URL via `--dart-define`:

```bash
# wss:// (recommended — TLS, works over mobile data, no security exceptions)
flutter build apk     --release --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws
flutter build ipa     --release --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws   # on a Mac / CI
```

- No `--dart-define` → falls back to `ws://localhost:8000/ws` (dev/emulator).
- The host is **baked in at build time** — there is no in-app server-URL field (it was removed
  for the MVP). To point a build at a different host, rebuild with a different `--dart-define`.
- `ws://` (no TLS) also works — Android allows cleartext (`usesCleartextTraffic`) and iOS has
  an ATS exception in `Info.plist`. For an **App Store** build with a `wss://` host, remove the
  `NSAllowsArbitraryLoads` exception so iOS uses TLS-only.

## Android — ready now

A release APK is built here and is installable by sideload (debug-signed, fine for testing):

```
build/app/outputs/flutter-apk/app-release.apk
```

Give it to the tester → they enable "Install unknown apps" → install. Or push over USB:
```bash
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

**Cleaner distribution (optional):** Google Play **Internal testing** track — upload an
`.aab` (`flutter build appbundle --release`), add the lead's email as a tester, they install
from a Play link. Needs a Google Play Console account ($25 once) and a real upload keystore
(the current build uses the debug key — replace it via `android/key.properties` +
`signingConfigs` for Play).

## iOS — build on a Mac (step by step for whoever has the MacBook)

The `ios/` project is fully scaffolded and ready to open in Xcode: iOS 13+ target, location +
microphone permissions, an ATS exception (so the `wss://` host works), and `CFBundleDisplayName`
"AI Audio Guide". The backend host is **baked in at build time** via `--dart-define=WS_URL=…`,
so once installed it just connects — nothing to configure on the phone.

Backend host (already deployed, TLS): `wss://178.83.121.62.sslip.io/ws`

### 0. One-time prerequisites on the Mac
- **Xcode** (from the Mac App Store) → open it once, accept the license, let it install components.
- **Flutter SDK** — https://docs.flutter.dev/get-started/install/macos (then `flutter doctor`).
- **CocoaPods** — `sudo gem install cocoapods` (or `brew install cocoapods`). Flutter runs
  `pod install` for you on the first build.
- An **Apple ID** (a free one is enough to install on *your own* iPhone; see path A).

### 1. Get the code and fetch packages
```bash
git clone https://github.com/efrom-K/AI-Guide.git
cd AI-Guide/mobile
flutter pub get          # also generates ios/Podfile + runs pod install on first build
```

### 2. Set the signing team in Xcode (required — the project ships with none)
```bash
open ios/Runner.xcworkspace   # MUST be the .xcworkspace, not the .xcodeproj
```
In Xcode: select the **Runner** target → **Signing & Capabilities** →
- tick **Automatically manage signing**,
- pick your **Team** (your free Apple ID works for installing on your own device),
- if signing fails with "bundle identifier is not available", change **Bundle Identifier**
  from `com.example.aiAudioGuide` to something unique, e.g. `com.<yourname>.aiguide`.

### 3a. Install on your own iPhone — free, simplest (7-day cert, re-run weekly)
Plug in the iPhone (unlock, tap **Trust**), then from `mobile/`:
```bash
flutter devices   # find your iPhone's id
flutter run --release -d <iphone-id> --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws
```
First launch: on the iPhone go to **Settings → General → VPN & Device Management → trust your
developer cert**, reopen the app, and **allow Location + Microphone** when prompted. (You can
also just hit ▶ Run in Xcode — but then set WS_URL via Product → Scheme → Run → Arguments:
`--dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws`, or it defaults to localhost. There is
no in-app URL field, so the `--dart-define` is the only way to set the host.)

### 3b. TestFlight — to send to other testers (needs a paid Apple Developer account, $99/yr)
```bash
flutter build ipa --release --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws
# upload build/ios/ipa/*.ipa via Xcode → Organizer (Distribute App) or the Transporter app,
# then add testers in App Store Connect → TestFlight; they install via the TestFlight app.
```

### Gotchas
- **`flutter run` defaults to `ws://localhost:8000/ws`** if you forget the `--dart-define` — the
  app then can't reach the backend. Always pass the `WS_URL` (there is no in-app URL field to
  fix it afterwards — you must rebuild).
- If pods are stale after a Flutter/plugin change: `cd ios && pod repo update && pod install`.
- Test on **mobile data**, not Wi-Fi — that's what reproduces real connection conditions.
- **Background audio (known follow-up, not done):** iOS pauses the narration when the screen
  locks. To keep the guide talking with the phone in a pocket, add `UIBackgroundModes` → `audio`
  to `Info.plist` **and** configure an `AVAudioSession` playback category (e.g.
  `flutter_tts.setIosAudioCategory(...)`). Don't add the background mode alone — Apple rejects a
  declared capability the app doesn't actually use. Fine to leave for the first TestFlight (the
  guide works foregrounded with the screen on).

### Alternative: Codemagic (cloud build, no Mac needed)
`codemagic.yaml` (in this folder) has three ready workflows: `android-test`, `ios-testflight`
(needs an App Store Connect API key + real bundle id) and `ios-unsigned` (free, builds an
unsigned IPA you sideload with Sideloadly/AltStore). Connect the repo at codemagic.io, set
`WS_URL`, run a workflow.

## Before store submission (not needed for TestFlight/sideload testing)

- **Bundle id / org:** currently `com.example.ai_audio_guide`. Apple/Google reject `com.example`.
  Change it to your own, e.g. re-scaffold platforms with `--org com.yourcompany`, or edit
  `android` `applicationId` + the iOS bundle id in Xcode.
- **Signing:** real Android upload keystore; iOS distribution certificate + provisioning
  (Codemagic/Xcode automatic signing handles iOS).
- **App icon:** still the default Flutter icon — add a real one (e.g. `flutter_launcher_icons`).
