// Client-side achievement engine (design/PROFILE_ACHIEVEMENTS.md). Pure + testable:
// everything is derived from data the client already has (walk_count from /me; per-walk
// city/district/distance/objects/language/time from /walks). No backend tables.
//
// NOTE: titles/descriptions are RU for now (the app's primary language). Localising them
// to the l10n .arb files (EN + 6 langs) is a follow-up, consistent with the other new
// redesign strings.

import 'package:flutter/foundation.dart';

/// Aggregated signals used to evaluate achievements. Built from /me + /walks.
@immutable
class ProfileStats {
  final int walks;
  final int cities;
  final int districts;
  final int distanceM;
  final int objects;
  final int languages;
  final int streakDays; // longest run of consecutive calendar days with a walk
  final bool hasEarlyWalk; // a walk started before 07:00
  final bool hasNightWalk; // a walk started at/after 22:00
  final bool isPaid;
  final bool signedIn;

  /// Names of the distinct cities visited (for the "cities" stat detail). Optional —
  /// [cities] is the source of truth for the count/achievement.
  final List<String> cityNames;

  const ProfileStats({
    this.walks = 0,
    this.cities = 0,
    this.districts = 0,
    this.distanceM = 0,
    this.objects = 0,
    this.languages = 0,
    this.streakDays = 0,
    this.hasEarlyWalk = false,
    this.hasNightWalk = false,
    this.isPaid = false,
    this.signedIn = false,
    this.cityNames = const [],
  });
}

@immutable
class Achievement {
  final String id;
  final String emoji;
  final String title;
  final String description;
  const Achievement(this.id, this.emoji, this.title, this.description);
}

@immutable
class AchievementState {
  final Achievement def;
  final bool unlocked;
  final double progress; // 0..1
  final String progressLabel; // e.g. "12 / 15 прогулок"
  const AchievementState(this.def, this.unlocked, this.progress, this.progressLabel);
}

/// A single threshold-based achievement over one integer signal.
class _Rule {
  final Achievement def;
  final int Function(ProfileStats) value;
  final int target;
  final String unit; // for the progress label ("прогулок", "км", …)
  final double Function(int)? display; // transform value for the label (e.g. m→km)
  const _Rule(this.def, this.value, this.target, this.unit, {this.display});
}

// Boolean achievements (unlock is all-or-nothing).
Achievement _welcome = const Achievement('welcome', '👋', 'Добро пожаловать', 'Ты в клубе пешеходов! Выдаётся за создание аккаунта.');
Achievement _polyglot = const Achievement('polyglot', '🈶', 'Полиглот', 'Слушал гида на трёх разных языках — мир без границ.');
Achievement _early = const Achievement('early_bird', '🌅', 'Ранняя пташка', 'Вышел на прогулку до 7 утра, пока город ещё спит.');
Achievement _night = const Achievement('night_owl', '🌙', 'Полуночник', 'Гулял после 22:00 — ночной город раскрывается иначе.');
Achievement _premium = const Achievement('premium', '💎', 'Поддержал проект', 'Оформил премиум и поддержал развитие гида. Спасибо!');

final List<_Rule> _rules = [
  _Rule(const Achievement('first_walk', '🥾', 'Первые шаги', 'Завершил свою первую прогулку с гидом. Начало положено!'), (s) => s.walks, 1, 'прогулок'),
  _Rule(const Achievement('walks_5', '🚶', 'Постоянный ходок', 'Пять прогулок за плечами — гулять входит в привычку.'), (s) => s.walks, 5, 'прогулок'),
  _Rule(const Achievement('walks_15', '🏙️', 'Знаток района', 'Пятнадцать прогулок — ты уже знаешь город лучше многих.'), (s) => s.walks, 15, 'прогулок'),
  _Rule(const Achievement('walks_30', '🧭', 'Городской исследователь', 'Тридцать прогулок. Тебя не остановить.'), (s) => s.walks, 30, 'прогулок'),
  _Rule(const Achievement('walks_100', '👑', 'Легенда прогулок', 'Сто прогулок! Это уже уровень легенды.'), (s) => s.walks, 100, 'прогулок'),
  _Rule(const Achievement('cities_2', '🗺️', 'Турист', 'Прогулялся с гидом в двух разных городах.'), (s) => s.cities, 2, 'городов'),
  _Rule(const Achievement('cities_5', '✈️', 'Путешественник', 'Пять городов на карте твоих прогулок.'), (s) => s.cities, 5, 'городов'),
  _Rule(const Achievement('districts_10', '🏘️', 'Открыватель', 'Исследовал десять разных районов.'), (s) => s.districts, 10, 'районов'),
  _Rule(const Achievement('dist_5k', '📏', 'Первые 5 км', 'Прошёл пять километров суммарно.'), (s) => s.distanceM, 5000, 'км', display: (m) => m / 1000),
  _Rule(const Achievement('dist_marathon', '🏃', 'Марафон', 'Прошёл 42 км суммарно — целый марафон пешком!'), (s) => s.distanceM, 42000, 'км', display: (m) => m / 1000),
  _Rule(const Achievement('dist_100k', '🌍', 'Сотня', 'Сто километров пешком. Внушает уважение.'), (s) => s.distanceM, 100000, 'км', display: (m) => m / 1000),
  _Rule(const Achievement('obj_50', '🔎', 'Любопытный', 'Гид рассказал тебе о пятидесяти местах.'), (s) => s.objects, 50, 'объектов'),
  _Rule(const Achievement('obj_250', '📚', 'Эрудит', 'Двести пятьдесят мест — ходячая энциклопедия города.'), (s) => s.objects, 250, 'объектов'),
  _Rule(const Achievement('streak_3', '🔥', 'На волне', 'Гулял три дня подряд — держишь ритм.'), (s) => s.streakDays, 3, 'дней'),
  _Rule(const Achievement('streak_7', '⚡', 'Неделя в движении', 'Серия из семи дней подряд. Огонь!'), (s) => s.streakDays, 7, 'дней'),
];

String _fmt(num v) => v == v.roundToDouble() ? v.round().toString() : v.toStringAsFixed(1);

/// Evaluate every achievement for [s], ordered unlocked-first then by definition order.
List<AchievementState> achievementsFor(ProfileStats s) {
  final out = <AchievementState>[];

  AchievementState boolAch(Achievement a, bool ok) => AchievementState(a, ok, ok ? 1 : 0, '');

  out.add(boolAch(_welcome, s.signedIn));
  for (final r in _rules) {
    final raw = r.value(s);
    final unlocked = raw >= r.target;
    final shown = r.display == null ? raw.toDouble() : r.display!(raw);
    final targetShown = r.display == null ? r.target.toDouble() : r.display!(r.target);
    out.add(AchievementState(
      r.def,
      unlocked,
      (raw / r.target).clamp(0.0, 1.0),
      '${_fmt(shown)} / ${_fmt(targetShown)} ${r.unit}',
    ));
  }
  out.add(boolAch(_polyglot, s.languages >= 3));
  out.add(boolAch(_early, s.hasEarlyWalk));
  out.add(boolAch(_night, s.hasNightWalk));
  out.add(boolAch(_premium, s.isPaid));

  out.sort((a, b) {
    if (a.unlocked != b.unlocked) return a.unlocked ? -1 : 1;
    return 0; // stable: keep definition order within each group
  });
  return out;
}

int unlockedCount(List<AchievementState> list) => list.where((a) => a.unlocked).length;
