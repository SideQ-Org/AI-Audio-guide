// Thin wrapper around supabase_flutter's auth, exposed as a ChangeNotifier so the UI
// (login screen, history screen, settings tile) and the WebSocket layer can react to
// sign-in / sign-out. When accounts are disabled (no Supabase config) every getter is
// inert and every action is a no-op — the guest path is completely untouched.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'accounts_config.dart';
import 'api_client.dart';
import 'models.dart';

/// SharedPreferences key for the stubbed-premium override (see [setStubEntitlement]).
/// Only used while billing is stubbed; remove when real store billing is wired.
const _kStubPaidPref = 'stub_paid';

class AuthService extends ChangeNotifier {
  AuthService._();
  static final AuthService instance = AuthService._();

  bool _ready = false;
  bool _refreshing = false; // guards against overlapping refreshEntitlement() calls
  bool _stubPaid = false; // local premium override while billing is stubbed
  StreamSubscription<AuthState>? _sub;
  UserProfile? _profile; // entitlements from GET /me (tier, quota, saved-walk counts)

  /// Initialize Supabase once, at app start. No-op when accounts are disabled.
  Future<void> init() async {
    if (!AccountsConfig.enabled || _ready) return;
    await Supabase.initialize(
      url: AccountsConfig.supabaseUrl,
      // The public client key (Supabase renamed anon -> publishable; same value).
      publishableKey: AccountsConfig.supabaseAnonKey,
    );
    _ready = true;
    // Restore a stubbed-premium override from a previous session (billing stub only).
    try {
      final prefs = await SharedPreferences.getInstance();
      _stubPaid = prefs.getBool(_kStubPaidPref) ?? false;
    } catch (_) {
      _stubPaid = false;
    }
    // Re-emit on every auth change (sign-in, sign-out, token refresh) so the WS
    // layer re-sends the `auth` message and the UI reflects the new state. Also refresh
    // entitlements (tier/quota) — refreshEntitlement itself notifies listeners.
    _sub = Supabase.instance.client.auth.onAuthStateChange.listen(
      (_) => refreshEntitlement(),
    );
  }

  bool get available => AccountsConfig.enabled && _ready;

  /// The user's entitlements (tier, quota, saved-walk counts), or null when signed
  /// out / not yet loaded. UI reads this to show the right tier + upgrade prompts.
  UserProfile? get profile => _profile;

  /// Whether the user has premium. The backend profile is authoritative; [_stubPaid]
  /// is a local override for the stubbed purchase flow (removed when real billing lands).
  bool get isPaid => _stubPaid || (_profile?.isPaid ?? false);

  /// Locally grant/revoke premium for the stubbed purchase flow, persisted across
  /// restarts. NOTE: client-side only — the backend still sees the real tier, so
  /// server-enforced quotas aren't lifted. Replace with real receipt verification.
  Future<void> setStubEntitlement(bool paid) async {
    _stubPaid = paid;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_kStubPaidPref, paid);
    } catch (_) {
      // best-effort persistence; the in-memory flag still applies this session
    }
    notifyListeners();
  }

  /// Reload the profile from the backend (GET /me). Called on every auth change and
  /// after a successful purchase. Best-effort: a network error keeps the last value.
  Future<void> refreshEntitlement() async {
    if (!available || !isSignedIn) {
      _profile = null;
      notifyListeners();
      return;
    }
    // Collapse overlapping refreshes into one in-flight request. Several triggers
    // (auth-state stream, tour end, history screen) can fire near-simultaneously;
    // without this they stack up redundant GET /me calls.
    if (_refreshing) return;
    _refreshing = true;
    try {
      _profile = await WalkApi.getMe();
    } catch (_) {
      // keep the last known profile; the UI degrades gracefully
    } finally {
      _refreshing = false;
    }
    notifyListeners();
  }

  Session? get _session =>
      available ? Supabase.instance.client.auth.currentSession : null;

  bool get isSignedIn => _session != null;

  /// The Supabase access token (JWT) to send to the backend over WS / REST.
  String? get accessToken => _session?.accessToken;

  String? get email =>
      available ? Supabase.instance.client.auth.currentUser?.email : null;

  String? get userId =>
      available ? Supabase.instance.client.auth.currentUser?.id : null;

  Future<void> signInWithEmail(String email, String password) async {
    await Supabase.instance.client.auth
        .signInWithPassword(email: email, password: password);
  }

  Future<void> signUpWithEmail(String email, String password) async {
    await Supabase.instance.client.auth
        .signUp(email: email, password: password);
  }

  /// Send a password-reset email. The link opens the app via the OAuth deep link.
  Future<void> sendPasswordReset(String email) async {
    await Supabase.instance.client.auth.resetPasswordForEmail(
      email,
      redirectTo: kIsWeb ? null : AccountsConfig.oauthRedirect,
    );
  }

  Future<void> signInWithGoogle() => _oauth(OAuthProvider.google);
  Future<void> signInWithApple() => _oauth(OAuthProvider.apple);

  Future<void> _oauth(OAuthProvider provider) async {
    await Supabase.instance.client.auth.signInWithOAuth(
      provider,
      redirectTo: kIsWeb ? null : AccountsConfig.oauthRedirect,
    );
  }

  Future<void> signOut() async {
    if (!available) return;
    await Supabase.instance.client.auth.signOut();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}
