import 'package:ai_audio_guide/ui/achievements.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('achievementsFor', () {
    test('empty guest unlocks nothing', () {
      final list = achievementsFor(const ProfileStats());
      expect(unlockedCount(list), 0);
    });

    test('signed-in with no walks unlocks only Welcome', () {
      final list = achievementsFor(const ProfileStats(signedIn: true));
      expect(unlockedCount(list), 1);
      final welcome = list.firstWhere((a) => a.def.id == 'welcome');
      expect(welcome.unlocked, isTrue);
    });

    test('the seeded Kolbasenko profile unlocks the expected 15', () {
      const s = ProfileStats(
        walks: 16, cities: 3, districts: 16, distanceM: 43600, objects: 195,
        languages: 3, streakDays: 7, hasEarlyWalk: true, hasNightWalk: true,
        isPaid: true, signedIn: true,
      );
      final list = achievementsFor(s);
      final unlocked = {for (final a in list) if (a.unlocked) a.def.id};
      // Count milestones up to 15 (not 30/100), 2+ cities (not 5), 10+ districts,
      // 5k + marathon distance (not 100k), 50 objects (not 250), 3/7-day streaks,
      // polyglot, early/night, premium, welcome.
      expect(unlocked, containsAll(<String>{
        'welcome', 'first_walk', 'walks_5', 'walks_15',
        'cities_2', 'districts_10', 'dist_5k', 'dist_marathon',
        'obj_50', 'streak_3', 'streak_7', 'polyglot', 'early_bird', 'night_owl', 'premium',
      }));
      expect(unlocked, isNot(contains('walks_30')));
      expect(unlocked, isNot(contains('cities_5')));
      expect(unlocked, isNot(contains('dist_100k')));
      expect(unlocked, isNot(contains('obj_250')));
      expect(unlockedCount(list), 15);
    });

    test('unlocked achievements sort before locked ones', () {
      final list = achievementsFor(const ProfileStats(walks: 1, signedIn: true));
      final firstLocked = list.indexWhere((a) => !a.unlocked);
      final lastUnlocked = list.lastIndexWhere((a) => a.unlocked);
      expect(lastUnlocked, lessThan(firstLocked));
    });

    test('progress label reports distance in km', () {
      final list = achievementsFor(const ProfileStats(distanceM: 2500));
      final d5 = list.firstWhere((a) => a.def.id == 'dist_5k');
      expect(d5.unlocked, isFalse);
      expect(d5.progressLabel, '2.5 / 5 км');
    });
  });
}
