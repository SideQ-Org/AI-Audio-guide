// Tab screens + Home overlay for the premium redesign. Presentational: they take
// plain data + callbacks from the HomePage state (which owns all mechanics).
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:share_plus/share_plus.dart';

import '../l10n/app_localizations.dart';
import 'achievements.dart';
import 'components.dart';
import 'design.dart';
import 'level.dart';

// ── Home overlay (over the live map) ─────────────────────────────────────────
/// The modules layered over the map on the Home tab. Inactive: header + go-premium
/// + focus picker + swipe-to-start. Active: status chip (top) + player (bottom).
/// Blur of the underlying map is handled by the caller.
class HomeModules extends StatefulWidget {
  final bool active;
  // header
  final String greeting, nick, prompt;
  final bool isDark;
  final VoidCallback onToggleTheme, onSystemTheme;
  // go premium
  final bool showPremium;
  final String premiumTitle, premiumSubtitle;
  final VoidCallback onUpgrade;
  // focus picker
  final String focusTitle;
  final List<({String code, IconData icon})> focusItems;
  final String focusSelected;
  final ValueChanged<String> onFocus;
  // swipe
  final String swipeLabel;
  final VoidCallback onStart;
  // status
  final String statusLabel;
  final Color statusColor;
  final bool statusActive;
  // player
  final String? title, text;
  final bool paused, recording, voice;
  final VoidCallback? onStop, onPause, onAsk, onMic, onToggleVoice, onHistory;

  const HomeModules({
    super.key,
    required this.active,
    required this.greeting,
    required this.nick,
    required this.prompt,
    required this.isDark,
    required this.onToggleTheme,
    required this.onSystemTheme,
    required this.showPremium,
    required this.premiumTitle,
    required this.premiumSubtitle,
    required this.onUpgrade,
    required this.focusTitle,
    required this.focusItems,
    required this.focusSelected,
    required this.onFocus,
    required this.swipeLabel,
    required this.onStart,
    required this.statusLabel,
    required this.statusColor,
    required this.statusActive,
    this.title,
    this.text,
    required this.paused,
    required this.recording,
    required this.voice,
    this.onStop,
    this.onPause,
    this.onAsk,
    this.onMic,
    this.onToggleVoice,
    this.onHistory,
  });

  @override
  State<HomeModules> createState() => _HomeModulesState();
}

class _HomeModulesState extends State<HomeModules> {
  // Staggered choreography: on activation the inactive blocks leave first (driven by
  // `active`), then ~180 ms later the tour UI (island + control panel) slides in
  // (driven by `_tourUi`). On stop the tour UI leaves immediately, blocks return.
  bool _tourUi = false;

  @override
  void initState() {
    super.initState();
    _tourUi = widget.active;
  }

  @override
  void didUpdateWidget(covariant HomeModules old) {
    super.didUpdateWidget(old);
    if (widget.active == old.active) return;
    if (widget.active) {
      Future.delayed(const Duration(milliseconds: 520), () {
        if (mounted && widget.active) setState(() => _tourUi = true);
      });
    } else {
      setState(() => _tourUi = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.active;
    final tourUi = _tourUi;
    final topPad = MediaQuery.of(context).padding.top;
    final bottomPad = MediaQuery.of(context).padding.bottom;
    // Blocks travel off-screen while staying visible (fade == slide), so the "разъезд"
    // reads. Controls then arrive after a clear pause. Long, gentle easing = buttery.
    const leaveDur = Duration(milliseconds: 1100);
    const arriveDur = Duration(milliseconds: 1150);
    const leaveCurve = Curves.easeInOutCubic;
    const arriveCurve = Curves.easeInOutCubic;
    return Stack(children: [
      // inactive content
      IgnorePointer(
        ignoring: active,
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnimatedSlide(
            duration: leaveDur, curve: leaveCurve,
            offset: Offset(0, active ? -2.4 : 0),
            child: AnimatedOpacity(
              duration: leaveDur, curve: leaveCurve, opacity: active ? 0 : 1,
              child: _Header(
                topPad: topPad, greeting: widget.greeting, nick: widget.nick, prompt: widget.prompt,
                isDark: widget.isDark, onToggleTheme: widget.onToggleTheme, onSystemTheme: widget.onSystemTheme,
              ),
            ),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
              child: Column(children: [
                if (widget.showPremium)
                  AnimatedSlide(
                    duration: leaveDur, curve: leaveCurve,
                    offset: Offset(0, active ? -2.8 : 0),
                    child: AnimatedOpacity(
                      duration: leaveDur, curve: leaveCurve, opacity: active ? 0 : 1,
                      child: GoPremiumCard(title: widget.premiumTitle, subtitle: widget.premiumSubtitle, onTap: widget.onUpgrade),
                    ),
                  ),
                const Spacer(),
                AnimatedSlide(
                  duration: leaveDur, curve: leaveCurve,
                  offset: Offset(0, active ? 3.0 : 0),
                  child: AnimatedOpacity(
                    duration: leaveDur, curve: leaveCurve, opacity: active ? 0 : 1,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        FocusPicker(title: widget.focusTitle, items: widget.focusItems, selected: widget.focusSelected, onSelect: widget.onFocus),
                        const SizedBox(height: Gap.lg),
                        SwipeToStart(label: widget.swipeLabel, onComplete: widget.onStart),
                      ],
                    ),
                  ),
                ),
                SizedBox(height: bottomPad + 96),
              ]),
            ),
          ),
        ]),
      ),
      // active: Dynamic-Island status (slides down after the blocks have left)
      Positioned(
        top: topPad + 8, left: 0, right: 0,
        child: IgnorePointer(
          ignoring: !tourUi,
          child: AnimatedSlide(
            duration: arriveDur, curve: arriveCurve,
            offset: Offset(0, tourUi ? 0 : -2.2),
            child: AnimatedOpacity(
              duration: arriveDur, curve: arriveCurve, opacity: tourUi ? 1 : 0,
              child: Center(child: StatusIsland(label: widget.statusLabel, color: widget.statusColor, active: widget.statusActive)),
            ),
          ),
        ),
      ),
      // active: control panel (slides up after the blocks have left)
      Positioned(
        left: 0, right: 0, bottom: 0,
        child: IgnorePointer(
          ignoring: !tourUi,
          child: AnimatedSlide(
            duration: arriveDur, curve: arriveCurve,
            offset: Offset(0, tourUi ? 0 : 1.5),
            child: AnimatedOpacity(
              duration: arriveDur, curve: arriveCurve, opacity: tourUi ? 1 : 0,
              child: TourControls(
                title: widget.title, text: widget.text,
                paused: widget.paused, recording: widget.recording, voice: widget.voice,
                onPause: widget.onPause, onStop: widget.onStop, onAsk: widget.onAsk,
                onMic: widget.onMic, onToggleVoice: widget.onToggleVoice, onHistory: widget.onHistory,
              ),
            ),
          ),
        ),
      ),
    ]);
  }
}

class _Header extends StatelessWidget {
  final double topPad;
  final String greeting, nick, prompt;
  final bool isDark;
  final VoidCallback onToggleTheme, onSystemTheme;
  const _Header({
    required this.topPad, required this.greeting, required this.nick, required this.prompt,
    required this.isDark, required this.onToggleTheme, required this.onSystemTheme,
  });
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget corner(IconData icon, {VoidCallback? onTap, VoidCallback? onLong}) => GestureDetector(
          onTap: onTap, onLongPress: onLong,
          child: Container(
            width: 40, height: 40, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.07), border: Border.all(color: c.glassBorder)),
            child: Icon(icon, size: 19, color: c.textPrimary),
          ),
        );
    return AnimatedContainer(
      duration: Motion.med,
      padding: EdgeInsets.fromLTRB(20, topPad + 14, 16, 22),
      decoration: BoxDecoration(
        color: c.header,
        borderRadius: const BorderRadius.vertical(bottom: Radius.circular(Radii.xl)),
        boxShadow: [BoxShadow(color: c.shadow, blurRadius: 30, spreadRadius: -20, offset: const Offset(0, 12))],
      ),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(greeting, style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w600, color: c.textSecondary)),
            const SizedBox(height: 5),
            Text('$nick,\n$prompt', style: display(context)),
          ]),
        ),
        const SizedBox(width: 8),
        corner(isDark ? AppIcons.sun : AppIcons.moon, onTap: onToggleTheme, onLong: onSystemTheme),
      ]),
    );
  }
}

// ── shared page scaffold for tab screens ─────────────────────────────────────
class TabPage extends StatelessWidget {
  final String title;
  final List<Widget> children;
  const TabPage({super.key, required this.title, required this.children});
  @override
  Widget build(BuildContext context) {
    final topPad = MediaQuery.of(context).padding.top;
    return GradientBackground(
      child: ListView(
        padding: EdgeInsets.fromLTRB(16, topPad + 16, 16, MediaQuery.of(context).padding.bottom + 110),
        children: [
          Text(title, style: h1(context)),
          const SizedBox(height: Gap.lg),
          ...children,
        ],
      ),
    );
  }
}

// ── Community (coming soon) ───────────────────────────────────────────────────
class CommunityTab extends StatelessWidget {
  const CommunityTab({super.key});
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return TabPage(title: l.tabCommunity, children: [
      const SizedBox(height: 40),
      GlassModule(
        padding: const EdgeInsets.all(24),
        child: Column(children: [
          Container(
            width: 64, height: 64, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
            child: Icon(AppIcons.usersThree, size: 30, color: c.primary),
          ),
          const SizedBox(height: 16),
          Text(l.communitySoonTitle, textAlign: TextAlign.center, style: h2(context)),
          const SizedBox(height: 8),
          Text(l.communitySoonBody, textAlign: TextAlign.center,
              style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w500, height: 1.45, color: c.textSecondary)),
        ]),
      ),
    ]);
  }
}

// ── Profile (real level/XP + achievements from walk stats) ───────────────────
class ProfileTab extends StatefulWidget {
  final String nick;
  final ProfileStats stats;
  final bool signedIn;
  final VoidCallback onSignOut;
  final VoidCallback onFriends;
  final VoidCallback onInvite;
  final VoidCallback? onEdit; // open account editing (null => hidden, e.g. guest)
  final List<({String id, String nick, int walks, bool paid})> friends;
  final ValueChanged<({String id, String nick, int walks, bool paid})>? onOpenFriend;
  final String inviteUrl;
  final String? avatarUrl; // null => bundled default backpacker avatar
  const ProfileTab({
    super.key,
    required this.nick,
    required this.stats,
    required this.signedIn,
    required this.onSignOut,
    required this.onFriends,
    required this.onInvite,
    this.onEdit,
    this.friends = const [],
    this.onOpenFriend,
    this.inviteUrl = '',
    this.avatarUrl,
  });

  @override
  State<ProfileTab> createState() => _ProfileTabState();
}

class _ProfileTabState extends State<ProfileTab> {
  bool _achExpanded = false;

  // Flat, one-tone translucent block fill (mockup colours): white over the warm light
  // gradient (so blocks pop), the dark glass token over the dark gradient.
  Color _blockFill(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark ? context.colors.glass : const Color(0x8CFFFFFF);

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final stats = widget.stats;
    final fill = _blockFill(context);
    final lvl = LevelInfo.fromWalks(stats.walks);
    final achs = achievementsFor(stats);
    final gotCount = unlockedCount(achs);

    return TabPage(title: l.tabProfile, children: [
      // ── identity + level (with share) ──
      GlassModule(
        fill: fill, sheen: false,
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 20),
        child: Stack(children: [
          if (widget.onEdit != null)
            Positioned(
              top: 0, left: 0,
              child: _RoundGlassButton(icon: Icons.edit_rounded, tooltip: 'Редактировать', onTap: widget.onEdit!),
            ),
          Positioned(
            top: 0, right: 0,
            child: _RoundGlassButton(icon: Icons.ios_share_rounded, tooltip: 'Поделиться профилем', onTap: _share),
          ),
          Column(children: [
            const SizedBox(height: 2),
            TravelerAvatar(size: 96, premium: widget.stats.isPaid, imageUrl: widget.avatarUrl),
            const SizedBox(height: 12),
            Text(widget.nick, style: h2(context)),
            const SizedBox(height: 3),
            Text(l.profileLevelN(lvl.level), style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800, color: c.primary)),
            const SizedBox(height: 16),
            Row(children: [
              Text(l.xpValue(lvl.points), style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w700, color: c.textSecondary)),
              const Spacer(),
              Text(lvl.atMax ? l.profileAtMax : l.profileToNext(lvl.level + 1, lvl.xpToNext),
                  style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w700, color: c.textSecondary)),
            ]),
            const SizedBox(height: 8),
            XpBar(value: lvl.progress),
          ]),
        ]),
      ),
      const SizedBox(height: Gap.lg),

      // ── colourful, tappable stat cards ──
      Row(children: [
        Expanded(child: _StatTile(icon: AppIcons.mountains, tint: c.primary, value: _km(stats.distanceM), label: 'км пройдено',
            hint: stats.walks > 0 ? '≈ ${stats.walks} прогулок' : null, fill: fill, onTap: () => _showDistance(context))),
        const SizedBox(width: 10),
        Expanded(child: _StatTile(icon: AppIcons.bank, tint: const Color(0xFF5090EA), value: '${stats.cities}', label: 'городов',
            hint: _citiesHint(), fill: fill, onTap: () => _showCities(context))),
      ]),
      const SizedBox(height: 10),
      Row(children: [
        Expanded(child: _StatTile(icon: AppIcons.list, tint: const Color(0xFFDE9B3C), value: '${stats.objects}', label: 'объектов',
            hint: stats.languages > 0 ? '${stats.languages} языка' : null, fill: fill, onTap: () => _showObjects(context))),
        const SizedBox(width: 10),
        Expanded(child: _StatTile(icon: AppIcons.lightning, tint: const Color(0xFFEC6A6A), value: '${stats.streakDays}', label: 'дней серия',
            hint: stats.streakDays >= 3 ? 'в ударе!' : null, fill: fill, onTap: () => _showStreak(context))),
      ]),
      const SizedBox(height: Gap.lg),

      // ── achievements (collapsible) ──
      GlassModule(
        fill: fill, sheen: false,
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 6),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(l.achievements, style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: .4, color: c.textPrimary)),
            const Spacer(),
            Text('$gotCount / ${achs.length}', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, color: c.primary)),
          ]),
          const SizedBox(height: 14),
          AnimatedSize(
            duration: Motion.med, curve: Motion.emphasized, alignment: Alignment.topCenter,
            child: _achExpanded ? _achGrid(context, achs) : _achPreview(context, achs.take(5).toList()),
          ),
          // Expand / collapse chevron.
          Center(
            child: IconButton(
              tooltip: _achExpanded ? 'Свернуть' : 'Показать все',
              onPressed: () => setState(() => _achExpanded = !_achExpanded),
              icon: AnimatedRotation(
                turns: _achExpanded ? 0.5 : 0,
                duration: Motion.fast,
                child: Icon(Icons.keyboard_arrow_down_rounded, color: c.textSecondary),
              ),
            ),
          ),
        ]),
      ),
      const SizedBox(height: Gap.lg),

      // ── friends ──
      Row(children: [
        Expanded(child: AppButton(l.friends, icon: AppIcons.usersThree, kind: AppBtnKind.secondary, onTap: widget.onFriends)),
        const SizedBox(width: 10),
        Expanded(child: AppButton(l.invite, icon: AppIcons.userPlus, onTap: widget.onInvite)),
      ]),
      const SizedBox(height: Gap.lg),
      if (widget.friends.isEmpty)
        Container(
          height: 74, alignment: Alignment.center,
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(Radii.lg), border: Border.all(color: c.glassBorder, width: 1.5)),
          child: Text(l.friendsSoon, style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w600, color: c.textFaint)),
        )
      else
        GlassModule(
          fill: fill, sheen: false,
          padding: const EdgeInsets.fromLTRB(16, 14, 0, 14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: Row(children: [
                Text(l.friends.toUpperCase(), style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: .4, color: c.textPrimary)),
                const Spacer(),
                Text('${widget.friends.length}', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, color: c.primary)),
              ]),
            ),
            const SizedBox(height: 14),
            SizedBox(
              height: 92,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.only(right: 16),
                itemCount: widget.friends.length,
                separatorBuilder: (_, __) => const SizedBox(width: 16),
                itemBuilder: (_, i) => Pressable(
                  onTap: widget.onOpenFriend == null ? null : () => widget.onOpenFriend!(widget.friends[i]),
                  child: _FriendChip(nick: widget.friends[i].nick, premium: widget.friends[i].paid),
                ),
              ),
            ),
          ]),
        ),
      if (widget.signedIn) ...[
        const SizedBox(height: Gap.lg),
        AppButton(l.signOut, icon: AppIcons.signOut, kind: AppBtnKind.ghost, onTap: widget.onSignOut),
      ],
    ]);
  }

  static String _km(int m) => (m / 1000) >= 10 ? (m ~/ 1000).toString() : (m / 1000).toStringAsFixed(1);

  // One-row preview: the first few badges (unlocked-sorted), evenly spaced.
  Widget _achPreview(BuildContext context, List<AchievementState> preview) => Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [for (final a in preview) _badge(context, a)],
      );

  // Full grid: even rows of five, evenly spaced (incomplete last row padded so the
  // columns line up instead of collapsing to the left).
  Widget _achGrid(BuildContext context, List<AchievementState> achs) {
    const per = 5;
    final rows = <Widget>[];
    for (var i = 0; i < achs.length; i += per) {
      final end = (i + per) > achs.length ? achs.length : (i + per);
      final slice = achs.sublist(i, end);
      rows.add(Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            for (final a in slice) _badge(context, a),
            for (var k = slice.length; k < per; k++) const SizedBox(width: 48, height: 48),
          ],
        ),
      ));
    }
    return Column(children: rows);
  }

  Widget _badge(BuildContext context, AchievementState a) => Pressable(
        onTap: () => _showAchievement(context, a),
        child: AchievementBadge(emoji: a.def.emoji, locked: !a.unlocked),
      );

  // Share a text summary of the profile + invite link (native share sheet).
  void _share() {
    final s = widget.stats;
    final lvl = LevelInfo.fromWalks(s.walks);
    final got = unlockedCount(achievementsFor(s));
    final msg = 'Мой профиль в AI Audio Guide 🥾\n'
        'Уровень ${lvl.level} · ${_km(s.distanceM)} км пешком · ${s.cities} города · $got достижений.\n'
        'Аудиогид сам рассказывает про места вокруг на прогулке — попробуй: ${widget.inviteUrl}';
    Share.share(msg);
  }

  String? _citiesHint() {
    final n = widget.stats.cityNames;
    if (n.isEmpty) return null;
    return n.length == 1 ? n.first : '${n.first} +${n.length - 1}';
  }

  static String _cityArt(String city) {
    final c = city.toLowerCase();
    if (c.contains('москв')) return '🏛️';
    if (c.contains('петербург') || c.contains('спб') || c.contains('питер')) return '🏰';
    if (c.contains('казан')) return '🕌';
    if (c.contains('сочи')) return '🌴';
    if (c.contains('новосиб') || c.contains('екатер')) return '🏙️';
    return '📍';
  }

  Widget _statSheet(BuildContext ctx, {required String value, required String title, required String note, required Widget art, List<Widget> extra = const []}) {
    final c = context.colors;
    return CardSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).padding.bottom + 24),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          _grabber(c),
          art,
          const SizedBox(height: 14),
          Text(value, style: GoogleFonts.manrope(fontSize: 34, fontWeight: FontWeight.w800, color: c.primary)),
          const SizedBox(height: 2),
          Text(title, style: h2(context)),
          const SizedBox(height: 6),
          Text(note, textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
          ...extra,
        ]),
      ),
    );
  }

  void _showDistance(BuildContext context) {
    final s = widget.stats;
    showModalBottomSheet<void>(
      context: context, backgroundColor: Colors.transparent,
      builder: (ctx) => _statSheet(ctx,
        art: SizedBox(width: double.infinity, height: 90, child: CustomPaint(painter: _RouteArt(context.colors))),
        value: '${_km(s.distanceM)} км',
        title: 'Пройдено пешком',
        note: 'Суммарная дистанция всех прогулок с гидом. Это примерно ${(s.distanceM / 1000 / 0.4).round()} минут в пути.'),
    );
  }

  void _showCities(BuildContext context) {
    final c = context.colors;
    final s = widget.stats;
    showModalBottomSheet<void>(
      context: context, backgroundColor: Colors.transparent,
      builder: (ctx) => _statSheet(ctx,
        art: const Text('🗺️', style: TextStyle(fontSize: 40)),
        value: '${s.cities}',
        title: s.cities == 1 ? 'город' : 'города',
        note: 'Где ты гулял с гидом.',
        extra: [
          const SizedBox(height: 16),
          if (s.cityNames.isEmpty)
            Text('Список появится после первых прогулок.', style: caption(context))
          else
            Column(children: [
              for (final city in s.cityNames)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(children: [
                    Container(
                      width: 40, height: 40, alignment: Alignment.center,
                      decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), gradient: LinearGradient(colors: [c.sage.withValues(alpha: .5), c.primary.withValues(alpha: .35)])),
                      child: Text(_cityArt(city), style: const TextStyle(fontSize: 20)),
                    ),
                    const SizedBox(width: 12),
                    Expanded(child: Text(city, style: titleS(context))),
                  ]),
                ),
            ]),
        ]),
    );
  }

  void _showObjects(BuildContext context) {
    final s = widget.stats;
    showModalBottomSheet<void>(
      context: context, backgroundColor: Colors.transparent,
      builder: (ctx) => _statSheet(ctx,
        art: const Text('🔎', style: TextStyle(fontSize: 40)),
        value: '${s.objects}',
        title: 'объектов рассказано',
        note: 'Столько мест — памятников, зданий, парков — гид раскрыл тебе на прогулках. Каждый со своей историей.'),
    );
  }

  void _showStreak(BuildContext context) {
    final s = widget.stats;
    showModalBottomSheet<void>(
      context: context, backgroundColor: Colors.transparent,
      builder: (ctx) => _statSheet(ctx,
        art: const Text('🔥', style: TextStyle(fontSize: 40)),
        value: '${s.streakDays} дн.',
        title: 'серия подряд',
        note: s.streakDays >= 3 ? 'Ты гулял несколько дней подряд — так держать!' : 'Гуляй несколько дней подряд, чтобы собрать серию.'),
    );
  }

  void _showAchievement(BuildContext context, AchievementState a) {
    final c = context.colors;
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) => CardSheet(
        child: Padding(
          padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).padding.bottom + 24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            _grabber(c),
            Opacity(
              opacity: a.unlocked ? 1 : 0.5,
              child: Container(
                width: 72, height: 72, alignment: Alignment.center,
                decoration: BoxDecoration(borderRadius: BorderRadius.circular(20), color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
                child: Text(a.def.emoji, style: const TextStyle(fontSize: 34)),
              ),
            ),
            const SizedBox(height: 14),
            Text(a.def.title, style: h2(context), textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text(a.def.description, textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
            if (a.progressLabel.isNotEmpty) ...[
              const SizedBox(height: 16),
              XpBar(value: a.progress),
              const SizedBox(height: 6),
              Text(a.unlocked ? 'Получено' : a.progressLabel,
                  style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w700, color: a.unlocked ? c.primary : c.textSecondary)),
            ] else ...[
              const SizedBox(height: 14),
              Text(a.unlocked ? 'Получено' : 'Ещё не открыто',
                  style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w700, color: a.unlocked ? c.primary : c.textFaint)),
            ],
          ]),
        ),
      ),
    );
  }

  Widget _grabber(AppColors c) => Container(
        width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16),
        decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)),
      );
}

/// Small circular glass button (share, etc.).
class _RoundGlassButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;
  const _RoundGlassButton({required this.icon, required this.tooltip, required this.onTap});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Tooltip(
      message: tooltip,
      child: Pressable(
        onTap: onTap,
        scale: 0.9,
        child: Container(
          width: 36, height: 36, alignment: Alignment.center,
          decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
          child: Icon(icon, size: 18, color: c.textSecondary),
        ),
      ),
    );
  }
}

/// One friend: round avatar with an initial + nick caption, sized for a scroll strip.
/// Shows a premium crown for paid friends.
class _FriendChip extends StatelessWidget {
  final String nick;
  final bool premium;
  const _FriendChip({required this.nick, this.premium = false});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final circle = Container(
      width: 58, height: 58, alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [c.sage, c.primary]),
        border: Border.all(color: c.glassBorder, width: 2),
      ),
      child: Text(nick.characters.firstOrNull?.toUpperCase() ?? '?',
          style: GoogleFonts.manrope(fontSize: 22, fontWeight: FontWeight.w800, color: Colors.white)),
    );
    return SizedBox(
      width: 62,
      child: Column(children: [
        SizedBox(
          width: 58, height: 58,
          child: Stack(clipBehavior: Clip.none, children: [
            circle,
            if (premium) const Positioned(right: -2, bottom: -2, child: PremiumBadge(size: 20)),
          ]),
        ),
        const SizedBox(height: 6),
        Text(nick, maxLines: 1, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center,
            style: GoogleFonts.manrope(fontSize: 11.5, fontWeight: FontWeight.w700, color: c.textPrimary)),
      ]),
    );
  }
}

/// A little winding-route illustration for the distance detail sheet.
class _RouteArt extends CustomPainter {
  final AppColors c;
  _RouteArt(this.c);
  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    final track = Paint()
      ..color = c.primary
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round;
    final path = Path()
      ..moveTo(w * 0.08, h * 0.8)
      ..cubicTo(w * 0.24, h * 0.5, w * 0.30, h * 0.15, w * 0.46, h * 0.28)
      ..cubicTo(w * 0.62, h * 0.4, w * 0.68, h * 0.85, w * 0.84, h * 0.7)
      ..cubicTo(w * 0.9, h * 0.64, w * 0.9, h * 0.4, w * 0.94, h * 0.3);
    canvas.drawPath(path, track);
    final dot = Paint()..color = c.primary;
    final ring = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5;
    for (final p in [Offset(w * 0.08, h * 0.8), Offset(w * 0.94, h * 0.3)]) {
      canvas.drawCircle(p, 6, dot);
      canvas.drawCircle(p, 6, ring);
    }
  }

  @override
  bool shouldRepaint(covariant _RouteArt old) => old.c != c;
}

/// A colourful, tappable stat card: tinted icon chip + value + label + a hint line.
/// Presses with a light scale animation.
class _StatTile extends StatelessWidget {
  final IconData icon;
  final Color tint;
  final String value, label;
  final String? hint; // small secondary line ("Москва +2", "3 языка"…)
  final Color fill;
  final VoidCallback onTap;
  const _StatTile({required this.icon, required this.tint, required this.value, required this.label, this.hint, required this.fill, required this.onTap});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: GlassModule(
        fill: fill, sheen: false,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        child: Row(children: [
          Container(
            width: 40, height: 40, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, color: tint.withValues(alpha: 0.16)),
            child: Icon(icon, size: 21, color: tint),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(value, style: GoogleFonts.manrope(fontSize: 21, fontWeight: FontWeight.w800, height: 1, color: c.textPrimary)),
              const SizedBox(height: 3),
              Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: GoogleFonts.manrope(fontSize: 11.5, fontWeight: FontWeight.w700, color: c.textSecondary)),
              if (hint != null && hint!.isNotEmpty) ...[
                const SizedBox(height: 1),
                Text(hint!, maxLines: 1, overflow: TextOverflow.ellipsis, style: GoogleFonts.manrope(fontSize: 10.5, fontWeight: FontWeight.w600, color: c.textFaint)),
              ],
            ]),
          ),
        ]),
      ),
    );
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────
class SettingsTab extends StatelessWidget {
  // appearance
  final ThemeMode themeMode;
  final ValueChanged<ThemeMode> onThemeMode;
  final String langLabel;
  final VoidCallback onLanguage;
  // account
  final bool accountsEnabled;
  final String? accountTitle; // "email · tier"
  final VoidCallback? onAccount; // open advanced account sheet (manage/delete)
  final VoidCallback onUpgrade;
  final bool isPaid;
  // developer
  final bool simulate;
  final ValueChanged<bool>? onSimulate; // null when a tour is active (locked)
  final String? routeLabel;
  final VoidCallback? onRoute;

  const SettingsTab({
    super.key,
    required this.themeMode,
    required this.onThemeMode,
    required this.langLabel,
    required this.onLanguage,
    required this.accountsEnabled,
    required this.accountTitle,
    required this.onAccount,
    required this.onUpgrade,
    required this.isPaid,
    required this.simulate,
    required this.onSimulate,
    required this.routeLabel,
    required this.onRoute,
  });

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    // Translucent matte fill, no gradient sheen — same as the Profile blocks.
    final fill = Theme.of(context).brightness == Brightness.dark
        ? context.colors.glass
        : const Color(0x8CFFFFFF);
    return TabPage(title: l.settings, children: [
      BlockLabel(l.appearance),
      GlassModule(fill: fill, sheen: false, child: Column(children: [
        SettingRow(
          icon: AppIcons.sun,
          title: l.themeLabel,
          trailing: SegControl<ThemeMode>(
            items: [
              (value: ThemeMode.system, label: l.themeSystem),
              (value: ThemeMode.light, label: l.themeLight),
              (value: ThemeMode.dark, label: l.themeDark),
            ],
            selected: themeMode,
            onChanged: onThemeMode,
          ),
        ),
        const RowDivider(),
        SettingRow(icon: AppIcons.globe, title: l.language, value: langLabel, onTap: onLanguage),
      ])),
      if (accountsEnabled) ...[
        const SizedBox(height: Gap.lg),
        BlockLabel(l.sectionAccount),
        GlassModule(fill: fill, sheen: false, child: Column(children: [
          SettingRow(icon: AppIcons.profile, title: accountTitle ?? '—', chevron: onAccount != null, onTap: onAccount),
          const RowDivider(),
          SettingRow(icon: AppIcons.lightning, title: l.goPremium, value: isPaid ? l.premiumActive : null, chevron: !isPaid, onTap: isPaid ? null : onUpgrade),
        ])),
      ],
      const SizedBox(height: Gap.lg),
      BlockLabel(l.sectionDeveloper),
      GlassModule(fill: fill, sheen: false, child: Column(children: [
        SettingRow(
          icon: AppIcons.list,
          title: l.simulatedWalk,
          trailing: _MiniSwitch(value: simulate, onChanged: onSimulate),
        ),
        if (simulate) ...[
          const RowDivider(),
          SettingRow(icon: AppIcons.flag, title: l.route, value: routeLabel, onTap: onRoute),
        ],
      ])),
    ]);
  }
}

class _MiniSwitch extends StatelessWidget {
  final bool value;
  final ValueChanged<bool>? onChanged;
  const _MiniSwitch({required this.value, required this.onChanged});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final on = value;
    return GestureDetector(
      onTap: onChanged == null ? null : () => onChanged!(!value),
      child: Opacity(
        opacity: onChanged == null ? 0.5 : 1,
        child: AnimatedContainer(
          duration: Motion.fast,
          width: 42, height: 24,
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(Radii.pill), color: on ? c.primary : c.textFaint.withValues(alpha: .35)),
          child: Align(
            alignment: on ? Alignment.centerRight : Alignment.centerLeft,
            child: Padding(
              padding: const EdgeInsets.all(2.5),
              child: Container(width: 19, height: 19, decoration: const BoxDecoration(shape: BoxShape.circle, color: Colors.white)),
            ),
          ),
        ),
      ),
    );
  }
}
