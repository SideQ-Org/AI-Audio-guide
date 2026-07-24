// Community tab (design/COMMUNITY.md) — the real screen replacing the coming-soon stub.
// Loads from CommunityApi: the caller's community profile, the weekly + custom challenges
// with leaderboards, friends (streak + "на прогулке" presence), friends' routes, incoming
// requests and the activity feed. Pull-to-refresh; graceful states for guest / no-handle.

import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart' show Ticker;
import 'package:share_plus/share_plus.dart';

import '../accounts/accounts_config.dart';
import '../accounts/api_client.dart';
import '../accounts/auth_service.dart';
import '../accounts/community_models.dart';
import '../accounts/realtime_service.dart';
import '../accounts/walk_detail_screen.dart';
import '../l10n/app_localizations.dart';
import 'components.dart';
import 'design.dart';
import 'track_map.dart';
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
  DateTime? _authBecameReadyAt;
  int _warmRetryLeft = 2;
  bool _authExpired = false;

  bool get _available =>
      AccountsConfig.enabled && AuthService.instance.isSignedIn;

  /// Last successfully loaded data, kept for the app-run lifetime (static): reopening
  /// the tab renders INSTANTLY from this while a background refresh catches up —
  /// without it every open stared at a spinner for the slowest of 8 endpoints.
  static _CommunityData? _lastData;

  @override
  void initState() {
    super.initState();
    if (_available) {
      _authBecameReadyAt = DateTime.now();
      if (_lastData != null) {
        // Stale-while-refresh: show the cached tab immediately, update silently.
        _future = Future.value(_lastData);
        _load().then((d) {
          if (mounted) setState(() => _future = Future.value(d));
        }).catchError((_) {/* keep showing the cached data */});
      } else {
        _future = _load();
      }
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
    Future<_CommunityData> attempt() async {
      // `me` is required (identifies the account); if IT fails, show the error card.
      // Prod pool-fix: the old code fanned out 8 requests at once, which saturated the
      // Supavisor session pooler and produced EMAXCONNSESSION bursts in the field. Keep a
      // small bounded concurrency: fast enough for UX, but no longer spikes the DB pool.
      final meF = CommunityApi.me();
      // Every other section loads independently — one flaky/slow endpoint must NOT blank
      // the whole tab (it just renders that section empty).
      Future<T> safe<T>(Future<T> f, T fallback) => f.catchError((_) => fallback);
      final jobs = [
        () => safe(CommunityApi.challenges(), <Challenge>[]),
        () => safe(CommunityApi.friends(), <CommunityUser>[]),
        () => safe(CommunityApi.friendsWalks(), <FriendWalk>[]),
        () => safe(CommunityApi.myWalks(limit: paid ? 12 : 10), <FriendWalk>[]),
        () => safe(CommunityApi.groupStreaks(), <GroupStreak>[]),
        () => safe(CommunityApi.feed(limit: 20), <FeedItem>[]),
        () => safe(CommunityApi.requests(), const FriendRequests()),
      ];
      final r = <dynamic>[];
      const chunk = 2;
      for (var i = 0; i < jobs.length; i += chunk) {
        final part = await Future.wait([
          for (final j in jobs.sublist(i, (i + chunk).clamp(0, jobs.length))) j(),
        ]);
        r.addAll(part);
      }
      final me = await meF;  // awaited last: a failure here still shows the error card
      final data = _CommunityData(
        me,
        r[0] as List<Challenge>,
        r[1] as List<CommunityUser>,
        r[2] as List<FriendWalk>,
        r[3] as List<FriendWalk>,
        r[4] as List<GroupStreak>,
        r[5] as List<FeedItem>,
        r[6] as FriendRequests,
      );
      _lastData = data; // feed the instant-render cache for the next tab open
      return data;
    }

    try {
      final data = await attempt();
      _warmRetryLeft = 2;
      _authExpired = false;
      return data;
    } on ApiException catch (e) {
      if (e.statusCode == 401) {
        _authExpired = true;
        await AuthService.instance.refreshEntitlement();
      }
      final warmWindow = !_authExpired &&
          _authBecameReadyAt != null &&
          DateTime.now().difference(_authBecameReadyAt!) < const Duration(seconds: 8);
      if (warmWindow && _warmRetryLeft > 0) {
        _warmRetryLeft -= 1;
        await Future<void>.delayed(const Duration(milliseconds: 450));
        return attempt();
      }
      rethrow;
    } catch (e) {
      final warmWindow = _authBecameReadyAt != null &&
          DateTime.now().difference(_authBecameReadyAt!) < const Duration(seconds: 8);
      if (warmWindow && _warmRetryLeft > 0) {
        _warmRetryLeft -= 1;
        await Future<void>.delayed(const Duration(milliseconds: 450));
        return attempt();
      }
      rethrow;
    }
  }

  Future<void> _refresh() async {
    _warmRetryLeft = 2;
    _authBecameReadyAt = DateTime.now();
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
              final warmWindow = !_authExpired &&
                  _authBecameReadyAt != null &&
                  DateTime.now().difference(_authBecameReadyAt!) < const Duration(seconds: 8);
              if (warmWindow && _warmRetryLeft > 0) {
                Future<void>.microtask(() async {
                  if (!mounted) return;
                  _warmRetryLeft -= 1;
                  await Future<void>.delayed(const Duration(milliseconds: 450));
                  if (!mounted) return;
                  setState(() => _future = _load());
                });
                return ListView(children: [
                  SizedBox(height: topPad + 120),
                  Center(child: CircularProgressIndicator(color: c.primary)),
                ]);
              }
              final authError = _authExpired ||
                  (snap.error is ApiException && (snap.error as ApiException).statusCode == 401);
              return ListView(children: [
                SizedBox(height: topPad + 100),
                _CenterCard(
                  icon: authError ? Icons.lock_outline_rounded : Icons.wifi_off_rounded,
                  title: l.tabCommunity,
                  body: authError ? l.authErrorNetwork : l.authErrorNetwork,
                  action: TextButton(
                    onPressed: _refresh,
                    child: Text(l.retry),
                  ),
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

    // Section cards below; each is wrapped in a staggered FadeSlideIn at the end of
    // this method, so the tab content reveals top-down when it first loads.
    final sections = <Widget>[
        // Title + add-friend (friends are managed here + in the profile).
        Row(children: [
          Expanded(child: Text(l.tabCommunity, style: h1(context))),
          Pressable(
            onTap: () => _openAddFriend(d),
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
        if (d.feed.isNotEmpty) ...[
          _NewsMarquee(items: d.feed),
          const SizedBox(height: Gap.lg),
        ],

        // Friend requests: incoming (accept/decline) + outgoing (compact "request sent").
        if (d.requests.incoming.isNotEmpty || d.requests.outgoing.isNotEmpty) ...[
          _RequestsCard(
              incoming: d.requests.incoming,
              outgoing: d.requests.outgoing,
              onChanged: _refresh),
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
              itemBuilder: (_, i) =>
                  _RouteCard(walk: d.myWalks[i], showUser: false, onDeleted: _refresh),
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
          const SizedBox(height: Gap.lg),
        ],

        // 6. Friends (streak + live "walking now" + unfriend on ⋮ / long-press).
        _SectionHeader(title: l.communityFriends),
        const SizedBox(height: Gap.sm),
        if (d.friends.isEmpty)
          _MutedNote(l.communityNoFriends)
        else
          _FriendsList(friends: d.friends, onChanged: _refresh),
    ];
    return ListView(
      padding: EdgeInsets.fromLTRB(16, topPad + 16, 16, MediaQuery.of(context).padding.bottom + 110),
      children: [
        for (var i = 0; i < sections.length; i++)
          FadeSlideIn.stagger(i, child: sections[i]),
      ],
    );
  }

  void _openMyRoutes() {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const MyRoutesScreen()));
  }

  Future<void> _openAddFriend(_CommunityData d) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AddFriendSheet(
        friendIds: d.friends.map((f) => f.id).toSet(),
        pendingIds: d.requests.outgoing.map((f) => f.id).toSet(),
      ),
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
      case 'walk_shared':
        return l.feedWalkShared(name);
      default:
        return l.feedChallenge(name);
    }
  }

  String _emoji(String kind) => switch (kind) {
        'walk' => '🥾',
        'walk_shared' => '🗺️',
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
  const _RequestsCard({required this.incoming, this.outgoing = const [], required this.onChanged});
  final List<CommunityUser> incoming;
  final List<CommunityUser> outgoing;
  final Future<void> Function() onChanged;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
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
        // Outgoing: compact one-liners — the request is waiting on the other side.
        for (final u in outgoing)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 3),
            child: Row(children: [
              Icon(Icons.schedule_rounded, size: 15, color: c.textFaint),
              const SizedBox(width: 8),
              Expanded(
                child: Text(l.communityRequestOutgoing(u.handle ?? u.name),
                    maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
              ),
            ]),
          ),
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
        // AnimatedSize: joining reveals the progress bar with a smooth expand.
        child: AnimatedSize(
          duration: Motion.med,
          curve: Motion.easeOut,
          alignment: Alignment.topCenter,
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
  const _RouteCard({required this.walk, this.showUser = true, this.onDeleted});
  final FriendWalk walk;
  final bool showUser; // friends' routes show @user; my routes show distance · places
  // Owner cards only: inline delete (confirm -> API -> parent refresh) so a walk can
  // be removed straight from the list without opening the detail screen first.
  final VoidCallback? onDeleted;

  Future<void> _delete(BuildContext context) async {
    final l = AppLocalizations.of(context)!;
    final ok = await showBrandConfirm(
      context,
      icon: Icons.delete_outline_rounded,
      title: l.deleteWalk,
      message: l.deleteWalkConfirm,
      confirmLabel: l.delete,
      cancelLabel: l.cancel,
      destructive: true,
    );
    if (!ok || !context.mounted) return;
    try {
      await WalkApi.deleteWalk(walk.id);
      onDeleted?.call();
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(AppLocalizations.of(context)!.authErrorNetwork)),
        );
      }
    }
  }

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
            // Real map preview with the ASPECT-TRUE route (the shared TrackMap renderer:
            // tiles + CameraFit.bounds). The old _RoutePainter stretched lat/lon
            // independently to fill the card and drew on a blank background — the route
            // shape had nothing to do with the real walk. Non-interactive, so taps fall
            // through to the card's Pressable (open detail). The painter stays only as
            // the no-track placeholder (a gentle decorative wave).
            child: Stack(fit: StackFit.expand, children: [
              walk.path.length >= 2
                  ? TrackMap(
                      path: walk.path,
                      height: double.infinity,
                      width: double.infinity,
                      borderRadius: 0, // the card's ClipRRect already rounds the corners
                      strokeWidth: 3,
                      padding: 16,
                    )
                  : Container(
                      width: double.infinity,
                      color: c.glassFill(0.03),
                      child: CustomPaint(painter: _RoutePainter(walk.path, c.primary)),
                    ),
              // Inline delete for the OWNER's walks: a small glass chip over the map
              // corner (solid fill for contrast on tiles — no blur, Impeller-safe).
              if (onDeleted != null)
                Positioned(
                  top: 8,
                  right: 8,
                  child: Pressable(
                    onTap: () => _delete(context),
                    child: Container(
                      width: 32,
                      height: 32,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: _blockFill(context),
                        border: Border.all(color: c.glassBorder),
                      ),
                      child: Icon(Icons.delete_outline_rounded, size: 17, color: c.err),
                    ),
                  ),
                ),
            ]),
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

/// Placeholder-only painter for a card whose walk carries NO track (path < 2 points):
/// a gentle decorative wave. Real tracks are rendered by the shared TrackMap (tiles +
/// aspect-true CameraFit) — never by this painter (it can't preserve geography).
class _RoutePainter extends CustomPainter {
  _RoutePainter(this.path, this.color);
  final List<List<double>> path;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final stroke = Paint()
      ..color = color.withValues(alpha: 0.4)
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    const pad = 18.0;
    final p = Path()
      ..moveTo(pad, size.height * 0.6)
      ..cubicTo(size.width * 0.35, size.height * 0.2, size.width * 0.6, size.height * 0.85,
          size.width - pad, size.height * 0.4);
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
    } catch (e) {
      if (mounted) {
        // A real conflict/validation error means the handle itself is the problem;
        // anything else (timeout, socket, 5xx) is a network problem — say so.
        final conflict = e is ApiException &&
            (e.statusCode == 409 || e.statusCode == 400 || e.statusCode == 422);
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(conflict ? l.communityHandleTaken : l.authErrorNetwork)));
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
                    decoration: bareInput(
                      isCollapsed: true,
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
  const _AddFriendSheet({this.friendIds = const {}, this.pendingIds = const {}});
  /// Already-accepted friends — marked in results, no second request.
  final Set<String> friendIds;
  /// Users with an outgoing request pending — marked "request sent".
  final Set<String> pendingIds;
  @override
  State<_AddFriendSheet> createState() => _AddFriendSheetState();
}

class _AddFriendSheetState extends State<_AddFriendSheet> {
  final _ctrl = TextEditingController();
  Timer? _debounce;
  List<CommunityUser> _results = [];
  final Set<String> _sent = {}; // requests sent from THIS sheet
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

  bool _isFriend(CommunityUser u) => widget.friendIds.contains(u.id);
  bool _isPending(CommunityUser u) => widget.pendingIds.contains(u.id) || _sent.contains(u.id);

  Future<void> _add(CommunityUser u) async {
    final l = AppLocalizations.of(context)!;
    if (u.handle == null || _isFriend(u) || _isPending(u)) return;
    await CommunityApi.requestByHandle(u.handle!);
    _changed = true;
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l.communityRequestSent)));
      setState(() => _sent.add(u.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return CardSheet(
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
                decoration: bareInput(isCollapsed: true,
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
                  if (_isFriend(u))
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 6),
                      child: Text(l.communityAlreadyFriends,
                          style: caption(context).copyWith(fontWeight: FontWeight.w700)),
                    )
                  else if (_isPending(u))
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 6),
                      child: Text(l.communityRequestSent,
                          style: caption(context).copyWith(fontWeight: FontWeight.w700)),
                    )
                  else
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
    return CardSheet(
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
              decoration: bareInput(isCollapsed: true,
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
          // Share the room code so a friend can join (system share sheet).
          Pressable(
            onTap: () => Share.share(l.communityCoWalkShareMsg(rt.coWalkCode ?? '')),
            child: Semantics(
              label: l.communityCoWalkShare,
              button: true,
              child: Container(
                width: 34, height: 34, alignment: Alignment.center,
                margin: const EdgeInsets.only(right: 6),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: c.glassFill(0.06),
                  border: Border.all(color: c.glassBorder),
                ),
                child: Icon(Icons.ios_share_rounded, size: 17, color: c.primary),
              ),
            ),
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
    return CardSheet(
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
                  decoration: bareInput(isCollapsed: true,
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
        _MiniButton(label: l.communityStreakLeave, onTap: () async {
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
    return CardSheet(
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

// ── friends list (shared by the tab section and the profile entry) ───────────

class _FriendsList extends StatelessWidget {
  const _FriendsList({required this.friends, required this.onChanged});
  final List<CommunityUser> friends;
  final Future<void> Function() onChanged;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return GlassModule(
      fill: _blockFill(context), sheen: false,
      padding: const EdgeInsets.symmetric(vertical: 4),
      // AnimatedSize: removing a friend (refresh with one row fewer) collapses the
      // card smoothly instead of snapping to the shorter height.
      child: AnimatedSize(
        duration: Motion.med,
        curve: Motion.easeOut,
        alignment: Alignment.topCenter,
        child: Column(children: [
          for (int i = 0; i < friends.length; i++) ...[
            if (i > 0) Divider(height: 1, color: c.glassBorder),
            _FriendTile(user: friends[i], onChanged: onChanged),
          ],
        ]),
      ),
    );
  }
}

class _FriendTile extends StatelessWidget {
  const _FriendTile({required this.user, required this.onChanged});
  final CommunityUser user;
  final Future<void> Function() onChanged;

  Future<void> _confirmUnfriend(BuildContext context) async {
    final l = AppLocalizations.of(context)!;
    final ok = await showBrandConfirm(
      context,
      icon: Icons.person_remove_rounded,
      title: l.communityUnfriend,
      message: l.communityUnfriendConfirm(user.name),
      confirmLabel: l.delete,
      cancelLabel: l.cancel,
      destructive: true,
    );
    if (!ok) return;
    await CommunityApi.unfriend(user.id);
    await onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final sub = [
      if (user.handle != null) '@${user.handle}',
      l.profileLevelN(user.level),
    ].join(' · ');
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onLongPress: () => _confirmUnfriend(context),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 4, 8),
        child: Row(children: [
          Stack(clipBehavior: Clip.none, children: [
            _Avatar(user: user, size: 42),
            if (user.walkingNow)
              Positioned(
                right: -1, bottom: -1,
                child: Container(
                  width: 13, height: 13,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: c.ok,
                    border: Border.all(color: c.glassBorder, width: 2),
                  ),
                ),
              ),
          ]),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(user.name, maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: titleS(context).copyWith(fontSize: 14.5)),
              const SizedBox(height: 1),
              user.walkingNow
                  ? Text('$sub · ${l.communityWalkingNow}',
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: caption(context).copyWith(color: c.ok, fontWeight: FontWeight.w700))
                  : Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
            ]),
          ),
          if (user.streak > 0) ...[
            const SizedBox(width: 8),
            Text('🔥 ${user.streak}',
                style: caption(context).copyWith(fontWeight: FontWeight.w800, color: c.primary)),
          ],
          Pressable(
            onTap: () => _confirmUnfriend(context),
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Icon(Icons.more_vert_rounded, size: 20, color: c.textFaint),
            ),
          ),
        ]),
      ),
    );
  }
}

// ── friends (full page — the profile "Друзья" entry point) ───────────────────

/// Backend-driven friends screen: the same tiles as the Community tab (streak, live
/// "walking now" presence, unfriend) plus incoming/outgoing requests and add-friend.
/// Replaces the legacy metadata-backed [FriendsScreen] as the profile entry.
class CommunityFriendsScreen extends StatefulWidget {
  const CommunityFriendsScreen({super.key});
  @override
  State<CommunityFriendsScreen> createState() => _CommunityFriendsScreenState();
}

class _FriendsPageData {
  final List<CommunityUser> friends;
  final FriendRequests requests;
  const _FriendsPageData(this.friends, this.requests);
}

class _CommunityFriendsScreenState extends State<CommunityFriendsScreen> {
  Future<_FriendsPageData>? _future;

  bool get _available => AccountsConfig.enabled && AuthService.instance.isSignedIn;

  @override
  void initState() {
    super.initState();
    if (_available) _future = _load();
  }

  Future<_FriendsPageData> _load() async {
    // Friends are required; requests degrade to empty so a flaky endpoint
    // doesn't blank the page.
    final friendsF = CommunityApi.friends();
    final requests = await CommunityApi.requests()
        .catchError((_) => const FriendRequests());
    return _FriendsPageData(await friendsF, requests);
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  Future<void> _openAddFriend(_FriendsPageData? d) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AddFriendSheet(
        friendIds: d?.friends.map((f) => f.id).toSet() ?? const {},
        pendingIds: d?.requests.outgoing.map((f) => f.id).toSet() ?? const {},
      ),
    );
    if (changed == true) _refresh();
  }

  Widget _header(BuildContext context, _FriendsPageData? d) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(6, 6, 8, 8),
      child: Row(children: [
        IconButton(
          icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
          onPressed: () => Navigator.of(context).maybePop(),
        ),
        Expanded(
          child: Text(l.communityFriends,
              style: h2(context), maxLines: 1, overflow: TextOverflow.ellipsis),
        ),
        if (_available)
          IconButton(
            icon: Icon(Icons.person_add_alt_1_rounded, color: c.primary),
            onPressed: () => _openAddFriend(d),
          ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: !_available
              ? Column(children: [
                  _header(context, null),
                  Expanded(
                    child: _CenterCard(
                        icon: AppIcons.usersThree,
                        title: l.communityFriends,
                        body: l.communityGuest),
                  ),
                ])
              : FutureBuilder<_FriendsPageData>(
                  future: _future,
                  builder: (context, snap) {
                    final d = snap.data;
                    Widget bodyW;
                    if (d != null) {
                      bodyW = RefreshIndicator(
                        onRefresh: _refresh,
                        color: c.primary,
                        child: ListView(
                          padding: EdgeInsets.fromLTRB(
                              16, 4, 16, MediaQuery.of(context).padding.bottom + 24),
                          children: [
                            if (d.requests.incoming.isNotEmpty ||
                                d.requests.outgoing.isNotEmpty) ...[
                              _RequestsCard(
                                  incoming: d.requests.incoming,
                                  outgoing: d.requests.outgoing,
                                  onChanged: _refresh),
                              const SizedBox(height: Gap.lg),
                            ],
                            if (d.friends.isEmpty)
                              _MutedNote(l.communityNoFriends)
                            else
                              _FriendsList(friends: d.friends, onChanged: _refresh),
                          ],
                        ),
                      );
                    } else if (snap.hasError) {
                      bodyW = ListView(children: [
                        const SizedBox(height: 60),
                        _CenterCard(
                          icon: Icons.wifi_off_rounded,
                          title: l.communityFriends,
                          body: l.authErrorNetwork,
                          action: TextButton(onPressed: _refresh, child: Text(l.retry)),
                        ),
                      ]);
                    } else {
                      bodyW = Center(child: CircularProgressIndicator(color: c.primary));
                    }
                    return Column(children: [
                      _header(context, d),
                      Expanded(child: bodyW),
                    ]);
                  },
                ),
        ),
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
  late Future<List<FriendWalk>> _future =
      CommunityApi.myWalks(limit: AuthService.instance.isPaid ? 50 : 10);

  void _reload() {
    setState(() {
      _future = CommunityApi.myWalks(limit: AuthService.instance.isPaid ? 50 : 10);
    });
  }

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
                        (context, i) => FadeSlideIn.stagger(
                            i,
                            child: _RouteCard(
                                walk: snap.data![i], showUser: false, onDeleted: _reload)),
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
