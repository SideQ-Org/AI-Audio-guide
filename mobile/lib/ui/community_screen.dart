// Community tab (design/COMMUNITY.md) — the real screen replacing the coming-soon stub.
// Loads from CommunityApi: the caller's community profile, the weekly + custom challenges
// with leaderboards, friends (streak + "на прогулке" presence), friends' routes, incoming
// requests and the activity feed. Pull-to-refresh; graceful states for guest / no-handle.

import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart' show Ticker;

import '../accounts/accounts_config.dart';
import '../accounts/api_client.dart';
import '../accounts/auth_service.dart';
import '../accounts/community_models.dart';
import '../accounts/realtime_service.dart';
import '../accounts/walk_detail_screen.dart';
import '../l10n/app_localizations.dart';
import 'components.dart';
import 'design.dart';
import 'wheel_picker.dart';

// Card colour — same as the Profile blocks: clean white frosted glass in light, the
// theme glass tint in dark, no sheen (avoids the "gradient" look of the default fill).
Color _blockFill(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? context.colors.glass
        : const Color(0x8CFFFFFF);

class CommunityScreen extends StatefulWidget {
  const CommunityScreen({super.key});

  @override
  State<CommunityScreen> createState() => _CommunityScreenState();
}

class _CommunityData {
  final CommunityUser me;
  final List<Challenge> challenges;
  final List<CommunityUser> friends;
  final List<FriendWalk> friendWalks;
  final List<FriendWalk> myWalks;
  final List<GroupStreak> groupStreaks;
  final List<FeedItem> feed;
  final FriendRequests requests;
  const _CommunityData(this.me, this.challenges, this.friends, this.friendWalks, this.myWalks,
      this.groupStreaks, this.feed, this.requests);
}

class _CommunityScreenState extends State<CommunityScreen> {
  Future<_CommunityData>? _future;

  bool get _available =>
      AccountsConfig.enabled && AuthService.instance.isSignedIn;

  @override
  void initState() {
    super.initState();
    if (_available) {
      _future = _load();
      RealtimeService.instance.startPresence(); // ensure live status is up
      RealtimeService.instance.addListener(_onRealtime); // live "на прогулке" + co-walk
    }
  }

  @override
  void dispose() {
    RealtimeService.instance.removeListener(_onRealtime);
    super.dispose();
  }

  void _onRealtime() {
    if (mounted) setState(() {}); // presence changed → repaint friend statuses / co-walk
  }

  Future<_CommunityData> _load() async {
    final paid = AuthService.instance.isPaid;
    // `me` is required (identifies the account); if IT fails, show the error card. Fire it
    // ALONGSIDE the rest (not in a first, separate await) so all requests open pooled DB
    // connections in one wave — awaiting me first cost a whole extra cold round-trip to the
    // (remote) Supabase pooler on the first load. We await its result last, so a failure still
    // surfaces as the error card exactly as before.
    final meF = CommunityApi.me();
    // Every other section loads independently — one flaky/slow endpoint must NOT blank
    // the whole tab (it just renders that section empty).
    Future<T> safe<T>(Future<T> f, T fallback) => f.catchError((_) => fallback);
    final r = await Future.wait([
      safe(CommunityApi.challenges(), <Challenge>[]),
      safe(CommunityApi.friends(), <CommunityUser>[]),
      safe(CommunityApi.friendsWalks(), <FriendWalk>[]),
      safe(CommunityApi.myWalks(limit: paid ? 12 : 10), <FriendWalk>[]),
      safe(CommunityApi.groupStreaks(), <GroupStreak>[]),
      safe(CommunityApi.feed(limit: 20), <FeedItem>[]),
      safe(CommunityApi.requests(), const FriendRequests()),
    ]);
    final me = await meF;  // awaited last: a failure here still shows the error card
    return _CommunityData(
      me,
      r[0] as List<Challenge>,
      r[1] as List<CommunityUser>,
      r[2] as List<FriendWalk>,
      r[3] as List<FriendWalk>,
      r[4] as List<GroupStreak>,
      r[5] as List<FeedItem>,
      r[6] as FriendRequests,
    );
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final topPad = MediaQuery.of(context).padding.top;

    if (!_available) {
      return GradientBackground(
        child: _CenterCard(
          icon: AppIcons.usersThree,
          title: l.tabCommunity,
          body: l.communityGuest,
        ),
      );
    }

    return GradientBackground(
      child: RefreshIndicator(
        onRefresh: _refresh,
        color: c.primary,
        child: FutureBuilder<_CommunityData>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return ListView(children: [
                SizedBox(height: topPad + 120),
                Center(child: CircularProgressIndicator(color: c.primary)),
              ]);
            }
            if (snap.hasError || !snap.hasData) {
              return ListView(children: [
                SizedBox(height: topPad + 100),
                _CenterCard(
                  icon: Icons.wifi_off_rounded,
                  title: l.tabCommunity,
                  body: l.authErrorNetwork,
                  action: TextButton(onPressed: _refresh, child: Text(l.retry)),
                ),
              ]);
            }
            return _content(context, snap.data!);
          },
        ),
      ),
    );
  }

  Widget _content(BuildContext context, _CommunityData d) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final topPad = MediaQuery.of(context).padding.top;
    final weekly = d.challenges.where((c) => c.isSystem).toList();
    final custom = d.challenges.where((c) => !c.isSystem).toList();

    return ListView(
      padding: EdgeInsets.fromLTRB(16, topPad + 16, 16, MediaQuery.of(context).padding.bottom + 110),
      children: [
        // Title + add-friend (friends are managed here + in the profile).
        Row(children: [
          Expanded(child: Text(l.tabCommunity, style: h1(context))),
          Pressable(
            onTap: _openAddFriend,
            child: Container(
              width: 40, height: 40, alignment: Alignment.center,
              decoration: BoxDecoration(shape: BoxShape.circle, color: _blockFill(context), border: Border.all(color: c.glassBorder)),
              child: Icon(Icons.person_add_alt_1_rounded, size: 20, color: c.primary),
            ),
          ),
        ]),
        const SizedBox(height: Gap.lg),

        if (d.me.handle == null) ...[
          _HandleSetupCard(onDone: _refresh),
          const SizedBox(height: Gap.lg),
        ],

        // 1. News marquee (friends' events + what's new).
        if (d.feed.isNotEmpty || true) ...[
          _NewsMarquee(items: d.feed),
          const SizedBox(height: Gap.lg),
        ],

        // Incoming friend requests (kept accessible; friend LIST lives in the profile).
        if (d.requests.incoming.isNotEmpty) ...[
          _RequestsCard(incoming: d.requests.incoming, onChanged: _refresh),
          const SizedBox(height: Gap.lg),
        ],

        // 2. My routes.
        _SectionHeader(
          title: l.communityMyRoutes,
          action: d.myWalks.isNotEmpty ? l.communitySeeAll : null,
          onAction: d.myWalks.isNotEmpty ? () => _openMyRoutes() : null,
        ),
        const SizedBox(height: Gap.sm),
        if (d.myWalks.isEmpty)
          _MutedNote(l.communityNoRoutes)
        else
          SizedBox(
            height: 168,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: d.myWalks.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (_, i) => _RouteCard(walk: d.myWalks[i], showUser: false),
            ),
          ),
        const SizedBox(height: Gap.lg),

        // 3. Together (joint activities).
        _TogetherBlock(friends: d.friends, groupStreaks: d.groupStreaks, onChanged: _refresh),
        const SizedBox(height: Gap.lg),

        // 4. Competitions.
        _SectionHeader(title: l.communityChallenges, action: l.communityCreateChallenge,
            onAction: () => _openCreateChallenge()),
        const SizedBox(height: Gap.sm),
        for (final ch in [...weekly, ...custom]) ...[
          _ChallengeCard(challenge: ch, onChanged: _refresh),
          const SizedBox(height: Gap.sm),
        ],
        if (weekly.isEmpty && custom.isEmpty)
          _MutedNote(l.communityNoChallenges),
        const SizedBox(height: Gap.lg),

        // 5. Friends' shared routes.
        if (d.friendWalks.isNotEmpty) ...[
          _SectionHeader(title: l.communityFriendsRoutes),
          const SizedBox(height: Gap.sm),
          SizedBox(
            height: 168,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: d.friendWalks.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (_, i) => _RouteCard(walk: d.friendWalks[i]),
            ),
          ),
        ],
      ],
    );
  }

  void _openMyRoutes() {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const MyRoutesScreen()));
  }

  Future<void> _openAddFriend() async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => const _AddFriendSheet(),
    );
    if (changed == true) _refresh();
  }

  Future<void> _openCreateChallenge() async {
    final made = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => const _CreateChallengeSheet(),
    );
    if (made == true) _refresh();
  }
}

// ── sections / cards ─────────────────────────────────────────────────────────

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, this.action, this.onAction});
  final String title;
  final String? action;
  final VoidCallback? onAction;
  @override
  Widget build(BuildContext context) {
    return Row(children: [
      Expanded(child: Text(title, style: label(context).copyWith(fontSize: 13))),
      if (action != null && onAction != null)
        Pressable(
          onTap: onAction,
          child: Row(children: [
            Text(action!, style: caption(context).copyWith(color: context.colors.primary, fontWeight: FontWeight.w800)),
            Icon(Icons.chevron_right_rounded, size: 16, color: context.colors.primary),
          ]),
        ),
    ]);
  }
}

class _MutedNote extends StatelessWidget {
  const _MutedNote(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => GlassModule(
        fill: _blockFill(context), sheen: false,
        padding: const EdgeInsets.all(16),
        child: Text(text, style: caption(context).copyWith(fontSize: 13.5)),
      );
}

class _CenterCard extends StatelessWidget {
  const _CenterCard({required this.icon, required this.title, required this.body, this.action});
  final IconData icon;
  final String title;
  final String body;
  final Widget? action;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: GlassModule(
          fill: _blockFill(context), sheen: false,
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: 64, height: 64, alignment: Alignment.center,
              decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
              child: Icon(icon, size: 30, color: c.primary),
            ),
            const SizedBox(height: 16),
            Text(title, textAlign: TextAlign.center, style: h2(context)),
            const SizedBox(height: 8),
            Text(body, textAlign: TextAlign.center, style: body_(context)),
            if (action != null) ...[const SizedBox(height: 8), action!],
          ]),
        ),
      ),
    );
  }
}

TextStyle body_(BuildContext ctx) =>
    body(ctx).copyWith(fontSize: 14, fontWeight: FontWeight.w500, color: ctx.colors.textSecondary);

// One-line, seamlessly-looping news ticker: "what's new" + friends' events.
class _NewsMarquee extends StatefulWidget {
  const _NewsMarquee({required this.items});
  final List<FeedItem> items;
  @override
  State<_NewsMarquee> createState() => _NewsMarqueeState();
}

class _NewsMarqueeState extends State<_NewsMarquee> with SingleTickerProviderStateMixin {
  final _sc = ScrollController();
  Ticker? _ticker;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _ticker = createTicker((_) {
        if (!_sc.hasClients || !_sc.position.hasContentDimensions) return;
        final content = (_sc.position.maxScrollExtent + _sc.position.viewportDimension) / 2;
        if (content <= 0) return;
        var next = _sc.offset + 0.6; // px/tick — slow, readable
        if (next >= content) next -= content; // loop at one copy → seamless
        _sc.jumpTo(next);
      })..start();
    });
  }

  @override
  void dispose() {
    _ticker?.dispose();
    _sc.dispose();
    super.dispose();
  }

  String _text(AppLocalizations l, FeedItem it) {
    final name = it.user.name;
    final p = it.payload;
    switch (it.kind) {
      case 'walk':
        final city = (p['city'] as String?) ?? '';
        return city.isEmpty ? l.feedWalked(name) : l.feedWalkedIn(name, city);
      case 'streak':
      case 'group_streak':
        return l.feedStreak(name, (p['days'] as num?)?.toInt() ?? 0);
      case 'badge':
        return l.feedBadge(name, (p['badge'] as String?) ?? '');
      default:
        return l.feedChallenge(name);
    }
  }

  String _emoji(String kind) => switch (kind) {
        'walk' => '🥾',
        'streak' || 'group_streak' => '🔥',
        'badge' => '🏅',
        _ => '🏆',
      };

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final entries = <({String emoji, String text})>[
      (emoji: '✨', text: l.communityWhatsNew),
      for (final it in widget.items) (emoji: _emoji(it.kind), text: _text(l, it)),
    ];
    Widget strip() => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(width: 14),
            for (final e in entries)
              Padding(
                padding: const EdgeInsets.only(right: 24),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Text(e.emoji, style: const TextStyle(fontSize: 14)),
                  const SizedBox(width: 6),
                  Text(e.text,
                      style: caption(context).copyWith(
                          fontSize: 13, fontWeight: FontWeight.w600, color: context.colors.textPrimary)),
                ]),
              ),
          ],
        );
    return GlassModule(
      fill: _blockFill(context), sheen: false,
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: SizedBox(
        height: 20,
        child: ClipRect(
          child: SingleChildScrollView(
            controller: _sc,
            scrollDirection: Axis.horizontal,
            physics: const NeverScrollableScrollPhysics(),
            child: Row(mainAxisSize: MainAxisSize.min, children: [strip(), strip()]),
          ),
        ),
      ),
    );
  }
}

class _RequestsCard extends StatelessWidget {
  const _RequestsCard({required this.incoming, required this.onChanged});
  final List<CommunityUser> incoming;
  final Future<void> Function() onChanged;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return GlassModule(
      fill: _blockFill(context), sheen: false,
      padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.communityRequests, style: label(context).copyWith(fontSize: 12)),
        const SizedBox(height: 8),
        for (final u in incoming) ...[
          Row(children: [
            _Avatar(user: u, size: 38),
            const SizedBox(width: 10),
            Expanded(child: Text(u.name, style: titleS(context).copyWith(fontSize: 14.5))),
            _MiniButton(label: l.communityAccept, filled: true, onTap: () async {
              await CommunityApi.accept(u.id);
              await onChanged();
            }),
            const SizedBox(width: 6),
            _MiniButton(label: l.communityDecline, onTap: () async {
              await CommunityApi.decline(u.id);
              await onChanged();
            }),
          ]),
          const SizedBox(height: 6),
        ],
      ]),
    );
  }
}

class _ChallengeCard extends StatelessWidget {
  const _ChallengeCard({required this.challenge, required this.onChanged});
  final Challenge challenge;
  final Future<void> Function() onChanged;

  String _goalLabel(AppLocalizations l) {
    switch (challenge.metric) {
      case 'places':
        return l.communityGoalPlaces(challenge.goal);
      case 'districts':
        return l.communityGoalDistricts(challenge.goal);
      default:
        return l.communityGoalKm((challenge.goal / 1000).round());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Pressable(
      onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => ChallengeDetailScreen(challengeId: challenge.id))),
      child: GlassModule(
        fill: _blockFill(context), sheen: false,
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Container(
              width: 42, height: 42, alignment: Alignment.center,
              decoration: BoxDecoration(shape: BoxShape.circle, gradient: LinearGradient(colors: [c.lime, c.primary])),
              child: const Text('🏆', style: TextStyle(fontSize: 20)),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(challenge.title, style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 2),
                Text('${_goalLabel(l)} · ${challenge.participants} 👥',
                    style: caption(context)),
              ]),
            ),
            if (challenge.myRank != null)
              _RankBadge(rank: challenge.myRank!)
            else if (!challenge.joined)
              _MiniButton(label: l.communityJoin, filled: true, onTap: () async {
                await CommunityApi.joinChallenge(challenge.id);
                await onChanged();
              }),
          ]),
          if (challenge.joined) ...[
            const SizedBox(height: 12),
            _ProgressBar(fraction: challenge.progressFraction),
          ],
        ]),
      ),
    );
  }
}

class _RankBadge extends StatelessWidget {
  const _RankBadge({required this.rank});
  final int rank;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: c.lime.withValues(alpha: 0.22),
        borderRadius: BorderRadius.circular(Radii.pill),
      ),
      child: Text('🏆 ${l.communityRankPlace(rank)}',
          style: caption(context).copyWith(fontWeight: FontWeight.w800, color: c.primary)),
    );
  }
}

class _ProgressBar extends StatelessWidget {
  const _ProgressBar({required this.fraction});
  final double fraction;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return ClipRRect(
      borderRadius: BorderRadius.circular(Radii.pill),
      child: LinearProgressIndicator(
        value: fraction,
        minHeight: 8,
        backgroundColor: c.glassFill(0.08),
        valueColor: AlwaysStoppedAnimation(c.primary),
      ),
    );
  }
}

class _RouteCard extends StatelessWidget {
  const _RouteCard({required this.walk, this.showUser = true});
  final FriendWalk walk;
  final bool showUser; // friends' routes show @user; my routes show distance · places
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final l = AppLocalizations.of(context)!;
    final km = walk.distanceM == null ? null : (walk.distanceM! / 1000);
    final sub = showUser
        ? '${walk.user.handle != null ? '@${walk.user.handle}' : walk.user.name} · ${l.profileLevelN(walk.user.level)}'
        : [
            if (km != null) l.communityGoalKm(km.round()),
            if (walk.objectCount > 0) l.communityGoalPlaces(walk.objectCount),
          ].join(' · ');
    return Pressable(
      onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => WalkDetailScreen(
          walkId: walk.id,
          title: walk.city ?? walk.title ?? '—',
          owner: !showUser, // my routes → owner (can share/delete); friends' → not
          community: showUser, // friends' → community endpoint (shared walk)
          subtitle: showUser
              ? (walk.user.handle != null ? '@${walk.user.handle}' : walk.user.name)
              : null,
        ),
      )),
      child: Container(
        width: 220,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(Radii.lg),
          color: _blockFill(context),
          border: Border.all(color: c.glassBorder),
        ),
        child: ClipRRect(
        borderRadius: BorderRadius.circular(Radii.lg),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Container(
              width: double.infinity,
              color: c.glassFill(0.03),
              child: CustomPaint(painter: _RoutePainter(walk.path, c.primary)),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(walk.city ?? walk.title ?? '—',
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 2),
              Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
            ]),
          ),
        ]),
        ),
      ),
    );
  }
}

class _RoutePainter extends CustomPainter {
  _RoutePainter(this.path, this.color);
  final List<List<double>> path;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final stroke = Paint()
      ..color = color
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    const pad = 18.0;
    if (path.length < 2) {
      // placeholder gentle wave
      final p = Path()
        ..moveTo(pad, size.height * 0.6)
        ..cubicTo(size.width * 0.35, size.height * 0.2, size.width * 0.6, size.height * 0.85,
            size.width - pad, size.height * 0.4);
      canvas.drawPath(p, stroke..color = color.withValues(alpha: 0.4));
      return;
    }
    double minLat = path[0][0], maxLat = path[0][0], minLon = path[0][1], maxLon = path[0][1];
    for (final pt in path) {
      minLat = pt[0] < minLat ? pt[0] : minLat;
      maxLat = pt[0] > maxLat ? pt[0] : maxLat;
      minLon = pt[1] < minLon ? pt[1] : minLon;
      maxLon = pt[1] > maxLon ? pt[1] : maxLon;
    }
    final dLat = (maxLat - minLat).abs() < 1e-9 ? 1.0 : (maxLat - minLat);
    final dLon = (maxLon - minLon).abs() < 1e-9 ? 1.0 : (maxLon - minLon);
    final w = size.width - pad * 2, h = size.height - pad * 2;
    Offset map(List<double> pt) => Offset(
          pad + ((pt[1] - minLon) / dLon) * w,
          pad + (1 - (pt[0] - minLat) / dLat) * h, // lat up
        );
    final p = Path()..moveTo(map(path.first).dx, map(path.first).dy);
    for (final pt in path.skip(1)) {
      final o = map(pt);
      p.lineTo(o.dx, o.dy);
    }
    canvas.drawPath(p, stroke);
  }

  @override
  bool shouldRepaint(_RoutePainter old) => old.path != path || old.color != color;
}

class _Avatar extends StatelessWidget {
  const _Avatar({required this.user, this.size = 44});
  final CommunityUser user;
  final double size;
  @override
  Widget build(BuildContext context) {
    return TravelerAvatar(size: size, imageUrl: user.avatarUrl, premium: false);
  }
}

class _MiniButton extends StatelessWidget {
  const _MiniButton({required this.label, required this.onTap, this.filled = false});
  final String label;
  final VoidCallback onTap;
  final bool filled;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: filled ? c.primary : c.glassFill(0.06),
          borderRadius: BorderRadius.circular(Radii.pill),
          border: filled ? null : Border.all(color: c.glassBorder),
        ),
        child: Text(label,
            style: caption(context).copyWith(
                fontWeight: FontWeight.w800, color: filled ? c.onPrimary : c.textPrimary)),
      ),
    );
  }
}

class _HandleSetupCard extends StatefulWidget {
  const _HandleSetupCard({required this.onDone});
  final Future<void> Function() onDone;
  @override
  State<_HandleSetupCard> createState() => _HandleSetupCardState();
}

class _HandleSetupCardState extends State<_HandleSetupCard> {
  final _ctrl = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final l = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      await CommunityApi.setProfile(
        handle: _ctrl.text,
        avatarUrl: AuthService.instance.avatarUrl,
        displayName: AuthService.instance.displayName,
      );
      await widget.onDone();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l.communityHandleTaken)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return GlassModule(
      fill: _blockFill(context), sheen: false,
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.communityPickHandleTitle, style: h2(context).copyWith(fontSize: 18)),
        const SizedBox(height: 4),
        Text(l.communityPickHandleBody, style: body_(context)),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: Container(
              height: 48,
              padding: const EdgeInsets.symmetric(horizontal: 14),
              decoration: BoxDecoration(
                color: c.glassFill(0.05),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: c.glassBorder),
              ),
              child: Row(children: [
                Text('@', style: titleS(context).copyWith(color: c.textFaint)),
                const SizedBox(width: 4),
                Expanded(
                  child: TextField(
                    controller: _ctrl,
                    autocorrect: false,
                    cursorColor: c.primary,
                    style: body(context).copyWith(fontWeight: FontWeight.w600),
                    decoration: InputDecoration(
                      isCollapsed: true,
                      border: InputBorder.none,
                      hintText: l.communityHandleField,
                      hintStyle: body(context).copyWith(color: c.textFaint, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ]),
            ),
          ),
          const SizedBox(width: 10),
          _MiniButton(label: l.communityHandleSave, filled: true, onTap: _busy ? () {} : _save),
        ]),
      ]),
    );
  }
}

// ── add friend sheet ─────────────────────────────────────────────────────────

class _AddFriendSheet extends StatefulWidget {
  const _AddFriendSheet();
  @override
  State<_AddFriendSheet> createState() => _AddFriendSheetState();
}

class _AddFriendSheetState extends State<_AddFriendSheet> {
  final _ctrl = TextEditingController();
  Timer? _debounce;
  List<CommunityUser> _results = [];
  bool _busy = false;
  bool _changed = false;

  @override
  void dispose() {
    _debounce?.cancel();
    _ctrl.dispose();
    super.dispose();
  }

  void _onChanged(String q) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () async {
      if (q.trim().length < 2) {
        setState(() => _results = []);
        return;
      }
      setState(() => _busy = true);
      try {
        final r = await CommunityApi.search(q.trim());
        if (mounted) setState(() => _results = r);
      } finally {
        if (mounted) setState(() => _busy = false);
      }
    });
  }

  Future<void> _add(CommunityUser u) async {
    final l = AppLocalizations.of(context)!;
    if (u.handle == null) return;
    await CommunityApi.requestByHandle(u.handle!);
    _changed = true;
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l.communityRequestSent)));
      setState(() => _results = _results.where((x) => x.id != u.id).toList());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return RoundedSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(context).viewInsets.bottom + 24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _SheetHeader(icon: Icons.person_add_alt_1_rounded, title: l.communityAddFriend, subtitle: l.communitySearchHandle),
          const SizedBox(height: 14),
          Container(
            height: 50, padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(color: c.glassFill(0.05), borderRadius: BorderRadius.circular(14), border: Border.all(color: c.glassBorder)),
            child: Row(children: [
              Icon(Icons.search_rounded, size: 20, color: c.textFaint),
              const SizedBox(width: 10),
              Expanded(child: TextField(
                controller: _ctrl, autofocus: true, autocorrect: false, cursorColor: c.primary,
                onChanged: _onChanged,
                style: body(context).copyWith(fontWeight: FontWeight.w600),
                decoration: InputDecoration(isCollapsed: true, border: InputBorder.none,
                    hintText: l.communitySearchHandle,
                    hintStyle: body(context).copyWith(color: c.textFaint, fontWeight: FontWeight.w600)),
              )),
              if (_busy) SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: c.primary)),
            ]),
          ),
          const SizedBox(height: 12),
          ..._results.map((u) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(children: [
                  _Avatar(user: u, size: 40),
                  const SizedBox(width: 10),
                  Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(u.name, style: titleS(context).copyWith(fontSize: 14.5)),
                    Text('@${u.handle} · ${l.profileLevelN(u.level)}', style: caption(context)),
                  ])),
                  _MiniButton(label: l.communitySendRequest, filled: true, onTap: () => _add(u)),
                ]),
              )),
          const SizedBox(height: 8),
          TextButton(onPressed: () => Navigator.pop(context, _changed), child: Text(l.close)),
        ]),
      ),
    );
  }
}

// ── create challenge sheet ───────────────────────────────────────────────────

class _CreateChallengeSheet extends StatefulWidget {
  const _CreateChallengeSheet();
  @override
  State<_CreateChallengeSheet> createState() => _CreateChallengeSheetState();
}

class _CreateChallengeSheetState extends State<_CreateChallengeSheet> {
  final _title = TextEditingController();
  String _metric = 'distance';
  int _goal = 10; // km / places / districts depending on metric
  int _days = 7;
  bool _busy = false;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  int get _goalMax => _metric == 'distance' ? 50 : (_metric == 'places' ? 100 : 20);

  void _setMetric(String m) {
    setState(() {
      _metric = m;
      if (_goal > _goalMax) _goal = _goalMax;
    });
  }

  Future<void> _create() async {
    if (_title.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      final goal = _metric == 'distance' ? _goal * 1000 : _goal;
      await CommunityApi.createChallenge(
          title: _title.text.trim(), metric: _metric, goal: goal, days: _days);
      if (mounted) Navigator.pop(context, true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    Widget chip(String label, bool sel, VoidCallback onTap) => Pressable(
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
            decoration: BoxDecoration(
              color: sel ? c.primary : c.glassFill(0.05),
              borderRadius: BorderRadius.circular(Radii.pill),
              border: sel ? null : Border.all(color: c.glassBorder),
            ),
            child: Text(label, style: caption(context).copyWith(
                fontWeight: FontWeight.w800, color: sel ? c.onPrimary : c.textPrimary)),
          ),
        );
    return RoundedSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(context).viewInsets.bottom + 24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _SheetHeader(icon: Icons.emoji_events_rounded, title: l.communityCreateChallenge, subtitle: l.communityTeamChallengeSub),
          const SizedBox(height: 16),
          Container(
            height: 52, padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(color: c.glassFill(0.05), borderRadius: BorderRadius.circular(14), border: Border.all(color: c.glassBorder)),
            child: Center(child: TextField(
              controller: _title, autocorrect: false, cursorColor: c.primary,
              style: body(context).copyWith(fontWeight: FontWeight.w600),
              decoration: InputDecoration(isCollapsed: true, border: InputBorder.none,
                  hintText: l.communityChallengeTitle,
                  hintStyle: body(context).copyWith(color: c.textFaint, fontWeight: FontWeight.w600)),
            )),
          ),
          const SizedBox(height: 16),
          Text(l.communityMetric, style: label(context).copyWith(fontSize: 12)),
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: [
            chip(l.communityMetricDistance, _metric == 'distance', () => _setMetric('distance')),
            chip(l.communityMetricPlaces, _metric == 'places', () => _setMetric('places')),
            chip(l.communityMetricDistricts, _metric == 'districts', () => _setMetric('districts')),
          ]),
          const SizedBox(height: 16),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: NumberWheel(label: l.communityGoalLabel, value: _goal, min: 1, max: _goalMax, onChanged: (v) => setState(() => _goal = v))),
            const SizedBox(width: 12),
            Expanded(child: NumberWheel(label: l.communityDaysLabel, value: _days, min: 1, max: 30, onChanged: (v) => setState(() => _days = v))),
          ]),
          const SizedBox(height: 18),
          AppButton(l.communityCreateChallenge, icon: Icons.check_rounded, onTap: _busy ? null : _create),
        ]),
      ),
    );
  }
}

/// Consistent polished header for the community bottom sheets: grabber + a gradient icon
/// chip + title + subtitle.
class _SheetHeader extends StatelessWidget {
  const _SheetHeader({required this.icon, required this.title, this.subtitle});
  final IconData icon;
  final String title;
  final String? subtitle;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Center(
        child: Container(
          width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16),
          decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)),
        ),
      ),
      Row(children: [
        Container(
          width: 46, height: 46, alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: LinearGradient(colors: [c.lime, c.primary]),
            boxShadow: [BoxShadow(color: c.primary.withValues(alpha: .35), blurRadius: 14, spreadRadius: -4, offset: const Offset(0, 6))],
          ),
          child: Icon(icon, color: c.onPrimary, size: 24),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: h2(context).copyWith(fontSize: 19)),
            if (subtitle != null) ...[
              const SizedBox(height: 2),
              Text(subtitle!, style: caption(context).copyWith(fontSize: 12.5)),
            ],
          ]),
        ),
      ]),
    ]);
  }
}

// ── challenge detail (leaderboard) ───────────────────────────────────────────

class ChallengeDetailScreen extends StatefulWidget {
  const ChallengeDetailScreen({super.key, required this.challengeId});
  final String challengeId;
  @override
  State<ChallengeDetailScreen> createState() => _ChallengeDetailScreenState();
}

class _ChallengeDetailScreenState extends State<ChallengeDetailScreen> {
  late Future<ChallengeDetail> _future = CommunityApi.challengeDetail(widget.challengeId);

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: FutureBuilder<ChallengeDetail>(
            future: _future,
            builder: (context, snap) {
              if (!snap.hasData) {
                return Center(child: CircularProgressIndicator(color: c.primary));
              }
              final d = snap.data!;
              return ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                children: [
                  Row(children: [
                    IconButton(onPressed: () => Navigator.pop(context), icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary)),
                    Expanded(child: Text(d.challenge.title, style: h2(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
                  ]),
                  const SizedBox(height: 8),
                  if (!d.challenge.joined)
                    AppButton(l.communityJoin, onTap: () async {
                      await CommunityApi.joinChallenge(widget.challengeId);
                      setState(() => _future = CommunityApi.challengeDetail(widget.challengeId));
                    }),
                  const SizedBox(height: 12),
                  Text(l.communityLeaderboard, style: label(context)),
                  const SizedBox(height: 8),
                  GlassModule(
                    fill: _blockFill(context), sheen: false,
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Column(children: [
                      for (int i = 0; i < d.leaderboard.length; i++) ...[
                        if (i > 0) Divider(height: 1, color: c.glassBorder),
                        _LeaderRow(entry: d.leaderboard[i], metric: d.challenge.metric),
                      ],
                      if (d.leaderboard.isEmpty)
                        Padding(padding: const EdgeInsets.all(16), child: Text(l.communityNoParticipants, style: caption(context))),
                    ]),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}

class _LeaderRow extends StatelessWidget {
  const _LeaderRow({required this.entry, required this.metric});
  final LeaderboardEntry entry;
  final String metric;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final medal = switch (entry.rank) { 1 => '🥇', 2 => '🥈', 3 => '🥉', _ => '${entry.rank}' };
    final progress = metric == 'distance'
        ? l.communityGoalKm((entry.progress / 1000).round())
        : '${entry.progress}';
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      child: Row(children: [
        SizedBox(width: 30, child: Text(medal, style: titleS(context).copyWith(fontWeight: FontWeight.w800))),
        const SizedBox(width: 4),
        _Avatar(user: entry.user, size: 38),
        const SizedBox(width: 10),
        Expanded(child: Text(entry.user.name, style: titleS(context).copyWith(fontSize: 14.5))),
        Text(progress, style: caption(context).copyWith(fontWeight: FontWeight.w800, color: c.primary)),
      ]),
    );
  }
}

// ── co-walk (realtime) ───────────────────────────────────────────────────────

String _coWalkCode() {
  const cs = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  final r = Random();
  return List.generate(4, (_) => cs[r.nextInt(cs.length)]).join();
}

class _CoWalkCard extends StatelessWidget {
  const _CoWalkCard({required this.friends});
  final List<CommunityUser> friends;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final rt = RealtimeService.instance;

    if (rt.inCoWalk) {
      final peers = rt.coWalkPeers;
      final label = peers.isEmpty
          ? l.communityCoWalkWaiting
          : peers.map((p) => p.name ?? '—').join(', ');
      return GlassModule(
        fill: c.primary.withValues(alpha: 0.12), sheen: false,
        padding: const EdgeInsets.all(14),
        child: Row(children: [
          Container(
            width: 42, height: 42, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, color: c.primary.withValues(alpha: .18)),
            child: Icon(Icons.directions_walk_rounded, color: c.primary),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${l.communityCoWalkActive} · ${rt.coWalkCode}',
                  style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 2),
              Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
            ]),
          ),
          _MiniButton(label: l.communityCoWalkLeave, onTap: () => rt.leaveCoWalk()),
        ]),
      );
    }

    return Pressable(
      onTap: () => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        backgroundColor: Colors.transparent,
        builder: (_) => const _CoWalkSheet(),
      ),
      child: GlassModule(
        fill: _blockFill(context), sheen: false,
        padding: const EdgeInsets.all(14),
        child: Row(children: [
          Container(
            width: 42, height: 42, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, gradient: LinearGradient(colors: [c.lime, c.primary])),
            child: Icon(Icons.directions_walk_rounded, color: c.onPrimary),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(l.communityCoWalk, style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 2),
              Text(l.communityCoWalkSub, style: caption(context)),
            ]),
          ),
          Icon(Icons.chevron_right_rounded, color: c.textFaint),
        ]),
      ),
    );
  }
}

class _CoWalkSheet extends StatefulWidget {
  const _CoWalkSheet();
  @override
  State<_CoWalkSheet> createState() => _CoWalkSheetState();
}

class _CoWalkSheetState extends State<_CoWalkSheet> {
  final _code = TextEditingController();
  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    await RealtimeService.instance.startCoWalk(_coWalkCode());
    if (mounted) Navigator.pop(context);
  }

  Future<void> _join() async {
    if (_code.text.trim().isEmpty) return;
    await RealtimeService.instance.startCoWalk(_code.text.trim());
    if (mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return RoundedSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(context).viewInsets.bottom + 24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _SheetHeader(icon: Icons.directions_walk_rounded, title: l.communityCoWalk, subtitle: l.communityCoWalkSub),
          const SizedBox(height: 14),
          Text(l.communityCoWalkExplain, style: caption(context).copyWith(fontSize: 13.5)),
          const SizedBox(height: 16),
          AppButton(l.communityCoWalkCreate, icon: Icons.add_rounded, onTap: _create),
          const SizedBox(height: 16),
          _OrLine(label: l.communityCoWalkOrJoin),
          const SizedBox(height: 16),
          Row(children: [
            Expanded(
              child: Container(
                height: 50, padding: const EdgeInsets.symmetric(horizontal: 14),
                decoration: BoxDecoration(color: c.glassFill(0.05), borderRadius: BorderRadius.circular(14), border: Border.all(color: c.glassBorder)),
                child: Center(child: TextField(
                  controller: _code, autocorrect: false, textCapitalization: TextCapitalization.characters,
                  cursorColor: c.primary, textAlign: TextAlign.center,
                  style: h2(context).copyWith(fontSize: 20, letterSpacing: 4),
                  decoration: InputDecoration(isCollapsed: true, border: InputBorder.none,
                      hintText: l.communityCoWalkEnterCode,
                      hintStyle: body(context).copyWith(color: c.textFaint, fontWeight: FontWeight.w600, letterSpacing: 0)),
                )),
              ),
            ),
            const SizedBox(width: 10),
            _MiniButton(label: l.communityCoWalkJoin, filled: true, onTap: _join),
          ]),
        ]),
      ),
    );
  }
}

class _OrLine extends StatelessWidget {
  const _OrLine({required this.label});
  final String label;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget line() => Expanded(child: Container(height: 1, color: c.glassBorder));
    return Row(children: [
      line(),
      Padding(padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Text(label.toUpperCase(), style: caption(context).copyWith(fontWeight: FontWeight.w700, color: c.textFaint))),
      line(),
    ]);
  }
}

// ── together (joint activities) ──────────────────────────────────────────────

class _TogetherBlock extends StatelessWidget {
  const _TogetherBlock({required this.friends, required this.groupStreaks, required this.onChanged});
  final List<CommunityUser> friends;
  final List<GroupStreak> groupStreaks;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _SectionHeader(title: l.communityTogether),
      const SizedBox(height: Gap.sm),
      // Co-walk (realtime): active banner or the "walk together" tile.
      _CoWalkCard(friends: friends),
      // Active group streaks.
      for (final gs in groupStreaks) ...[
        const SizedBox(height: Gap.sm),
        _GroupStreakCard(streak: gs, onChanged: onChanged),
      ],
      const SizedBox(height: Gap.sm),
      Row(children: [
        Expanded(child: _ActionTile(
          icon: Icons.local_fire_department_rounded,
          title: l.communityGroupStreak,
          sub: l.communityGroupStreakSub,
          onTap: () async {
            final made = await showModalBottomSheet<bool>(
              context: context, isScrollControlled: true, useSafeArea: true,
              backgroundColor: Colors.transparent,
              builder: (_) => _GroupStreakSheet(friends: friends),
            );
            if (made == true) onChanged();
          },
        )),
        const SizedBox(width: 12),
        Expanded(child: _ActionTile(
          icon: Icons.emoji_events_rounded,
          title: l.communityTeamChallenge,
          sub: l.communityTeamChallengeSub,
          onTap: () async {
            final made = await showModalBottomSheet<bool>(
              context: context, isScrollControlled: true, useSafeArea: true,
              backgroundColor: Colors.transparent,
              builder: (_) => const _CreateChallengeSheet(),
            );
            if (made == true) onChanged();
          },
        )),
      ]),
    ]);
  }
}

class _ActionTile extends StatelessWidget {
  const _ActionTile({required this.icon, required this.title, required this.sub, required this.onTap});
  final IconData icon;
  final String title;
  final String sub;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: Container(
        height: 108,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _blockFill(context),
          borderRadius: BorderRadius.circular(Radii.lg),
          border: Border.all(color: c.glassBorder),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          Container(
            width: 36, height: 36, alignment: Alignment.center,
            decoration: BoxDecoration(shape: BoxShape.circle, gradient: LinearGradient(colors: [c.lime, c.primary])),
            child: Icon(icon, size: 20, color: c.onPrimary),
          ),
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: titleS(context).copyWith(fontSize: 14, fontWeight: FontWeight.w800)),
            Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context).copyWith(fontSize: 11.5)),
          ]),
        ]),
      ),
    );
  }
}

class _GroupStreakCard extends StatelessWidget {
  const _GroupStreakCard({required this.streak, required this.onChanged});
  final GroupStreak streak;
  final Future<void> Function() onChanged;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return GlassModule(
      fill: c.lime.withValues(alpha: 0.14), sheen: false,
      padding: const EdgeInsets.all(14),
      child: Row(children: [
        Text('🔥', style: TextStyle(fontSize: 26, color: c.primary)),
        const SizedBox(width: 4),
        Text('${streak.days}', style: h1(context).copyWith(fontSize: 26, color: c.primary)),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(streak.title?.trim().isNotEmpty == true ? streak.title! : l.communityGroupStreak,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
            const SizedBox(height: 2),
            Text(
              '${l.communityGroupStreakDays(streak.days)} · ${streak.members.map((m) => m.handle != null ? '@${m.handle}' : m.name).join(', ')}',
              maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context),
            ),
          ]),
        ),
        _MiniButton(label: l.communityCoWalkLeave, onTap: () async {
          await CommunityApi.leaveGroupStreak(streak.id);
          await onChanged();
        }),
      ]),
    );
  }
}

class _GroupStreakSheet extends StatefulWidget {
  const _GroupStreakSheet({required this.friends});
  final List<CommunityUser> friends;
  @override
  State<_GroupStreakSheet> createState() => _GroupStreakSheetState();
}

class _GroupStreakSheetState extends State<_GroupStreakSheet> {
  final Set<String> _picked = {}; // handles
  bool _busy = false;

  Future<void> _create() async {
    if (_picked.isEmpty) return;
    setState(() => _busy = true);
    try {
      await CommunityApi.createGroupStreak(_picked.toList());
      if (mounted) Navigator.pop(context, true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final withHandle = widget.friends.where((f) => f.handle != null).toList();
    return RoundedSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(context).viewInsets.bottom + 24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _SheetHeader(icon: Icons.local_fire_department_rounded, title: l.communityGroupStreak, subtitle: l.communityGroupStreakPick),
          const SizedBox(height: 16),
          if (withHandle.isEmpty)
            _MutedNote(l.communityGroupStreakEmpty)
          else
            ...withHandle.map((f) {
              final on = _picked.contains(f.handle);
              return Pressable(
                onTap: () => setState(() => on ? _picked.remove(f.handle) : _picked.add(f.handle!)),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(children: [
                    _Avatar(user: f, size: 40),
                    const SizedBox(width: 10),
                    Expanded(child: Text('@${f.handle}', style: titleS(context).copyWith(fontSize: 14.5))),
                    Icon(on ? Icons.check_circle_rounded : Icons.circle_outlined,
                        color: on ? c.primary : c.textFaint),
                  ]),
                ),
              );
            }),
          const SizedBox(height: 16),
          AppButton(l.communityGroupStreak,
              icon: Icons.local_fire_department_rounded,
              onTap: (_busy || _picked.isEmpty) ? null : _create),
        ]),
      ),
    );
  }
}

// ── my routes (full page) ────────────────────────────────────────────────────

class MyRoutesScreen extends StatefulWidget {
  const MyRoutesScreen({super.key});
  @override
  State<MyRoutesScreen> createState() => _MyRoutesScreenState();
}

class _MyRoutesScreenState extends State<MyRoutesScreen> {
  late final Future<List<FriendWalk>> _future =
      CommunityApi.myWalks(limit: AuthService.instance.isPaid ? 50 : 10);

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: FutureBuilder<List<FriendWalk>>(
            future: _future,
            builder: (context, snap) {
              return CustomScrollView(slivers: [
                SliverToBoxAdapter(
                  child: Row(children: [
                    IconButton(onPressed: () => Navigator.pop(context), icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary)),
                    Text(l.communityMyRoutes, style: h2(context)),
                  ]),
                ),
                if (!snap.hasData)
                  SliverFillRemaining(hasScrollBody: false, child: Center(child: CircularProgressIndicator(color: c.primary)))
                else if (snap.data!.isEmpty)
                  SliverFillRemaining(hasScrollBody: false, child: Center(child: Text(l.communityNoRoutes, style: caption(context))))
                else
                  SliverPadding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                    sliver: SliverGrid(
                      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 2, mainAxisSpacing: 12, crossAxisSpacing: 12, childAspectRatio: 0.82),
                      delegate: SliverChildBuilderDelegate(
                        (context, i) => _RouteCard(walk: snap.data![i], showUser: false),
                        childCount: snap.data!.length,
                      ),
                    ),
                  ),
              ]);
            },
          ),
        ),
      ),
    );
  }
}
