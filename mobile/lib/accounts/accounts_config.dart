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
  static const _wsUrl =
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

  static String get apiBase =>
      _apiUrlOverride.isNotEmpty ? _apiUrlOverride : _deriveApiBase(_wsUrl);

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
