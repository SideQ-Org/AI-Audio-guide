// Realtime layer (design/COMMUNITY.md §6 "v2") over Supabase Realtime presence channels.
//
//  • Global presence — `presence:community`: every signed-in user tracks {uid, name,
//    walking, coarse lat/lon}. Friends see each other's live "на прогулке" status without
//    polling. Positions are rounded to ~110 m for privacy.
//  • Co-walk — `presence:cowalk:<CODE>`: a shared room two friends join to walk together;
//    each tracks their live position so the other sees a dot on the map.
//
// Entirely optional and self-contained: no-op when accounts are off / signed out, and any
// Realtime error is swallowed so it can never break the tour.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'accounts_config.dart';
import 'auth_service.dart';

@immutable
class PeerState {
  final String uid;
  final String? name;
  final bool walking;
  final double? lat;
  final double? lon;
  const PeerState(this.uid, this.name, this.walking, this.lat, this.lon);

  bool get hasPosition => lat != null && lon != null;
}

class RealtimeService extends ChangeNotifier {
  RealtimeService._();
  static final RealtimeService instance = RealtimeService._();

  RealtimeChannel? _presence;
  RealtimeChannel? _room;
  String? _roomCode;

  bool _selfWalking = false;
  double? _selfLat;
  double? _selfLon;

  final Map<String, PeerState> _peers = {}; // global presence, keyed by uid
  final Map<String, PeerState> _roomPeers = {}; // co-walk room, keyed by uid

  bool get _enabled => AccountsConfig.enabled && AuthService.instance.isSignedIn;
  String? get _uid => AuthService.instance.userId;

  /// uids currently online / walking (from global presence).
  Set<String> get onlineIds => _peers.keys.toSet();
  Set<String> get walkingIds =>
      {for (final p in _peers.values) if (p.walking) p.uid};
  PeerState? peer(String uid) => _peers[uid];

  /// Co-walk state.
  String? get coWalkCode => _roomCode;
  bool get inCoWalk => _roomCode != null;
  List<PeerState> get coWalkPeers =>
      _roomPeers.values.where((p) => p.uid != _uid).toList();

  // Round to ~110 m so we never share a precise location.
  double _coarse(double v) => (v * 1000).roundToDouble() / 1000;

  Map<String, dynamic> _payload() => {
        'uid': _uid,
        'name': AuthService.instance.displayName,
        'walking': _selfWalking,
        if (_selfLat != null) 'lat': _selfLat,
        if (_selfLon != null) 'lon': _selfLon,
      };

  // ── global presence ────────────────────────────────────────────────────────

  Future<void> startPresence() async {
    if (!_enabled || _presence != null) return;
    try {
      final ch = Supabase.instance.client.channel(
        'presence:community',
        opts: const RealtimeChannelConfig(self: true),
      );
      ch.onPresenceSync((_) => _rebuild(ch, _peers));
      ch.subscribe((status, _) async {
        if (status == RealtimeSubscribeStatus.subscribed) {
          try {
            await ch.track(_payload());
          } catch (_) {/* best-effort */}
        }
      });
      _presence = ch;
    } catch (_) {/* realtime is optional */}
  }

  Future<void> stopPresence() async {
    final ch = _presence;
    _presence = null;
    _peers.clear();
    try {
      await ch?.unsubscribe();
    } catch (_) {}
    await leaveCoWalk();
    notifyListeners();
  }

  /// Update our own live state; re-tracks on both the global channel and the co-walk
  /// room (if any). Call on each position update and when the tour starts/stops.
  Future<void> updateSelf({bool? walking, double? lat, double? lon}) async {
    if (walking != null) _selfWalking = walking;
    if (lat != null) _selfLat = _coarse(lat);
    if (lon != null) _selfLon = _coarse(lon);
    final payload = _payload();
    try {
      await _presence?.track(payload);
    } catch (_) {}
    try {
      await _room?.track(payload);
    } catch (_) {}
  }

  // ── co-walk room ───────────────────────────────────────────────────────────

  Future<void> startCoWalk(String code) async {
    if (!_enabled) return;
    await leaveCoWalk();
    final room = code.trim().toUpperCase();
    if (room.isEmpty) return;
    _roomCode = room;
    try {
      final ch = Supabase.instance.client.channel(
        'presence:cowalk:$room',
        opts: const RealtimeChannelConfig(self: false),
      );
      ch.onPresenceSync((_) => _rebuild(ch, _roomPeers));
      ch.subscribe((status, _) async {
        if (status == RealtimeSubscribeStatus.subscribed) {
          try {
            await ch.track(_payload());
          } catch (_) {}
        }
      });
      _room = ch;
    } catch (_) {}
    notifyListeners();
  }

  Future<void> leaveCoWalk() async {
    final ch = _room;
    _room = null;
    _roomCode = null;
    _roomPeers.clear();
    try {
      await ch?.unsubscribe();
    } catch (_) {}
    notifyListeners();
  }

  // ── presence parsing ───────────────────────────────────────────────────────

  void _rebuild(RealtimeChannel ch, Map<String, PeerState> into) {
    into.clear();
    try {
      for (final state in ch.presenceState()) {
        for (final pres in state.presences) {
          final p = pres.payload;
          final uid = p['uid'] as String?;
          if (uid == null) continue;
          into[uid] = PeerState(
            uid,
            p['name'] as String?,
            p['walking'] == true,
            (p['lat'] as num?)?.toDouble(),
            (p['lon'] as num?)?.toDouble(),
          );
        }
      }
    } catch (_) {/* payload shape guard */}
    notifyListeners();
  }
}
