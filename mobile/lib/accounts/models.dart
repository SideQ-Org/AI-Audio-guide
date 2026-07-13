// Dart models for the walk-history REST payloads (backend app/services/accounts/api.py).

/// The signed-in user's profile + entitlements (backend GET /me). Mirrors MeOut.
/// `null` limits mean "unlimited" (paid tier). Drives the upgrade prompts + ad/limit
/// gating on the client; the backend enforces the same rules authoritatively.
class UserProfile {
  final String id;
  final String? email;
  final String? displayName; // user-chosen nickname (backend `display_name`)
  final String tier; // "free" | "paid"
  final int toursToday;
  final int? dailyTourLimit; // null => unlimited
  final int walkCount;
  final int? walkLimit; // null => unlimited
  final DateTime? subscriptionExpiresAt;

  UserProfile({
    required this.id,
    required this.tier,
    required this.toursToday,
    required this.walkCount,
    this.email,
    this.displayName,
    this.dailyTourLimit,
    this.walkLimit,
    this.subscriptionExpiresAt,
  });

  bool get isPaid => tier == 'paid';

  /// True when a free user has used up today's tours (so the tour button should
  /// route to the upgrade prompt instead of starting).
  bool get dailyToursExhausted =>
      !isPaid && dailyTourLimit != null && toursToday >= dailyTourLimit!;

  /// True when a free user's saved-history is at the cap (show the upgrade banner).
  bool get walksAtLimit =>
      !isPaid && walkLimit != null && walkCount >= walkLimit!;

  factory UserProfile.fromJson(Map<String, dynamic> j) => UserProfile(
        id: j['id'] as String,
        email: j['email'] as String?,
        displayName: j['display_name'] as String?,
        tier: j['tier'] as String? ?? 'free',
        toursToday: (j['tours_today'] as num?)?.toInt() ?? 0,
        dailyTourLimit: (j['daily_tour_limit'] as num?)?.toInt(),
        walkCount: (j['walk_count'] as num?)?.toInt() ?? 0,
        walkLimit: (j['walk_limit'] as num?)?.toInt(),
        subscriptionExpiresAt: j['subscription_expires_at'] == null
            ? null
            : DateTime.parse(j['subscription_expires_at'] as String),
      );
}

/// Parse a GPS route JSON `[[lat, lon(, paused)], ...]` into `[[lat, lon, paused]]` (paused=0.0
/// on unpaused/legacy points). Shared by WalkSummary (downsampled preview) and WalkDetail (full).
List<List<double>> _parsePath(dynamic raw) => ((raw as List?) ?? [])
    .whereType<List>()
    .where((p) => p.length >= 2)
    .map((p) => [
          (p[0] as num).toDouble(),
          (p[1] as num).toDouble(),
          if (p.length > 2) (p[2] as num).toDouble() else 0.0,
        ])
    .toList();

class WalkSummary {
  final String id;
  final DateTime startedAt;
  final DateTime? endedAt;
  final String language;
  final String? city;
  final String? district;
  final int? distanceM;
  final int objectCount;
  final String? title;

  /// Downsampled GPS route ([[lat, lon, paused]]) for the history-list track preview. Empty for
  /// walks recorded before the route feature (the tile then falls back to a plain route icon).
  final List<List<double>> path;

  WalkSummary({
    required this.id,
    required this.startedAt,
    required this.language,
    required this.objectCount,
    this.endedAt,
    this.city,
    this.district,
    this.distanceM,
    this.title,
    this.path = const [],
  });

  factory WalkSummary.fromJson(Map<String, dynamic> j) => WalkSummary(
        id: j['id'] as String,
        startedAt: DateTime.parse(j['started_at'] as String),
        endedAt: j['ended_at'] == null
            ? null
            : DateTime.parse(j['ended_at'] as String),
        language: j['language'] as String? ?? 'en',
        city: j['city'] as String?,
        district: j['district'] as String?,
        distanceM: (j['distance_m'] as num?)?.toInt(),
        objectCount: (j['object_count'] as num?)?.toInt() ?? 0,
        title: j['title'] as String?,
        path: _parsePath(j['path']),
      );

  /// Best-effort duration between start and last activity.
  Duration? get duration => endedAt?.difference(startedAt);
}

class WalkEventItem {
  final int seq;
  final String placeId;
  final String name;
  final String category;
  final double lat;
  final double lon;
  final String significance;
  final String? narration;
  final DateTime saidAt;

  WalkEventItem({
    required this.seq,
    required this.placeId,
    required this.name,
    required this.category,
    required this.lat,
    required this.lon,
    required this.significance,
    required this.saidAt,
    this.narration,
  });

  factory WalkEventItem.fromJson(Map<String, dynamic> j) => WalkEventItem(
        seq: (j['seq'] as num).toInt(),
        placeId: j['place_id'] as String,
        name: j['name'] as String,
        category: j['category'] as String,
        lat: (j['lat'] as num).toDouble(),
        lon: (j['lon'] as num).toDouble(),
        significance: j['significance'] as String? ?? 'MEDIUM',
        narration: j['narration'] as String?,
        saidAt: DateTime.parse(j['said_at'] as String),
      );
}

class WalkDetail extends WalkSummary {
  final List<WalkEventItem> events;

  // `path` (the full route here, vs the list's downsampled preview) is inherited from WalkSummary.

  /// Structured end-of-walk recap (kept server-side; null for old walks / when generation failed).
  final String? summary;

  WalkDetail({
    required super.id,
    required super.startedAt,
    required super.language,
    required super.objectCount,
    required this.events,
    this.summary,
    super.path,
    super.endedAt,
    super.city,
    super.district,
    super.distanceM,
    super.title,
  });

  factory WalkDetail.fromJson(Map<String, dynamic> j) => WalkDetail(
        id: j['id'] as String,
        startedAt: DateTime.parse(j['started_at'] as String),
        endedAt: j['ended_at'] == null
            ? null
            : DateTime.parse(j['ended_at'] as String),
        language: j['language'] as String? ?? 'en',
        city: j['city'] as String?,
        district: j['district'] as String?,
        distanceM: (j['distance_m'] as num?)?.toInt(),
        objectCount: (j['object_count'] as num?)?.toInt() ?? 0,
        title: j['title'] as String?,
        events: ((j['events'] as List?) ?? [])
            .map((e) => WalkEventItem.fromJson(e as Map<String, dynamic>))
            .toList(),
        path: _parsePath(j['path']),
        summary: (j['summary'] as String?)?.trim(),
      );
}
