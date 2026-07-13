import 'package:ai_audio_guide/ui/level.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('levelThreshold T(L) = 25·L·(L+1)', () {
    test('matches the spec §6 checkpoints', () {
      expect(levelThreshold(0), 0);
      expect(levelThreshold(1), 50); // 1 walk
      expect(levelThreshold(2), 150); // 3 walks
      expect(levelThreshold(3), 300); // 6 walks
    });
  });

  group('LevelInfo.fromWalks', () {
    test('0 walks -> level 0, empty progress', () {
      final li = LevelInfo.fromWalks(0);
      expect(li.level, 0);
      expect(li.points, 0);
      expect(li.progress, 0.0);
      expect(li.xpToNext, 50); // needs T(1)=50 to reach level 1
    });

    test('1 walk (50 pts) -> exactly level 1', () {
      final li = LevelInfo.fromWalks(1);
      expect(li.level, 1);
      expect(li.points, 50);
      expect(li.progress, 0.0); // just crossed the threshold
    });

    test('3 walks (150 pts) -> level 2', () {
      expect(LevelInfo.fromWalks(3).level, 2);
    });

    test('6 walks (300 pts) -> level 3', () {
      expect(LevelInfo.fromWalks(6).level, 3);
    });

    test('mid-level progress is a fraction in (0,1)', () {
      // 2 walks = 100 pts: level 1 (T1=50), span to T2=150 is 100, into = 50 -> 0.5.
      final li = LevelInfo.fromWalks(2);
      expect(li.level, 1);
      expect(li.progress, closeTo(0.5, 1e-9));
      expect(li.xpIntoLevel, 50);
      expect(li.xpToNext, 50);
    });

    test('caps at level 50 and reports full progress', () {
      final li = LevelInfo.fromWalks(100000);
      expect(li.level, 50);
      expect(li.atMax, isTrue);
      expect(li.progress, 1.0);
      expect(li.xpToNext, 0);
    });

    test('negative walk counts are clamped to zero', () {
      expect(LevelInfo.fromWalks(-5).level, 0);
    });
  });
}
