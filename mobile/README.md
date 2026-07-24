# AI Audio Guide — Flutter client

Mobile/desktop/web client for the AI Audio Guide backend. Streams position to the
backend over WebSocket, **speaks** the guide's narration aloud, shows where you are on
an **OpenStreetMap** map, lets you **ask by voice or text**, and runs in **8 languages**.

## Features
- **Multilingual** — 8 languages (English, Русский, Español, Français, Deutsch, Italiano,
  Português, 中文). The picker in the app bar switches the **UI**, the **TTS voice**, and the
  **guide's narration + speech recognition** at once; the chosen language is sent to the
  backend (`{type:"language"}`) on every (re)connect. On first launch it auto-selects the
  **system language**, falling back to English. UI strings come from `lib/l10n/*.arb` via
  Flutter `gen-l10n`.
- **Map-first dark UI** — the map fills the screen (CARTO dark tiles via `flutter_map`, no
  API key) with your bearing arrow and a pin per narrated place (current = amber, past = grey).
  A glassy bottom card shows the agent status (pulsing dot), the current place + narration,
  and **one primary action** that connects-and-walks / stops. Floating top pills: 🎧 brand,
  language, speaker, settings.
- **Tappable pins** — tap any place pin to open a sheet with its name and full story
  (follow-up narrations about the same place accumulate).
- **Guided mode ("Проведи меня")** — a chooser sheet (nav-bar-style mode toggle Прогулка / До
  места + a duration slider) asks the backend to plan a route. **Planning doesn't start the tour**:
  it shows a "building route" overlay, and only on **accept** ("Поехали") does the UI enter the
  active-tour state. The planned route draws as a dashed street-following line with numbered stop
  pins, plus a next-stop chip (name · distance · a locally-computed direction arrow).
- **Clean GPS track** — the drawn track rejects spoof/glitch teleports and smooths jitter locally,
  and prefers the backend's **street-snapped** track (OSRM map-matching) when it arrives — so the
  line follows footpaths instead of showing raw GPS wander, live and in the summary/history.
- **Smooth camera** — animated (eased) follow + recenter instead of snapping; a **compass**
  FAB (shown when the map is rotated) orients back to north; the camera keeps your **cursor
  above the bottom card** so it's never hidden. A follow / free-browse FAB re-centres after you pan.
- **Position source** — **real device GPS by default**; a *Simulated walk* toggle in
  **Settings** replays a real ~4 km Moscow route (Волгоградский проспект → Павелецкая) at
  **human walking speed (~7 km/h)** for demos / the emulator. Heading comes from the GPS
  course; `gaze_confidence=low` (the documented compass-in-pocket fallback).
- **Spoken narration** — on-device TTS (`flutter_tts`, voice follows the selected language);
  speaker toggle up top; asking (voice or text) hushes the guide (barge-in). Narration
  **never cuts a line mid-sentence** — a newer line waits, and only the freshest is queued.
- **Ask the guide** — tap-to-talk mic (`record`, 16 kHz WAV → backend STT) or the keyboard
  button for a typed question; the reply is shown and spoken back.
- **Settings & history** — dev controls (simulated-walk toggle, theme/language, account-related
  settings when configured) live in a Settings/Profile surface; the full message feed is a swipe-up
  **History** sheet. Auto-reconnect with backoff is built in, plus a **keepalive ping** (so an idle
  socket isn't reaped mid-walk) and a stable per-launch **session id** sent as `?sid=` so a dropped
  link **resumes the same tour** (no repeated narration) instead of starting over.
- **Background operation (screen locked)** — keep walking with the phone in a pocket and an
  earbud in. A foreground **LOCATION** service (`flutter_foreground_task`, type `location`) holds
  the process alive so GPS, the WebSocket and TTS keep running with the screen off; a shade card
  ("AI Audio Guide — telling you about places around you") shows a **Pause / Resume** button.
  Pause stops the narration and freezes the paced tour; Resume continues. Stopping the walk
  removes the card. (Android/iOS only — web tabs sleep when locked.)

## Platforms
- ✅ **Android** — builds an APK, runs on the `guide_emu` emulator (and real devices).
  Background-while-locked operation supported.
- ✅ **iOS** — builds from source on **macOS + Xcode** (background modes + audio session
  configured; see *iOS build* below). Not buildable from Windows/Linux.
- ✅ **Web / Windows desktop** — `flutter run -d chrome` / `-d windows` (mic/GPS/audio vary
  by platform; the simulated walk always works; no background-while-locked on web).

## Run
1. Start the backend on `:8000` (see `../backend`, host `0.0.0.0` for devices/emulator).
2. Android emulator:
   ```bash
   flutter build apk --debug --dart-define=WS_URL=ws://localhost:8000/ws
   adb -s emulator-5554 install -r build/app/outputs/flutter-apk/app-debug.apk
   adb -s emulator-5554 reverse tcp:8000 tcp:8000          # localhost:8000 -> host
   adb -s emulator-5554 shell pm grant com.example.ai_audio_guide android.permission.RECORD_AUDIO
   ```
   Or just `flutter run -d chrome` for the web build.
3. In the app: pick a language from the 🌐 menu (defaults to your system language) →
   tap **▶ Walk** (connects + starts; real GPS by default) → the guide narrates in that
   language and the map follows you; tap the mic or ⌨ to ask. The *Simulated walk* toggle in
   ⚙ Settings replays the demo route on the emulator / without GPS.

Backend URL is now **baked at build time**. On emulator/dev builds you may point it at
`ws://localhost:8000/ws`; on real phones you must build with a publicly reachable
`wss://…/ws` host. There is no longer an in-app WebSocket URL field to repair a bad APK.

## iOS build (macOS + Xcode only)
iOS binaries **cannot** be built on Windows/Linux. The iOS project is fully configured
(deployment target **13.0**; `Info.plist` has location/mic usage strings, `UIBackgroundModes`
`location`/`audio`/`fetch`, and the `flutter_foreground_task` `BGTaskScheduler` id;
`AppDelegate.swift` registers the foreground-task plugin). On a Mac:
```bash
flutter pub get
cd ios && pod install && cd ..          # CocoaPods resolves the native pods
open ios/Runner.xcworkspace             # set your Team / Bundle ID for signing (once)
# Device build (signed) baked to the prod backend:
flutter build ipa --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws
# …or run on a tethered device / simulator:
flutter run -d <device-id> --dart-define=WS_URL=wss://178.83.121.62.sslip.io/ws
```
Signing needs an Apple Developer account (free tier works for on-device testing; the App
Store needs a paid one). For real background location with the screen off, grant **Always**
location in iOS Settings (the app requests *When Using* first). TestFlight/App Store
distribution is a paid-account step done from Xcode / `flutter build ipa`.

## Checks
```bash
flutter analyze
flutter test          # widget smoke test
flutter build apk --debug
```

## Dependencies
`web_socket_channel` (WS), `geolocator` (GPS), `flutter_tts` (speech), `record` +
`path_provider` (mic), `flutter_map` + `latlong2` (OpenStreetMap), `flutter_foreground_task`
(background-while-locked service + Pause-button shade card), `sensors_plus` (compass facing),
`shared_preferences` (saved theme/language), `flutter_localizations` + `intl` (8-language UI
via `gen-l10n`).

## Notes
- `android/gradle.properties` sets `kotlin.incremental=false` — required when the pub cache
  (`C:`) and the project (`D:`) live on different drives, otherwise the Kotlin build fails
  with "this and base files have different roots".
- Public OSM tiles are fine for the prototype but not for production load — switch to a tile
  provider or self-host before shipping.
- **Background operation** needs the ongoing notification (Android requires it for a foreground
  service — it disappears on Stop). Aggressive-battery OEMs (Samsung/Xiaomi/Huawei) can still
  freeze the service: the app asks once to ignore battery optimization; if background drops,
  disable battery optimization for the app in system settings. Voice barge-in while pocketed
  isn't supported (the screen is off — nothing to tap); passive listening is.

## Next (real device)
Run on a physical phone for real GPS, the system TTS voices, and the microphone (the
emulator has no real GPS/voice/mic input). A walking phone needs the backend reachable
**along the route**, not just on home Wi-Fi — over mobile data a private LAN IP won't work,
so use a tunnel (cloudflared/ngrok), a private VPN (Tailscale), or a cloud host, and bake that
`wss://…/ws` URL into the build via `--dart-define=WS_URL=…`.
