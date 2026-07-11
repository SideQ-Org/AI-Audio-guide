// REST client for walk history (backend /me, /walks). Bearer token comes from the
// Supabase session via AuthService. Throws [ApiException] on non-2xx so the UI can
// show a friendly error / retry.

import 'dart:convert';

import 'package:http/http.dart' as http;

import 'accounts_config.dart';
import 'auth_service.dart';
import 'community_models.dart';
import 'models.dart';

class ApiException implements Exception {
  final int statusCode;
  final String message;
  ApiException(this.statusCode, this.message);
  @override
  String toString() => 'ApiException($statusCode): $message';
}

class WalkApi {
  /// Bound every request so a hung/slow socket can't pile up (the history screen
  /// reloads on auth changes; without this a stalled /me would accumulate).
  static const _timeout = Duration(seconds: 12);

  static Map<String, String> _authHeaders() {
    final token = AuthService.instance.accessToken;
    if (token == null) throw ApiException(401, 'not signed in');
    return {'Authorization': 'Bearer $token'};
  }

  static Uri _u(String path) => Uri.parse('${AccountsConfig.apiBase}$path');

  /// The signed-in user's profile + entitlements (tier, quota, saved-walk counts).
  static Future<UserProfile> getMe() async {
    final r = await http.get(_u('/me'), headers: _authHeaders()).timeout(_timeout);
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
    return UserProfile.fromJson(
      jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>,
    );
  }

  /// Send a Google Play purchase token to the backend for receipt verification; on
  /// success the backend flips the account to paid and returns the fresh profile.
  static Future<UserProfile> verifyGooglePurchase(
    String purchaseToken,
    String productId,
  ) async {
    final r = await http
        .post(
          _u('/billing/google/verify'),
          headers: {..._authHeaders(), 'Content-Type': 'application/json'},
          body: jsonEncode({'purchase_token': purchaseToken, 'product_id': productId}),
        )
        .timeout(_timeout);
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
    return UserProfile.fromJson(
      jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>,
    );
  }

  static Future<List<WalkSummary>> listWalks({int limit = 30}) async {
    final r = await http.get(
      _u('/walks?limit=$limit'),
      headers: _authHeaders(),
    ).timeout(_timeout);
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
    final j = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    return ((j['walks'] as List?) ?? [])
        .map((e) => WalkSummary.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static Future<WalkDetail> getWalk(String id) async {
    final r = await http.get(_u('/walks/$id'), headers: _authHeaders()).timeout(_timeout);
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
    return WalkDetail.fromJson(
      jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>,
    );
  }

  static Future<void> deleteWalk(String id) async {
    final r = await http.delete(_u('/walks/$id'), headers: _authHeaders()).timeout(_timeout);
    if (r.statusCode != 204) throw ApiException(r.statusCode, r.body);
  }

  /// Delete the account's data (profile + all walks). Right to be forgotten.
  static Future<void> deleteAccount() async {
    final r = await http.delete(_u('/me'), headers: _authHeaders()).timeout(_timeout);
    if (r.statusCode != 204) throw ApiException(r.statusCode, r.body);
  }
}

/// REST client for the Community layer (backend /community/*, design/COMMUNITY.md).
/// Shares [WalkApi]'s auth-header + URL helpers (same library/file).
class CommunityApi {
  static const _timeout = Duration(seconds: 12);

  static Map<String, dynamic> _json(http.Response r, {int ok = 200}) {
    if (r.statusCode != ok) throw ApiException(r.statusCode, r.body);
    return jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
  }

  static Future<http.Response> _get(String path) =>
      http.get(WalkApi._u(path), headers: WalkApi._authHeaders()).timeout(_timeout);

  static Future<http.Response> _post(String path, [Map<String, dynamic>? body]) =>
      http.post(
        WalkApi._u(path),
        headers: {...WalkApi._authHeaders(), 'Content-Type': 'application/json'},
        body: body == null ? null : jsonEncode(body),
      ).timeout(_timeout);

  // -- profile / search --
  static Future<CommunityUser> me() async => CommunityUser.fromJson(_json(await _get('/community/me')));

  static Future<CommunityUser> setProfile({String? handle, String? avatarUrl, String? displayName}) async {
    final r = await _post('/community/profile', {
      if (handle != null) 'handle': handle,
      if (avatarUrl != null) 'avatar_url': avatarUrl,
      if (displayName != null) 'display_name': displayName,
    });
    return CommunityUser.fromJson(_json(r));
  }

  static Future<List<CommunityUser>> search(String q) async {
    final j = _json(await _get('/community/search?q=${Uri.encodeQueryComponent(q)}'));
    return ((j['friends'] as List?) ?? []).map((e) => CommunityUser.fromJson(e as Map<String, dynamic>)).toList();
  }

  // -- friends --
  static Future<List<CommunityUser>> friends() async {
    final j = _json(await _get('/community/friends'));
    return ((j['friends'] as List?) ?? []).map((e) => CommunityUser.fromJson(e as Map<String, dynamic>)).toList();
  }

  static Future<FriendRequests> requests() async =>
      FriendRequests.fromJson(_json(await _get('/community/friends/requests')));

  /// Send a request by @handle. Returns 'pending' | 'accepted' | 'self' | 'exists'.
  static Future<String> requestByHandle(String handle) async {
    final j = _json(await _post('/community/friends/request', {'handle': handle}));
    return j['status'] as String? ?? 'pending';
  }

  static Future<void> accept(String userId) async {
    final r = await _post('/community/friends/$userId/accept');
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
  }

  static Future<void> decline(String userId) async {
    final r = await _post('/community/friends/$userId/decline');
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
  }

  static Future<void> unfriend(String userId) async {
    final r = await http
        .delete(WalkApi._u('/community/friends/$userId'), headers: WalkApi._authHeaders())
        .timeout(_timeout);
    if (r.statusCode != 204) throw ApiException(r.statusCode, r.body);
  }

  // -- feed / friends' walks --
  static Future<List<FeedItem>> feed({int limit = 30}) async {
    final j = _json(await _get('/community/feed?limit=$limit'));
    return ((j['items'] as List?) ?? []).map((e) => FeedItem.fromJson(e as Map<String, dynamic>)).toList();
  }

  static Future<List<FriendWalk>> friendsWalks({int limit = 12}) async {
    final j = _json(await _get('/community/friends/walks?limit=$limit'));
    return ((j['walks'] as List?) ?? []).map((e) => FriendWalk.fromJson(e as Map<String, dynamic>)).toList();
  }

  // -- my routes --
  static Future<List<FriendWalk>> myWalks({int limit = 12}) async {
    final j = _json(await _get('/community/my/walks?limit=$limit'));
    return ((j['walks'] as List?) ?? []).map((e) => FriendWalk.fromJson(e as Map<String, dynamic>)).toList();
  }

  // -- group streaks --
  static Future<List<GroupStreak>> groupStreaks() async {
    final j = _json(await _get('/community/streaks'));
    return ((j['streaks'] as List?) ?? []).map((e) => GroupStreak.fromJson(e as Map<String, dynamic>)).toList();
  }

  static Future<GroupStreak> createGroupStreak(List<String> handles, {String? title}) async {
    final r = await _post('/community/streaks', {'handles': handles, if (title != null) 'title': title});
    return GroupStreak.fromJson(_json(r));
  }

  static Future<void> leaveGroupStreak(String id) async {
    final r = await _post('/community/streaks/$id/leave');
    if (r.statusCode != 200) throw ApiException(r.statusCode, r.body);
  }

  // -- challenges --
  static Future<List<Challenge>> challenges() async {
    final j = _json(await _get('/community/challenges'));
    return ((j['challenges'] as List?) ?? []).map((e) => Challenge.fromJson(e as Map<String, dynamic>)).toList();
  }

  static Future<Challenge> createChallenge({
    required String title,
    String metric = 'distance',
    int goal = 10000,
    String scope = 'friends',
    int days = 7,
  }) async {
    final r = await _post('/community/challenges',
        {'title': title, 'metric': metric, 'goal': goal, 'scope': scope, 'days': days});
    return Challenge.fromJson(_json(r));
  }

  static Future<bool> joinChallenge(String id) async {
    final j = _json(await _post('/community/challenges/$id/join'));
    return j['joined'] == true;
  }

  static Future<ChallengeDetail> challengeDetail(String id) async =>
      ChallengeDetail.fromJson(_json(await _get('/community/challenges/$id')));
}
