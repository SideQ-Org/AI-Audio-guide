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

  /// The user's nickname for the profile. Prefers the backend `display_name`, then the
  /// Supabase user metadata (`display_name`/`name`/`full_name`), else the email's local
  /// part. Null when nothing is known (guest) — the UI shows a generic label.
  String? get displayName {
    final fromProfile = _profile?.displayName;
    if (fromProfile != null && fromProfile.trim().isNotEmpty) return fromProfile.trim();
    if (available) {
      final md = Supabase.instance.client.auth.currentUser?.userMetadata;
      for (final k in const ['display_name', 'name', 'full_name', 'nickname']) {
        final v = md?[k];
        if (v is String && v.trim().isNotEmpty) return v.trim();
      }
    }
    final e = email;
    if (e != null && e.contains('@')) {
      final u = e.split('@').first;
      if (u.isNotEmpty) return u;
    }
    return null;
  }

  String? get userId =>
      available ? Supabase.instance.client.auth.currentUser?.id : null;

  Future<void> signInWithEmail(String email, String password) async {
    await Supabase.instance.client.auth
        .signInWithPassword(email: email, password: password);
  }

  /// Create an account. Optional profile fields ([nick] / [birthdayIso] / [avatarUrl])
  /// are written to user_metadata up-front so they survive the email-confirm step. When
  /// email confirmation is on, no session is returned — the caller then collects the
  /// 6-digit code and calls [verifySignupOtp].
  Future<void> signUpWithEmail(
    String email,
    String password, {
    String? nick,
    String? birthdayIso,
    String? avatarUrl,
    String? addressForm,
  }) async {
    final data = <String, dynamic>{};
    if (nick != null && nick.isNotEmpty) data['display_name'] = nick;
    if (birthdayIso != null && birthdayIso.isNotEmpty) data['birthday'] = birthdayIso;
    if (avatarUrl != null && avatarUrl.isNotEmpty) data['avatar_url'] = avatarUrl;
    // Only persist a non-neutral choice up-front; neutral is the default with no key.
    if (addressForm == 'masculine' || addressForm == 'feminine') {
      data['address_form'] = addressForm;
    }
    await Supabase.instance.client.auth
        .signUp(email: email, password: password, data: data.isEmpty ? null : data);
  }

  /// Confirm a fresh signup with the 6-digit code from the email. On success a session
  /// is established (the auth-state stream fires → the gate swaps to the app).
  Future<void> verifySignupOtp(String email, String code) async {
    await Supabase.instance.client.auth.verifyOTP(
      type: OtpType.signup,
      email: email,
      token: code.trim(),
    );
  }

  /// Re-send the signup confirmation code to [email].
  Future<void> resendSignupOtp(String email) async {
    await Supabase.instance.client.auth.resend(type: OtpType.signup, email: email);
  }

  /// Send a password-reset email. The link opens the app via the OAuth deep link.
  Future<void> sendPasswordReset(String email) async {
    await Supabase.instance.client.auth.resetPasswordForEmail(
      email,
      redirectTo: kIsWeb ? null : AccountsConfig.oauthRedirect,
    );
  }

  /// The user's birthday (ISO `YYYY-MM-DD`), stored in Supabase user_metadata and NOT
  /// shown on the profile — kept only for a future "happy birthday" greeting.
  String? get birthday {
    if (!available) return null;
    final v = Supabase.instance.client.auth.currentUser?.userMetadata?['birthday'];
    return v is String && v.isNotEmpty ? v : null;
  }

  /// The user's OPTIONAL form of address — how the guide should address them grammatically
  /// ("masculine" | "feminine" | "" = neutral, the default). Stored in user_metadata; sent to
  /// the backend on connect so narration uses "ты прошёл/прошла" or a neutral phrasing.
  String get addressForm {
    if (!available) return '';
    final v = Supabase.instance.client.auth.currentUser?.userMetadata?['address_form'];
    return (v == 'masculine' || v == 'feminine') ? v as String : '';
  }

  /// The user's chosen avatar, stored in user_metadata as `avatar_url`. Either a real
  /// URL or a `data:image/...;base64,…` thumbnail (see [TravelerAvatar]). Null => the
  /// bundled default backpacker avatar is shown.
  String? get avatarUrl {
    if (!available) return null;
    final v = Supabase.instance.client.auth.currentUser?.userMetadata?['avatar_url'];
    return v is String && v.isNotEmpty ? v : null;
  }

  /// The user's friends, stored (for now) in user_metadata as `[{id, nick}]`. Empty
  /// when signed out or none set. A durable `friendships` table is the proper home
  /// later (see mobile/db/friendships.sql).
  List<({String id, String nick, int walks, bool paid})> get friends {
    if (!available) return const [];
    final raw = Supabase.instance.client.auth.currentUser?.userMetadata?['friends'];
    if (raw is List) {
      return [
        for (final f in raw)
          if (f is Map && f['id'] is String)
            (
              id: f['id'] as String,
              nick: (f['nick'] as String?)?.trim().isNotEmpty == true ? f['nick'] as String : '—',
              walks: (f['walks'] as num?)?.toInt() ?? 0,
              paid: f['paid'] == true,
            ),
      ];
    }
    return const [];
  }

  /// Save editable account data. Nick goes to the durable `users.display_name` (drives
  /// /me + the profile) and to user_metadata; birthday goes to user_metadata only.
  /// No-op when signed out. Returns nothing; UI reads back via [refreshEntitlement].
  Future<void> updateProfile(
      {String? nick, String? birthdayIso, String? avatarUrl, String? addressForm}) async {
    if (!available || !isSignedIn) return;
    final meta = <String, dynamic>{};
    if (nick != null) meta['display_name'] = nick;
    if (birthdayIso != null) meta['birthday'] = birthdayIso;
    if (avatarUrl != null) meta['avatar_url'] = avatarUrl;
    // "" is a valid value (neutral) — persist it so the choice is explicit.
    if (addressForm != null) meta['address_form'] = addressForm;
    if (meta.isNotEmpty) {
      await Supabase.instance.client.auth.updateUser(UserAttributes(data: meta));
    }
    // Mirror the nick into the durable users row so /me (and thus the profile) reflects
    // it. Owner-scoped update; best-effort.
    final uid = userId;
    if (nick != null && uid != null) {
      try {
        await Supabase.instance.client.from('users').update({'display_name': nick}).eq('id', uid);
      } catch (_) {/* best-effort */}
    }
    await refreshEntitlement();
  }

  /// Change the signed-in user's password. Throws on failure (weak/short/etc.).
  Future<void> changePassword(String newPassword) async {
    if (!available || !isSignedIn) return;
    await Supabase.instance.client.auth.updateUser(UserAttributes(password: newPassword));
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
