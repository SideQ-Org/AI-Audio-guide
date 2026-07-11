// Community tab (design/COMMUNITY.md) — the real screen replacing the coming-soon stub.
// Loads from CommunityApi: the caller's community profile, the weekly + custom challenges
// with leaderboards, friends (streak + "на прогулке" presence), friends' routes, incoming
// requests and the activity feed. Pull-to-refresh; graceful states for guest / no-handle.

import 'dart:async';

import 'package:flutter/material.dart';

import '../accounts/accounts_config.dart';
import '../accounts/api_client.dart';
import '../accounts/auth_service.dart';
import '../accounts/community_models.dart';
import '../l10n/app_localizations.dart';
import 'components.dart';
import 'design.dart';

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
  final List<FeedItem> feed;
  final FriendRequests requests;
  const _CommunityData(this.me, this.challenges, this.friends, this.friendWalks, this.feed,
      this.requests);
}

class _CommunityScreenState extends State<CommunityScreen> {
  Future<_CommunityData>? _future;

  bool get _available =>
      AccountsConfig.enabled && AuthService.instance.isSignedIn;

  @override
  void initState() {
    super.initState();
    if (_available) _future = _load();
  }

  Future<_CommunityData> _load() async {
    final results = await Future.wait([
      CommunityApi.me(),
      CommunityApi.challenges(),
      CommunityApi.friends(),
      CommunityApi.friendsWalks(),
      CommunityApi.feed(limit: 20),
      CommunityApi.requests(),
    ]);
    return _CommunityData(
      results[0] as CommunityUser,
      results[1] as List<Challenge>,
      results[2] as List<CommunityUser>,
      results[3] as List<FriendWalk>,
      results[4] as List<FeedItem>,
      results[5] as FriendRequests,
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
    final topPad = MediaQuery.of(context).padding.top;
    final weekly = d.challenges.where((c) => c.isSystem).toList();
    final custom = d.challenges.where((c) => !c.isSystem).toList();

    return ListView(
      padding: EdgeInsets.fromLTRB(16, topPad + 16, 16, MediaQuery.of(context).padding.bottom + 110),
      children: [
        Text(l.tabCommunity, style: h1(context)),
        const SizedBox(height: Gap.lg),

        if (d.me.handle == null) ...[
          _HandleSetupCard(onDone: _refresh),
          const SizedBox(height: Gap.lg),
        ],

        if (d.feed.isNotEmpty) ...[
          _FeedTicker(items: d.feed),
          const SizedBox(height: Gap.lg),
        ],

        if (d.requests.incoming.isNotEmpty) ...[
          _RequestsCard(incoming: d.requests.incoming, onChanged: _refresh),
          const SizedBox(height: Gap.lg),
        ],

        // Weekly + custom challenges
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

        // Friends' routes
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
          const SizedBox(height: Gap.lg),
        ],

        // Friends
        _SectionHeader(title: l.communityFriends, action: l.communityAddFriend,
            onAction: () => _openAddFriend()),
        const SizedBox(height: Gap.sm),
        if (d.friends.isEmpty)
          _MutedNote(l.communityNoFriends)
        else
          GlassModule(
            fill: context.colors.glassFill(0.04), sheen: false,
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (int i = 0; i < d.friends.length; i++) ...[
                  if (i > 0) Divider(height: 1, color: context.colors.glassBorder),
                  _FriendRow(user: d.friends[i]),
                ],
              ],
            ),
          ),
      ],
    );
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
        fill: context.colors.glassFill(0.04), sheen: false,
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

class _FeedTicker extends StatelessWidget {
  const _FeedTicker({required this.items});
  final List<FeedItem> items;

  String _text(AppLocalizations l, FeedItem it) {
    final name = it.user.name;
    final p = it.payload;
    switch (it.kind) {
      case 'walk':
        final city = (p['city'] as String?) ?? '';
        return city.isEmpty ? l.feedWalked(name) : l.feedWalkedIn(name, city);
      case 'streak':
        return l.feedStreak(name, (p['days'] as num?)?.toInt() ?? 0);
      case 'badge':
        return l.feedBadge(name, (p['badge'] as String?) ?? '');
      case 'challenge_join':
      case 'challenge_win':
        return l.feedChallenge(name);
      default:
        return name;
    }
  }

  String _emoji(String kind) => switch (kind) {
        'walk' => '🥾',
        'streak' => '🔥',
        'badge' => '🏅',
        _ => '🏆',
      };

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return GlassModule(
      fill: context.colors.glassFill(0.04), sheen: false,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Column(
        children: [
          for (int i = 0; i < items.length && i < 5; i++) ...[
            if (i > 0) const SizedBox(height: 8),
            Row(children: [
              Text(_emoji(items[i].kind), style: const TextStyle(fontSize: 15)),
              const SizedBox(width: 10),
              Expanded(
                child: Text(_text(l, items[i]),
                    maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: caption(context).copyWith(fontSize: 13, color: context.colors.textPrimary)),
              ),
            ]),
          ],
        ],
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
    final c = context.colors;
    return GlassModule(
      fill: c.glassFill(0.05), sheen: false,
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
        fill: c.glassFill(0.05), sheen: false,
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
  const _RouteCard({required this.walk});
  final FriendWalk walk;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Container(
      width: 220,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(Radii.lg),
        color: c.glassFill(0.05),
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
              Text('${walk.user.handle != null ? '@${walk.user.handle}' : walk.user.name} · ${AppLocalizations.of(context)!.profileLevelN(walk.user.level)}',
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
            ]),
          ),
        ]),
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

class _FriendRow extends StatelessWidget {
  const _FriendRow({required this.user});
  final CommunityUser user;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(children: [
        _Avatar(user: user, size: 44),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(user.name, style: titleS(context).copyWith(fontSize: 15, fontWeight: FontWeight.w800)),
            const SizedBox(height: 2),
            Text('${user.handle != null ? '@${user.handle} · ' : ''}${l.profileLevelN(user.level)}',
                style: caption(context)),
          ]),
        ),
        if (user.walkingNow)
          Text(l.communityWalkingNow, style: caption(context).copyWith(color: c.primary, fontWeight: FontWeight.w800))
        else if (user.streak > 0)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(color: c.lime.withValues(alpha: 0.2), borderRadius: BorderRadius.circular(Radii.pill)),
            child: Text('🔥 ${user.streak}', style: caption(context).copyWith(fontWeight: FontWeight.w800, color: c.primary)),
          ),
      ]),
    );
  }
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
          Center(child: Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16),
              decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)))),
          Text(l.communityAddFriend, style: h2(context)),
          const SizedBox(height: 12),
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
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
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
          Center(child: Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16),
              decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)))),
          Text(l.communityCreateChallenge, style: h2(context)),
          const SizedBox(height: 14),
          Container(
            height: 50, padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(color: c.glassFill(0.05), borderRadius: BorderRadius.circular(14), border: Border.all(color: c.glassBorder)),
            child: Center(child: TextField(
              controller: _title, autocorrect: false, cursorColor: c.primary,
              style: body(context).copyWith(fontWeight: FontWeight.w600),
              decoration: InputDecoration(isCollapsed: true, border: InputBorder.none,
                  hintText: l.communityChallengeTitle,
                  hintStyle: body(context).copyWith(color: c.textFaint, fontWeight: FontWeight.w600)),
            )),
          ),
          const SizedBox(height: 14),
          Text(l.communityMetric, style: label(context).copyWith(fontSize: 12)),
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: [
            chip(l.communityMetricDistance, _metric == 'distance', () => setState(() => _metric = 'distance')),
            chip(l.communityMetricPlaces, _metric == 'places', () => setState(() => _metric = 'places')),
            chip(l.communityMetricDistricts, _metric == 'districts', () => setState(() => _metric = 'districts')),
          ]),
          const SizedBox(height: 14),
          Row(children: [
            Expanded(child: _Stepper(label: l.communityGoalLabel, value: _goal, min: 1, step: _metric == 'distance' ? 1 : 1, onChanged: (v) => setState(() => _goal = v))),
            const SizedBox(width: 12),
            Expanded(child: _Stepper(label: l.communityDaysLabel, value: _days, min: 1, max: 30, onChanged: (v) => setState(() => _days = v))),
          ]),
          const SizedBox(height: 18),
          AppButton(l.communityCreateChallenge, onTap: _busy ? null : _create),
        ]),
      ),
    );
  }
}

class _Stepper extends StatelessWidget {
  const _Stepper({required this.label, required this.value, required this.onChanged, this.min = 1, this.max = 999, this.step = 1});
  final String label;
  final int value;
  final int min, max, step;
  final ValueChanged<int> onChanged;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget btn(IconData i, VoidCallback t) => Pressable(onTap: t, child: Icon(i, size: 22, color: c.primary));
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: caption(context)),
      const SizedBox(height: 6),
      Container(
        height: 46, padding: const EdgeInsets.symmetric(horizontal: 12),
        decoration: BoxDecoration(color: c.glassFill(0.05), borderRadius: BorderRadius.circular(12), border: Border.all(color: c.glassBorder)),
        child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          btn(Icons.remove_rounded, () { if (value - step >= min) onChanged(value - step); }),
          Text('$value', style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
          btn(Icons.add_rounded, () { if (value + step <= max) onChanged(value + step); }),
        ]),
      ),
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
                    fill: c.glassFill(0.04), sheen: false,
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
