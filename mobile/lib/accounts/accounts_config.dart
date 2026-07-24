// Compile-time accounts config (dart-define), mirroring the WS_URL/WS_TOKEN pattern
// in main.dart. When SUPABASE_URL / SUPABASE_ANON_KEY are empty the whole accounts
// feature is DISABLED and the app behaves exactly as before (guest-only) — so the
// client builds and runs without a Supabase project (see SUPABASE_SETUP.md for phase 7).

class AccountsConfig {
  static const supabaseUrl =
      String.fromEnvironment('SUPABASE_URL', defaultValue: '');
  static const supabaseAnonKey =
      String.fromEnvironment('SUPABASE_ANON_KEY', defaultValue: '');

  // The REST base for /me + /walks. Defaults to deriving it from the WS URL
  // (ws://host/ws -> http://host), overridable with --dart-define=API_URL.
  static const wsUrl =
      String.fromEnvironment('WS_URL', defaultValue: 'ws://localhost:8000/ws');
  static const _apiUrlOverride =
      String.fromEnvironment('API_URL', defaultValue: '');

  // OAuth deep-link back into the app after the browser sign-in (Google/Apple).
  // Must match a redirect URL configured in the Supabase project + platform.
  static const oauthRedirect = String.fromEnvironment(
    'SUPABASE_OAUTH_REDIRECT',
    defaultValue: 'aiguide://login-callback',
  );

  static bool get enabled =>
      supabaseUrl.isNotEmpty && supabaseAnonKey.isNotEmpty;

  static bool get hasApiOverride => _apiUrlOverride.isNotEmpty;

  static String get apiBase =>
      hasApiOverride ? _apiUrlOverride : _deriveApiBase(wsUrl);

  static Uri? get wsUri => Uri.tryParse(wsUrl);
  static Uri? get apiUri => Uri.tryParse(apiBase);

  static String? endpointConfigError({required bool isReleaseBuild}) {
    final ws = wsUri;
    if (ws == null || !_validUri(ws, const {'ws', 'wss'})) {
      return 'Некорректный WS_URL: $wsUrl';
    }
    final api = apiUri;
    if (api == null || !_validUri(api, const {'http', 'https'})) {
      final source = hasApiOverride ? 'API_URL' : 'WS_URL';
      return 'Некорректный $source: $apiBase';
    }
    if (isReleaseBuild) {
      if (_looksLocalHost(ws.host)) {
        return 'APK собран без prod WS_URL: $wsUrl';
      }
      if (_looksLocalHost(api.host)) {
        return 'APK собран без prod API_URL: $apiBase';
      }
    }
    return null;
  }

  static bool _validUri(Uri uri, Set<String> schemes) =>
      schemes.contains(uri.scheme) && uri.host.isNotEmpty;

  static bool _looksLocalHost(String host) {
    final h = host.trim().toLowerCase();
    return h == 'localhost' || h == '127.0.0.1' || h == '0.0.0.0';
  }

  static String _deriveApiBase(String wsUrl) {
    var u = wsUrl;
    if (u.startsWith('wss://')) {
      u = 'https://${u.substring(6)}';
    } else if (u.startsWith('ws://')) {
      u = 'http://${u.substring(5)}';
    }
    if (u.endsWith('/ws')) u = u.substring(0, u.length - 3);
    return u;
  }
}
