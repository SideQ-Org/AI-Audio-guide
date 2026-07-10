// AI Audio Guide — Flutter client.
//
// Map-first, dark, minimalist. The map fills the screen; a glassy bottom card
// shows the agent status, the current place + narration, one primary action
// (connect+walk / stop) and the mic. Dev controls (WS URL, simulated walk) live
// in a Settings sheet. Real device GPS is the default; the simulated Red Square
// walk is a demo fallback (emulator / no GPS).

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';
import 'dart:ui' show ImageFilter;

import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb, TargetPlatform;
import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'accounts/accounts_config.dart';
import 'accounts/api_client.dart';
import 'accounts/auth_service.dart';
import 'accounts/login_screen.dart';
import 'ads/ads_service.dart';
import 'billing/billing_service.dart';
import 'map_config.dart';
import 'compass.dart';
import 'l10n/app_localizations.dart';
import 'walk_history_screen.dart';

// Persisted-preference keys.
const _kPrefTheme = 'themeMode';
const _kPrefLang = 'lang';

ThemeMode _parseThemeMode(String? v) => switch (v) {
      'light' => ThemeMode.light,
      'dark' => ThemeMode.dark,
      _ => ThemeMode.system,
    };

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Set up the channel the foreground-service isolate uses to talk back to the UI
  // isolate (notification button presses). Android/iOS only; a no-op-guard on web.
  if (!kIsWeb) FlutterForegroundTask.initCommunicationPort();
  // Initialize accounts (Supabase) if this build was configured with keys; a no-op
  // otherwise, so the guest-only app runs unchanged. Never fatal — degrade to guest.
  try {
    await AuthService.instance.init();
  } catch (_) {}
  // Ads (free tier) + billing (paid tier). Both degrade to no-ops when unavailable
  // (web / no AdMob / accounts off), so the guest path is untouched.
  try {
    await AdsService.instance.init();
  } catch (_) {}
  try {
    await BillingService.instance.init();
  } catch (_) {}
  final prefs = await SharedPreferences.getInstance();
  runApp(GuideApp(
    initialThemeMode: _parseThemeMode(prefs.getString(_kPrefTheme)),
    initialLang: prefs.getString(_kPrefLang),
  ));
}

// Notification action ids for the foreground-service card buttons.
const _kFgPauseAction = 'pause';
const _kFgFinishAction = 'finish';

// Entry point for the foreground service's background isolate. It holds no app
// state — its only job is to forward notification interactions to the UI isolate
// (where the WebSocket, TTS and tour state live). The service's mere existence is
// what keeps the process alive with the screen locked.
@pragma('vm:entry-point')
void guideServiceCallback() {
  FlutterForegroundTask.setTaskHandler(_GuideServiceHandler());
}

class _GuideServiceHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {}
  @override
  void onRepeatEvent(DateTime timestamp) {}
  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {}
  // Pause/Resume button -> ask the UI isolate to toggle the tour.
  @override
  void onNotificationButtonPressed(String id) => FlutterForegroundTask.sendDataToMain(id);
  // Tapping the notification body reopens the map.
  @override
  void onNotificationPressed() => FlutterForegroundTask.launchApp();
}

// Supported guide languages: code -> (native label for the picker, TTS BCP-47 tag).
// Codes are ISO-639-1 and match the backend's languages.py / Whisper.
const kLangs = <String, ({String label, String tts})>{
  'en': (label: 'English', tts: 'en-US'),
  'ru': (label: 'Русский', tts: 'ru-RU'),
  'es': (label: 'Español', tts: 'es-ES'),
  'fr': (label: 'Français', tts: 'fr-FR'),
  'de': (label: 'Deutsch', tts: 'de-DE'),
  'it': (label: 'Italiano', tts: 'it-IT'),
  'pt': (label: 'Português', tts: 'pt-BR'),
  'zh': (label: '中文', tts: 'zh-CN'),
};

// Map an arbitrary locale code to a supported one, else fall back to English.
String normLang(String code) => kLangs.containsKey(code) ? code : 'en';

// A stable session id for this app launch, sent as ?sid= on every (re)connect so a
// dropped link (WiFi/cell) resumes the SAME backend session — preserving the
// seen-list / history so the tour continues instead of repeating from scratch.
String _genSessionId() {
  // Random.secure() (CSPRNG), 32 chars: the sid resumes a session, so a guessable one
  // would let someone else resume your tour (GPS track + history). 36^32 ≈ 165 bits.
  final r = Random.secure();
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  return List.generate(32, (_) => chars[r.nextInt(chars.length)]).join();
}

// Default backend URL — baked at build time so a test build points at the host
// with no manual setup:  flutter build ... --dart-define=WS_URL=wss://host/ws
// The in-app Settings field overrides it. Falls back to localhost for dev/emulator.
const kDefaultWsUrl = String.fromEnvironment('WS_URL', defaultValue: 'ws://localhost:8000/ws');
// Shared access token for the /ws endpoint ('' => open). Baked in at build time.
const kWsToken = String.fromEnvironment('WS_TOKEN', defaultValue: '');
// Test-only: when set to a kRoutes key, the app auto-enables the simulated walk on
// that route and starts it on launch (for emulator acceptance runs). Empty = off.
const kAutoWalkRoute = String.fromEnvironment('AUTO_WALK_ROUTE', defaultValue: '');

// True under `flutter test` — lets us skip live map-tile network there.
bool _underTest() {
  try {
    return Platform.environment.containsKey('FLUTTER_TEST');
  } catch (_) {
    return false; // web has no Platform.environment
  }
}

// ── Palette ──────────────────────────────────────────────────────────────────
// A calm, pastel identity: a soft teal accent over muted map markers. The accent
// and pins are theme-independent (they must read over both the light and dark map
// tiles); the frosted "glass" chrome lives in the AppColors extension below so it
// flips with light/dark. Low-saturation on purpose — pastel, not neon.
const _accent = Color(0xFF5B8DEF); // soft iOS blue — the brand accent
const _accentDeep = Color(0xFF3D6FD6); // deeper blue — CTA gradient tail
const _onAccent = Color(0xFFFFFFFF); // white ink that reads on the blue accent
const _accentAlt = Color(0xFFA9A0F0); // soft lavender — secondary accent / highlights
const _pinCurrent = Color(0xFFF3B34A); // warm amber — the place being narrated
const _pinPast = Color(0xFF97A0B0); // soft slate — already seen
const _userArrow = Color(0xFF3AB6F0); // soft sky — the user's bearing

// Shared animation vocabulary so map + UI transitions feel like one system (no jerk).
const _animFast = Duration(milliseconds: 200); // micro state swaps (icons/labels/colors)
const _animMed = Duration(milliseconds: 320); // card/panel resizes, heading rotation
const _animCurve = Curves.easeInOutCubic;

// App-specific surface/text colours that Material's ColorScheme doesn't model well
// (translucent "glass" card/pills/sheets over the map, hairlines, soft shadow, tiered
// text). One variant per brightness; the alphas are tuned to be *frosted* — a real
// BackdropFilter blur sits behind them (see _Frosted), so they stay translucent and
// let the map ghost through. Looked up via `Theme.of(context).extension<AppColors>()!`.
@immutable
class AppColors extends ThemeExtension<AppColors> {
  final Color glassCard; // the bottom narration card
  final Color glassPill; // top-bar + FAB pills
  final Color sheetBg; // modal bottom sheets
  final Color hairline; // thin borders
  final Color shadow; // soft ambient shadow under floating chrome
  final Color textPrimary;
  final Color textSecondary;
  final Color textFaint;

  const AppColors({
    required this.glassCard,
    required this.glassPill,
    required this.sheetBg,
    required this.hairline,
    required this.shadow,
    required this.textPrimary,
    required this.textSecondary,
    required this.textFaint,
  });

  static const dark = AppColors(
    glassCard: Color(0xC61A1C22), // ~78% — frosted charcoal over the map
    glassPill: Color(0xBA1C1E25),
    sheetBg: Color(0xE614161B),
    hairline: Color(0x1FFFFFFF),
    shadow: Color(0x73000000),
    textPrimary: Color(0xFFF2F4F7),
    textSecondary: Color(0xFFB4BCC8),
    textFaint: Color(0xFF79828F),
  );

  static const light = AppColors(
    glassCard: Color(0xD9FFFFFF), // ~85% frosted white
    glassPill: Color(0xCCFFFFFF),
    sheetBg: Color(0xF2F5F7FA),
    hairline: Color(0x14111827),
    shadow: Color(0x1F2A3550), // soft blue-grey ambient (not harsh black)
    textPrimary: Color(0xFF111827),
    textSecondary: Color(0xFF4B5563),
    textFaint: Color(0xFF98A1AF),
  );

  @override
  AppColors copyWith({
    Color? glassCard,
    Color? glassPill,
    Color? sheetBg,
    Color? hairline,
    Color? shadow,
    Color? textPrimary,
    Color? textSecondary,
    Color? textFaint,
  }) =>
      AppColors(
        glassCard: glassCard ?? this.glassCard,
        glassPill: glassPill ?? this.glassPill,
        sheetBg: sheetBg ?? this.sheetBg,
        hairline: hairline ?? this.hairline,
        shadow: shadow ?? this.shadow,
        textPrimary: textPrimary ?? this.textPrimary,
        textSecondary: textSecondary ?? this.textSecondary,
        textFaint: textFaint ?? this.textFaint,
      );

  @override
  AppColors lerp(AppColors? other, double t) => t < 0.5 ? this : (other ?? this);
}

// Convenience accessor used throughout the widget tree.
AppColors _c(BuildContext context) => Theme.of(context).extension<AppColors>()!;

// ── Frosted glass ────────────────────────────────────────────────────────────
// The signature Apple/One-UI surface: a translucent panel with the content behind
// it blurred (vibrancy), a hairline edge, and a soft ambient shadow. Used for the
// bottom card, the top-bar pills, the map FABs and the bottom sheets. NOT used for
// the many category pins (a BackdropFilter each would be too costly) — those stay
// plain translucent discs.
class _Frosted extends StatelessWidget {
  const _Frosted({
    required this.child,
    this.radius = 26,
    this.circle = false,
    this.color,
    this.padding,
  });

  final Widget child;
  final double radius;
  final bool circle;
  final Color? color; // defaults to AppColors.glassPill
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    final c = _c(context);
    final fill = color ?? c.glassPill;
    final shape = circle ? BoxShape.circle : BoxShape.rectangle;
    final br = circle ? null : BorderRadius.circular(radius);
    return DecoratedBox(
      // Shadow lives outside the clip so it can bleed past the panel edge.
      decoration: BoxDecoration(
        shape: shape,
        borderRadius: br,
        boxShadow: [
          BoxShadow(color: c.shadow, blurRadius: 28, spreadRadius: -6, offset: const Offset(0, 12)),
          BoxShadow(color: c.shadow, blurRadius: 8, spreadRadius: -4, offset: const Offset(0, 2)),
        ],
      ),
      child: ClipPath(
        clipper: ShapeBorderClipper(
          shape: circle ? const CircleBorder() : RoundedRectangleBorder(borderRadius: br!),
        ),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
          child: Container(
            padding: padding,
            decoration: BoxDecoration(
              shape: shape,
              borderRadius: br,
              color: fill,
              border: Border.all(color: c.hairline, width: 0.8),
            ),
            child: child,
          ),
        ),
      ),
    );
  }
}

// A small grab handle for the top of a bottom sheet (Apple/One-UI affordance).
class _SheetGrabber extends StatelessWidget {
  const _SheetGrabber();
  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          width: 38,
          height: 4,
          margin: const EdgeInsets.only(bottom: 12),
          decoration: BoxDecoration(
            color: _c(context).textFaint.withValues(alpha: 0.5),
            borderRadius: BorderRadius.circular(2),
          ),
        ),
      );
}

class GuideApp extends StatefulWidget {
  final ThemeMode initialThemeMode;
  final String? initialLang; // null => derive from the system locale
  const GuideApp({super.key, required this.initialThemeMode, this.initialLang});

  @override
  State<GuideApp> createState() => _GuideAppState();
}

class _GuideAppState extends State<GuideApp> {
  late Locale _locale;
  late ThemeMode _themeMode;

  @override
  void initState() {
    super.initState();
    _themeMode = widget.initialThemeMode;
    // Use the saved language; else auto-select the system language (fall back to en).
    final sys = WidgetsBinding.instance.platformDispatcher.locale.languageCode;
    _locale = Locale(normLang(widget.initialLang ?? sys));
  }

  Future<void> _persist(String key, String value) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(key, value);
  }

  void _setLocale(String code) {
    setState(() => _locale = Locale(normLang(code)));
    _persist(_kPrefLang, normLang(code));
  }

  void _setThemeMode(ThemeMode mode) {
    setState(() => _themeMode = mode);
    _persist(_kPrefTheme, mode.name);
  }

  ThemeData _theme(Brightness brightness) {
    final dark = brightness == Brightness.dark;
    final ext = dark ? AppColors.dark : AppColors.light;
    final scheme = ColorScheme.fromSeed(seedColor: _accent, brightness: brightness).copyWith(
      primary: _accent,
      onPrimary: _onAccent,
      surface: dark ? const Color(0xFF14161B) : Colors.white,
      onSurface: ext.textPrimary,
      onSurfaceVariant: ext.textSecondary,
      outlineVariant: ext.hairline,
    );
    final scaffold = dark ? const Color(0xFF0C0D10) : const Color(0xFFEDF0F4);
    // Tighter tracking on the larger sizes gives the crisp, condensed feel of SF/One-UI
    // headings without shipping a custom font.
    final baseText = (dark ? Typography.material2021().white : Typography.material2021().black)
        .apply(bodyColor: ext.textPrimary, displayColor: ext.textPrimary);
    return ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      scaffoldBackgroundColor: scaffold,
      extensions: [ext],
      textTheme: baseText.copyWith(
        titleLarge: baseText.titleLarge?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.4),
        titleMedium: baseText.titleMedium?.copyWith(fontWeight: FontWeight.w600, letterSpacing: -0.2),
        headlineSmall: baseText.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.5),
      ),
      splashFactory: InkSparkle.splashFactory,
      appBarTheme: AppBarThemeData(
        backgroundColor: scaffold,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
            fontSize: 20, fontWeight: FontWeight.w700, letterSpacing: -0.4, color: ext.textPrimary),
        iconTheme: IconThemeData(color: ext.textPrimary),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, letterSpacing: -0.2, fontSize: 15),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          foregroundColor: ext.textPrimary,
          side: BorderSide(color: ext.hairline, width: 1.2),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, letterSpacing: -0.2),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: _accent,
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      inputDecorationTheme: InputDecorationThemeData(
        filled: true,
        fillColor: dark ? const Color(0x14FFFFFF) : const Color(0x0A111827),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: ext.hairline)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: const BorderSide(color: _accent, width: 1.6)),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected) ? _accent.withValues(alpha: 0.16) : Colors.transparent),
          foregroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected) ? _accent : ext.textSecondary),
          side: WidgetStatePropertyAll(BorderSide(color: ext.hairline)),
          shape: WidgetStatePropertyAll(
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12))),
          textStyle: const WidgetStatePropertyAll(
              TextStyle(fontWeight: FontWeight.w600, fontSize: 13, letterSpacing: -0.1)),
        ),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? Colors.white : null),
        trackColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? _accent : null),
        trackOutlineColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? Colors.transparent : null),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: ext.textSecondary,
        titleTextStyle: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: ext.textPrimary),
        subtitleTextStyle: TextStyle(fontSize: 13, color: ext.textSecondary),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: dark ? const Color(0xFF1B1E24) : Colors.white,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      dividerTheme: DividerThemeData(color: ext.hairline, thickness: 1),
    );
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI Guide',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: _themeMode,
      locale: _locale,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      // Auth gate: when accounts are configured, sign-in is mandatory — there is no
      // guest mode. Show the login screen until a session exists, then the map. When
      // accounts are OFF (no Supabase keys) the gate is inert and the app runs as before.
      home: AnimatedBuilder(
        animation: AuthService.instance,
        builder: (context, _) {
          if (AccountsConfig.enabled && !AuthService.instance.isSignedIn) {
            return const LoginScreen(isGate: true);
          }
          return HomePage(
            locale: _locale,
            onLocaleChanged: _setLocale,
            themeMode: _themeMode,
            onThemeModeChanged: _setThemeMode,
          );
        },
      ),
    );
  }
}

class Msg {
  final String kind; // guide | reply | you | meta
  final String text;
  Msg(this.kind, this.text);
}

// A narrated place to pin on the map (tap a pin to read its story).
class PlaceMark {
  final String id;
  final LatLng point;
  final String name;
  String text; // accumulated narration(s) about this place
  PlaceMark(this.id, this.point, this.name, this.text);
}

// A found-but-not-yet-narrated object from the search disc (lite: name + type),
// pinned faintly on the map so the user sees everything around them, not only the
// place currently being narrated. Server pushes these in a "places" frame.
class NearbyObject {
  final String id;
  final LatLng point;
  final String name;
  final String category;
  NearbyObject(this.id, this.point, this.name, this.category);
}

// Muted family accents for the lite pins — the icon SHAPE tells you what it is; the
// colour just groups it (culture/nature/water/civic/everyday). Kept low-saturation so a
// disc full of them doesn't turn into confetti. Readable on both light and dark tiles.
const _catCulture = Color(0xFFDE9B3C); // soft ochre — museums, monuments, worship, art
const _catNature = Color(0xFF2FA37C); // soft green — parks, forests, terrain
const _catWater = Color(0xFF5090EA); // soft blue — water, rivers, fountains
const _catCivic = Color(0xFF8A94A6); // soft slate — civic, transport, structures
const _catEveryday = Color(0xFFA7B0BE); // faint slate — shops, cafes, plain buildings

// Map a backend category (OSM-derived, see backend geo/categories.py) to a minimalist
// Material icon + family colour, so the map reads without a tap. Unknown categories fall
// back to a neutral dot-like marker.
({IconData icon, Color color}) _categoryStyle(String category) {
  switch (category) {
    // -- culture / historic / art --
    case 'museum':
      return (icon: Icons.museum, color: _catCulture);
    case 'gallery':
    case 'artwork':
    case 'arts_centre':
      return (icon: Icons.palette, color: _catCulture);
    case 'monument':
    case 'obelisk':
      return (icon: Icons.tour, color: _catCulture);
    case 'memorial':
      return (icon: Icons.local_florist, color: _catCulture);
    case 'castle':
    case 'fort':
    case 'palace':
    case 'city_gate':
      return (icon: Icons.castle, color: _catCulture);
    case 'place_of_worship':
    case 'monastery':
      return (icon: Icons.church, color: _catCulture);
    case 'historic':
    case 'ruins':
    case 'archaeological_site':
    case 'arch':
      return (icon: Icons.account_balance, color: _catCulture);
    case 'attraction':
      return (icon: Icons.attractions, color: _catCulture);
    case 'theatre':
      return (icon: Icons.theater_comedy, color: _catCulture);
    case 'concert_hall':
      return (icon: Icons.music_note, color: _catCulture);
    case 'cinema':
      return (icon: Icons.local_movies, color: _catCulture);
    case 'viewpoint':
      return (icon: Icons.visibility, color: _catCulture);
    case 'lighthouse':
      return (icon: Icons.flare, color: _catCulture);
    case 'tower':
      return (icon: Icons.cell_tower, color: _catCulture);
    // -- nature / green --
    case 'park':
      return (icon: Icons.park, color: _catNature);
    case 'garden':
      return (icon: Icons.yard, color: _catNature);
    case 'forest':
    case 'wood':
    case 'nature_reserve':
      return (icon: Icons.forest, color: _catNature);
    case 'orchard':
    case 'vineyard':
      return (icon: Icons.eco, color: _catNature);
    case 'allotments':
    case 'common':
      return (icon: Icons.grass, color: _catNature);
    case 'peak':
    case 'hill':
    case 'ridge':
    case 'volcano':
    case 'cliff':
    case 'rock':
    case 'cave_entrance':
      return (icon: Icons.terrain, color: _catNature);
    case 'beach':
    case 'bay':
      return (icon: Icons.beach_access, color: _catNature);
    case 'glacier':
      return (icon: Icons.ac_unit, color: _catNature);
    // -- water --
    case 'water':
    case 'reservoir':
    case 'waterfall':
    case 'wetland':
    case 'aqueduct':
      return (icon: Icons.water, color: _catWater);
    case 'river':
      return (icon: Icons.waves, color: _catWater);
    case 'spring':
    case 'geyser':
    case 'fountain':
    case 'water_tower':
      return (icon: Icons.water_drop, color: _catWater);
    case 'marina':
      return (icon: Icons.sailing, color: _catWater);
    case 'watermill':
    case 'windmill':
      return (icon: Icons.wind_power, color: _catWater);
    // -- civic / transport / structures --
    case 'townhall':
    case 'exhibition_centre':
      return (icon: Icons.account_balance, color: _catCivic);
    case 'courthouse':
      return (icon: Icons.gavel, color: _catCivic);
    case 'university':
    case 'college':
      return (icon: Icons.school, color: _catCivic);
    case 'library':
      return (icon: Icons.local_library, color: _catCivic);
    case 'marketplace':
      return (icon: Icons.storefront, color: _catCivic);
    case 'train_station':
      return (icon: Icons.train, color: _catCivic);
    case 'stadium':
      return (icon: Icons.stadium, color: _catCivic);
    case 'square':
      return (icon: Icons.location_city, color: _catCivic);
    case 'pedestrian':
      return (icon: Icons.directions_walk, color: _catCivic);
    case 'cemetery':
      return (icon: Icons.spa, color: _catCivic);
    case 'bridge':
      return (icon: Icons.linear_scale, color: _catCivic);
    // -- everyday / commercial --
    case 'cafe':
      return (icon: Icons.local_cafe, color: _catEveryday);
    case 'restaurant':
    case 'fast_food':
      return (icon: Icons.restaurant, color: _catEveryday);
    case 'bar':
    case 'pub':
      return (icon: Icons.local_bar, color: _catEveryday);
    case 'shop':
      return (icon: Icons.shopping_bag, color: _catEveryday);
    case 'building':
      return (icon: Icons.apartment, color: _catEveryday);
    default:
      return (icon: Icons.place, color: _catEveryday);
  }
}

// A small, minimalist map badge for a found-but-not-narrated object: a translucent
// "glass" disc (matches the top-bar pills) with the category icon tinted its family
// colour. Reads at a glance without a tap; still tappable for the info card.
class _CategoryPin extends StatelessWidget {
  const _CategoryPin({required this.style});
  final ({IconData icon, Color color}) style;

  @override
  Widget build(BuildContext context) {
    final c = _c(context);
    return Container(
      decoration: BoxDecoration(
        color: c.glassPill,
        shape: BoxShape.circle,
        border: Border.all(color: c.hairline),
        boxShadow: [
          BoxShadow(color: c.shadow, blurRadius: 5, spreadRadius: -1, offset: const Offset(0, 2)),
        ],
      ),
      alignment: Alignment.center,
      child: Icon(style.icon, size: 17, color: style.color),
    );
  }
}

// Angular spread of a small window of bearings (deg), handling the 360/0 wrap —
// the max gap from the first sample, mirrored for the short side. Used to decide
// whether the GPS course is steady enough to trust as a facing for "left/right".
double _bearingSpread(List<double> xs) {
  if (xs.length < 2) return 0;
  final ref = xs.first;
  var mx = 0.0;
  for (final x in xs) {
    var d = (x - ref).abs() % 360.0;
    if (d > 180.0) d = 360.0 - d;
    if (d > mx) mx = d;
  }
  return mx;
}

// One queued utterance for the TTS. Narration paragraphs signal `played` to the
// server (to pace the continuous story); replies don't.
class _Speech {
  final String text;
  final bool isNarration;
  _Speech(this.text, this.isNarration);
}

// Tour themes the user can switch to ("" = let the guide choose automatically).
// `code` is the backend-facing topic string (Russian — the agent maps it, don't
// change it); the visible label comes from l10n at render time, with an icon.
const List<({String code, IconData icon})> kThemes = [
  (code: '', icon: Icons.casino_rounded),
  (code: 'история', icon: Icons.account_balance_rounded),
  (code: 'архитектура', icon: Icons.architecture_rounded),
  (code: 'люди и судьбы', icon: Icons.people_alt_rounded),
  (code: 'культура и искусство', icon: Icons.theater_comedy_rounded),
  (code: 'легенды и тайны', icon: Icons.local_fire_department_rounded),
];

// The visible label for a theme code, localized.
String _themeLabel(AppLocalizations l, String code) => switch (code) {
      'история' => l.themeHistory,
      'архитектура' => l.themeArchitecture,
      'люди и судьбы' => l.themePeople,
      'культура и искусство' => l.themeCulture,
      'легенды и тайны' => l.themeLegends,
      _ => l.themeAuto,
    };

// Demo/test routes: real Moscow walks, waypoints joined in order (straight
// segments). Selectable in Settings when "simulated walk" is on; played back at
// kWalkSpeedMps (~7 km/h). R5 is the original demo route.
const Map<String, List<List<double>>> kRoutes = {
  'r1': [
    [55.792815, 37.587988],
    [55.795015, 37.584619],
    [55.808762, 37.580492],
  ],
  'r2': [
    [55.922993, 37.529511],
    [55.903751, 37.540102],
    [55.897771, 37.551916],
  ],
  'r3': [
    [55.639642, 37.793154],
    [55.639741, 37.801981],
    [55.637460, 37.801954],
  ],
  'r4': [
    [55.847738, 37.584899],
    [55.842658, 37.584763],
    [55.842470, 37.586884],
    [55.835994, 37.590916],
  ],
  'r5': [
    [55.725789, 37.685192],
    [55.728789, 37.677015],
    [55.741959, 37.653943],
    [55.732312, 37.639737],
  ],
};
// Display labels for the route picker (keys are ASCII so they pass cleanly via
// --dart-define on every platform).
const Map<String, String> kRouteLabels = {
  'r1': 'Маршрут 1',
  'r2': 'Маршрут 2',
  'r3': 'Маршрут 3',
  'r4': 'Маршрут 4',
  'r5': 'Маршрут 5 (демо)',
};

const double kWalkSpeedMps = 1.95; // ~7 km/h (brisk human pace)
const double kStepM = 8; // metres between simulated GPS fixes (matches the real distanceFilter)

class HomePage extends StatefulWidget {
  const HomePage({
    super.key,
    required this.locale,
    required this.onLocaleChanged,
    required this.themeMode,
    required this.onThemeModeChanged,
  });

  final Locale locale; // current UI/guide language
  final void Function(String code) onLocaleChanged; // swap MaterialApp.locale
  final ThemeMode themeMode; // current appearance (system/light/dark)
  final void Function(ThemeMode mode) onThemeModeChanged; // swap appearance + persist

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with TickerProviderStateMixin {
  final _askCtrl = TextEditingController();
  final _scroll = ScrollController();
  WebSocketChannel? _ch;
  bool _connected = false;
  String _state = '—';
  final List<Msg> _log = [];
  Timer? _walkTimer;
  List<Map<String, double>> _points = [];
  int _idx = 0;

  // Position source: false = real device GPS (default), true = simulated route.
  bool _simulate = false;
  String _routeKey = kRoutes.keys.first; // which simulated route to walk
  StreamSubscription<Position>? _gpsSub;

  // Device compass (real GPS only): when the phone is held up and steady, we send
  // its facing as the heading with gaze_confidence=high so the guide can say
  // "left/right"; otherwise we fall back to the GPS course (low).
  final CompassService _compass = CompassService();
  StreamSubscription<CompassReading>? _compassSub;
  CompassReading? _compassReading;
  final List<double> _recentCourses = []; // recent GPS courses, for a steady-walk check

  // On-device TTS — the guide speaks the narration aloud.
  final FlutterTts _tts = FlutterTts();
  bool _voice = true; // speaker on/off
  final List<_Speech> _speakQueue = []; // paragraphs/replies awaiting TTS (in order)
  String _theme = ''; // current tour theme code ("" = auto)
  late String _lang; // current guide language code (en|ru|es|…)

  // Microphone — ask the guide by voice (barge-in).
  final AudioRecorder _rec = AudioRecorder();
  bool _recording = false;
  StreamSubscription<Uint8List>? _audioSub; // mic capture stream (cross-platform)
  final List<int> _audioBuf = []; // accumulated PCM16 while recording

  // Map (CARTO dark tiles via flutter_map).
  final MapController _map = MapController();
  AnimationController? _camCtrl; // drives smooth recenter/follow camera moves
  AnimationController? _rotCtrl; // drives the "orient north" animation
  bool _mapReady = false;
  bool _follow = true; // auto-centre on the user vs free pan
  double _mapRotation = 0; // current map bearing (deg); 0 = north up
  LatLng _here = const LatLng(55.7525, 37.6231); // Red Square until first fix
  double _heading = 0; // degrees, for the bearing arrow
  double _screenH = 800; // logical screen height (for keeping the cursor above the card)
  final List<PlaceMark> _places = []; // narrated places pinned on the map
  List<NearbyObject> _nearby = []; // all found objects (lite pins from "places" frame)
  String? _currentPlaceId; // the place being narrated now (highlighted)

  // What the bottom card shows now.
  String? _curTitle; // current place name
  String? _curText; // current narration / reply text
  bool _curIsReply = false;

  bool _speaking = false; // TTS currently talking
  int _narrationsSinceAd = 0; // free-tier mid-tour ad cadence (every kMidTourAdEvery)
  bool _paused = false; // tour paused from the notification's Pause button
  bool _askedBatteryOpt = false; // only nudge battery-optimization once per launch
  bool _wantConnected = false; // user intends a live connection (drives auto-reconnect)
  Timer? _reconnectTimer;
  int _retries = 0;
  Timer? _heartbeat; // app-level WS keepalive: ping the server so a NAT/proxy can't
  // reap the idle socket during a narration lull (the reconnect-storm fix).
  Timer? _watchdog; // liveness watchdog: force-reconnect if the socket goes silent
  DateTime _lastRxAt = DateTime.now(); // last inbound frame (any type) — resets the watchdog
  Map<String, dynamic>? _lastPositionMsg; // last position sent — replayed on reconnect
  final String _sid = _genSessionId(); // stable id for resume-on-reconnect

  bool get _active => _walkTimer != null || _gpsSub != null;

  @override
  void initState() {
    super.initState();
    _lang = normLang(widget.locale.languageCode);
    // React to sign-in / sign-out: refresh the settings UI and (re)send the auth
    // token to the backend so the running tour binds/unbinds the user id live.
    AuthService.instance.addListener(_onAuthChanged);
    // Notification button presses (Pause/Resume/Finish) arrive here from the service isolate.
    if (!kIsWeb) FlutterForegroundTask.addTaskDataCallback(_onFgServiceData);
    _initTts();
    // Ask for mic + location up front and centre the map on the real position,
    // rather than sitting on the Moscow default until a walk starts.
    WidgetsBinding.instance.addPostFrameCallback((_) => _initLocationAndPermissions());
    // Test-only headless acceptance run: auto-select the route and start walking.
    if (kAutoWalkRoute.isNotEmpty && kRoutes.containsKey(kAutoWalkRoute)) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        setState(() {
          _simulate = true;
          _routeKey = kAutoWalkRoute;
        });
        Future.delayed(const Duration(seconds: 3), () {
          if (mounted && !_active) _primary();
        });
      });
    }
  }

  Future<void> _initLocationAndPermissions() async {
    // Mic permission up front (best-effort; some browsers only surface the prompt
    // on a user gesture — the mic button still requests it on tap as a fallback).
    try {
      await _rec.hasPermission();
    } catch (_) {}
    // Location permission, then centre the map on the user's real position now.
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return;
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
        return;
      }
      final pos =
          await Geolocator.getCurrentPosition().timeout(const Duration(seconds: 12));
      if (!mounted) return;
      setState(() {
        _here = LatLng(pos.latitude, pos.longitude);
        if (pos.heading >= 0) _heading = pos.heading;
      });
      if (_mapReady) _animateTo(_here);
    } catch (_) {/* keep the default centre if location is unavailable */}
  }

  Future<void> _initTts() async {
    await _applyTtsLanguage(_lang);
    // Web maps rate straight onto SpeechSynthesis (1.0 = normal), so 0.5 there is
    // half-speed and unpleasant; Android scales differently and 0.5 is a calm pace.
    await _tts.setSpeechRate(kIsWeb ? 1.0 : 0.5);
    await _tts.setPitch(1.0);
    await _tts.awaitSpeakCompletion(true);
    // iOS: a playback audio session lets narration keep playing with the screen
    // locked (paired with the `audio` UIBackgroundMode) and routes to a Bluetooth
    // earbud while ducking any music the user has on.
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.iOS) {
      await _tts.setIosAudioCategory(
        IosTextToSpeechAudioCategory.playback,
        [
          IosTextToSpeechAudioCategoryOptions.mixWithOthers,
          IosTextToSpeechAudioCategoryOptions.duckOthers,
          IosTextToSpeechAudioCategoryOptions.allowBluetoothA2DP,
          IosTextToSpeechAudioCategoryOptions.allowAirPlay,
        ],
        IosTextToSpeechAudioMode.spokenAudio,
      );
    }
    // The queue is driven by awaiting speak() in _speakNext (reliable across
    // platforms). On web the browser's SpeechSynthesis 'end' event is sometimes
    // dropped mid-utterance (a known Chrome bug, easy to hit when an overlay opens),
    // which used to leave _speaking stuck true and the guide permanently silent —
    // so we don't rely on these callbacks to advance, only to reflect UI state.
    _tts.setStartHandler(() {
      if (mounted) setState(() => _speaking = true);
    });
    _tts.setCancelHandler(() {
      if (mounted) setState(() => _speaking = false);
    });
  }

  // Queue a paragraph/reply for TTS (never cut a line mid-sentence). Narration
  // paragraphs are paced by the server via the `played` signal; with the voice
  // muted we still ack narration so the story keeps flowing on screen.
  void _enqueueSpeech(String text, {required bool isNarration}) {
    // Mic open: never speak a narration over the user. The server is already
    // paused, so don't ack `played` either — just drop this stray paragraph.
    if (_recording && isNarration) return;
    if (!_voice) {
      if (isNarration) _send({'type': 'played'});
      return;
    }
    _speakQueue.add(_Speech(text, isNarration));
    // Paused: narration paragraphs stay queued and un-acked so the server's paced
    // producer waits — BUT a reply (barge-in answer) may speak, so the user who
    // stopped to ask actually HEARS the answer (pause-and-ask, A6). Tour stays paused.
    if (!_speaking && (!_paused || !isNarration)) _speakNext();
  }

  // A narration about an object you're passing RIGHT NOW (server `interrupt` flag): cut
  // the line currently playing and drop the queue, then speak this one immediately — so
  // "прямо перед тобой" lands while you're still there, not after you've walked on.
  Future<void> _speakInterrupting(String text) async {
    if (_voice && !_recording) await _hush();  // cut current + clear queue
    _enqueueSpeech(text, isNarration: true);    // then speak it now (or ack if muted)
  }

  Future<void> _speakNext() async {
    if (_speaking || _speakQueue.isEmpty) return;
    // While paused, only a reply may play; narration stays queued until resume. Speak
    // the first reply, skipping any queued narration ahead of it (it waits its turn).
    var idx = 0;
    if (_paused) {
      idx = _speakQueue.indexWhere((s) => !s.isNarration);
      if (idx < 0) return; // only narration queued -> stay silent while paused
    }
    final s = _speakQueue.removeAt(idx);
    setState(() => _speaking = true); // claim synchronously to avoid overlap
    // Pace the server the moment a paragraph starts (so it prepares the next one).
    // Sent here — not from the TTS start callback — because that callback is
    // unreliable on web; missing it stalled the whole story after one paragraph.
    if (s.isNarration) _send({'type': 'played'});
    // Chrome's SpeechSynthesis clips an utterance at ~15s, cutting long lines off
    // mid-phrase. Speak in sentence-sized chunks so each stays well under that.
    final chunks = kIsWeb ? _chunkForTts(s.text) : [s.text];
    for (final c in chunks) {
      if (!mounted || !_voice) break; // unmounted or muted mid-line
      try {
        if (kIsWeb) {
          // Per-chunk watchdog: release if the browser drops the 'end' event so the
          // queue can never get stuck (generous vs. a ~140-char chunk's real length).
          final estMs = (c.length / 9.0 * 1000).clamp(2500, 14000).toInt();
          await Future.any([
            _tts.speak(c),
            Future<void>.delayed(Duration(milliseconds: estMs + 4000)),
          ]);
        } else {
          await _tts.speak(c); // mobile: awaitSpeakCompletion is reliable
        }
      } catch (_) {/* keep the queue moving even if one chunk fails */}
    }
    if (!mounted) return;
    setState(() => _speaking = false);
    _speakNext(); // drive the next paragraph ourselves (don't depend on callbacks)
  }

  // Split a paragraph into <=~140-char chunks at sentence boundaries (then spaces
  // for an over-long sentence) so web TTS never hits Chrome's ~15s cutoff mid-phrase.
  List<String> _chunkForTts(String text, {int maxLen = 140}) {
    final out = <String>[];
    var buf = '';
    void flush() {
      if (buf.trim().isNotEmpty) out.add(buf.trim());
      buf = '';
    }
    for (var sent in text.split(RegExp(r'(?<=[.!?…])\s+'))) {
      sent = sent.trim();
      if (sent.isEmpty) continue;
      while (sent.length > maxLen) {
        var cut = sent.lastIndexOf(' ', maxLen);
        if (cut <= 0) cut = maxLen;
        flush();
        out.add(sent.substring(0, cut).trim());
        sent = sent.substring(cut).trim();
      }
      if (buf.isEmpty) {
        buf = sent;
      } else if (buf.length + 1 + sent.length <= maxLen) {
        buf = '$buf $sent';
      } else {
        flush();
        buf = sent;
      }
    }
    flush();
    return out.isEmpty ? [text] : out;
  }

  // Point the TTS voice at the given language (best-effort; unknown tags are no-ops).
  Future<void> _applyTtsLanguage(String code) async {
    try {
      await _tts.setLanguage(kLangs[code]!.tts);
    } catch (_) {/* some platforms lack the voice — the card still shows the text */}
  }

  // User picked a language: swap UI strings + TTS voice + tell the backend.
  Future<void> _changeLanguage(String code) async {
    code = normLang(code);
    if (code == _lang) return;
    final l = AppLocalizations.of(context)!; // capture before awaits
    setState(() => _lang = code);
    widget.onLocaleChanged(code); // rebuilds MaterialApp with the new locale
    await _applyTtsLanguage(code);
    if (_connected) _send({'type': 'language', 'language': code});
    final ok = await _tts.isLanguageAvailable(kLangs[code]!.tts);
    if (ok != true && mounted) {
      _toast(l.metaVoiceUnavailable(kLangs[code]!.label));
    }
  }

  // Hush whatever is playing and drop the queue (barge-in: the user is talking).
  Future<void> _hush() async {
    _speakQueue.clear();
    await _tts.stop();
    // Reset explicitly: on web stop() maps to onComplete, not onCancel, so the
    // cancel handler may not fire — leaving _speaking stuck and the queue frozen.
    if (mounted) setState(() => _speaking = false);
  }

  // The conversation feed holds ONLY real dialog (guide | you | reply). System and
  // status lines go to a transient toast instead, so the history stays readable.
  void _add(String kind, String text) {
    setState(() => _log.add(Msg(kind, text)));
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
      }
    });
  }

  // A brief, non-intrusive status/error message (GPS, mic, connection). Never enters
  // the conversation history.
  void _toast(String text) {
    if (!mounted) return;
    final m = ScaffoldMessenger.maybeOf(context);
    m?.hideCurrentSnackBar();
    m?.showSnackBar(SnackBar(content: Text(text), duration: const Duration(seconds: 3)));
  }

  bool get _hasDialog =>
      _log.any((m) => m.kind == 'guide' || m.kind == 'reply' || m.kind == 'you');

  // Pin a narrated place on the map (dedup by id; the latest is "current").
  // Follow-up narrations about the same place accumulate into its story.
  void _addPlace(Map<String, dynamic> m) {
    final id = m['place_id'] as String?;
    final lat = (m['lat'] as num?)?.toDouble();
    final lon = (m['lon'] as num?)?.toDouble();
    if (id == null || lat == null || lon == null) return;
    final txt = (m['text'] as String?) ?? '';
    setState(() {
      _currentPlaceId = id;
      PlaceMark? existing;
      for (final p in _places) {
        if (p.id == id) existing = p;
      }
      if (existing == null) {
        _places.add(PlaceMark(id, LatLng(lat, lon), (m['place_name'] as String?) ?? '', txt));
      } else if (txt.isNotEmpty && !existing.text.contains(txt)) {
        existing.text = '${existing.text}\n\n$txt';
      }
    });
  }

  // Replace the lite map pins with the latest search disc (server pushes the full
  // set whenever the disc (re)fetches). Narrated places (`_places`) are drawn on top.
  void _setNearby(Map<String, dynamic> m) {
    final items = (m['items'] as List?) ?? const [];
    final next = <NearbyObject>[];
    for (final it in items) {
      final o = it as Map<String, dynamic>;
      final id = o['id'] as String?;
      final lat = (o['lat'] as num?)?.toDouble();
      final lon = (o['lon'] as num?)?.toDouble();
      if (id == null || lat == null || lon == null) continue;
      next.add(NearbyObject(id, LatLng(lat, lon), (o['name'] as String?) ?? '',
          (o['category'] as String?) ?? ''));
    }
    setState(() => _nearby = next);
  }

  void _connect() {
    _wantConnected = true;
    _reconnectTimer?.cancel();
    // Tear down any previous (possibly half-open) socket first so we never run two
    // overlapping connections — the churn seen in the prod logs on a flaky link.
    _heartbeat?.cancel();
    _watchdog?.cancel();
    _ch?.sink.close();
    _lastRxAt = DateTime.now();
    // Backend URL is baked in at build time (--dart-define WS_URL); not user-facing.
    var url = kDefaultWsUrl;
    final params = <String>['sid=$_sid']; // resume the same session on reconnect
    if (kWsToken.isNotEmpty) params.add('token=${Uri.encodeComponent(kWsToken)}');
    final sep = url.contains('?') ? '&' : '?';
    url += sep + params.join('&');
    final ch = WebSocketChannel.connect(Uri.parse(url));
    ch.stream.listen(
      (data) {
        _retries = 0; // a live message proves the link is healthy
        _lastRxAt = DateTime.now(); // any inbound frame (incl. server ping) = alive
        final m = jsonDecode(data as String) as Map<String, dynamic>;
        switch (m['type']) {
          case 'state':
            setState(() => _state = m['state'] as String);
            break;
          case 'narration':
            final t = m['text'] as String;
            // Guard: never display or speak the silence sentinel (belt-and-suspenders;
            // the backend already filters it). Still ack `played` so the server's paced
            // producer isn't left waiting on this dropped paragraph.
            if (t.trim().isEmpty || t.trim() == '[SILENCE]') {
              _send({'type': 'played'});
              break;
            }
            _addPlace(m); // pin it on the map
            setState(() {
              _curTitle = m['place_name'] as String?;
              _curText = t;
              _curIsReply = false;
            });
            _add('guide', t);
            if (m['interrupt'] == true) {
              _speakInterrupting(t); // object you're passing now — cut current, speak it
            } else {
              _enqueueSpeech(t, isNarration: true); // queued; paced by `played`
            }
            _maybeShowMidAd(); // free tier: an ad break every few narrations
            break;
          case 'reply':
            final t = m['text'] as String;
            setState(() {
              _curText = t;
              _curIsReply = true;
            });
            _add('reply', t);
            _enqueueSpeech(t, isNarration: false); // answer; doesn't pace the story
            break;
          case 'places':
            _setNearby(m); // pin everything the search disc found (lite)
            break;
          case 'transcript':
            _add('you', m['text'] as String);
            break;
          case 'quota':
            // Free tier hit a server cap (daily tours) — surface the upgrade sheet
            // and refresh entitlements so the UI reflects the current counts.
            AuthService.instance.refreshEntitlement();
            _showUpgrade();
            break;
          case 'error':
            _toast('${m['message']}');
            break;
        }
      },
      onDone: _onDisconnected,
      onError: (e) => _toast('$e'),
    );
    setState(() {
      _ch = ch;
      _connected = true;
      _state = '—';
    });
    // (Re)send the language first so narration + STT use it before any
    // position/audio arrives, then the theme. With ?sid= the backend resumes the
    // same session, so this is idempotent and the tour continues where it left off.
    _send({'type': 'language', 'language': _lang});
    if (_theme.isNotEmpty) _send({'type': 'theme', 'theme': _theme});
    _sendAuth(); // bind the signed-in user to this (resumable) session, if any
    // Replay the last position so the tour resumes immediately on reconnect instead of
    // sitting idle until the next GPS fix. Only a real fix (never the startup default).
    if (_lastPositionMsg != null) _send(_lastPositionMsg!);
    // Re-assert pause across a reconnect: the fresh server runtime starts un-paused, so
    // without this a drop mid-pause would let the tour resume generating on its own.
    if (_paused) _send({'type': 'pause'});
    // Keepalive: ping while connected so an idle socket isn't reaped mid-lull.
    _heartbeat = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_connected) _send({'type': 'ping'});
    });
    // Liveness watchdog: a mobile socket can go half-open (no FIN — metro/elevator/cell
    // handover) so onDone/onError never fire and the guide silently freezes. The server
    // pings every ~20s and narrates, so >40s of total inbound silence means the link is
    // dead — force-close it to trigger the reconnect path.
    _watchdog = Timer.periodic(const Duration(seconds: 10), (_) {
      if (_wantConnected &&
          _connected &&
          DateTime.now().difference(_lastRxAt) > const Duration(seconds: 40)) {
        _ch?.sink.close(); // -> onDone -> _onDisconnected -> reconnect
      }
    });
  }

  // Socket dropped: reflect it, and auto-reconnect if the user still wants to be on.
  void _onDisconnected() {
    _heartbeat?.cancel();
    _watchdog?.cancel();
    // Closing the socket during dispose() (e.g. sign-out swaps the auth gate and tears
    // HomePage down) fires this synchronously. `mounted` is still true mid-dispose, so
    // guard on _disposed too — otherwise setState/_toast hit a defunct element.
    if (_disposed || !mounted) return;
    setState(() => _connected = false);
    if (!_wantConnected) return;
    final base = (1 << _retries).clamp(1, 16); // 1,2,4,8,16s exponential backoff
    // Jitter (0–1000 ms) so a server restart doesn't trigger a synchronized reconnect
    // storm from every client at once (thundering herd).
    final delay = Duration(milliseconds: base * 1000 + Random().nextInt(1000));
    _retries++;
    _toast(AppLocalizations.of(context)!.metaConnectionLost(base));
    _reconnectTimer = Timer(delay, () {
      if (_wantConnected) _connect();
    });
  }

  void _disconnect() {
    _wantConnected = false;
    _reconnectTimer?.cancel();
    _heartbeat?.cancel();
    _watchdog?.cancel();
    _stopWalk();
    _hush();
    _ch?.sink.close();
    setState(() {
      _ch = null;
      _connected = false;
      _state = '—';
    });
  }

  void _send(Map<String, dynamic> obj) => _ch?.sink.add(jsonEncode(obj));

  // Identify the user to the backend over the open socket (design §6). Sent right
  // after connect and whenever the session changes. An EMPTY token unbinds the session
  // back to guest — so signing out mid-tour stops history being written under the old
  // user. No-op entirely when accounts aren't built into this client.
  void _sendAuth() {
    if (!AccountsConfig.enabled) return;
    _send({'type': 'auth', 'token': AuthService.instance.accessToken ?? ''});
  }

  void _onAuthChanged() {
    if (!mounted) return;
    setState(() {}); // refresh the account tile in settings
    if (_connected) _sendAuth();
  }

  // Primary action: one button to start the experience and to stop it.
  void _primary() {
    if (_active) {
      _stopWalk();
      _disconnect();
      // A tour just ended — refresh counts (tours_today / saved-walk count changed).
      if (AccountsConfig.enabled) AuthService.instance.refreshEntitlement();
    } else {
      _startWithGate(); // async (daily-quota gate + pre-roll ad); intentionally not awaited
    }
  }

  // Free tier: enforce the daily tour quota and play a pre-roll ad before starting.
  // Paid users pass straight through; guests (no profile) skip the quota gate but still
  // see ads (they are free tier). The backend enforces the same quota authoritatively.
  Future<void> _startWithGate() async {
    final prof = AuthService.instance.profile;
    if (prof != null && prof.dailyToursExhausted) {
      _showUpgrade(); // out of free tours for today
      return;
    }
    await AdsService.instance.showPreroll(); // no-op for paid / web / no ad loaded
    if (!mounted) return;
    _narrationsSinceAd = 0;
    if (!_connected) _connect();
    _start();
  }

  // Free tier: an ad break every kMidTourAdEvery narrations. Fire-and-forget — the ad
  // takes audio focus so the TTS ducks; it resumes when the ad is dismissed.
  void _maybeShowMidAd() {
    if (AuthService.instance.isPaid) return;
    if (++_narrationsSinceAd >= kMidTourAdEvery) {
      _narrationsSinceAd = 0;
      AdsService.instance.showMid();
    }
  }

  // ---- walk simulation ---------------------------------------------------
  static double _rad(double d) => d * pi / 180;

  static double _dist(List<double> a, List<double> b) {
    const r = 6371000.0;
    final dl = _rad(b[0] - a[0]), dn = _rad(b[1] - a[1]);
    final h = pow(sin(dl / 2), 2) +
        cos(_rad(a[0])) * cos(_rad(b[0])) * pow(sin(dn / 2), 2);
    return 2 * r * asin(sqrt(h.toDouble()));
  }

  static double _bearing(List<double> a, List<double> b) {
    final la1 = _rad(a[0]), la2 = _rad(b[0]), dn = _rad(b[1] - a[1]);
    final y = sin(dn) * cos(la2);
    final x = cos(la1) * sin(la2) - sin(la1) * cos(la2) * cos(dn);
    return (atan2(y, x) * 180 / pi + 360) % 360;
  }

  List<Map<String, double>> _buildPoints() {
    const stepM = kStepM;
    final route = kRoutes[_routeKey] ?? kRoutes.values.first;
    final pts = <Map<String, double>>[];
    for (var i = 0; i < route.length - 1; i++) {
      final a = route[i], b = route[i + 1];
      final len = _dist(a, b), brg = _bearing(a, b);
      for (var t = 0.0; t < len; t += stepM) {
        final f = t / len;
        pts.add({'lat': a[0] + (b[0] - a[0]) * f, 'lon': a[1] + (b[1] - a[1]) * f, 'dir': brg});
      }
    }
    final last = route.last;
    pts.add({'lat': last[0], 'lon': last[1], 'dir': 0});
    return pts;
  }

  // Start whichever source the toggle selects.
  void _start() => _simulate ? _startWalk() : _startGps();

  void _startWalk() {
    _points = _buildPoints();
    _idx = 0;
    _walkTimer?.cancel();
    // Fire one fix every kStepM metres at human pace (kStepM / speed seconds).
    final ms = (kStepM / kWalkSpeedMps * 1000).round();
    _walkTimer = Timer.periodic(Duration(milliseconds: ms), (_) {
      if (_idx >= _points.length) {
        _stopWalk();
        return;
      }
      final p = _points[_idx++];
      _sendPosition(p['lat']!, p['lon']!, p['dir']!, 'slow');
    });
    setState(() {});
  }

  // Send a position and reflect it on the map. `gaze` is 'low' by default (GPS
  // course / simulated walk); the real-GPS path passes 'high' when the held-up
  // compass gives a trustworthy facing.
  void _sendPosition(double lat, double lon, double dir, String pace,
      {String gaze = 'low'}) {
    final msg = {
      'type': 'position',
      'lat': lat,
      'lon': lon,
      'direction_deg': dir,
      'gaze_confidence': gaze,
      'pace': pace,
    };
    _lastPositionMsg = msg; // replayed on reconnect so the tour resumes at once
    _send(msg);
    setState(() {
      _here = LatLng(lat, lon);
      _heading = dir;
    });
    if (_mapReady && _follow) {
      _animateTo(_followCenter(), duration: const Duration(milliseconds: 400)); // smooth follow
    }
  }

  // ---- real GPS ----------------------------------------------------------
  // Facing (for left/right) comes from one of two trustworthy sources: a held-up
  // compass (phone raised + steady) OR a steady GPS course while walking — the user
  // moves the way they face. Either earns gaze_confidence=high; otherwise (standing,
  // wandering, pocketed) we fall back to the raw course at 'low'.
  Future<void> _startGps() async {
    final l = AppLocalizations.of(context)!;
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _toast(l.metaGeoDisabled);
        return;
      }
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
        _toast(l.metaGeoNoPermission);
        return;
      }
    } catch (e) {
      _toast(l.metaGpsUnavailable('$e'));
      return;
    }

    // Start the compass so a held-up phone yields a real facing (left/right).
    _compass.start();
    _compassSub ??= _compass.readings.listen((r) => _compassReading = r);
    // Background operation: keep the tour going with the screen locked / phone in a
    // pocket. A foreground LOCATION service holds the process alive so GPS, the WebSocket
    // and TTS keep running, and shows a quiet tour card with Pause/Finish buttons. Started
    // here, while the app is in the foreground — Android 12+/Samsung block starting a
    // foreground service from the background, so "show only when minimized" isn't reliable;
    // like nav apps, the card sits silently in the shade for the whole tour instead.
    _paused = false;
    await _startForegroundService();
    // On iOS we still need AppleSettings to allow background location updates.
    final LocationSettings settings;
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      settings = AndroidSettings(accuracy: LocationAccuracy.high, distanceFilter: 5);
    } else if (!kIsWeb && defaultTargetPlatform == TargetPlatform.iOS) {
      settings = AppleSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 5,
        allowBackgroundLocationUpdates: true,
        showBackgroundLocationIndicator: true,
        pauseLocationUpdatesAutomatically: false,
        activityType: ActivityType.fitness,
      );
    } else {
      settings = const LocationSettings(accuracy: LocationAccuracy.high, distanceFilter: 5);
    }
    _gpsSub = Geolocator.getPositionStream(locationSettings: settings).listen(
      (pos) {
        // Track recent GPS courses (only while actually moving) to tell a steady
        // walk from a wander. A steady course IS a trustworthy facing — the user
        // moves the way they look — so it earns gaze=high even without the compass.
        final course = pos.heading;
        final walking = pos.speed > 1.0 && course >= 0;
        if (walking) {
          _recentCourses.add(course);
          if (_recentCourses.length > 6) _recentCourses.removeAt(0);
        } else {
          _recentCourses.clear();
        }
        final steadyCourse =
            walking && _recentCourses.length >= 4 && _bearingSpread(_recentCourses) < 25.0;

        // Facing priority: held-up compass > steady walking course > raw course.
        final cr = _compassReading;
        final useCompass = cr != null && cr.confident;
        final dir = useCompass
            ? cr.headingDeg
            : (course >= 0 ? course : 0.0);
        _sendPosition(
          pos.latitude,
          pos.longitude,
          dir,
          pos.speed > 1.5 ? 'fast' : 'slow',
          gaze: (useCompass || steadyCourse) ? 'high' : 'low',
        );
      },
      onError: (e) => _toast(l.metaGpsError('$e')),
    );
    _toast(l.metaRealGpsOn);
    setState(() {});
  }

  void _stopWalk() {
    _walkTimer?.cancel();
    _walkTimer = null;
    _gpsSub?.cancel();
    _gpsSub = null;
    _compass.stop();
    _compassReading = null;
    _recentCourses.clear();
    _paused = false;
    _stopForegroundService(); // drops the shade card + frees the foreground service
    setState(() {});
  }

  // ---- foreground service (background operation + shade card) -------------
  // The notification with the Pause button lives in a foreground LOCATION service;
  // while it runs the OS keeps our process alive with the screen off and grants
  // background location. Android/iOS only — a no-op on web.
  Future<void> _startForegroundService() async {
    if (kIsWeb) return;
    final l = AppLocalizations.of(context)!;
    // Android 13+ needs the notification permission for the card to show; the
    // service still runs without it. Aggressive-battery OEMs (Samsung et al.) can
    // freeze even a foreground service, so nudge battery-optimization off once.
    final perm = await FlutterForegroundTask.checkNotificationPermission();
    if (perm != NotificationPermission.granted) {
      await FlutterForegroundTask.requestNotificationPermission();
    }
    if (!_askedBatteryOpt) {
      _askedBatteryOpt = true;
      try {
        if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
          await FlutterForegroundTask.requestIgnoreBatteryOptimization();
        }
      } catch (_) {/* best-effort; not fatal */}
    }
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'ai_guide_tour',
        channelName: l.bgNotifTitle,
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
        onlyAlertOnce: true,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.nothing(),
        allowWakeLock: true,
        autoRunOnBoot: false,
        allowWifiLock: true,
      ),
    );
    if (await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.startService(
      serviceId: 4242,
      serviceTypes: [ForegroundServiceTypes.location],
      notificationTitle: l.bgNotifTitle,
      notificationText: _paused ? l.bgNotifPaused : l.bgNotifText,
      notificationButtons: _fgButtons(l),
      callback: guideServiceCallback,
    );
  }

  // The two shade-card actions: Pause/Resume (accent) + Finish (coral). Android styles
  // notification buttons itself, so "pretty" here means coloured text + clear labels.
  List<NotificationButton> _fgButtons(AppLocalizations l) => [
        NotificationButton(
          id: _kFgPauseAction,
          text: _paused ? l.bgResume : l.bgPause,
          textColor: _accent,
        ),
        NotificationButton(
          id: _kFgFinishAction,
          text: l.bgFinish,
          textColor: const Color(0xFFEC6A6A),
        ),
      ];

  Future<void> _stopForegroundService() async {
    if (kIsWeb) return;
    try {
      if (await FlutterForegroundTask.isRunningService) {
        await FlutterForegroundTask.stopService();
      }
    } catch (_) {/* already gone */}
  }

  // Re-render the shade card to reflect play/paused state (button label + text).
  Future<void> _updateFgNotification() async {
    if (kIsWeb || !mounted) return;
    final l = AppLocalizations.of(context)!;
    try {
      if (!await FlutterForegroundTask.isRunningService) return;
      await FlutterForegroundTask.updateService(
        notificationTitle: l.bgNotifTitle,
        notificationText: _paused ? l.bgNotifPaused : l.bgNotifText,
        notificationButtons: _fgButtons(l),
      );
    } catch (_) {/* service may have just stopped */}
  }

  // A notification button press arrives here from the service isolate.
  void _onFgServiceData(Object data) {
    if (data == _kFgPauseAction) {
      _togglePause();
    } else if (data == _kFgFinishAction) {
      // Finish: end the tour entirely. _stopWalk() also stops the service, so the
      // shade card disappears. Mirrors tapping Stop on the primary button.
      if (_active) {
        _stopWalk();
        _disconnect();
        if (AccountsConfig.enabled) AuthService.instance.refreshEntitlement();
      } else {
        _stopForegroundService();
      }
    }
  }

  // Pause: stop talking now and let the queue accumulate without acking `played`, and
  // tell the server to halt generation (it also stops discovery/enrichment, keeps the
  // session alive, and flags the walked-while-paused GPS on the history route). Resume:
  // drain the queue (re-acks) and let the server continue the SAME tour.
  Future<void> _togglePause() async {
    if (_paused) {
      _send({'type': 'resume'});
      setState(() => _paused = false);
      await _updateFgNotification();
      _speakNext(); // resume — play whatever queued up while paused
    } else {
      _send({'type': 'pause'});
      setState(() => _paused = true);
      await _tts.stop();
      if (mounted) setState(() => _speaking = false);
      await _updateFgNotification();
    }
  }

  void _ask() {
    final t = _askCtrl.text.trim();
    if (t.isEmpty || _ch == null) return;
    _hush(); // barge-in: hush the narration while we ask
    _add('you', t);
    _send({'type': 'utterance', 'text': t});
    _askCtrl.clear();
  }

  void _toggleVoice() {
    setState(() => _voice = !_voice);
    if (!_voice) {
      _hush();
    } else if (!_speaking && _speakQueue.isEmpty && (_curText?.isNotEmpty ?? false)) {
      // Unmute: don't make the user wait for the next line — replay the current one
      // now. isNarration:false so it doesn't re-trigger server pacing (`played`).
      _enqueueSpeech(_curText!, isNarration: false);
    }
  }

  // User picked a tour theme: tell the backend to revolve the story around it.
  void _setTheme(String code) {
    setState(() => _theme = code);
    if (_connected) _send({'type': 'theme', 'theme': code});
  }

  // ---- voice barge-in (mic) ---------------------------------------------
  Future<void> _toggleMic() async {
    if (_recording) {
      await _stopRecAndSend();
    } else {
      await _startRec();
    }
  }

  Future<void> _startRec() async {
    if (_ch == null) return;
    final l = AppLocalizations.of(context)!; // capture before awaits
    if (!await _rec.hasPermission()) {
      _toast(l.metaMicNoPermission);
      return;
    }
    _hush(); // barge-in: stop the guide locally...
    _send({'type': 'listen', 'on': true}); // ...and tell the server to hold the tour
    _audioBuf.clear();
    try {
      // Stream PCM into memory — works on web AND mobile (no path_provider /
      // dart:io File, which throw on web and made the mic button do nothing there).
      final stream = await _rec.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1),
      );
      _audioSub = stream.listen(_audioBuf.addAll);
      setState(() => _recording = true);
    } catch (e) {
      _send({'type': 'listen', 'on': false}); // mic failed — let the tour resume
      _toast(l.metaMicNoPermission);
    }
  }

  Future<void> _stopRecAndSend() async {
    await _rec.stop();
    await _audioSub?.cancel();
    _audioSub = null;
    setState(() => _recording = false);
    if (_audioBuf.isEmpty) {
      _send({'type': 'listen', 'on': false}); // nothing captured — resume the tour
      return;
    }
    final wav = _wavFromPcm16(_audioBuf, sampleRate: 16000, channels: 1);
    _audioBuf.clear();
    // The audio frame is itself the barge-in; the server answers then resumes.
    _send({'type': 'audio', 'data_b64': base64Encode(wav), 'format': 'wav'});
  }

  // Wrap raw PCM16 (mono, 16 kHz) in a minimal WAV container so the backend's
  // Whisper STT can decode it. Built in memory — no filesystem, web-safe.
  List<int> _wavFromPcm16(List<int> pcm, {required int sampleRate, required int channels}) {
    final byteRate = sampleRate * channels * 2;
    final out = <int>[];
    void s(String x) => out.addAll(x.codeUnits);
    void u32(int v) => out.addAll([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >> 24) & 0xff]);
    void u16(int v) => out.addAll([v & 0xff, (v >> 8) & 0xff]);
    s('RIFF');
    u32(36 + pcm.length);
    s('WAVE');
    s('fmt ');
    u32(16); // PCM fmt chunk size
    u16(1); // audio format = PCM
    u16(channels);
    u32(sampleRate);
    u32(byteRate);
    u16(channels * 2); // block align
    u16(16); // bits per sample
    s('data');
    u32(pcm.length);
    out.addAll(pcm);
    return out;
  }

  // Set at the very top of dispose(). During dispose() `mounted` is still true (Flutter
  // nulls the element only *after* dispose runs) yet the element is already defunct, so
  // `mounted` can't guard against setState from a socket-close callback fired here — this
  // flag can. Closing the socket below (sink.close) synchronously calls _onDisconnected.
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    AuthService.instance.removeListener(_onAuthChanged);
    _walkTimer?.cancel();
    _gpsSub?.cancel();
    _compassSub?.cancel();
    _compass.dispose();
    _reconnectTimer?.cancel();
    _heartbeat?.cancel();
    _watchdog?.cancel();
    _audioSub?.cancel();
    if (!kIsWeb) {
      FlutterForegroundTask.removeTaskDataCallback(_onFgServiceData);
      _stopForegroundService();
    }
    _tts.stop();
    _rec.dispose();
    _ch?.sink.close();
    _scroll.dispose();
    _camCtrl?.dispose();
    _rotCtrl?.dispose();
    _map.dispose();
    super.dispose();
  }

  // -- status -------------------------------------------------------------
  // Curated pastel status palette (matches the calm chrome — no neon Material defaults).
  static const _stBlue = Color(0xFF8E9BF0); // thinking / expanding (periwinkle — distinct from the blue accent)
  static const _stMint = Color(0xFF5FD0C0); // listening / answering
  static const _stAmber = Color(0xFFEFB55E); // reconnecting
  static const _stCoral = Color(0xFFF08A8A); // upstream trouble
  static const _stRed = Color(0xFFEC6A6A); // offline
  static const _stGreen = Color(0xFF48C79B); // ready

  ({String label, Color color, bool active}) _status(AppLocalizations l) {
    if (!_connected && _wantConnected) return (label: l.chipReconnecting, color: _stAmber, active: true);
    if (!_connected) return (label: l.chipNotConnected, color: _pinPast, active: false);
    if (_speaking) return (label: l.chipSpeaking, color: _accent, active: true);
    return switch (_state) {
      'scoring' => (label: l.chipScoring, color: _stBlue, active: true),
      'narrating' => (label: l.chipNarrating, color: _accent, active: true),
      'switching' => (label: l.chipSwitching, color: _accent, active: true),
      'listening' => (label: l.chipListening, color: _stMint, active: true),
      'answering' => (label: l.chipAnswering, color: _stMint, active: true),
      'expanding' => (label: l.chipExpanding, color: _stBlue, active: true),
      // Upstream trouble: the guide can't reach its data/LLM source. Surface it
      // (was silently swallowed into "ready"/silence) so the user knows it's a
      // problem, not just "nothing nearby".
      'error' || 'recovery' => (label: l.chipError, color: _stCoral, active: true),
      'offline' => (label: l.chipOffline, color: _stRed, active: false),
      _ => (label: l.chipReady, color: _stGreen, active: false),
    };
  }

  Widget _statusPill(AppLocalizations l) {
    final s = _status(l);
    return AnimatedContainer(
      duration: _animFast,
      curve: _animCurve,
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
        color: s.color.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: s.color.withValues(alpha: 0.32)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        _PulsingDot(color: s.color, active: s.active),
        const SizedBox(width: 8),
        AnimatedSwitcher(
          duration: _animFast,
          child: Text(s.label,
              key: ValueKey(s.label),
              style: TextStyle(
                  fontSize: 12.5, color: s.color, fontWeight: FontWeight.w600, letterSpacing: -0.1)),
        ),
      ]),
    );
  }

  // Camera target that keeps the user's cursor in the visible area ABOVE the
  // bottom card: shift the centre south so the user sits ~1/3 from the top.
  LatLng _followCenter() {
    if (!_mapReady) return _here;
    final shiftPx = _screenH * 0.18; // move the user from 50% up to ~32% of the screen
    final mpp = 156543.03392 * cos(_here.latitude * pi / 180) / pow(2, _map.camera.zoom);
    final shiftLat = (shiftPx * mpp) / 111320.0;
    return LatLng(_here.latitude - shiftLat, _here.longitude);
  }

  // Smoothly glide the camera to `dest` instead of snapping.
  void _animateTo(LatLng dest,
      {Duration duration = const Duration(milliseconds: 650), double? zoom}) {
    if (!_mapReady) return;
    _camCtrl?.dispose();
    final startLat = _map.camera.center.latitude;
    final startLng = _map.camera.center.longitude;
    final startZoom = _map.camera.zoom;
    final endZoom = zoom ?? startZoom; // tween zoom too when a target is given
    final ctrl = AnimationController(vsync: this, duration: duration);
    _camCtrl = ctrl;
    final curve = CurvedAnimation(parent: ctrl, curve: _animCurve);
    curve.addListener(() {
      final t = curve.value;
      _map.move(
        LatLng(startLat + (dest.latitude - startLat) * t,
            startLng + (dest.longitude - startLng) * t),
        startZoom + (endZoom - startZoom) * t,
      );
    });
    ctrl.forward();
  }

  // Shared top-rounded shape for the modal sheets.
  static const _sheetShape = RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(28)));

  // A soft tinted rounded-square holding an icon — the header glyph on info sheets.
  Widget _iconChip(IconData icon, Color tint) => Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          color: tint.withValues(alpha: 0.16),
          borderRadius: BorderRadius.circular(12),
        ),
        alignment: Alignment.center,
        child: Icon(icon, color: tint, size: 22),
      );

  // Tap a narrated pin -> a card with the place's name and its accumulated story.
  void _showPlaceInfo(PlaceMark p) {
    final c = _c(context);
    final tint = p.id == _currentPlaceId ? _pinCurrent : _pinPast;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: c.sheetBg,
      shape: _sheetShape,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.45,
        maxChildSize: 0.85,
        builder: (ctx, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(22, 12, 22, 28),
          children: [
            const _SheetGrabber(),
            Row(children: [
              _iconChip(Icons.place_rounded, tint),
              const SizedBox(width: 12),
              Expanded(
                child: Text(p.name.isEmpty ? '—' : p.name,
                    style: TextStyle(
                        fontSize: 21,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.4,
                        color: c.textPrimary)),
              ),
            ]),
            const SizedBox(height: 16),
            Text(
              p.text.isEmpty ? '…' : p.text,
              style: TextStyle(fontSize: 15, height: 1.58, letterSpacing: 0.1, color: c.textSecondary),
            ),
          ],
        ),
      ),
    );
  }

  // Tap a found-but-not-narrated pin -> a light card: name + type + a hint that the
  // guide will tell its story once you walk up to it (no facts yet). Outline icon and
  // the faint accent distinguish it from a narrated place.
  void _showNearbyInfo(NearbyObject o) {
    final c = _c(context);
    final style = _categoryStyle(o.category);
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: c.sheetBg,
      shape: _sheetShape,
      builder: (ctx) {
        final l = AppLocalizations.of(ctx)!;
        return Padding(
          padding: const EdgeInsets.fromLTRB(22, 12, 22, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const _SheetGrabber(),
              Row(children: [
                _iconChip(style.icon, style.color),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(o.name.isEmpty ? o.category : o.name,
                          style: TextStyle(
                              fontSize: 19,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.3,
                              color: c.textPrimary)),
                      if (o.category.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(o.category, style: TextStyle(fontSize: 13.5, color: c.textSecondary)),
                      ],
                    ],
                  ),
                ),
              ]),
              const SizedBox(height: 16),
              Text(l.nearbyHint,
                  style: TextStyle(fontSize: 14, height: 1.5, color: c.textFaint)),
            ],
          ),
        );
      },
    );
  }

  // Zoom the map by one step (used by the +/- buttons) — glides via _animateTo's zoom
  // tween instead of snapping.
  void _zoomBy(double delta) {
    if (!_mapReady) return;
    final z = (_map.camera.zoom + delta).clamp(3.0, 19.0);
    _animateTo(_map.camera.center, duration: _animFast, zoom: z);
  }

  // -- map ----------------------------------------------------------------
  Widget _mapView() {
    // CARTO light/dark basemaps — the light (Positron) and dark (Dark Matter)
    // counterparts share the exact same tile path, so the light theme is as
    // reliable as the dark one we already shipped. The ValueKey forces flutter_map
    // to rebuild the tile layer (re-fetch tiles) when the theme flips.
    final dark = Theme.of(context).brightness == Brightness.dark;
    // Provider is build-time configurable (MapConfig); defaults to public CARTO.
    final tileUrl = MapConfig.tileUrl(dark: dark);
    return FlutterMap(
      mapController: _map,
      options: MapOptions(
        initialCenter: _here,
        initialZoom: 16,
        onMapReady: () {
          _mapReady = true;
          _animateTo(_here); // snap to the real position if it resolved before the map
        },
        onPositionChanged: (camera, hasGesture) {
          if (hasGesture && _follow) setState(() => _follow = false);
          if (camera.rotation != _mapRotation) {
            setState(() => _mapRotation = camera.rotation);
          }
        },
      ),
      children: [
        if (!_underTest())
          TileLayer(
            key: ValueKey(dark),
            urlTemplate: tileUrl,
            subdomains: MapConfig.subdomains,
            userAgentPackageName: 'com.example.ai_audio_guide',
          ),
        // Lite pins: every object the search disc found (drawn under narrated pins;
        // a narrated place's own pin overrides its lite dot by id).
        MarkerLayer(markers: [
          for (final o in _nearby)
            if (!_places.any((p) => p.id == o.id))
              Marker(
                point: o.point,
                width: 30,
                height: 30,
                child: GestureDetector(
                  onTap: () => _showNearbyInfo(o),
                  child: _CategoryPin(style: _categoryStyle(o.category)),
                ),
              ),
        ]),
        MarkerLayer(markers: [
          for (final p in _places)
            Marker(
              point: p.point,
              width: 46,
              height: 46,
              child: GestureDetector(
                onTap: () => _showPlaceInfo(p),
                child: Icon(
                  Icons.location_on,
                  size: p.id == _currentPlaceId ? 36 : 26,
                  color: p.id == _currentPlaceId ? _pinCurrent : _pinPast,
                  shadows: const [
                    Shadow(color: Color(0x59000000), blurRadius: 6, offset: Offset(0, 2)),
                  ],
                ),
              ),
            ),
          Marker(
            point: _here,
            width: 52,
            height: 52,
            child: _UserPuck(heading: _heading),
          ),
        ]),
        RichAttributionWidget(
          attributions: [TextSourceAttribution(MapConfig.attribution)],
        ),
      ],
    );
  }

  // -- top bar ------------------------------------------------------------
  Widget _iconPill(IconData icon, String tooltip, VoidCallback onTap) {
    final c = _c(context);
    return _Frosted(
      circle: true,
      child: IconButton(
        tooltip: tooltip,
        icon: Icon(icon, size: 20, color: c.textSecondary),
        onPressed: onTap,
      ),
    );
  }

  // Shared look for the two dropdown ("popup") pills so their menus match the chrome.
  ShapeBorder get _menuShape =>
      RoundedRectangleBorder(borderRadius: BorderRadius.circular(18));

  Widget _topBar(AppLocalizations l) {
    final c = _c(context);
    return Row(children: [
      // Small reserved slot top-left for a future brand icon (logo/name removed —
      // the controls on the right need the room).
      const SizedBox(width: 40, height: 40),
      const Spacer(),
      _Frosted(
        circle: true,
        child: PopupMenuButton<String>(
          tooltip: l.language,
          icon: Icon(Icons.translate, size: 20, color: c.textSecondary),
          initialValue: _lang,
          color: c.sheetBg,
          shape: _menuShape,
          onSelected: _changeLanguage,
          itemBuilder: (_) => [
            for (final e in kLangs.entries)
              PopupMenuItem(value: e.key, child: Text('${e.value.label}  ${e.key}')),
          ],
        ),
      ),
      const SizedBox(width: 8),
      _Frosted(
        circle: true,
        child: PopupMenuButton<String>(
          tooltip: l.themeTopic,
          icon: Icon(Icons.auto_stories_rounded, size: 20, color: c.textSecondary),
          initialValue: _theme,
          color: c.sheetBg,
          shape: _menuShape,
          onSelected: _setTheme,
          itemBuilder: (_) => [
            for (final t in kThemes)
              PopupMenuItem(
                value: t.code,
                child: Row(children: [
                  Icon(t.icon, size: 18, color: c.textSecondary),
                  const SizedBox(width: 10),
                  Text(_themeLabel(l, t.code)),
                ]),
              ),
          ],
        ),
      ),
      const SizedBox(width: 8),
      _iconPill(_voice ? Icons.volume_up_rounded : Icons.volume_off_rounded,
          _voice ? l.voiceOn : l.voiceOff, _toggleVoice),
      const SizedBox(width: 8),
      _iconPill(Icons.route_rounded, l.walkHistory,
          () => Navigator.of(context).push(MaterialPageRoute<void>(
              builder: (_) => WalkHistoryScreen(onUpgrade: _showUpgrade)))),
      const SizedBox(width: 8),
      _iconPill(Icons.tune_rounded, l.settings, _openSettings),
    ]);
  }

  // -- bottom card --------------------------------------------------------
  Widget _bottomCard(AppLocalizations l) {
    final c = _c(context);
    final hasNarration = _curText != null && _curText!.isNotEmpty;
    final title = _curIsReply ? l.chipAnswering : _curTitle;
    return _Frosted(
      radius: 30,
      color: c.glassCard,
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          _statusPill(l),
          const Spacer(),
          IconButton(
            tooltip: l.history,
            visualDensity: VisualDensity.compact,
            icon: Icon(Icons.history_rounded, size: 20, color: c.textFaint),
            onPressed: _hasDialog ? _openHistory : null,
          ),
        ]),
        const SizedBox(height: 8),
        // Ease the card's height as narration text appears/changes instead of jumping.
        AnimatedSize(
          duration: _animMed,
          curve: _animCurve,
          alignment: Alignment.topCenter,
          child: _cardBody(l, c, hasNarration, title),
        ),
        const SizedBox(height: 16),
        Row(children: [
          _primaryButton(l),
          if (_active) ...[
            const SizedBox(width: 10),
            _roundAction(
              icon: _paused ? Icons.play_arrow_rounded : Icons.pause_rounded,
              tooltip: _paused ? l.bgResume : l.bgPause,
              fill: _paused ? _accent : null,
              fg: _paused ? Colors.white : c.textSecondary,
              onTap: _togglePause,
            ),
          ],
          const SizedBox(width: 10),
          _roundAction(
            icon: _recording ? Icons.stop_rounded : Icons.mic_rounded,
            tooltip: _recording ? l.micStop : l.micAsk,
            fill: _recording ? _stRed : null,
            fg: _recording ? Colors.white : c.textSecondary,
            onTap: _connected ? _toggleMic : null,
          ),
          const SizedBox(width: 10),
          _roundAction(
            icon: Icons.keyboard_rounded,
            tooltip: l.ask,
            fg: c.textSecondary,
            onTap: _connected ? _openAsk : null,
          ),
        ]),
      ]),
    );
  }

  // The changing middle of the bottom card: narration (title + scrollable body) or the
  // idle hint. Kept as its own widget so AnimatedSize can ease the height between states.
  Widget _cardBody(AppLocalizations l, AppColors c, bool hasNarration, String? title) {
    if (!hasNarration) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 16),
        child: Text(l.emptyHint,
            style: TextStyle(fontSize: 15, height: 1.45, color: c.textFaint)),
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (title != null && title.isNotEmpty) ...[
          Text(title,
              style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.4,
                  height: 1.15,
                  color: c.textPrimary)),
          const SizedBox(height: 8),
        ],
        ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.26),
          child: SingleChildScrollView(
            child: Text(_curText!,
                style: TextStyle(
                    fontSize: 15, height: 1.5, letterSpacing: 0.1, color: c.textSecondary)),
          ),
        ),
      ],
    );
  }

  // The primary call-to-action: a soft two-tone gradient (teal to start, coral to
  // stop) with a matching glow — the one saturated element, so it reads as *the*
  // action against the calm pastel chrome.
  Widget _primaryButton(AppLocalizations l) {
    final active = _active;
    const startGrad = LinearGradient(
        colors: [_accent, _accentDeep], begin: Alignment.topLeft, end: Alignment.bottomRight);
    const stopGrad = LinearGradient(
        colors: [Color(0xFFF79393), Color(0xFFEC6A6A)],
        begin: Alignment.topLeft,
        end: Alignment.bottomRight);
    final glow = active ? const Color(0xFFEC6A6A) : _accent;
    final fg = active ? Colors.white : _onAccent;
    return Expanded(
      child: AnimatedContainer(
        duration: _animFast,
        curve: _animCurve,
        decoration: BoxDecoration(
          gradient: active ? stopGrad : startGrad,
          borderRadius: BorderRadius.circular(17),
          boxShadow: [
            BoxShadow(color: glow.withValues(alpha: 0.34), blurRadius: 20, spreadRadius: -2, offset: const Offset(0, 8)),
          ],
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(17),
            onTap: _primary,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: AnimatedSwitcher(
                duration: _animFast,
                child: Row(
                  key: ValueKey(active),
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(active ? Icons.stop_rounded : Icons.play_arrow_rounded, color: fg, size: 22),
                    const SizedBox(width: 8),
                    Text(active ? l.stop : l.startWalk,
                        style: TextStyle(
                            color: fg, fontWeight: FontWeight.w700, fontSize: 16, letterSpacing: -0.2)),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  // A circular secondary action. `fill` null => frosted glass; a colour => a solid,
  // softly-glowing button (used for the live "recording" state).
  Widget _roundAction({
    required IconData icon,
    required String tooltip,
    required Color fg,
    Color? fill,
    VoidCallback? onTap,
  }) {
    final btn = IconButton(
      tooltip: tooltip,
      padding: const EdgeInsets.all(14),
      icon: AnimatedSwitcher(
        duration: _animFast,
        child: Icon(icon, key: ValueKey(icon), color: fg),
      ),
      onPressed: onTap,
    );
    return Opacity(
      opacity: onTap == null ? 0.4 : 1,
      child: fill == null
          ? _Frosted(circle: true, child: btn)
          : Container(
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: fill,
                boxShadow: [
                  BoxShadow(color: fill.withValues(alpha: 0.4), blurRadius: 14, offset: const Offset(0, 4)),
                ],
              ),
              child: btn,
            ),
    );
  }

  // Smoothly rotate the map back to north (shortest way round).
  void _resetNorth() {
    if (!_mapReady) return;
    _rotCtrl?.dispose();
    final start = _map.camera.rotation;
    final delta = (-start + 540) % 360 - 180; // normalise to [-180, 180]
    final ctrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 400));
    _rotCtrl = ctrl;
    final curve = CurvedAnimation(parent: ctrl, curve: Curves.easeInOut);
    curve.addListener(() => _map.rotate(start + delta * curve.value));
    ctrl.forward();
  }

  // A small +/- zoom control column for the map (frosted-glass pills).
  Widget _zoomFab(AppLocalizations l) {
    final c = _c(context);
    Widget btn(IconData icon, String tip, VoidCallback onTap) => _Frosted(
          circle: true,
          child: IconButton(
            tooltip: tip,
            icon: Icon(icon, color: c.textSecondary, size: 22),
            onPressed: onTap,
          ),
        );
    return Column(mainAxisSize: MainAxisSize.min, children: [
      btn(Icons.add_rounded, l.zoomIn, () => _zoomBy(1)),
      const SizedBox(height: 10),
      btn(Icons.remove_rounded, l.zoomOut, () => _zoomBy(-1)),
    ]);
  }

  // Compass button: the needle reflects the map bearing; tap orients to north.
  Widget _compassFab(AppLocalizations l) {
    return _Frosted(
      circle: true,
      child: IconButton(
        tooltip: l.compassNorth,
        onPressed: _resetNorth,
        icon: Transform.rotate(
          angle: -_mapRotation * pi / 180,
          child: const Icon(Icons.navigation_rounded, color: Color(0xFFEC6A6A), size: 20),
        ),
      ),
    );
  }

  // -- follow FAB ---------------------------------------------------------
  Widget _followFab(AppLocalizations l) {
    final c = _c(context);
    void onTap() {
      setState(() => _follow = true);
      _animateTo(_followCenter()); // smooth glide; keep the cursor above the card
    }
    if (_follow) {
      // Active: the accent gradient with a soft glow — mirrors the primary CTA.
      return Container(
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: const LinearGradient(
              colors: [_accent, _accentDeep], begin: Alignment.topLeft, end: Alignment.bottomRight),
          boxShadow: [
            BoxShadow(color: _accent.withValues(alpha: 0.4), blurRadius: 14, offset: const Offset(0, 4)),
          ],
        ),
        child: IconButton(
          tooltip: l.following,
          onPressed: onTap,
          icon: const Icon(Icons.my_location_rounded, color: _onAccent),
        ),
      );
    }
    return _Frosted(
      circle: true,
      child: IconButton(
        tooltip: l.freeBrowse,
        onPressed: onTap,
        icon: Icon(Icons.location_searching_rounded, color: c.textSecondary),
      ),
    );
  }

  // -- sheets -------------------------------------------------------------
  void _openAsk() {
    final l = AppLocalizations.of(context)!;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: _c(context).sheetBg,
      shape: _sheetShape,
      builder: (ctx) => Padding(
        padding: EdgeInsets.fromLTRB(16, 12, 16, MediaQuery.of(ctx).viewInsets.bottom + 16),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const _SheetGrabber(),
          Row(children: [
            Expanded(
              child: TextField(
                controller: _askCtrl,
                autofocus: true,
                decoration: InputDecoration(hintText: l.askHint),
                onSubmitted: (_) {
                  _ask();
                  Navigator.pop(ctx);
                },
              ),
            ),
            const SizedBox(width: 10),
            FilledButton(
              onPressed: () {
                _ask();
                Navigator.pop(ctx);
              },
              style: FilledButton.styleFrom(
                minimumSize: const Size(56, 56),
                padding: EdgeInsets.zero,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
              ),
              child: const Icon(Icons.arrow_upward_rounded),
            ),
          ]),
        ]),
      ),
    );
  }

  void _openHistory() {
    final l = AppLocalizations.of(context)!;
    // Conversation only: the guide's narration, its replies, and your questions —
    // never system/status lines (those are transient toasts).
    final dialog = _log.where((m) => m.kind != 'meta').toList();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: _c(context).sheetBg,
      shape: _sheetShape,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.6,
        maxChildSize: 0.92,
        builder: (c, controller) => Column(children: [
          const Padding(
            padding: EdgeInsets.only(top: 12),
            child: _SheetGrabber(),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 8, 4),
            child: Row(children: [
              Text(l.history,
                  style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      letterSpacing: -0.3,
                      color: _c(context).textPrimary)),
              const Spacer(),
              TextButton.icon(
                onPressed: () {
                  setState(_log.clear);
                  Navigator.pop(ctx);
                },
                icon: const Icon(Icons.delete_sweep_outlined, size: 18),
                label: Text(l.clearFeed),
              ),
            ]),
          ),
          Expanded(
            child: ListView.builder(
              controller: controller,
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 16),
              itemCount: dialog.length,
              itemBuilder: (_, i) => _logTile(dialog[i]),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _logTile(Msg m) {
    final c = _c(context);
    final mine = m.kind == 'you';
    final (bg, fg) = switch (m.kind) {
      'guide' => (_accent.withValues(alpha: 0.11), c.textPrimary),
      'reply' => (_stMint.withValues(alpha: 0.16), c.textPrimary),
      _ => (_accent.withValues(alpha: 0.20), c.textPrimary), // 'you'
    };
    const r = Radius.circular(18);
    final radius = BorderRadius.only(
      topLeft: r,
      topRight: r,
      bottomLeft: mine ? r : const Radius.circular(6),
      bottomRight: mine ? const Radius.circular(6) : r,
    );
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.82),
        decoration: BoxDecoration(color: bg, borderRadius: radius),
        child: Text(m.text, style: TextStyle(color: fg, fontSize: 14.5, height: 1.4)),
      ),
    );
  }

  // Delete the account (profile + all saved walks) after confirmation, then sign out.
  Future<void> _deleteAccount(void Function(void Function()) setSheet) async {
    final l = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l.deleteAccount),
        content: Text(l.deleteAccountConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l.cancel)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(ctx).colorScheme.error),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l.delete),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await WalkApi.deleteAccount();
    } catch (_) {
      // best-effort; sign out regardless so the local session is cleared
    }
    // Close the settings sheet before signing out (see the sign-out button): the gate
    // disposes HomePage on sign-out, and a lingering sheet would hit a defunct context.
    if (mounted) Navigator.of(context).pop();
    await AuthService.instance.signOut();
  }

  // Account row in the settings sheet (only when accounts are configured). Signed in:
  // show the email + sign out. Signed out: a tile that opens the login screen.
  Widget _accountTile(AppLocalizations l, void Function(void Function()) setSheet) {
    final auth = AuthService.instance;
    if (auth.isSignedIn) {
      return Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.account_circle_outlined),
          title: Text(l.signedInAs(auth.email ?? '')),
          trailing: TextButton(
            onPressed: () async {
              // Close the settings sheet BEFORE signing out: sign-out flips the auth gate,
              // which disposes HomePage; a still-open sheet would then rebuild against its
              // now-defunct context and throw ("State no longer has a context").
              Navigator.of(context).pop();
              await auth.signOut();
            },
            child: Text(l.signOut),
          ),
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: () => _deleteAccount(setSheet),
            icon: const Icon(Icons.delete_outline, size: 18),
            label: Text(l.deleteAccount),
            style: TextButton.styleFrom(foregroundColor: Theme.of(context).colorScheme.error),
          ),
        ),
      ]);
    }
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: const Icon(Icons.account_circle_outlined),
      title: Text(l.signIn),
      subtitle: Text(l.loginSubtitle, maxLines: 2, overflow: TextOverflow.ellipsis),
      trailing: const Icon(Icons.chevron_right),
      onTap: () async {
        await Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const LoginScreen()),
        );
        setSheet(() {});
      },
    );
  }

  // The Premium upgrade sheet — benefits + monthly/yearly buy buttons. Shown from the
  // daily-quota gate, the "history full" banner, the quota WS frame, and the settings
  // tile. Styled per the app's frosted/pastel design language.
  void _showUpgrade() {
    final l = AppLocalizations.of(context)!;
    final billing = BillingService.instance;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: _c(context).sheetBg,
      shape: _sheetShape,
      builder: (ctx) => AnimatedBuilder(
        // Rebuild on billing (busy/price) AND auth (tier flips to paid on success).
        animation: Listenable.merge([billing, AuthService.instance]),
        builder: (ctx, _) {
          final c = _c(context);
          final paid = AuthService.instance.isPaid;
          final signedIn = AuthService.instance.isSignedIn;
          Widget benefit(IconData icon, String text) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(children: [
                  Icon(icon, color: _accent, size: 20),
                  const SizedBox(width: 12),
                  Expanded(
                      child: Text(text, style: TextStyle(fontSize: 15, color: c.textPrimary))),
                ]),
              );
          Widget planButton(String title, String price, VoidCallback onTap,
                  {bool highlight = false}) =>
              DecoratedBox(
                decoration: BoxDecoration(
                  gradient: highlight
                      ? const LinearGradient(colors: [_accent, _accentDeep])
                      : null,
                  color: highlight ? null : _accent.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Material(
                  color: Colors.transparent,
                  child: InkWell(
                    borderRadius: BorderRadius.circular(16),
                    onTap: onTap,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      child: Column(children: [
                        Text(title,
                            style: TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 15,
                                color: highlight ? _onAccent : c.textPrimary)),
                        const SizedBox(height: 2),
                        Text(price,
                            style: TextStyle(
                                fontSize: 13,
                                color: highlight ? _onAccent : c.textSecondary)),
                      ]),
                    ),
                  ),
                ),
              );
          return Padding(
            padding:
                EdgeInsets.fromLTRB(22, 12, 22, MediaQuery.of(ctx).viewInsets.bottom + 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const _SheetGrabber(),
                Row(children: [
                  _iconChip(Icons.workspace_premium_rounded, _accentAlt),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(l.premiumTitle,
                            style: TextStyle(
                                fontSize: 20,
                                fontWeight: FontWeight.w700,
                                letterSpacing: -0.4,
                                color: c.textPrimary)),
                        Text(l.premiumTagline,
                            style: TextStyle(fontSize: 13.5, color: c.textSecondary)),
                      ],
                    ),
                  ),
                ]),
                const SizedBox(height: 16),
                benefit(Icons.auto_awesome_rounded, l.premiumModel),
                benefit(Icons.block_rounded, l.premiumNoAds),
                benefit(Icons.all_inclusive_rounded, l.premiumUnlimitedTours),
                benefit(Icons.bookmark_rounded, l.premiumUnlimitedSaves),
                const SizedBox(height: 18),
                if (paid)
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(children: [
                        const Icon(Icons.check_circle_rounded, color: _accent),
                        const SizedBox(width: 10),
                        Text(l.premiumActive,
                            style: TextStyle(
                                fontWeight: FontWeight.w700,
                                color: c.textPrimary,
                                fontSize: 16)),
                      ]),
                      // Stubbed billing: let the tester drop back to the free tier.
                      if (kStubBilling)
                        Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton(
                            onPressed: () => billing.cancelStub(),
                            child: Text(l.cancelSubscription),
                          ),
                        ),
                    ],
                  )
                else if (!signedIn)
                  FilledButton(
                    onPressed: () async {
                      Navigator.pop(ctx);
                      await Navigator.of(context).push(
                          MaterialPageRoute<void>(builder: (_) => const LoginScreen()));
                    },
                    child: Text(l.signIn),
                  )
                else if (billing.busy)
                  const Center(
                    child: Padding(
                        padding: EdgeInsets.all(10), child: CircularProgressIndicator()),
                  )
                else ...[
                  Row(children: [
                    Expanded(
                        child: planButton(
                            l.premiumMonthly, billing.monthly?.price ?? r'$5.99/mo',
                            billing.buyMonthly)),
                    const SizedBox(width: 10),
                    Expanded(
                        child: planButton(
                            l.premiumYearly, billing.yearly?.price ?? r'$39.99/yr',
                            billing.buyYearly,
                            highlight: true)),
                  ]),
                  // Stub premium persists locally, so "restore" is meaningless there.
                  if (!kStubBilling) ...[
                    const SizedBox(height: 6),
                    Center(
                      child: TextButton(
                        onPressed: billing.available ? billing.restore : null,
                        child: Text(l.premiumRestore),
                      ),
                    ),
                  ],
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  // The account/subscription row in settings: Go Premium (free) / Premium active (paid).
  Widget _premiumTile(AppLocalizations l) {
    final paid = AuthService.instance.isPaid;
    // Keep the row tappable when paid so the sheet (incl. the stub "cancel" action) is
    // reachable; a real, backend-granted subscription just shows the "active" state.
    final tappable = !paid || kStubBilling;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: const Icon(Icons.workspace_premium_rounded, color: _accentAlt),
      title: Text(paid ? l.premiumActive : l.goPremium),
      trailing: tappable ? const Icon(Icons.chevron_right) : null,
      onTap: tappable ? _showUpgrade : null,
    );
  }

  void _openSettings() {
    final l = AppLocalizations.of(context)!;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      // Transparent here so the panel colour is painted INSIDE the StatefulBuilder
      // below — that way the sheet recolours live when the theme is switched from
      // its own toggle (a fixed backgroundColor would stay the old colour until
      // the sheet is closed and reopened).
      backgroundColor: Colors.transparent,
      builder: (ctx) => StatefulBuilder(
        builder: (c, setSheet) {
          final cc = _c(context); // re-read on every (setSheet) rebuild → live theme
          return Container(
            decoration: BoxDecoration(
              color: cc.sheetBg,
              borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            ),
            padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).viewInsets.bottom + 24),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const _SheetGrabber(),
            Text(l.settings,
                style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.3,
                    color: cc.textPrimary)),
            if (AccountsConfig.enabled) ...[
              const SizedBox(height: 8),
              _accountTile(l, setSheet),
              _premiumTile(l),
            ],
            const SizedBox(height: 16),
            Text(l.appearance,
                style: TextStyle(
                    fontSize: 13, fontWeight: FontWeight.w600, color: cc.textSecondary)),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: SegmentedButton<ThemeMode>(
                segments: [
                  ButtonSegment(
                      value: ThemeMode.system,
                      icon: const Icon(Icons.brightness_auto_rounded, size: 18),
                      label: Text(l.themeSystem)),
                  ButtonSegment(
                      value: ThemeMode.light,
                      icon: const Icon(Icons.light_mode_rounded, size: 18),
                      label: Text(l.themeLight)),
                  ButtonSegment(
                      value: ThemeMode.dark,
                      icon: const Icon(Icons.dark_mode_rounded, size: 18),
                      label: Text(l.themeDark)),
                ],
                selected: {widget.themeMode},
                showSelectedIcon: false,
                onSelectionChanged: (s) {
                  widget.onThemeModeChanged(s.first);
                  setSheet(() {});
                },
              ),
            ),
            const SizedBox(height: 12),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(l.simulatedWalk),
              value: _simulate,
              // Can't switch source mid-walk.
              onChanged: _active ? null : (v) {
                setState(() => _simulate = v);
                setSheet(() {});
              },
            ),
            if (_simulate)
              DropdownButtonFormField<String>(
                initialValue: _routeKey,
                decoration: InputDecoration(
                  labelText: l.route,
                  filled: true,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                ),
                items: [
                  for (final k in kRoutes.keys)
                    DropdownMenuItem(value: k, child: Text(kRouteLabels[k] ?? k)),
                ],
                onChanged: _active
                    ? null
                    : (v) {
                        if (v == null) return;
                        setState(() => _routeKey = v);
                        setSheet(() {});
                      },
              ),
          ]),
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    _screenH = MediaQuery.of(context).size.height;
    return Scaffold(
      body: Stack(children: [
        Positioned.fill(child: _mapView()),
        // top controls
        Positioned(
          top: 0,
          left: 12,
          right: 12,
          child: SafeArea(bottom: false, child: Padding(
            padding: const EdgeInsets.only(top: 8),
            child: _topBar(l),
          )),
        ),
        // recenter FAB sits directly above the card (always visible).
        Positioned(left: 12, right: 12, bottom: 0, child: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Align(
                alignment: Alignment.centerRight,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    _zoomFab(l),
                    const SizedBox(height: 10),
                    // Compass FAB appears when the map is rotated. Kept as a plain
                    // conditional: wrapping it in AnimatedSwitcher forced this column to
                    // full width and shoved all the FABs to the screen centre.
                    if (_mapRotation.abs() > 0.5) ...[
                      _compassFab(l),
                      const SizedBox(height: 10),
                    ],
                    _followFab(l),
                  ]),
                ),
              ),
              _bottomCard(l),
            ]),
          ),
        )),
      ]),
    );
  }
}

// The user's position marker: a soft halo behind a white puck holding the bearing
// arrow (reads cleanly over both light and dark map tiles).
class _UserPuck extends StatefulWidget {
  const _UserPuck({required this.heading});
  final double heading;

  @override
  State<_UserPuck> createState() => _UserPuckState();
}

class _UserPuckState extends State<_UserPuck> {
  // Accumulated turns for AnimatedRotation. Tracked (rather than heading/360 directly)
  // so a 359°->1° step rotates the short way instead of spinning ~360° backwards.
  late double _turns = widget.heading / 360.0;

  @override
  void didUpdateWidget(_UserPuck old) {
    super.didUpdateWidget(old);
    if (old.heading != widget.heading) {
      var delta = (widget.heading / 360.0 - _turns) % 1.0; // Dart % is non-negative
      if (delta > 0.5) delta -= 1.0; // take the shorter arc
      setState(() => _turns += delta);
    }
  }

  @override
  Widget build(BuildContext context) => SizedBox(
        width: 52,
        height: 52,
        child: Stack(alignment: Alignment.center, children: [
          DecoratedBox(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: _userArrow.withValues(alpha: 0.16),
            ),
            child: const SizedBox.expand(),
          ),
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.white,
              boxShadow: [
                BoxShadow(color: Colors.black.withValues(alpha: 0.24), blurRadius: 6, offset: const Offset(0, 2)),
              ],
            ),
            alignment: Alignment.center,
            child: AnimatedRotation(
              turns: _turns,
              duration: _animMed,
              curve: Curves.easeOut,
              child: const Icon(Icons.navigation, color: _userArrow, size: 22),
            ),
          ),
        ]),
      );
}

// A small dot that gently pulses while the agent is active.
class _PulsingDot extends StatefulWidget {
  const _PulsingDot({required this.color, required this.active});
  final Color color;
  final bool active;

  @override
  State<_PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<_PulsingDot> with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
    if (widget.active) _c.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(_PulsingDot old) {
    super.didUpdateWidget(old);
    if (widget.active && !_c.isAnimating) {
      _c.repeat(reverse: true);
    } else if (!widget.active && _c.isAnimating) {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.active) return _dot(1);
    return AnimatedBuilder(
      animation: _c,
      builder: (_, __) => _dot(0.4 + 0.6 * _c.value),
    );
  }

  Widget _dot(double opacity) => Container(
        width: 9,
        height: 9,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: widget.color.withValues(alpha: opacity),
          boxShadow: [BoxShadow(color: widget.color.withValues(alpha: opacity * 0.6), blurRadius: 6)],
        ),
      );
}
