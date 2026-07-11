// Typed models for the Community API (backend /community/*, see design/COMMUNITY.md).

class CommunityUser {
  final String id;
  final String? handle;
  final String? displayName;
  final String? avatarUrl;
  final int level;
  final int streak;
  final bool walkingNow;
  final int walkCount;

  const CommunityUser({
    required this.id,
    this.handle,
    this.displayName,
    this.avatarUrl,
    this.level = 0,
    this.streak = 0,
    this.walkingNow = false,
    this.walkCount = 0,
  });

  /// Best display label: nickname, else @handle, else a short id.
  String get name =>
      (displayName?.trim().isNotEmpty ?? false)
          ? displayName!.trim()
          : (handle != null ? '@$handle' : id.substring(0, 6));

  factory CommunityUser.fromJson(Map<String, dynamic> j) => CommunityUser(
        id: j['id'] as String,
        handle: j['handle'] as String?,
        displayName: j['display_name'] as String?,
        avatarUrl: j['avatar_url'] as String?,
        level: (j['level'] as num?)?.toInt() ?? 0,
        streak: (j['streak'] as num?)?.toInt() ?? 0,
        walkingNow: j['walking_now'] == true,
        walkCount: (j['walk_count'] as num?)?.toInt() ?? 0,
      );
}

class FeedItem {
  final String id;
  final String kind; // walk | badge | streak | challenge_join | challenge_win
  final Map<String, dynamic> payload;
  final DateTime createdAt;
  final CommunityUser user;

  const FeedItem({
    required this.id,
    required this.kind,
    required this.payload,
    required this.createdAt,
    required this.user,
  });

  factory FeedItem.fromJson(Map<String, dynamic> j) => FeedItem(
        id: j['id'] as String,
        kind: j['kind'] as String,
        payload: (j['payload'] as Map?)?.cast<String, dynamic>() ?? const {},
        createdAt: DateTime.parse(j['created_at'] as String),
        user: CommunityUser.fromJson(j['user'] as Map<String, dynamic>),
      );
}

class FriendWalk {
  final String id;
  final DateTime startedAt;
  final String? city;
  final String? district;
  final int? distanceM;
  final int objectCount;
  final String? title;
  final List<List<double>> path;
  final CommunityUser user;

  const FriendWalk({
    required this.id,
    required this.startedAt,
    this.city,
    this.district,
    this.distanceM,
    this.objectCount = 0,
    this.title,
    this.path = const [],
    required this.user,
  });

  factory FriendWalk.fromJson(Map<String, dynamic> j) => FriendWalk(
        id: j['id'] as String,
        startedAt: DateTime.parse(j['started_at'] as String),
        city: j['city'] as String?,
        district: j['district'] as String?,
        distanceM: (j['distance_m'] as num?)?.toInt(),
        objectCount: (j['object_count'] as num?)?.toInt() ?? 0,
        title: j['title'] as String?,
        path: _parsePath(j['path']),
        user: CommunityUser.fromJson(j['user'] as Map<String, dynamic>),
      );

  static List<List<double>> _parsePath(dynamic raw) {
    if (raw is! List) return const [];
    final out = <List<double>>[];
    for (final p in raw) {
      if (p is List && p.length >= 2) {
        out.add([(p[0] as num).toDouble(), (p[1] as num).toDouble()]);
      }
    }
    return out;
  }
}

class Challenge {
  final String id;
  final String title;
  final String metric; // distance | places | districts
  final int goal;
  final String scope; // friends | global
  final DateTime startsAt;
  final DateTime endsAt;
  final String? creatorId;
  final bool joined;
  final int participants;
  final int myProgress;
  final int? myRank;

  const Challenge({
    required this.id,
    required this.title,
    required this.metric,
    required this.goal,
    required this.scope,
    required this.startsAt,
    required this.endsAt,
    this.creatorId,
    this.joined = false,
    this.participants = 0,
    this.myProgress = 0,
    this.myRank,
  });

  bool get isSystem => creatorId == null;

  /// 0..1 completion of the user's own progress toward the goal.
  double get progressFraction => goal <= 0 ? 0 : (myProgress / goal).clamp(0, 1).toDouble();

  factory Challenge.fromJson(Map<String, dynamic> j) => Challenge(
        id: j['id'] as String,
        title: j['title'] as String,
        metric: j['metric'] as String,
        goal: (j['goal'] as num).toInt(),
        scope: j['scope'] as String,
        startsAt: DateTime.parse(j['starts_at'] as String),
        endsAt: DateTime.parse(j['ends_at'] as String),
        creatorId: j['creator_id'] as String?,
        joined: j['joined'] == true,
        participants: (j['participants'] as num?)?.toInt() ?? 0,
        myProgress: (j['my_progress'] as num?)?.toInt() ?? 0,
        myRank: (j['my_rank'] as num?)?.toInt(),
      );
}

class LeaderboardEntry {
  final int rank;
  final int progress;
  final CommunityUser user;
  const LeaderboardEntry({required this.rank, required this.progress, required this.user});

  factory LeaderboardEntry.fromJson(Map<String, dynamic> j) => LeaderboardEntry(
        rank: (j['rank'] as num).toInt(),
        progress: (j['progress'] as num).toInt(),
        user: CommunityUser.fromJson(j['user'] as Map<String, dynamic>),
      );
}

class ChallengeDetail {
  final Challenge challenge;
  final List<LeaderboardEntry> leaderboard;
  const ChallengeDetail({required this.challenge, required this.leaderboard});

  factory ChallengeDetail.fromJson(Map<String, dynamic> j) => ChallengeDetail(
        challenge: Challenge.fromJson(j),
        leaderboard: ((j['leaderboard'] as List?) ?? [])
            .map((e) => LeaderboardEntry.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class GroupStreak {
  final String id;
  final String? title;
  final int days; // consecutive days all members walked
  final List<CommunityUser> members;
  const GroupStreak({required this.id, this.title, this.days = 0, this.members = const []});

  factory GroupStreak.fromJson(Map<String, dynamic> j) => GroupStreak(
        id: j['id'] as String,
        title: j['title'] as String?,
        days: (j['days'] as num?)?.toInt() ?? 0,
        members: ((j['members'] as List?) ?? [])
            .map((e) => CommunityUser.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class FriendRequests {
  final List<CommunityUser> incoming;
  final List<CommunityUser> outgoing;
  const FriendRequests({this.incoming = const [], this.outgoing = const []});

  factory FriendRequests.fromJson(Map<String, dynamic> j) => FriendRequests(
        incoming: ((j['incoming'] as List?) ?? [])
            .map((e) => CommunityUser.fromJson(e as Map<String, dynamic>))
            .toList(),
        outgoing: ((j['outgoing'] as List?) ?? [])
            .map((e) => CommunityUser.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}
