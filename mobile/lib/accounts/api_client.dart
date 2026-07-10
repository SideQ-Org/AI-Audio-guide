// REST client for walk history (backend /me, /walks). Bearer token comes from the
// Supabase session via AuthService. Throws [ApiException] on non-2xx so the UI can
// show a friendly error / retry.

import 'dart:convert';

import 'package:http/http.dart' as http;

import 'accounts_config.dart';
import 'auth_service.dart';
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
