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
import 'package:flutter/services.dart' show HapticFeedback;
import 'package:audioplayers/audioplayers.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'accounts/account_edit_screen.dart';
import 'accounts/accounts_config.dart';
import 'accounts/api_client.dart';
import 'accounts/auth_gate.dart';
import 'accounts/auth_service.dart';
import 'accounts/login_screen.dart';
import 'accounts/models.dart';
import 'accounts/realtime_service.dart';
import 'accounts/register_screen.dart';
import 'ads/ads_service.dart';
import 'billing/billing_service.dart';
import 'map_config.dart';
import 'compass.dart';
import 'l10n/app_localizations.dart';
import 'ui/achievements.dart' as ui;
import 'ui/community_screen.dart' as ui;
import 'ui/components.dart' as ui;
import 'ui/design.dart' as ui;
import 'ui/screens.dart' as ui;
import 'ui/social_screens.dart' as ui;
import 'ui/track_map.dart';

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

// Debug-only: inject a fully-populated demo profile (nick + rich walk stats) so the
// Profile tab can be exercised without a live backend. Off by default; enable with
// --dart-define=DEMO_PROFILE=true.
const kDemoProfile = bool.fromEnvironment('DEMO_PROFILE');

// Debug-only: boot straight into the Profile tab (for screenshotting a signed-in
// profile without tapping). Off by default; --dart-define=START_PROFILE=true.
const kStartProfile = bool.fromEnvironment('START_PROFILE');

// Debug-only: show the login screen at boot (to review/iterate the sign-in UI without
// the sign-out dance). Off by default; --dart-define=START_LOGIN=true.
const kStartLogin = bool.fromEnvironment('START_LOGIN');

// Debug-only: boot straight into the create-account screen (to review the register UI).
// Off by default; --dart-define=START_REGISTER=true.
const kStartRegister = bool.fromEnvironment('START_REGISTER');
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
// A calm, natural identity: a sage-green accent over muted map markers. The accent
// and pins are theme-independent (they must read over both the light and dark map
// tiles); the frosted "glass" chrome lives in the AppColors extension below so it
// flips with light/dark. Low-saturation on purpose — pastel, not neon.
// Kept in sync with the redesign's brand.primary (design/DESIGN_SPEC.md): a mid sage
// that reads on both the light and dark basemaps (the theme's own primary is lighter
// mint in dark / deeper forest in light, but map markers need one fixed colour).
const _accent = Color(0xFF4E9E77); // sage green — the brand accent
const _accentDeep = Color(0xFF35674E); // forest — CTA gradient tail
const _onAccent = Color(0xFFFFFFFF); // white ink that reads on the green accent
const _accentAlt = Color(0xFFC4E26B); // lime highlight — secondary accent (premium chip)
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
    this.circle = false,
  });

  final Widget child;
  final bool circle;

  @override
  Widget build(BuildContext context) {
    final c = _c(context);
    final fill = c.glassPill;
    final shape = circle ? BoxShape.circle : BoxShape.rectangle;
    final br = circle ? null : BorderRadius.circular(26);
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
    // Premium redesign palette (design/DESIGN_SPEC.md): the forest/mint sage primary
    // and warm/slate backgrounds. Carried alongside the legacy `ext` glass tokens so
    // both the new tab screens (context.colors) and the retained map chrome (_c) work.
    final uiC = dark ? ui.AppColors.dark : ui.AppColors.light;
    final primary = uiC.primary;
    final onPrimary = uiC.onPrimary;
    final scheme = ColorScheme.fromSeed(seedColor: primary, brightness: brightness).copyWith(
      primary: primary,
      onPrimary: onPrimary,
      surface: uiC.header,
      onSurface: ext.textPrimary,
      onSurfaceVariant: ext.textSecondary,
      outlineVariant: ext.hairline,
    );
    final scaffold = uiC.bgBottom;
    // Manrope everywhere (approved single UI font), tightened on the larger sizes.
    final baseText = GoogleFonts.manropeTextTheme(
            dark ? Typography.material2021().white : Typography.material2021().black)
        .apply(bodyColor: ext.textPrimary, displayColor: ext.textPrimary);
    return ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      scaffoldBackgroundColor: scaffold,
      extensions: [ext, uiC],
      textTheme: baseText.copyWith(
        titleLarge: baseText.titleLarge?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.4),
        titleMedium: baseText.titleMedium?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.2),
        headlineSmall: baseText.headlineSmall?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.5),
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
          foregroundColor: primary,
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
            borderSide: BorderSide(color: primary, width: 1.6)),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected) ? primary.withValues(alpha: 0.16) : Colors.transparent),
          foregroundColor: WidgetStateProperty.resolveWith((s) =>
              s.contains(WidgetState.selected) ? primary : ext.textSecondary),
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
            (s) => s.contains(WidgetState.selected) ? primary : null),
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
      // On sign-in the login "tears" apart along its divider to reveal the app (see
      // [AuthGate]).
      home: AnimatedBuilder(
        animation: AuthService.instance,
        builder: (context, _) {
          if (kStartRegister && !AuthService.instance.isSignedIn) return const RegisterScreen();
          final showLogin =
              (kStartLogin || AccountsConfig.enabled) && !AuthService.instance.isSignedIn;
          return AuthGate(
            showLogin: showLogin,
            home: HomePage(
              locale: _locale,
              onLocaleChanged: _setLocale,
              themeMode: _themeMode,
              onThemeModeChanged: _setThemeMode,
            ),
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
  String text; // accumulated narration(s) about this place (the spoken excursion)
  String category; // OSM-derived category (for the card icon + label)
  String? card; // structured, re-readable facts (narrator CARD block) — shown in the card
  String? image; // object photo URL (Wikipedia lead image), or null
  PlaceMark(this.id, this.point, this.name, this.text,
      {this.category = '', this.card, this.image});
}

// A narrated-place map pin: a colored `location_on` with a CRISP white halo behind it
// (a slightly larger white pin), not a blurred drop-shadow. Blurred shadows on markers
// smear/ghost when the map pans under Impeller (iOS sim); a solid halo doesn't.
class _MapPin extends StatelessWidget {
  const _MapPin({required this.size, required this.color});
  final double size;
  final Color color;
  @override
  Widget build(BuildContext context) => Stack(
        alignment: Alignment.center,
        children: [
          Icon(Icons.location_on, size: size + 3, color: Colors.white),
          Icon(Icons.location_on, size: size, color: color),
        ],
      );
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
// Human-readable category labels for the object cards — the raw OSM-derived category
// ("place_of_worship", "river") is a code, not a label. Russian + English (the app's primary
// locales; other languages fall back to English, matching the app's i18n state). Unmapped
// categories are prettified (snake_case -> "Snake case") so nothing shows a raw code.
const Map<String, ({String ru, String en})> _catLabels = {
  'museum': (ru: 'Музей', en: 'Museum'),
  'gallery': (ru: 'Галерея', en: 'Gallery'),
  'artwork': (ru: 'Арт-объект', en: 'Artwork'),
  'arts_centre': (ru: 'Центр искусств', en: 'Arts centre'),
  'monument': (ru: 'Памятник', en: 'Monument'),
  'obelisk': (ru: 'Обелиск', en: 'Obelisk'),
  'memorial': (ru: 'Мемориал', en: 'Memorial'),
  'castle': (ru: 'Замок', en: 'Castle'),
  'fort': (ru: 'Крепость', en: 'Fort'),
  'palace': (ru: 'Дворец', en: 'Palace'),
  'city_gate': (ru: 'Городские ворота', en: 'City gate'),
  'place_of_worship': (ru: 'Храм', en: 'Place of worship'),
  'monastery': (ru: 'Монастырь', en: 'Monastery'),
  'historic': (ru: 'Историческое место', en: 'Historic site'),
  'ruins': (ru: 'Руины', en: 'Ruins'),
  'arch': (ru: 'Арка', en: 'Arch'),
  'attraction': (ru: 'Достопримечательность', en: 'Attraction'),
  'theatre': (ru: 'Театр', en: 'Theatre'),
  'concert_hall': (ru: 'Концертный зал', en: 'Concert hall'),
  'cinema': (ru: 'Кинотеатр', en: 'Cinema'),
  'viewpoint': (ru: 'Смотровая площадка', en: 'Viewpoint'),
  'lighthouse': (ru: 'Маяк', en: 'Lighthouse'),
  'tower': (ru: 'Башня', en: 'Tower'),
  'manor': (ru: 'Усадьба', en: 'Manor'),
  'farm': (ru: 'Ферма', en: 'Farm'),
  'heritage': (ru: 'Объект наследия', en: 'Heritage site'),
  'park': (ru: 'Парк', en: 'Park'),
  'garden': (ru: 'Сад', en: 'Garden'),
  'forest': (ru: 'Лес', en: 'Forest'),
  'wood': (ru: 'Лес', en: 'Woods'),
  'nature_reserve': (ru: 'Заповедник', en: 'Nature reserve'),
  'orchard': (ru: 'Сад', en: 'Orchard'),
  'vineyard': (ru: 'Виноградник', en: 'Vineyard'),
  'common': (ru: 'Луг', en: 'Common'),
  'allotments': (ru: 'Огороды', en: 'Allotments'),
  'peak': (ru: 'Вершина', en: 'Peak'),
  'hill': (ru: 'Холм', en: 'Hill'),
  'ridge': (ru: 'Хребет', en: 'Ridge'),
  'volcano': (ru: 'Вулкан', en: 'Volcano'),
  'cliff': (ru: 'Скала', en: 'Cliff'),
  'rock': (ru: 'Скала', en: 'Rock'),
  'cave_entrance': (ru: 'Пещера', en: 'Cave'),
  'beach': (ru: 'Пляж', en: 'Beach'),
  'bay': (ru: 'Залив', en: 'Bay'),
  'glacier': (ru: 'Ледник', en: 'Glacier'),
  'water': (ru: 'Водоём', en: 'Water'),
  'reservoir': (ru: 'Водохранилище', en: 'Reservoir'),
  'waterfall': (ru: 'Водопад', en: 'Waterfall'),
  'wetland': (ru: 'Болото', en: 'Wetland'),
  'river': (ru: 'Река', en: 'River'),
  'spring': (ru: 'Источник', en: 'Spring'),
  'geyser': (ru: 'Гейзер', en: 'Geyser'),
  'fountain': (ru: 'Фонтан', en: 'Fountain'),
  'marina': (ru: 'Марина', en: 'Marina'),
  'watermill': (ru: 'Водяная мельница', en: 'Watermill'),
  'windmill': (ru: 'Ветряная мельница', en: 'Windmill'),
  'aqueduct': (ru: 'Акведук', en: 'Aqueduct'),
  'water_tower': (ru: 'Водонапорная башня', en: 'Water tower'),
  'townhall': (ru: 'Ратуша', en: 'Town hall'),
  'exhibition_centre': (ru: 'Выставочный центр', en: 'Exhibition centre'),
  'courthouse': (ru: 'Суд', en: 'Courthouse'),
  'university': (ru: 'Университет', en: 'University'),
  'college': (ru: 'Колледж', en: 'College'),
  'school': (ru: 'Школа', en: 'School'),
  'kindergarten': (ru: 'Детский сад', en: 'Kindergarten'),
  'library': (ru: 'Библиотека', en: 'Library'),
  'hospital': (ru: 'Больница', en: 'Hospital'),
  'clinic': (ru: 'Клиника', en: 'Clinic'),
  'community_centre': (ru: 'Дом культуры', en: 'Community centre'),
  'club': (ru: 'Клуб', en: 'Club'),
  'marketplace': (ru: 'Рынок', en: 'Market'),
  'train_station': (ru: 'Вокзал', en: 'Train station'),
  'stadium': (ru: 'Стадион', en: 'Stadium'),
  'square': (ru: 'Площадь', en: 'Square'),
  'pedestrian': (ru: 'Пешеходная улица', en: 'Promenade'),
  'cemetery': (ru: 'Кладбище', en: 'Cemetery'),
  'bridge': (ru: 'Мост', en: 'Bridge'),
  'cafe': (ru: 'Кафе', en: 'Cafe'),
  'restaurant': (ru: 'Ресторан', en: 'Restaurant'),
  'fast_food': (ru: 'Фастфуд', en: 'Fast food'),
  'bar': (ru: 'Бар', en: 'Bar'),
  'pub': (ru: 'Паб', en: 'Pub'),
  'shop': (ru: 'Магазин', en: 'Shop'),
  'building': (ru: 'Здание', en: 'Building'),
  'place': (ru: 'Место', en: 'Place'),
};

// Localized, human-readable label for an object category. `ru` picks the Russian label;
// anything else uses English. Unknown categories are prettified from snake_case.
String _categoryLabel(String category, {required bool ru}) {
  final e = _catLabels[category];
  if (e != null) return ru ? e.ru : e.en;
  if (category.isEmpty) return ru ? 'Место' : 'Place';
  final words = category.replaceAll('_', ' ').trim();
  return words.isEmpty ? category : '${words[0].toUpperCase()}${words.substring(1)}';
}

// Rounded hero photo for the activated-object card (Wikipedia lead image). Soft placeholder
// while loading; a broken URL collapses gracefully so the card just falls back to text-only.
class _CardHero extends StatelessWidget {
  final String url;
  const _CardHero({required this.url});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return SizedBox(
      height: 176,
      width: double.infinity,
      child: Stack(fit: StackFit.expand, children: [
        Container(color: c.glassFill(0.06)),
        Image.network(url, fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => const SizedBox.shrink(),
          loadingBuilder: (ctx, child, prog) => prog == null
              ? child
              : Center(
                  child: SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2, color: c.textFaint))),
        ),
        // grabber pill sits over the image top
        Positioned(
          top: 10,
          left: 0,
          right: 0,
          child: Center(
            child: Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.72),
                  borderRadius: BorderRadius.circular(2)),
            ),
          ),
        ),
      ]),
    );
  }
}

// Category icon in a tinted rounded chip — the shared object-card/pin glyph, coloured from
// the category accent (ui.Cat via _categoryStyle).
class _CatIconChip extends StatelessWidget {
  final IconData icon;
  final Color color;
  const _CatIconChip({required this.icon, required this.color});
  @override
  Widget build(BuildContext context) => Container(
        width: 44,
        height: 44,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(ui.Radii.sm),
          border: Border.all(color: color.withValues(alpha: 0.30)),
        ),
        child: Icon(icon, color: color, size: 22),
      );
}

// One structured fact line in the activated-object card — a small accent bullet + the fact.
class _FactRow extends StatelessWidget {
  final String text;
  final Color color;
  const _FactRow({required this.text, required this.color});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 7, right: 10),
          child: Container(
              width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        ),
        Expanded(
          child: Text(text,
              style: GoogleFonts.manrope(fontSize: 14.5, height: 1.45, color: c.textSecondary)),
        ),
      ]),
    );
  }
}

// Category accent colours now live in the design system (ui.Cat) — aliased here so the big
// _categoryStyle switch stays terse. One source of truth for pins + cards.
const _catCulture = ui.Cat.culture; // museums, monuments, worship, art
const _catNature = ui.Cat.nature; // parks, forests, terrain
const _catWater = ui.Cat.water; // water, rivers, fountains
const _catCivic = ui.Cat.civic; // civic, transport, structures
const _catEveryday = ui.Cat.everyday; // shops, cafes, plain buildings

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
    case 'manor':
    case 'farm':
    case 'farmhouse':
      return (icon: Icons.holiday_village, color: _catCulture);
    case 'heritage':
      return (icon: Icons.verified, color: _catCulture);
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
    case 'school':
      return (icon: Icons.menu_book, color: _catCivic);
    case 'kindergarten':
      return (icon: Icons.child_care, color: _catCivic);
    case 'library':
      return (icon: Icons.local_library, color: _catCivic);
    case 'hospital':
    case 'clinic':
      return (icon: Icons.local_hospital, color: _catCivic);
    case 'community_centre':
    case 'club':
      return (icon: Icons.groups_2, color: _catCivic);
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
// A friend's live position while co-walking (realtime): avatar dot + name label.
class _CoWalkPin extends StatelessWidget {
  const _CoWalkPin({this.name});
  final String? name;

  @override
  Widget build(BuildContext context) {
    final uc = Theme.of(context).extension<ui.AppColors>()!;
    final initial =
        (name != null && name!.trim().isNotEmpty) ? name!.trim()[0].toUpperCase() : '·';
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        // White border for separation; no blurred shadow (smears on map pan / Impeller).
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: uc.primary,
          border: Border.all(color: Colors.white, width: 2),
        ),
        child: Text(initial,
            style: TextStyle(color: uc.onPrimary, fontWeight: FontWeight.w800, fontSize: 15)),
      ),
      if (name != null && name!.trim().isNotEmpty) ...[
        const SizedBox(height: 2),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
          decoration: BoxDecoration(color: uc.primary, borderRadius: BorderRadius.circular(6)),
          child: Text(name!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: uc.onPrimary, fontSize: 10, fontWeight: FontWeight.w700)),
        ),
      ],
    ]);
  }
}

class _CategoryPin extends StatelessWidget {
  const _CategoryPin({required this.style});
  final ({IconData icon, Color color}) style;

  @override
  Widget build(BuildContext context) {
    final c = _c(context);
    return Container(
      // No blurred shadow: it smears when the map pans (Impeller). The filled pill +
      // hairline border give enough separation on the map.
      decoration: BoxDecoration(
        color: c.glassPill,
        shape: BoxShape.circle,
        border: Border.all(color: c.hairline),
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
  // Status-card title (place name) + reply styling to show WHILE this sentence is spoken —
  // so the bottom card tracks the sentence being read aloud, not the next one the server
  // already streamed ahead for gapless pacing.
  final String? title;
  final bool isReply;
  // Server-synthesized neural audio (paid tier). When present it's played via the
  // AudioPlayer; when null the text is spoken by the on-device flutter_tts voice.
  final Uint8List? audio;
  final String? mime;
  _Speech(this.text, this.isNarration,
      {this.title, this.isReply = false, this.audio, this.mime});
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

class _HomePageState extends State<HomePage>
    with TickerProviderStateMixin, WidgetsBindingObserver {
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

  // On-device TTS — the free tier speaks the narration aloud.
  final FlutterTts _tts = FlutterTts();
  // Completes when _initTts has finished applying the best installed voice. The first on-device
  // utterance awaits this so it never speaks in the default "compact" (robotic) voice while the
  // async getVoices->setVoice chain is still resolving (the "first line sounds bad" bug).
  final Completer<void> _ttsReady = Completer<void>();
  // Plays server-synthesized neural audio (paid tier). One reused instance; barge-in and
  // pause stop it just like _tts.stop().
  final AudioPlayer _audio = AudioPlayer();
  // Separate short-lived player for UI cue sounds (mic press start/send) so a cue never
  // disturbs the narration audio session on _audio / _tts.
  final AudioPlayer _cue = AudioPlayer();
  // iOS keep-alive: a SILENT looping clip keeps the audio session active during the pauses
  // BETWEEN sentences, so iOS doesn't suspend the app while screen-locked (background audio is
  // alive only while sound is actually playing). Android relies on the foreground LOCATION
  // service instead. Runs only for the duration of an active tour.
  final AudioPlayer _keepAlive = AudioPlayer();
  // Completed when the current neural clip finishes OR is stopped (barge-in/pause/mute),
  // so _speakNext's await never hangs when playback is cut short.
  Completer<void>? _audioDone;
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

  // What the player shows now.
  String? _curTitle; // current place name
  String? _curText; // current narration / reply text
  bool _curIsReply = false;

  int _tab = (kDemoProfile || kStartProfile) ? 2 : 0; // 0 Home · 1 Community · 2 Profile · 3 Settings

  ui.ProfileStats? _aggregatedStats; // real profile stats, aggregated from /walks
  bool _statsFetched = false;

  // Session tracking for the end-of-walk summary + the "record only if ≥10 min" rule.
  DateTime? _sessionStart; // when the current tour started (null when not touring)
  double _sessionMeters = 0; // distance walked this session (haversine accumulator)
  // Live GPS breadcrumb of the current walk ([[lat, lon(, 1.0 if paused)], ...]) — drawn as a
  // growing track on the map and shown in the end-of-walk summary. Reset when a session starts.
  final List<List<double>> _track = [];
  // The structured end-of-walk recap (arrives async over the WS after Stop); the summary sheet
  // shows a spinner until it lands. null = not ready.
  final ValueNotifier<String?> _walkSummary = ValueNotifier<String?>(null);
  Timer? _summaryTimer; // keeps the socket open briefly after Stop so the recap can arrive
  static const _kMinRecord = Duration(minutes: 10); // shorter walks are discarded, not saved

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
  // Stable id for resume-on-reconnect DURING a walk; regenerated when a NEW tour starts
  // (_startWithGate) so Stop→start never resumes the finished session on the backend.
  String _sid = _genSessionId();

  // `_touring` flips synchronously the instant a tour starts (before the async GPS
  // setup assigns `_gpsSub`), so the activation choreography renders immediately —
  // otherwise, in GPS mode with no fix yet, nothing would rebuild and the animation
  // (and the tour-UI slide-in) would never fire.
  bool _touring = false;
  bool get _active => _touring || _walkTimer != null || _gpsSub != null;

  @override
  void initState() {
    super.initState();
    // Observe app lifecycle so we can heal audio/mic state on return from background —
    // iOS suspends the app in the inter-sentence pause and can leave TTS/mic gates stuck.
    WidgetsBinding.instance.addObserver(this);
    _lang = normLang(widget.locale.languageCode);
    // React to sign-in / sign-out: refresh the settings UI and (re)send the auth
    // token to the backend so the running tour binds/unbinds the user id live.
    AuthService.instance.addListener(_onAuthChanged);
    // Live presence for Community ("на прогулке" + co-walk) — no-op if signed out.
    if (AccountsConfig.enabled && AuthService.instance.isSignedIn) {
      RealtimeService.instance.startPresence();
    }
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
    if (!kIsWeb) {
      await _audio.setReleaseMode(ReleaseMode.stop);
      await _cue.setReleaseMode(ReleaseMode.stop);
    }
    // iOS playback audio session (screen-locked playback, duck music, Bluetooth). Also
    // re-applied on resume from background, since iOS can drop the category across a suspend.
    await _applyIosAudioSession();
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
    // Voice is now selected — release the first-utterance gate in _speakNext.
    if (!_ttsReady.isCompleted) _ttsReady.complete();
  }

  // Apply the iOS playback audio session to every player (TTS + neural + cue) so narration
  // keeps going with the screen locked, ducks music, and routes to Bluetooth. Idempotent —
  // called at init AND on resume from background (iOS can drop the category across a suspend).
  Future<void> _applyIosAudioSession() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.iOS) return;
    try {
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
      // NB: with the `.playback` category, A2DP Bluetooth output and AirPlay are ON by default —
      // the explicit allowBluetoothA2DP/allowAirPlay options are ONLY valid for playAndRecord/
      // record and trip an audioplayers assert here (crashed the sim), so they're omitted.
      final ctx = AudioContext(
        iOS: AudioContextIOS(
          category: AVAudioSessionCategory.playback,
          options: const {
            AVAudioSessionOptions.mixWithOthers,
            AVAudioSessionOptions.duckOthers,
          },
        ),
      );
      await _audio.setAudioContext(ctx);
      await _cue.setAudioContext(ctx); // same session so a cue never re-negotiates iOS audio
    } catch (_) {/* best-effort — playback still works with the default session */}
  }

  // Decode the optional neural-audio payload on a narration/reply frame (paid tier).
  // Returns null when absent (free tier / TTS off / synth failed) => on-device voice.
  Uint8List? _decodeAudio(Map<String, dynamic> m) {
    final b64 = m['audio_b64'];
    if (b64 is! String || b64.isEmpty) return null;
    try {
      return base64Decode(b64);
    } catch (_) {
      return null; // malformed payload — fall back to on-device TTS
    }
  }

  // Heal audio/mic state on return from background. iOS suspends the app during the pause
  // BETWEEN sentences (background audio is alive only while sound is actually playing), so an
  // awaited `_tts.speak` can fail to start and leave `_speaking` stuck true — after which
  // `_speakNext` is never called again and the guide is silent though text keeps updating
  // ("вернулся и всё равно молчит, думает что говорит"). And a question left open when the app
  // was backgrounded can strand `_recording=true` with the server still holding `listen`.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    super.didChangeAppLifecycleState(state);
    if (kIsWeb) return;
    if (state == AppLifecycleState.paused || state == AppLifecycleState.inactive) {
      // Backgrounded mid-question: close the mic and release the server hold so the tour
      // isn't stuck "listening" forever (the stream can be torn down without _stopRecAndSend).
      if (_recording) {
        _audioSub?.cancel();
        _audioSub = null;
        _audioBuf.clear();
        _recording = false;
        _rec.stop().catchError((_) => null);
        if (_connected) _send({'type': 'listen', 'on': false});
      }
      return;
    }
    if (state != AppLifecycleState.resumed) return;
    // Back in the foreground: clear a stuck speaking gate and restart the queue. Any TTS future
    // still "awaited" from before the suspend is effectively dead (the engine stopped), so
    // resetting can't cause overlap — stop first to be safe, then drive the next sentence.
    () async {
      try {
        await _tts.stop();
      } catch (_) {/* best-effort */}
      if (!mounted) return;
      _speaking = false;
      // Re-assert the audio session (iOS can drop the category across a suspend) so playback
      // routes correctly and keeps going with the screen locked.
      await _applyIosAudioSession();
      if (!mounted) return;
      if (_voice && _speakQueue.isNotEmpty && (!_paused)) _speakNext();
    }();
  }

  // Queue a paragraph/reply for TTS (never cut a line mid-sentence). Narration
  // paragraphs are paced by the server via the `played` signal; with the voice
  // muted we still ack narration so the story keeps flowing on screen.
  void _enqueueSpeech(
    String text, {
    required bool isNarration,
    String? title,
    bool isReply = false,
    Uint8List? audio,
    String? mime,
  }) {
    // Mic open: never speak a narration over the user. The server is already
    // paused, so don't ack `played` either — just drop this stray paragraph.
    if (_recording && isNarration) return;
    if (!_voice) {
      // Muted: nothing will play, so reflect the text on the card NOW so the story still
      // flows on screen (there's no speak-start moment to sync to).
      setState(() {
        _curText = text;
        _curTitle = title;
        _curIsReply = isReply;
      });
      if (isNarration) _send({'type': 'played'});
      return;
    }
    _speakQueue.add(
        _Speech(text, isNarration, title: title, isReply: isReply, audio: audio, mime: mime));
    // Paused: narration paragraphs stay queued and un-acked so the server's paced
    // producer waits — BUT a reply (barge-in answer) may speak, so the user who
    // stopped to ask actually HEARS the answer (pause-and-ask, A6). Tour stays paused.
    if (!_speaking && (!_paused || !isNarration)) _speakNext();
  }

  // A narration about an object you're passing RIGHT NOW (server `interrupt` flag): cut
  // the line currently playing and drop the queue, then speak this one immediately — so
  // "прямо перед тобой" lands while you're still there, not after you've walked on.
  Future<void> _speakInterrupting(String text,
      {String? title, Uint8List? audio, String? mime}) async {
    if (_voice && !_recording) await _hush();  // cut current + clear queue
    _enqueueSpeech(text, isNarration: true, title: title, audio: audio, mime: mime);
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
    // Claim synchronously to avoid overlap AND move the status card to THIS sentence — the one
    // now starting to play — so the user reads what they're hearing, not the look-ahead frame.
    setState(() {
      _speaking = true;
      _curText = s.text;
      _curTitle = s.title;
      _curIsReply = s.isReply;
    });
    // Pace the server on START, not completion: ack `played` the moment this sentence begins
    // so the server delivers the NEXT one WHILE this is still playing. The client queues it
    // (1-sentence buffer) and plays it back-to-back — this is what kills the inter-phrase gap
    // (the client<->server round-trip used to fall between every sentence). A pause keeps the
    // next frame queued (not played) until resume; a voice barge-in drops it via `_recording`.
    if (s.isNarration && mounted && !_paused) _send({'type': 'played'});
    if (s.audio != null && !kIsWeb) {
      // Paid tier: play the server-synthesized neural voice. Await completion so the queue
      // advances only when this clip really ends. _stopAudio (barge-in/pause/mute) completes
      // _audioDone so this can't hang when the clip is cut short.
      await _playNeural(s);
    } else {
      // Wait for the good voice to be selected before the very first line, so it never speaks in
      // the default robotic voice (no-op after init; completes instantly on every later call).
      // Timeout so a stalled/failed init can never hang the queue — just speak with whatever's set.
      if (!kIsWeb) {
        await _ttsReady.future
            .timeout(const Duration(seconds: 4), onTimeout: () {});
      }
      if (!mounted) return;
      if (!_voice) { setState(() => _speaking = false); return; }
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
            // Mobile watchdog: awaitSpeakCompletion is usually reliable, but if the engine
            // drops the completion callback (e.g. an iOS interruption/suspend around this
            // utterance) the await would hang and wedge `_speaking` forever. Race it against a
            // GENEROUS timeout (well above a real sentence at 0.5 rate) so the queue recovers.
            final estMs = (c.length / 5.0 * 1000).clamp(8000, 60000).toInt();
            await Future.any([
              _tts.speak(c),
              Future<void>.delayed(Duration(milliseconds: estMs + 8000)),
            ]);
          }
        } catch (_) {/* keep the queue moving even if one chunk fails */}
      }
    }
    // `played` was already acked on START (above) so the next sentence streams in during this
    // one's playback and plays gaplessly. Nothing to ack here.
    if (!mounted) return;
    setState(() => _speaking = false);
    _speakNext(); // drive the next paragraph ourselves (don't depend on callbacks)
  }

  // Play one neural clip and await its end. Resolves on natural completion OR when
  // _stopAudio cuts it (barge-in/pause/mute), so the caller's pacing never stalls. On any
  // playback error, fall back to speaking the text so the line is never silently lost.
  Future<void> _playNeural(_Speech s) async {
    final done = Completer<void>();
    _audioDone = done;
    late final StreamSubscription<void> sub;
    sub = _audio.onPlayerComplete.listen((_) {
      if (!done.isCompleted) done.complete();
    });
    try {
      await _audio.stop(); // ensure idle before a fresh source
      await _audio.play(BytesSource(s.audio!, mimeType: s.mime));
      // Watchdog: if the completion event is ever dropped, don't hang the queue forever.
      await done.future.timeout(const Duration(seconds: 30), onTimeout: () {});
    } catch (_) {
      if (mounted && _voice) {
        try {
          await _tts.speak(s.text); // engine failed — speak the text instead
        } catch (_) {/* give up on this line, keep the queue moving */}
      }
    } finally {
      await sub.cancel();
      if (identical(_audioDone, done)) _audioDone = null;
    }
  }

  // Stop neural playback and release any await in _playNeural. Paired with _tts.stop()
  // everywhere the guide must go quiet (barge-in, pause, mute, dispose).
  Future<void> _stopAudio() async {
    final d = _audioDone;
    if (d != null && !d.isCompleted) d.complete();
    try {
      await _audio.stop();
    } catch (_) {/* already idle */}
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
    final tag = kLangs[code]!.tts;
    try {
      await _tts.setLanguage(tag);
      // Free tier only: the default on-device voice is the robotic "compact" one. Upgrade
      // to the best installed voice for this language (iOS enhanced/premium, Android's
      // higher-fidelity network voices). Paid tier gets server neural audio and ignores this.
      if (!kIsWeb) await _selectBestVoice(tag);
    } catch (_) {/* some platforms lack the voice — the card still shows the text */}
  }

  // Pick the highest-quality installed voice matching `tag` (e.g. "ru-RU"). Best-effort:
  // leaves the default untouched if nothing better is found or the API isn't supported.
  Future<void> _selectBestVoice(String tag) async {
    final base = tag.split(RegExp('[-_]')).first.toLowerCase(); // "ru-RU" -> "ru"
    List<dynamic> voices;
    try {
      voices = (await _tts.getVoices) as List<dynamic>;
      // A cold engine sometimes returns an empty/partial list on the first call — retry once
      // after a short beat so we don't leave the robotic default in place for the whole session.
      if (voices.isEmpty) {
        await Future<void>.delayed(const Duration(milliseconds: 400));
        voices = (await _tts.getVoices) as List<dynamic>;
      }
    } catch (_) {
      return; // platform doesn't expose voices (e.g. some web engines)
    }
    Map<dynamic, dynamic>? best;
    var bestScore = 0; // only switch when we find something better than the default
    for (final v in voices) {
      if (v is! Map) continue;
      final locale = (v['locale'] ?? '').toString().toLowerCase();
      final name = (v['name'] ?? '').toString();
      if (name.isEmpty || !locale.startsWith(base)) continue;
      final quality = (v['quality'] ?? '').toString().toLowerCase();
      final lname = name.toLowerCase();
      var score = 0;
      if (quality.contains('premium')) {
        score += 40; // iOS quality tiers
      } else if (quality.contains('enhanced')) {
        score += 30;
      }
      if (lname.contains('network')) score += 20; // Android network voices (higher fidelity)
      if (locale == tag.toLowerCase()) score += 3; // exact region match
      if (score > bestScore) {
        bestScore = score;
        best = v;
      }
    }
    if (best != null) {
      try {
        await _tts.setVoice(
          {'name': best['name'].toString(), 'locale': best['locale'].toString()},
        );
      } catch (_) {/* voice vanished between listing and setting — keep the default */}
    }
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
    await _stopAudio(); // cut neural playback too (paid tier)
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

  // Pin a narrated place on the map (dedup by id; the latest is "current").
  // Follow-up narrations about the same place accumulate into its story.
  void _addPlace(Map<String, dynamic> m) {
    final id = m['place_id'] as String?;
    final lat = (m['lat'] as num?)?.toDouble();
    final lon = (m['lon'] as num?)?.toDouble();
    if (id == null || lat == null || lon == null) return;
    final txt = (m['text'] as String?) ?? '';
    // Structured facts + photo + category ride along on the narration frame (repeated per
    // sentence). Capture the first non-empty value; the card reads these, not the spoken text.
    final card = (m['card'] as String?)?.trim();
    final image = (m['image'] as String?)?.trim();
    final cat = (m['category'] as String?) ?? '';
    setState(() {
      _currentPlaceId = id;
      PlaceMark? existing;
      for (final p in _places) {
        if (p.id == id) existing = p;
      }
      if (existing == null) {
        _places.add(PlaceMark(id, LatLng(lat, lon), (m['place_name'] as String?) ?? '', txt,
            category: cat, card: card, image: image));
      } else {
        if (txt.isNotEmpty && !existing.text.contains(txt)) {
          existing.text = '${existing.text}\n\n$txt';
        }
        if (card != null && card.isNotEmpty) existing.card = card;
        if (image != null && image.isNotEmpty) existing.image = image;
        if (cat.isNotEmpty) existing.category = cat;
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
            _add('guide', t);
            // The status card is set when this sentence STARTS being spoken (in _speakNext),
            // not now — because with ack-on-start pacing the next sentence arrives while the
            // current one is still playing. Carry the title so the card matches what's read.
            final title = m['place_name'] as String?;
            final audio = _decodeAudio(m); // paid tier: neural voice bytes (else null)
            final mime = m['audio_mime'] as String?;
            if (m['interrupt'] == true) {
              _speakInterrupting(t, title: title, audio: audio, mime: mime); // passing now — cut
            } else {
              _enqueueSpeech(t, isNarration: true, title: title, audio: audio, mime: mime);
            }
            _maybeShowMidAd(); // free tier: an ad break every few narrations
            break;
          case 'reply':
            final t = m['text'] as String;
            _add('reply', t);
            // Card is set at speak-start (in _speakNext) so it tracks the reply sentence
            // being read, matching the narration behaviour.
            _enqueueSpeech(t,
                isNarration: false,
                isReply: true,
                audio: _decodeAudio(m),
                mime: m['audio_mime'] as String?); // answer
            break;
          case 'places':
            _setNearby(m); // pin everything the search disc found (lite)
            break;
          case 'transcript':
            _add('you', m['text'] as String);
            break;
          case 'summary':
            // Structured end-of-walk recap — fills the Stop sheet (which shows a spinner
            // until it lands).
            _walkSummary.value = (m['text'] as String?)?.trim();
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
    _sendAddressForm(); // the walker's optional grammatical form of address
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

  // The walker's OPTIONAL grammatical form of address ("" neutral | masculine | feminine) —
  // sent on connect and whenever the account changes (e.g. after saving it in the profile), so
  // narration addresses them as "ты прошёл/прошла" or neutrally. Guest => neutral.
  void _sendAddressForm() {
    if (!AccountsConfig.enabled) return;
    _send({'type': 'address_form', 'form': AuthService.instance.addressForm});
  }

  void _onAuthChanged() {
    if (!mounted) return;
    setState(() {}); // refresh the account tile in settings
    if (_connected) {
      _sendAuth();
      _sendAddressForm(); // reflect a form-of-address change made in the profile
    }
    // Bring live presence up/down with the session.
    if (AccountsConfig.enabled && AuthService.instance.isSignedIn) {
      RealtimeService.instance.startPresence();
    } else {
      RealtimeService.instance.stopPresence();
    }
  }

  // Primary action: one button to start the experience and to stop it.
  void _primary() {
    if (_active) {
      _stopWalk();
      _disconnect();
      _clearWalkArtifacts(); // hard end here too — leave nothing on the home map
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
    // A brand-new tour: mint a FRESH session id so the backend starts clean instead of
    // resuming the just-finished walk, and wipe the previous walk's residue off the map.
    _summaryTimer?.cancel(); // a prior Stop's recap window must not disconnect THIS new tour
    _sid = _genSessionId();
    _clearWalkArtifacts();
    // Flip active + rebuild NOW so the activation choreography plays right after the
    // swipe, independent of when the first GPS fix / backend state arrives.
    _sessionStart = DateTime.now();
    _sessionMeters = 0;
    _walkSummary.value = null; // drop any recap from a previous walk
    setState(() => _touring = true);
    // Always (re)connect: _connect() tears down any lingering socket (e.g. the post-Stop
    // recap window) and dials with the new sid, so we never ride the old session.
    _connect();
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
    // Publish live presence (coarse) so friends see "на прогулке" + co-walk dots.
    RealtimeService.instance.updateSelf(walking: _active, lat: lat, lon: lon);
    // Accumulate walked distance for the session summary (ignore GPS jitter jumps).
    if (_sessionStart != null) {
      final step = _dist([_here.latitude, _here.longitude], [lat, lon]);
      if (step >= 1 && step < 200) _sessionMeters += step;
      // Grow the live track, distance-gated (~12 m, like the backend breadcrumb) so jitter and
      // standing still don't spam it; a point walked while PAUSED carries a trailing 1.0 flag.
      if (_track.isEmpty || _dist(_track.last, [lat, lon]) >= 12) {
        _track.add(_paused ? [lat, lon, 1.0] : [lat, lon]);
      }
    }
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
    _touring = false;
    _sessionStart = null;
    _walkTimer?.cancel();
    _walkTimer = null;
    _gpsSub?.cancel();
    _gpsSub = null;
    _compass.stop();
    _compassReading = null;
    _recentCourses.clear();
    _paused = false;
    _stopForegroundService(); // drops the shade card + frees the foreground service
    RealtimeService.instance.updateSelf(walking: false); // clear live "на прогулке"
    setState(() {});
  }

  // ---- foreground service (background operation + shade card) -------------
  // The notification with the Pause button lives in a foreground LOCATION service;
  // while it runs the OS keeps our process alive with the screen off and grants
  // background location. Android/iOS only — a no-op on web.
  // Start/stop the iOS silent keep-alive loop (no-op elsewhere). Tied to the active tour.
  Future<void> _startKeepAlive() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.iOS) return;
    try {
      await _keepAlive.setReleaseMode(ReleaseMode.loop);
      await _keepAlive.setAudioContext(AudioContext(
        iOS: AudioContextIOS(
          category: AVAudioSessionCategory.playback,
          options: const {
            AVAudioSessionOptions.mixWithOthers,
            AVAudioSessionOptions.duckOthers,
          },
        ),
      ));
      // The clip is pure silence, so volume 1.0 is inaudible; it just keeps the session live.
      await _keepAlive.play(AssetSource('sfx/silence.wav'), volume: 1.0);
    } catch (_) {/* best-effort — narration still works, may just pause screen-locked */}
  }

  Future<void> _stopKeepAlive() async {
    if (kIsWeb) return;
    try {
      await _keepAlive.stop();
    } catch (_) {/* already stopped */}
  }

  Future<void> _startForegroundService() async {
    if (kIsWeb) return;
    final l = AppLocalizations.of(context)!;
    await _startKeepAlive(); // iOS: keep the audio session alive across inter-sentence pauses
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
    await _stopKeepAlive();
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
      await _stopAudio(); // halt neural playback too (paid tier)
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
    // Recording-start feedback: a HAPTIC tap, NOT an audio cue. An audioplayers cue here starves
    // the AudioRecord stream on Android (its playback grabs the audio session ~80 ms in, cutting
    // capture to a single 2604-byte buffer -> STT hears silence -> "не расслышал"). Haptics never
    // touch the audio session, so the mic keeps capturing.
    HapticFeedback.mediumImpact();
    _hush(); // barge-in: stop the guide locally...
    _send({'type': 'listen', 'on': true}); // ...and tell the server to hold the tour
    _audioBuf.clear();
    // CRITICAL (iOS): release the silent keep-alive loop's `.playback` session before opening
    // the mic — an active playback session starves the recording input, so the mic captures
    // SILENCE and STT returns "" ("не расслышал"). Restored in _stopRecAndSend.
    await _stopKeepAlive();
    try {
      // Stream PCM into memory — works on web AND mobile (no path_provider /
      // dart:io File, which throw on web and made the mic button do nothing there).
      final stream = await _rec.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1),
      );
      // Keep capturing through a transient stream error — do NOT cancelOnError and do NOT
      // auto-stop here. A single transient error (an audio-focus blip) with cancelOnError:true
      // cancelled the subscription after the FIRST ~80 ms buffer, so only 2604 bytes reached the
      // server and STT heard nothing ("не расслышал"). Backgrounding mid-question is handled by
      // the app-lifecycle observer, which closes the mic there.
      _audioSub = stream.listen(
        _audioBuf.addAll,
        onError: (_) {/* swallow — a transient error must not stop the recording */},
      );
      setState(() => _recording = true);
    } catch (e) {
      _send({'type': 'listen', 'on': false}); // mic failed — let the tour resume
      await _applyIosAudioSession();
      _startKeepAlive(); // mic didn't open — restore the background keep-alive
      _toast(l.metaMicNoPermission);
    }
  }

  Future<void> _stopRecAndSend() async {
    await _rec.stop();
    await _audioSub?.cancel();
    _audioSub = null;
    setState(() => _recording = false);
    // Mic released: the `record` plugin left the iOS session in `.playAndRecord` (routes to the
    // earpiece), so re-assert `.playback` for narration/answer through the speaker, and resume
    // the silent keep-alive loop for screen-locked background playback.
    await _applyIosAudioSession();
    _startKeepAlive();
    if (_audioBuf.isEmpty) {
      _send({'type': 'listen', 'on': false}); // nothing captured — resume the tour
      return;
    }
    final wav = _wavFromPcm16(_audioBuf, sampleRate: 16000, channels: 1);
    _audioBuf.clear();
    HapticFeedback.lightImpact(); // "sent" — haptic, not audio (keep the audio session clean)
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
    WidgetsBinding.instance.removeObserver(this);
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
    _audio.dispose(); // neural-voice player (paid tier)
    _cue.dispose(); // mic-cue player
    _keepAlive.dispose(); // iOS silent keep-alive loop
    _summaryTimer?.cancel();
    _walkSummary.dispose();
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
    if (_paused) return (label: l.chipPaused, color: _stAmber, active: false);
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

  // Tap a narrated pin -> a card with the place's name and its accumulated story.
  // A phone number anywhere in the line, or a URL/e-mail.
  static final _phoneRe = RegExp(r'\+?\d[\d\-\s()]{6,}\d');
  static final _urlRe = RegExp(r'https?://|www\.|\b[\w.]+@[\w.]+\.\w', caseSensitive: false);
  // A directory-field LABEL at the very start of the line ("Адрес: …", "Телефон: …", "Часы …").
  // Anchored to line-start so a legit fact that merely mentions a street ("улица названа …") stays.
  static final _dirLabelRe = RegExp(
      r'^\s*(адрес|тел\.?|телефон|факс|моб\.?|часы\s+работы|время\s+работы|режим\s+работы|график\s+работы|сайт|веб-?сайт|e-?mail|почта|индекс|address|phone|tel|hours|website)\b\s*[:：]?',
      caseSensitive: false);

  // Drop CARD lines that are really a directory field (address / phone / hours / url / email)
  // rather than a fact about the object. Backstop over the narrator prompt; also cleans cards
  // generated before that prompt change. Conservative: only bare phones/urls and start-anchored
  // labels, so legitimate facts that mention a street/number survive.
  bool _looksInformational(String s) =>
      !_phoneRe.hasMatch(s) && !_urlRe.hasMatch(s) && !_dirLabelRe.hasMatch(s);

  void _showPlaceInfo(PlaceMark p) {
    final c = context.colors;
    final style = _categoryStyle(p.category);
    final ru = Localizations.localeOf(context).languageCode == 'ru';
    final label = _categoryLabel(p.category, ru: ru);
    // The card shows STRUCTURED facts (narrator CARD block), not the word-for-word excursion.
    // Split into lines, strip any leading bullet. Fall back to the spoken text only if the
    // narrator emitted no card (older walks / card disabled).
    final factLines = (p.card ?? '')
        .split('\n')
        .map((s) => s.trim().replaceFirst(RegExp(r'^[-•*·—]\s*'), '').trim())
        .where((s) => s.isNotEmpty)
        .where(_looksInformational) // drop leaked address/phone/hours lines (backstop over the prompt)
        .toList();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => _cardShell(
        ctx,
        // hero photo when present; otherwise NO reserved space — just the grabber.
        top: p.image != null
            ? _CardHero(url: p.image!)
            : const Padding(padding: EdgeInsets.only(top: 10), child: Center(child: _SheetGrabber())),
        topGap: p.image != null ? ui.Gap.lg : ui.Gap.md,
        children: [
          Row(children: [
            _CatIconChip(icon: style.icon, color: style.color),
            const SizedBox(width: ui.Gap.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(p.name.isEmpty ? label : p.name, style: ui.titleS(ctx)),
                  if (label.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(label,
                        style: GoogleFonts.manrope(
                            fontSize: 13, fontWeight: FontWeight.w600, color: style.color)),
                  ],
                ],
              ),
            ),
          ]),
          const SizedBox(height: ui.Gap.lg),
          if (factLines.isNotEmpty)
            ...factLines.map((f) => _FactRow(text: f, color: style.color))
          else if (p.text.trim().isNotEmpty)
            Text(p.text.trim(),
                style: GoogleFonts.manrope(fontSize: 15, height: 1.55, color: c.textSecondary)),
        ],
      ),
    );
  }

  // Content-SIZED object-card sheet: a warm cream sheet (the app's bg gradient, no expanding
  // mesh/blur — so it sizes to its content and never leaves a big empty area, and stays light
  // enough not to lag). `top` is the hero photo or grabber; `children` are the body rows.
  // The object-card sheet, now built on the shared ui.CardSheet (same content-sizing +
  // cream gradient every modal sheet uses). `top` is the hero photo or grabber; `children`
  // are the body rows under a comfortable padding.
  Widget _cardShell(BuildContext ctx, {required Widget top, double topGap = 0, required List<Widget> children}) {
    return ui.CardSheet(
      maxHeightFactor: 0.82,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          top,
          Padding(
            padding: EdgeInsets.fromLTRB(
                ui.Gap.xl, topGap, ui.Gap.xl, ui.Gap.xl + MediaQuery.of(ctx).padding.bottom),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: children,
            ),
          ),
        ],
      ),
    );
  }

  // Tap a found-but-not-narrated pin -> a light card: name + type + a hint that the
  // guide will tell its story once you walk up to it (no facts yet). Outline icon and
  // the faint accent distinguish it from a narrated place.
  void _showNearbyInfo(NearbyObject o) {
    final c = context.colors;
    final style = _categoryStyle(o.category);
    final ru = Localizations.localeOf(context).languageCode == 'ru';
    final label = _categoryLabel(o.category, ru: ru);
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        final l = AppLocalizations.of(ctx)!;
        return _cardShell(
          ctx,
          top: const Padding(padding: EdgeInsets.only(top: 10), child: Center(child: _SheetGrabber())),
          topGap: ui.Gap.md,
          children: [
            Row(children: [
              _CatIconChip(icon: style.icon, color: style.color),
              const SizedBox(width: ui.Gap.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(o.name.isEmpty ? label : o.name, style: ui.titleS(ctx)),
                    const SizedBox(height: 2),
                    Text(label,
                        style: GoogleFonts.manrope(
                            fontSize: 13, fontWeight: FontWeight.w600, color: style.color)),
                  ],
                ),
              ),
            ]),
            const SizedBox(height: ui.Gap.md),
            Text(l.nearbyHint,
                style: GoogleFonts.manrope(fontSize: 14, height: 1.5, color: c.textFaint)),
          ],
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
        // The walked GPS track, growing live (under the pins). Only while touring — otherwise it
        // would stay frozen under the blur on the inactive home. Glow + grey dashed on paused
        // stretches — same renderer as the summary / history so it looks identical everywhere.
        if (_touring && _track.length >= 2)
          PolylineLayer(
            polylines: trackPolylines(_track, liveColor: Theme.of(context).colorScheme.primary),
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
        // Co-walk: live dots of friends walking together with you (realtime presence).
        ListenableBuilder(
          listenable: RealtimeService.instance,
          builder: (context, _) => MarkerLayer(markers: [
            for (final peer in RealtimeService.instance.coWalkPeers)
              if (peer.hasPosition)
                Marker(
                  point: LatLng(peer.lat!, peer.lon!),
                  width: 96,
                  height: 58,
                  child: _CoWalkPin(name: peer.name),
                ),
          ]),
        ),
        MarkerLayer(markers: [
          for (final p in _places)
            Marker(
              point: p.point,
              width: 46,
              height: 46,
              child: GestureDetector(
                onTap: () => _showPlaceInfo(p),
                child: _MapPin(
                  size: p.id == _currentPlaceId ? 36 : 26,
                  color: p.id == _currentPlaceId ? _pinCurrent : _pinPast,
                ),
              ),
            ),
          Marker(
            point: _here,
            width: 52,
            height: 52,
            // Marker stays screen-upright (no rotate flag), so subtract the map bearing to keep
            // the arrow pointing at the true absolute heading when the map is rotated off north
            // (same correction the compass FAB applies).
            child: _UserPuck(heading: (_heading - _mapRotation) % 360),
          ),
        ]),
        RichAttributionWidget(
          attributions: [TextSourceAttribution(MapConfig.attribution)],
        ),
      ],
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
  // The Premium upgrade sheet — benefits + monthly/yearly buy buttons. Shown from the
  // daily-quota gate, the "history full" banner, the quota WS frame, and the settings
  // tile. Styled per the app's frosted/pastel design language.
  void _showUpgrade() {
    final l = AppLocalizations.of(context)!;
    final billing = BillingService.instance;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true, // keep the sheet clear of the notch / Dynamic Island
      backgroundColor: Colors.transparent,
      builder: (ctx) => AnimatedBuilder(
        // Rebuild on billing (busy/price) AND auth (tier flips to paid on success).
        animation: Listenable.merge([billing, AuthService.instance]),
        builder: (ctx, _) {
          final uc = Theme.of(context).extension<ui.AppColors>()!;
          final paid = AuthService.instance.isPaid;
          final signedIn = AuthService.instance.isSignedIn;
          TextStyle t(double s, FontWeight w, Color col) =>
              GoogleFonts.manrope(fontSize: s, fontWeight: w, color: col);
          Widget benefit(IconData icon, String text) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 7),
                child: Row(children: [
                  Container(
                    width: 34, height: 34, alignment: Alignment.center,
                    decoration: BoxDecoration(shape: BoxShape.circle, color: uc.primary.withValues(alpha: 0.14)),
                    child: Icon(icon, color: uc.primary, size: 18),
                  ),
                  const SizedBox(width: 12),
                  Expanded(child: Text(text, style: t(14.5, FontWeight.w600, uc.textPrimary))),
                ]),
              );
          Widget plan(String title, String price, VoidCallback onTap, {bool highlight = false}) =>
              ui.Pressable(
                onTap: onTap,
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 12),
                  decoration: BoxDecoration(
                    gradient: highlight ? LinearGradient(colors: [uc.primary, Color.lerp(uc.primary, Colors.black, .3)!]) : null,
                    color: highlight ? null : uc.glassFill(0.06),
                    borderRadius: BorderRadius.circular(ui.Radii.md),
                    border: highlight ? null : Border.all(color: uc.glassBorder),
                    boxShadow: highlight ? [BoxShadow(color: uc.primary.withValues(alpha: .4), blurRadius: 20, spreadRadius: -8, offset: const Offset(0, 10))] : null,
                  ),
                  child: Column(children: [
                    Text(title, style: t(15, FontWeight.w800, highlight ? uc.onPrimary : uc.textPrimary)),
                    const SizedBox(height: 3),
                    Text(price, style: t(13, FontWeight.w600, highlight ? uc.onPrimary.withValues(alpha: .85) : uc.textSecondary)),
                  ]),
                ),
              );
          return ui.CardSheet(
            scrollable: false,  // child owns its SingleChildScrollView; CardSheet just bounds it
            child: SingleChildScrollView(
              padding: EdgeInsets.fromLTRB(22, 12, 22, MediaQuery.of(ctx).viewInsets.bottom + 28),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                // Explicit down-arrow to dismiss — a bare grabber wasn't obvious enough.
                Center(
                  child: ui.Pressable(
                    onTap: () => Navigator.pop(ctx),
                    child: Container(
                      margin: const EdgeInsets.only(bottom: 14),
                      padding: const EdgeInsets.all(6),
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: uc.glassFill(0.06),
                        border: Border.all(color: uc.glassBorder),
                      ),
                      child: Icon(Icons.keyboard_arrow_down_rounded, color: uc.textSecondary, size: 26),
                    ),
                  ),
                ),
                const Center(child: ui.PremiumBadge(size: 56)),
                const SizedBox(height: 12),
                Text(l.premiumTitle, textAlign: TextAlign.center, style: t(24, FontWeight.w800, uc.textPrimary)),
                const SizedBox(height: 4),
                Text(l.premiumTagline, textAlign: TextAlign.center, style: t(14, FontWeight.w500, uc.textSecondary)),
                const SizedBox(height: 18),
                ui.GlassModule(
                  fill: uc.glassFill(0.05), sheen: false,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  child: Column(children: [
                    benefit(Icons.auto_awesome_rounded, l.premiumModel),
                    benefit(Icons.block_rounded, l.premiumNoAds),
                    benefit(Icons.all_inclusive_rounded, l.premiumUnlimitedTours),
                    benefit(Icons.bookmark_rounded, l.premiumUnlimitedSaves),
                  ]),
                ),
                const SizedBox(height: 20),
                if (paid) ...[
                  Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                    Icon(Icons.check_circle_rounded, color: uc.primary),
                    const SizedBox(width: 10),
                    Text(l.premiumActive, style: t(16, FontWeight.w800, uc.textPrimary)),
                  ]),
                  if (kStubBilling) ...[
                    const SizedBox(height: 6),
                    Center(child: TextButton(onPressed: () => billing.cancelStub(), child: Text(l.cancelSubscription))),
                  ],
                ] else if (!signedIn)
                  ui.AppButton(l.signIn, onTap: () async {
                    Navigator.pop(ctx);
                    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const LoginScreen()));
                  })
                else if (billing.busy)
                  const Center(child: Padding(padding: EdgeInsets.all(10), child: CircularProgressIndicator()))
                else ...[
                  Row(children: [
                    Expanded(child: plan(l.premiumMonthly, billing.monthly?.price ?? r'$5.99/mo', billing.buyMonthly)),
                    const SizedBox(width: 10),
                    Expanded(child: plan(l.premiumYearly, billing.yearly?.price ?? r'$39.99/yr', billing.buyYearly, highlight: true)),
                  ]),
                  if (!kStubBilling) ...[
                    const SizedBox(height: 6),
                    Center(child: TextButton(onPressed: billing.available ? billing.restore : null, child: Text(l.premiumRestore))),
                  ],
                ],
              ]),
            ),
          );
        },
      ),
    );
  }

  // The account card (opened from the Settings → account row). Identity + subscription
  // + account actions only. Theme and simulated-walk live in the Settings tab itself —
  // they were duplicated here and have been removed. App-styled (Manrope, matte glass).
  void _openSettings() {
    final l = AppLocalizations.of(context)!;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => StatefulBuilder(
        builder: (c, setSheet) {
          final uc = Theme.of(ctx).extension<ui.AppColors>()!;
          final auth = AuthService.instance;
          final fill = Theme.of(ctx).brightness == Brightness.dark ? uc.glass : const Color(0x8CFFFFFF);
          final paid = auth.isPaid;
          final premiumTappable = !paid || kStubBilling;
          final name = auth.displayName ?? '';
          TextStyle ts(double s, FontWeight w, Color col) => GoogleFonts.manrope(fontSize: s, fontWeight: w, color: col);
          return ui.CardSheet(
            child: Padding(
              padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).padding.bottom + 20),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                // dismiss
                Center(
                  child: ui.Pressable(
                    onTap: () => Navigator.pop(ctx),
                    child: Container(
                      margin: const EdgeInsets.only(bottom: 16),
                      padding: const EdgeInsets.all(6),
                      decoration: BoxDecoration(shape: BoxShape.circle, color: uc.glassFill(0.06), border: Border.all(color: uc.glassBorder)),
                      child: Icon(Icons.keyboard_arrow_down_rounded, color: uc.textSecondary, size: 24),
                    ),
                  ),
                ),
                // identity header
                ui.GlassModule(
                  fill: fill, sheen: false,
                  padding: const EdgeInsets.all(14),
                  child: Row(children: [
                    const ui.TravelerAvatar(size: 52),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(name.isEmpty ? l.homeGuest : name, maxLines: 1, overflow: TextOverflow.ellipsis, style: ts(17, FontWeight.w800, uc.textPrimary)),
                        const SizedBox(height: 2),
                        Text(auth.email ?? '', maxLines: 1, overflow: TextOverflow.ellipsis, style: ts(13, FontWeight.w600, uc.textFaint)),
                      ]),
                    ),
                    if (paid)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(color: uc.primary.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(ui.Radii.pill)),
                        child: Text(l.premiumActive, style: ts(11.5, FontWeight.w800, uc.primary)),
                      ),
                  ]),
                ),
                const SizedBox(height: 12),
                // subscription row
                ui.Pressable(
                  onTap: premiumTappable ? _showUpgrade : null,
                  child: ui.GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    child: Row(children: [
                      const Icon(Icons.workspace_premium_rounded, color: _accentAlt, size: 22),
                      const SizedBox(width: 12),
                      Expanded(child: Text(paid ? l.premiumActive : l.goPremium, style: ts(15, FontWeight.w700, uc.textPrimary))),
                      if (premiumTappable) Icon(Icons.chevron_right_rounded, color: uc.textFaint),
                    ]),
                  ),
                ),
                const SizedBox(height: 18),
                // sign out
                ui.AppButton(l.signOut, kind: ui.AppBtnKind.secondary, onTap: () async {
                  // Close the sheet BEFORE signing out: sign-out flips the auth gate, which
                  // disposes HomePage; a still-open sheet would rebuild against a dead context.
                  Navigator.of(context).pop();
                  await auth.signOut();
                }),
                const SizedBox(height: 4),
                Center(
                  child: TextButton.icon(
                    onPressed: () => _deleteAccount(setSheet),
                    icon: const Icon(Icons.delete_outline_rounded, size: 18),
                    label: Text(l.deleteAccount, style: ts(13.5, FontWeight.w700, uc.err)),
                    style: TextButton.styleFrom(foregroundColor: uc.err),
                  ),
                ),
              ]),
            ),
          );
        },
      ),
    );
  }

  // -- redesign: greeting + focus helpers ---------------------------------
  String _greeting(AppLocalizations l) {
    final h = DateTime.now().hour;
    if (h < 5) return l.greetNight;
    if (h < 12) return l.greetMorning;
    if (h < 18) return l.greetAfternoon;
    if (h < 23) return l.greetEvening;
    return l.greetNight;
  }

  // The user's nickname for the header/profile: backend display_name → Supabase
  // metadata → email local part (see AuthService.displayName), else "" (caller falls
  // back to a generic traveler label).
  String _nick() {
    if (kDemoProfile) return 'Kolbasenko';
    return AuthService.instance.displayName ?? '';
  }

  List<({String code, IconData icon})> _focusItems() =>
      [for (final t in kThemes) (code: t.code, icon: t.icon)];

  // A simple branded chooser sheet (language / route).
  void _pickFromList(String title, List<({String value, String label})> items,
      String selected, ValueChanged<String> onPick) {
    final cc = _c(context);
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) => Container(
        decoration: BoxDecoration(
          color: cc.sheetBg,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        ),
        padding: EdgeInsets.fromLTRB(12, 12, 12, MediaQuery.of(ctx).padding.bottom + 12),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const _SheetGrabber(),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(title, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: cc.textPrimary)),
            ),
          ),
          Flexible(
            child: ListView(
              shrinkWrap: true,
              children: [
                for (final it in items)
                  ListTile(
                    title: Text(it.label),
                    trailing: it.value == selected
                        ? Icon(Icons.check_rounded, color: cc.textPrimary)
                        : null,
                    onTap: () {
                      Navigator.of(ctx).pop();
                      onPick(it.value);
                    },
                  ),
              ],
            ),
          ),
        ]),
      ),
    );
  }

  void _pickLanguage() => _pickFromList(
        AppLocalizations.of(context)!.language,
        [for (final e in kLangs.entries) (value: e.key, label: e.value.label)],
        _lang,
        _changeLanguage,
      );

  void _pickRoute() {
    if (_active) return;
    _pickFromList(
      AppLocalizations.of(context)!.route,
      [for (final k in kRoutes.keys) (value: k, label: kRouteLabels[k] ?? k)],
      _routeKey,
      (v) => setState(() => _routeKey = v),
    );
  }

  // Home tab: the live map, blurred behind the inactive modules; status chip + player
  // when a tour is active. Map FABs sit top-right during an active tour.
  // Stop button → full session end. Sessions ≥10 min are kept + a summary is shown;
  // shorter ones are discarded (the backend is told to drop the walk) and just reset.
  // Wipe everything a finished walk left on the home screen — narrated pins, lite objects,
  // the GPS track and the current-narration card — so the idle map (under the blur) is clean
  // and a new tour never shows the previous walk's residue.
  void _clearWalkArtifacts() {
    if (!mounted) {
      _places.clear();
      _nearby = [];
      _track.clear();
      return;
    }
    setState(() {
      _places.clear();
      _nearby = [];
      _track.clear();
      _curTitle = null;
      _curText = null;
      _curIsReply = false;
    });
  }

  void _endSession() {
    final start = _sessionStart;
    final elapsed = start == null ? Duration.zero : DateTime.now().difference(start);
    final recorded = elapsed >= _kMinRecord;
    final places = _places.where((p) => p.text.trim().isNotEmpty).toList();
    final track = List<List<double>>.from(_track); // snapshot for the summary before we wipe
    final meters = _sessionMeters;
    _walkSummary.value = null; // fresh spinner in the sheet until the recap arrives
    _hush(); // cut narration/neural audio IMMEDIATELY (the deferred _disconnect no longer does it)
    // Tell the backend to keep or discard this session's walk BEFORE closing the socket.
    if (_connected) _send({'type': 'end', 'discard': !recorded});
    _stopWalk();
    // A kept walk gets an async structured recap over the WS — keep the socket open briefly so
    // it can land in the Stop sheet, then close. A discarded walk closes at once (no recap).
    _summaryTimer?.cancel();
    if (recorded && _connected) {
      // Keep the socket open for the async recap; if it never lands, stop the spinner (empty ==
      // section hidden, not an infinite load) and close.
      _summaryTimer = Timer(const Duration(seconds: 24), () {
        if (!mounted) return;
        if (_walkSummary.value == null) _walkSummary.value = '';
        _disconnect();
      });
    } else {
      _disconnect();
    }
    if (AccountsConfig.enabled) AuthService.instance.refreshEntitlement();
    _showSessionSummary(
        recorded: recorded, elapsed: elapsed, meters: meters, places: places, track: track);
    // Hard end: clear the finished walk off the home screen NOW (the summary sheet uses the
    // snapshots above), so nothing lingers on the blurred map behind/after it.
    _clearWalkArtifacts();
  }

  String _fmtDuration(AppLocalizations l, Duration d) {
    final h = d.inHours, m = d.inMinutes % 60;
    if (h > 0) return '$h ${l.unitHr} ${m.toString().padLeft(2, '0')} ${l.unitMin}';
    return '$m ${l.unitMin}';
  }

  String _fmtDistance(AppLocalizations l, double meters) {
    if (meters >= 1000) return '${(meters / 1000).toStringAsFixed(1)} ${l.unitKm}';
    return '${meters.round()} ${l.unitM}';
  }

  // Beautifully-formatted end-of-walk summary (duration · distance · places covered).
  void _showSessionSummary({
    required bool recorded,
    required Duration elapsed,
    required double meters,
    required List<PlaceMark> places,
    required List<List<double>> track,
  }) {
    final l = AppLocalizations.of(context)!;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        final uc = Theme.of(ctx).extension<ui.AppColors>()!;
        final accent = recorded ? uc.ok : _stAmber;
        Widget stat(IconData icon, String value, String label) => Expanded(
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 8),
                decoration: BoxDecoration(
                  color: uc.glassFill(0.05),
                  borderRadius: BorderRadius.circular(ui.Radii.md),
                  border: Border.all(color: uc.glassBorder),
                ),
                child: Column(children: [
                  Icon(icon, color: accent, size: 20),
                  const SizedBox(height: 8),
                  Text(value, maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: GoogleFonts.manrope(fontSize: 16, fontWeight: FontWeight.w800, color: uc.textPrimary)),
                  const SizedBox(height: 2),
                  Text(label, style: GoogleFonts.manrope(fontSize: 11.5, fontWeight: FontWeight.w600, color: uc.textFaint)),
                ]),
              ),
            );
        return ui.CardSheet(
          scrollable: true,  // one scroll region for the whole summary (stats+map+recap+places)
          child: Padding(
            padding: EdgeInsets.fromLTRB(20, 14, 20, MediaQuery.of(ctx).padding.bottom + 18),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              // celebratory / info badge
              Center(
                child: Container(
                  width: 64, height: 64, alignment: Alignment.center,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(colors: [accent, Color.lerp(accent, Colors.black, 0.28)!], begin: Alignment.topLeft, end: Alignment.bottomRight),
                    boxShadow: [BoxShadow(color: accent.withValues(alpha: 0.4), blurRadius: 20, spreadRadius: -4, offset: const Offset(0, 8))],
                  ),
                  child: Icon(recorded ? Icons.check_rounded : Icons.timer_off_rounded, color: uc.onPrimary, size: 32),
                ),
              ),
              const SizedBox(height: 14),
              Text(recorded ? l.summaryTitle : l.summaryDiscardTitle,
                  textAlign: TextAlign.center, style: ui.h1(context).copyWith(fontSize: 22)),
              const SizedBox(height: 16),
              Row(children: [
                stat(Icons.schedule_rounded, _fmtDuration(l, elapsed), l.summaryDuration),
                const SizedBox(width: 10),
                stat(Icons.straighten_rounded, _fmtDistance(l, meters), l.summaryDistance),
                const SizedBox(width: 10),
                stat(Icons.place_rounded, '${places.length}', l.summaryPlaces),
              ]),
              if (track.length >= 2) ...[
                const SizedBox(height: 14),
                TrackMap(path: track, height: 150, borderRadius: ui.Radii.md),
              ],
              if (recorded) ...[
                const SizedBox(height: 14),
                ValueListenableBuilder<String?>(
                  valueListenable: _walkSummary,
                  builder: (context, summary, _) {
                    if (summary == null) {
                      // Recap still generating — a quiet spinner (no text, so no locale issue).
                      return Container(
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        alignment: Alignment.center,
                        child: SizedBox(
                          width: 22, height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2.2, color: uc.textFaint),
                        ),
                      );
                    }
                    if (summary.isEmpty) return const SizedBox.shrink();
                    // No inner scroll / height cap: the whole sheet scrolls (scrollable:true),
                    // so the recap flows inline and a nested scroll view would conflict.
                    return Container(
                      width: double.infinity,
                      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
                      decoration: BoxDecoration(
                        color: uc.glassFill(0.05),
                        borderRadius: BorderRadius.circular(ui.Radii.md),
                        border: Border.all(color: uc.glassBorder),
                      ),
                      child: Text(summary, style: GoogleFonts.manrope(
                        fontSize: 13.5, height: 1.5, fontWeight: FontWeight.w600,
                        color: uc.textSecondary)),
                    );
                  },
                ),
              ],
              if (!recorded) ...[
                const SizedBox(height: 14),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: _stAmber.withValues(alpha: 0.10),
                    borderRadius: BorderRadius.circular(ui.Radii.md),
                    border: Border.all(color: _stAmber.withValues(alpha: 0.25)),
                  ),
                  child: Row(children: [
                    const Icon(Icons.info_outline_rounded, color: _stAmber, size: 18),
                    const SizedBox(width: 10),
                    Expanded(child: Text(l.summaryDiscardNote,
                        style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w600, height: 1.35, color: uc.textSecondary))),
                  ]),
                ),
              ],
              if (recorded && places.isNotEmpty) ...[
                const SizedBox(height: 18),
                Align(alignment: Alignment.centerLeft, child: Text(l.summaryTold, style: ui.label(context))),
                const SizedBox(height: 8),
                // Non-scrolling list: the outer CardSheet (scrollable:true) owns the scroll, so
                // the places flow inline and grow the single scroll region rather than nesting.
                for (var i = 0; i < places.length; i++) ...[
                  if (i > 0) const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: uc.glassFill(0.05),
                      borderRadius: BorderRadius.circular(ui.Radii.md),
                      border: Border.all(color: uc.glassBorder),
                    ),
                    child: Row(children: [
                      Container(
                        width: 26, height: 26, alignment: Alignment.center,
                        decoration: BoxDecoration(shape: BoxShape.circle, color: uc.primary.withValues(alpha: 0.16)),
                        child: Text('${i + 1}', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, color: uc.primary)),
                      ),
                      const SizedBox(width: 10),
                      Expanded(child: Text(places[i].name.isEmpty ? '—' : places[i].name, maxLines: 1, overflow: TextOverflow.ellipsis, style: ui.titleS(ctx))),
                    ]),
                  ),
                ],
              ],
              const SizedBox(height: 18),
              ui.AppButton(l.summaryDone, onTap: () => Navigator.pop(ctx)),
            ]),
          ),
        );
      },
    );
  }

  // Journal of the current walk: the places narrated so far (`_places`), newest first.
  void _openTourLog() {
    final l = AppLocalizations.of(context)!;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        final uc = Theme.of(ctx).extension<ui.AppColors>()!;
        final items = _places.reversed.where((p) => p.text.trim().isNotEmpty).toList();
        return ui.CardSheet(
          scrollable: false,  // holds a Flexible>ListView(shrinkWrap) of places; CardSheet bounds it
          child: Padding(
            padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).padding.bottom + 20),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Center(
                child: ui.Pressable(
                  onTap: () => Navigator.pop(ctx),
                  child: Container(
                    margin: const EdgeInsets.only(bottom: 14),
                    padding: const EdgeInsets.all(6),
                    decoration: BoxDecoration(shape: BoxShape.circle, color: uc.glassFill(0.06), border: Border.all(color: uc.glassBorder)),
                    child: Icon(Icons.keyboard_arrow_down_rounded, color: uc.textSecondary, size: 22),
                  ),
                ),
              ),
              Row(children: [
                Icon(ui.AppIcons.history, color: uc.primary, size: 22),
                const SizedBox(width: 10),
                Text(l.tourLogTitle, style: ui.h2(ctx)),
              ]),
              const SizedBox(height: 14),
              if (items.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 40),
                  child: Text(l.tourLogEmpty, textAlign: TextAlign.center,
                      style: GoogleFonts.manrope(fontSize: 14.5, fontWeight: FontWeight.w500, height: 1.4, color: uc.textFaint)),
                )
              else
                Flexible(
                  child: ListView.separated(
                    shrinkWrap: true,
                    padding: EdgeInsets.zero,
                    itemCount: items.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 10),
                    itemBuilder: (ctx, i) {
                      final p = items[i];
                      return ui.Pressable(
                        onTap: () {
                          Navigator.pop(ctx);
                          _animateTo(p.point, zoom: 17);
                        },
                        child: Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: uc.glassFill(0.05),
                            borderRadius: BorderRadius.circular(ui.Radii.md),
                            border: Border.all(color: uc.glassBorder),
                          ),
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Row(children: [
                              Container(
                                width: 26, height: 26, alignment: Alignment.center,
                                decoration: BoxDecoration(shape: BoxShape.circle, color: uc.primary.withValues(alpha: 0.16)),
                                child: Text('${items.length - i}', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, color: uc.primary)),
                              ),
                              const SizedBox(width: 10),
                              Expanded(child: Text(p.name.isEmpty ? '—' : p.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: ui.titleS(ctx))),
                            ]),
                            if (p.text.trim().isNotEmpty) ...[
                              const SizedBox(height: 8),
                              Text(p.text.trim(), style: GoogleFonts.manrope(fontSize: 13.5, fontWeight: FontWeight.w500, height: 1.45, color: uc.textSecondary)),
                            ],
                          ]),
                        ),
                      );
                    },
                  ),
                ),
            ]),
          ),
        );
      },
    );
  }

  Widget _homeTab(AppLocalizations l) {
    final active = _active;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final uc = Theme.of(context).extension<ui.AppColors>()!;
    final s = _status(l);
    final nick = _nick();
    return Stack(children: [
      Positioned.fill(child: _mapView()),
      // Frosted "matte glass" over the live map behind the inactive home modules: a
      // strong blur plus a translucent palette-tinted scrim so the map reads as frosted
      // glass (warm in light, slate in dark), not a washed-out blank.
      Positioned.fill(
        child: IgnorePointer(
          ignoring: active,
          child: AnimatedOpacity(
            duration: const Duration(milliseconds: 1100),
            curve: Curves.easeInOutCubic,
            opacity: active ? 0 : 1,
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: dark ? 5 : 3, sigmaY: dark ? 5 : 3),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      uc.bgTop.withValues(alpha: dark ? 0.16 : 0.03),
                      uc.bgBottom.withValues(alpha: dark ? 0.28 : 0.08),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
      // Map controls — always visible for the whole tour (zoom/compass/recenter),
      // centred on the right edge between the status island and the control panel.
      if (active)
        Positioned(
          top: 0, bottom: 0, right: 12,
          child: Center(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              _zoomFab(l),
              const SizedBox(height: 10),
              _compassFab(l),
              const SizedBox(height: 10),
              _followFab(l),
            ]),
          ),
        ),
      // The redesigned modules (header/premium/focus/swipe · status/player).
      Positioned.fill(
        child: ui.HomeModules(
          active: active,
          greeting: _greeting(l),
          nick: nick.isEmpty ? l.homeGuest : nick,
          prompt: l.homePrompt,
          isDark: dark,
          onToggleTheme: () => widget.onThemeModeChanged(dark ? ThemeMode.light : ThemeMode.dark),
          onSystemTheme: () => widget.onThemeModeChanged(ThemeMode.system),
          showPremium: !(AuthService.instance.isPaid || kDemoProfile),
          premiumTitle: l.goPremium,
          premiumSubtitle: l.premiumTrial,
          onUpgrade: _showUpgrade,
          focusTitle: l.focusTitle,
          focusItems: _focusItems(),
          focusSelected: _theme,
          onFocus: _setTheme,
          swipeLabel: l.swipeToStart,
          onStart: _primary,
          statusLabel: s.label,
          statusColor: s.color,
          statusActive: s.active,
          title: _curIsReply ? l.chipAnswering : _curTitle,
          text: _curText,
          paused: _paused,
          recording: _recording,
          voice: _voice,
          onStop: active ? _endSession : null,
          onPause: active ? _togglePause : null,
          onAsk: _connected ? _openAsk : null,
          onMic: _connected ? _toggleMic : null,
          onToggleVoice: _toggleVoice,
          onHistory: active ? _openTourLog : null,
        ),
      ),
    ]);
  }

  Widget _settingsTab(AppLocalizations l) {
    final signedIn = AccountsConfig.enabled && AuthService.instance.isSignedIn;
    final email = AuthService.instance.email;
    final tier = AuthService.instance.isPaid ? l.premiumActive : 'free';
    return ui.SettingsTab(
      themeMode: widget.themeMode,
      onThemeMode: widget.onThemeModeChanged,
      langLabel: kLangs[_lang]?.label ?? _lang,
      onLanguage: _pickLanguage,
      accountsEnabled: AccountsConfig.enabled,
      accountTitle: signedIn ? '${email ?? ''} · $tier' : l.continueAsGuest,
      onAccount: signedIn ? _openSettings : null,
      onUpgrade: _showUpgrade,
      isPaid: AuthService.instance.isPaid,
      simulate: _simulate,
      onSimulate: _active ? null : (v) => setState(() => _simulate = v),
      routeLabel: kRouteLabels[_routeKey] ?? _routeKey,
      onRoute: _active ? null : _pickRoute,
    );
  }

  // Fetch the signed-in user's walks once and aggregate them into full ProfileStats
  // (cities/distance/objects/languages/streak) for the profile. Cheap: one /walks call.
  Future<void> _ensureProfileStats() async {
    if (_statsFetched || !(AccountsConfig.enabled && AuthService.instance.isSignedIn)) return;
    _statsFetched = true;
    try {
      final walks = await WalkApi.listWalks();
      if (!mounted) return;
      setState(() => _aggregatedStats = _aggregateStats(walks, isPaid: AuthService.instance.isPaid));
    } catch (_) {/* keep the walk-count fallback */}
  }

  static ui.ProfileStats _aggregateStats(List<WalkSummary> walks, {required bool isPaid}) {
    final cities = <String>{}, districts = <String>{}, langs = <String>{};
    final cityOrder = <String>[]; // distinct, preserving first-seen order
    var distance = 0, objects = 0;
    var early = false, night = false;
    final days = <DateTime>{};
    for (final w in walks) {
      if (w.city != null && w.city!.isNotEmpty) {
        if (cities.add(w.city!)) cityOrder.add(w.city!);
      }
      if (w.district != null && w.district!.isNotEmpty) districts.add(w.district!);
      langs.add(w.language);
      distance += w.distanceM ?? 0;
      objects += w.objectCount;
      final t = w.startedAt.toLocal();
      if (t.hour < 7) early = true;
      if (t.hour >= 22) night = true;
      days.add(DateTime(t.year, t.month, t.day));
    }
    // Longest run of consecutive calendar days.
    final sorted = days.toList()..sort();
    var streak = sorted.isEmpty ? 0 : 1, run = sorted.isEmpty ? 0 : 1;
    for (var i = 1; i < sorted.length; i++) {
      run = sorted[i].difference(sorted[i - 1]).inDays == 1 ? run + 1 : 1;
      if (run > streak) streak = run;
    }
    return ui.ProfileStats(
      walks: walks.length,
      cities: cities.length,
      cityNames: cityOrder,
      districts: districts.length,
      distanceM: distance,
      objects: objects,
      languages: langs.length,
      streakDays: streak,
      hasEarlyWalk: early,
      hasNightWalk: night,
      isPaid: isPaid,
      signedIn: true,
    );
  }

  // Sign out after confirmation. On success the auth gate (root build) swaps back to
  // the login screen automatically via the AuthService listener.
  Future<void> _confirmSignOut() async {
    final l = AppLocalizations.of(context)!;
    final ok = await ui.showBrandConfirm(
      context,
      icon: Icons.logout_rounded,
      title: l.signOut,
      message: 'Выйти из аккаунта? Прогулки останутся сохранёнными.',
      confirmLabel: l.signOut,
      cancelLabel: l.cancel,
    );
    if (!ok) return;
    _disconnect(); // stop any tour/WS before the gate tears HomePage down
    await AuthService.instance.signOut();
    if (!mounted) return;
    // When accounts are enabled, the root auth gate swaps to the login screen on its own
    // (and this State is already unmounted by then). In demo / guest builds there is no
    // gate, so take the user to the login screen explicitly — "sign out" always lands on
    // the entry screen.
    if (!AccountsConfig.enabled) {
      Navigator.of(context, rootNavigator: true).pushAndRemoveUntil(
        MaterialPageRoute<void>(builder: (_) => const LoginScreen(isGate: true)),
        (route) => false,
      );
    }
  }

  Widget _profileTab(AppLocalizations l) {
    final nick = _nick();
    final signedIn = AccountsConfig.enabled && AuthService.instance.isSignedIn;
    // Demo: a fully-populated profile matching the seeded test account (Kolbasenko).
    // Real: level/count come from /me; the richer per-walk stats (cities/distance/…)
    // await a /walks fetch — a follow-up — so they read 0 until then.
    if (!kDemoProfile) _ensureProfileStats(); // real: aggregate from /walks (once)
    final stats = kDemoProfile
        ? const ui.ProfileStats(
            walks: 16, cities: 3, districts: 16, distanceM: 43600, objects: 195,
            languages: 3, streakDays: 7, hasEarlyWalk: true, hasNightWalk: true,
            isPaid: true, signedIn: true,
            cityNames: ['Москва', 'Санкт-Петербург', 'Казань'],
          )
        : (_aggregatedStats ??
            ui.ProfileStats(
              walks: AuthService.instance.profile?.walkCount ?? 0,
              isPaid: AuthService.instance.isPaid,
              signedIn: signedIn,
            ));
    final friends = kDemoProfile
        ? const [
            (id: 'd1', nick: 'Sosiskin', walks: 3, paid: false),
            (id: 'd2', nick: 'Аня', walks: 22, paid: true),
            (id: 'd3', nick: 'Макс', walks: 9, paid: false),
            (id: 'd4', nick: 'Лена', walks: 6, paid: true),
            (id: 'd5', nick: 'Игорь', walks: 1, paid: false),
          ]
        : AuthService.instance.friends;
    final myId = AuthService.instance.userId ?? 'me';
    final inviteUrl = 'https://aiguide.app/i/$myId';
    void openInvite() => Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => ui.InviteScreen(inviteUrl: inviteUrl, nick: nick.isEmpty ? l.homeGuest : nick)));
    return ui.ProfileTab(
      nick: nick.isEmpty ? l.homeGuest : nick,
      stats: stats,
      avatarUrl: kDemoProfile ? null : AuthService.instance.avatarUrl,
      signedIn: signedIn || kDemoProfile,
      onSignOut: _confirmSignOut,
      onFriends: () => Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => ui.FriendsScreen(friends: friends, onInvite: openInvite))),
      onInvite: openInvite,
      onEdit: (signedIn || kDemoProfile)
          ? () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const AccountEditScreen()))
          : null,
      friends: friends,
      onOpenFriend: (f) => Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => ui.FriendProfileScreen(friend: f))),
      inviteUrl: inviteUrl,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    _screenH = MediaQuery.of(context).size.height;
    final barHidden = _tab == 0 && _active;
    // Rebuild tabs that read AuthService (profile/settings/home premium) when it changes.
    return AnimatedBuilder(
      animation: AuthService.instance,
      builder: (context, _) => Scaffold(
        extendBody: true,
        body: Stack(children: [
          IndexedStack(index: _tab, children: [
            _homeTab(l),
            const ui.CommunityScreen(),
            _profileTab(l),
            _settingsTab(l),
          ]),
          // Floating tab bar (hidden during an active tour on Home).
          AnimatedPositioned(
            duration: ui.Motion.med,
            curve: ui.Motion.emphasized,
            left: 16,
            right: 16,
            bottom: barHidden ? -110 : MediaQuery.of(context).padding.bottom + 12,
            child: AnimatedOpacity(
              duration: ui.Motion.fast,
              opacity: barHidden ? 0 : 1,
              child: ui.FloatingTabBar(
                index: _tab,
                onChanged: (i) => setState(() => _tab = i),
              ),
            ),
          ),
        ]),
      ),
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
            // Crisp border ring instead of a blurred drop-shadow — the latter smears
            // when the map pans under Impeller (iOS sim).
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.white,
              border: Border.all(color: Colors.black.withValues(alpha: 0.18), width: 1.5),
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
