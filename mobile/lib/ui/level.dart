// Client-side level / XP system (design/DESIGN_SPEC.md §6). No backend: everything is
// derived from a single number, walk_count (from GET /me). Kept pure + testable.
//
//   points   = walk_count * _pointsPerWalk
//   T(L)     = 25 * L * (L+1)                 (rising threshold for reaching level L)
//   level    = max L such that points >= T(L), capped at _maxLevel (0 walks -> level 0)
//   progress = (points - T(level)) / (T(level+1) - T(level))   in [0, 1]
//
// Sanity: T(1)=50 (1 walk -> level 1), T(2)=150 (3 walks), T(3)=300 (6 walks).

const int _pointsPerWalk = 50;
const int _maxLevel = 50;

/// Threshold points required to *reach* level [l]. T(0) = 0.
int levelThreshold(int l) => 25 * l * (l + 1);

/// A snapshot of the user's progression, computed from [walkCount].
class LevelInfo {
  final int walkCount;
  final int points;
  final int level;

  /// Points banked into the current level (points - T(level)).
  final int xpIntoLevel;

  /// Points spanning the current level (T(level+1) - T(level)); 0 at the cap.
  final int xpSpan;

  /// Fraction toward the next level, in [0, 1]. 1.0 when at the cap.
  final double progress;

  const LevelInfo._({
    required this.walkCount,
    required this.points,
    required this.level,
    required this.xpIntoLevel,
    required this.xpSpan,
    required this.progress,
  });

  /// XP still needed to reach the next level (0 at the cap).
  int get xpToNext => atMax ? 0 : xpSpan - xpIntoLevel;

  bool get atMax => level >= _maxLevel;

  factory LevelInfo.fromWalks(int walkCount) {
    final wc = walkCount < 0 ? 0 : walkCount;
    final points = wc * _pointsPerWalk;

    // Highest L (<= cap) whose threshold the points have cleared.
    var level = 0;
    while (level < _maxLevel && points >= levelThreshold(level + 1)) {
      level++;
    }

    if (level >= _maxLevel) {
      final base = levelThreshold(_maxLevel);
      return LevelInfo._(
        walkCount: wc,
        points: points,
        level: _maxLevel,
        xpIntoLevel: points - base,
        xpSpan: 0,
        progress: 1.0,
      );
    }

    final base = levelThreshold(level);
    final next = levelThreshold(level + 1);
    final span = next - base;
    final into = points - base;
    return LevelInfo._(
      walkCount: wc,
      points: points,
      level: level,
      xpIntoLevel: into,
      xpSpan: span,
      progress: span == 0 ? 0.0 : (into / span).clamp(0.0, 1.0),
    );
  }
}
