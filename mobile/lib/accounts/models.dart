// Dart models for the walk-history REST payloads (backend app/services/accounts/api.py).

/// The signed-in user's profile + entitlements (backend GET /me). Mirrors MeOut.
/// `null` limits mean "unlimited" (paid tier). Drives the upgrade prompts + ad/limit
/// gating on the client; the backend enforces the same rules authoritatively.
class UserProfile {
  final String id;
  final String? email;
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

  /// Downsampled GPS route as [[lat, lon], ...]. A point walked while the tour was
  /// PAUSED carries a trailing 1.0 ([lat, lon, 1.0]) so the detail map can style that
  /// stretch; unpaused points stay 2-element. Empty for walks recorded before the route
  /// feature (the detail screen then falls back to place markers only).
  final List<List<double>> path;

  WalkDetail({
    required super.id,
    required super.startedAt,
    required super.language,
    required super.objectCount,
    required this.events,
    this.path = const [],
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
        path: ((j['path'] as List?) ?? [])
            .whereType<List>()
            .where((p) => p.length >= 2)
            .map((p) => [
                  (p[0] as num).toDouble(),
                  (p[1] as num).toDouble(),
                  // paused flag (3rd element); absent on unpaused/legacy points -> 0.0
                  if (p.length > 2) (p[2] as num).toDouble() else 0.0,
                ])
            .toList(),
      );
}
