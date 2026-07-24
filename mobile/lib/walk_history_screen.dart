// Walk history: sign-in gate -> the user's saved walks (from GET /walks) -> tap into a
// detail screen. When accounts aren't configured in this build it falls back to the
// original clean empty state, so nothing regresses for the guest-only path.

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'accounts/accounts_config.dart';
import 'accounts/api_client.dart';
import 'accounts/auth_service.dart';
import 'accounts/login_screen.dart';
import 'accounts/models.dart';
import 'accounts/walk_detail_screen.dart';
import 'l10n/app_localizations.dart';
import 'ui/components.dart' show FadeSlideIn, fadeThroughRoute;
import 'ui/design.dart';
import 'ui/track_map.dart';

class WalkHistoryScreen extends StatefulWidget {
  const WalkHistoryScreen({super.key, this.onUpgrade});

  /// Opens the Premium upgrade sheet (owned by HomePage). Shown on the "history full"
  /// banner when a free account has hit its saved-walk cap.
  final VoidCallback? onUpgrade;

  @override
  State<WalkHistoryScreen> createState() => _WalkHistoryScreenState();
}

class _WalkHistoryScreenState extends State<WalkHistoryScreen> {
  final _auth = AuthService.instance;
  Future<List<WalkSummary>>? _future;

  @override
  void initState() {
    super.initState();
    _auth.addListener(_onAuth);
    _reload(refreshProfile: true);
  }

  @override
  void dispose() {
    _auth.removeListener(_onAuth);
    super.dispose();
  }

  void _onAuth() {
    // Rebuild the list on a genuine auth change, but DO NOT refresh entitlement here:
    // refreshEntitlement() notifies listeners, which re-enters this very callback —
    // that feedback loop (each pass swapping in a fresh unfinished future) was the
    // perpetual-spinner / lag bug. Only explicit reloads refresh the profile.
    if (mounted) _reload();
  }

  void _reload({bool refreshProfile = false}) {
    setState(() {
      _future = (AccountsConfig.enabled && _auth.isSignedIn)
          ? WalkApi.listWalks()
          : null;
    });
    // Refresh entitlements so the "history full" banner reflects the current counts.
    // Guarded to explicit triggers (initState, pull-to-refresh, post-login/delete) so
    // the resulting notifyListeners() can't loop back through _onAuth.
    if (refreshProfile && AccountsConfig.enabled && _auth.isSignedIn) {
      _auth.refreshEntitlement();
    }
  }

  Future<void> _openLogin() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const LoginScreen()),
    );
    _reload(refreshProfile: true);
  }

  Future<void> _delete(WalkSummary w) async {
    final l = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l.deleteWalk),
        content: Text(l.deleteWalkConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l.delete)),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await WalkApi.deleteWalk(w.id);
    } catch (_) {
      // ignore — reload reflects the true server state either way
    }
    _reload(refreshProfile: true);
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            _BrandedHeader(title: l.walkHistory),
            Expanded(child: _body(l)),
          ]),
        ),
      ),
    );
  }

  Widget _body(AppLocalizations l) {
    // Accounts not built into this binary -> original empty state.
    if (!AccountsConfig.enabled) {
      return _EmptyState(
        title: l.walkHistoryEmptyTitle,
        subtitle: l.walkHistoryEmptySubtitle,
      );
    }
    // Configured but signed out -> invite to sign in.
    if (!_auth.isSignedIn) {
      return _SignInPrompt(onSignIn: _openLogin, prompt: l.historySignInPrompt, cta: l.signIn);
    }
    return FutureBuilder<List<WalkSummary>>(
      future: _future,
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return _ErrorRetry(message: l.historyLoadError, onRetry: _reload);
        }
        final walks = snap.data ?? const [];
        if (walks.isEmpty) {
          return _EmptyState(
            title: l.walkHistoryEmptyTitle,
            subtitle: l.walkHistoryEmptySubtitle,
          );
        }
        final profile = _auth.profile;
        final showUpgrade = (profile?.walksAtLimit ?? false) && widget.onUpgrade != null;
        return RefreshIndicator(
          onRefresh: () async => _reload(refreshProfile: true),
          child: ListView.builder(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
            // One extra leading item for the upgrade banner when the free cap is hit.
            itemCount: walks.length + (showUpgrade ? 1 : 0),
            itemBuilder: (_, i) {
              if (showUpgrade && i == 0) {
                return FadeSlideIn.stagger(
                  i,
                  child: _UpgradeBanner(
                    count: profile!.walkLimit ?? walks.length,
                    onUpgrade: widget.onUpgrade!,
                  ),
                );
              }
              final w = walks[i - (showUpgrade ? 1 : 0)];
              // Tiles reveal with a light stagger on the first build; the detail opens
              // with a fade-through (a Hero over the mini-map would clone a second live
              // TrackMap mid-flight — not cheap, so a soft fade it is).
              return FadeSlideIn.stagger(
                i,
                child: _WalkTile(
                  walk: w,
                  onTap: () async {
                    final deleted = await Navigator.of(context).push<bool>(
                      fadeThroughRoute<bool>(
                        (_) => WalkDetailScreen(walkId: w.id, title: _walkTitle(l, w)),
                      ),
                    );
                    if (deleted == true) _reload(refreshProfile: true);
                  },
                  onDelete: () => _delete(w),
                ),
              );
            },
          ),
        );
      },
    );
  }

  static String _walkTitle(AppLocalizations l, WalkSummary w) {
    if (w.title != null && w.title!.isNotEmpty) return w.title!;
    if (w.city != null && w.city!.isNotEmpty) return w.city!;
    return l.walkHistory;
  }
}

/// Branded screen header: back button + title, on the mesh gradient (no stock AppBar).
class _BrandedHeader extends StatelessWidget {
  const _BrandedHeader({required this.title});
  final String title;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(6, 6, 16, 8),
      child: Row(children: [
        IconButton(
          icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
          onPressed: () => Navigator.of(context).maybePop(),
          tooltip: MaterialLocalizations.of(context).backButtonTooltip,
        ),
        const SizedBox(width: 4),
        Expanded(child: Text(title, style: h1(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
      ]),
    );
  }
}

class _WalkTile extends StatelessWidget {
  const _WalkTile({required this.walk, required this.onTap, required this.onDelete});
  final WalkSummary walk;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final locale = Localizations.localeOf(context).toString();
    final when = DateFormat.yMMMd(locale).add_Hm().format(walk.startedAt.toLocal());
    final title = _WalkHistoryScreenState._walkTitle(l, walk);
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Dismissible(
        key: ValueKey(walk.id),
        direction: DismissDirection.endToStart,
        confirmDismiss: (_) async {
          onDelete();
          return false; // deletion + reload handled by onDelete
        },
        background: Container(
          alignment: Alignment.centerRight,
          padding: const EdgeInsets.only(right: 24),
          decoration: BoxDecoration(color: c.err.withValues(alpha: 0.18), borderRadius: BorderRadius.circular(Radii.lg)),
          child: Icon(Icons.delete_outline_rounded, color: c.err),
        ),
        child: GlassModule(
          child: InkWell(
            borderRadius: BorderRadius.circular(Radii.lg),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 12, 8, 12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                // Real map preview with the walk's route (same TrackMap renderer as the
                // summary sheet / walk detail, aspect-true track). Taps fall through to
                // the card's InkWell (TrackMap is non-interactive), opening the detail.
                if (walk.path.length >= 2) ...[
                  TrackMap(
                    path: walk.path,
                    height: 112,
                    borderRadius: 12,
                    strokeWidth: 3,
                    padding: 20,
                  ),
                  const SizedBox(height: 10),
                ],
                Row(children: [
                  if (walk.path.length < 2) ...[
                    Container(
                      width: 42, height: 42, alignment: Alignment.center,
                      decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
                      child: Icon(Icons.route_rounded, size: 20, color: c.primary),
                    ),
                    const SizedBox(width: 12),
                  ],
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: titleS(context)),
                      const SizedBox(height: 2),
                      Text('$when  ·  ${l.placesCount(walk.objectCount)}',
                          maxLines: 1, overflow: TextOverflow.ellipsis, style: caption(context)),
                    ]),
                  ),
                  IconButton(
                    icon: Icon(Icons.delete_outline_rounded, color: c.textFaint),
                    tooltip: l.deleteWalk,
                    onPressed: onDelete,
                  ),
                  Icon(Icons.chevron_right_rounded, color: c.textFaint),
                ]),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

/// Shared centred glass panel used by the empty / sign-in / error states.
class _StatePanel extends StatelessWidget {
  const _StatePanel({required this.icon, required this.title, this.subtitle, this.action});
  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 0, 24, 80),
        child: GlassModule(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 64, height: 64, alignment: Alignment.center,
                decoration: BoxDecoration(shape: BoxShape.circle, color: c.glassFill(0.06), border: Border.all(color: c.glassBorder)),
                child: Icon(icon, size: 30, color: c.primary),
              ),
              const SizedBox(height: 16),
              Text(title, textAlign: TextAlign.center, style: h2(context)),
              if (subtitle != null) ...[
                const SizedBox(height: 8),
                Text(subtitle!, textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.45)),
              ],
              if (action != null) ...[
                const SizedBox(height: 20),
                action!,
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.title, required this.subtitle});
  final String title;
  final String subtitle;
  @override
  Widget build(BuildContext context) =>
      _StatePanel(icon: Icons.route_rounded, title: title, subtitle: subtitle);
}

class _SignInPrompt extends StatelessWidget {
  const _SignInPrompt({required this.onSignIn, required this.prompt, required this.cta});
  final VoidCallback onSignIn;
  final String prompt;
  final String cta;
  @override
  Widget build(BuildContext context) => _StatePanel(
        icon: Icons.lock_outline_rounded,
        title: prompt,
        action: SizedBox(width: double.infinity, child: AppButton(cta, onTap: onSignIn)),
      );
}

class _UpgradeBanner extends StatelessWidget {
  const _UpgradeBanner({required this.count, required this.onUpgrade});
  final int count;
  final VoidCallback onUpgrade;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassModule(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.workspace_premium_rounded, color: c.primary, size: 22),
              const SizedBox(width: 10),
              Expanded(child: Text(l.historyFullTitle, style: titleS(context))),
            ]),
            const SizedBox(height: 6),
            Text(l.historyFullBody(count), style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
            const SizedBox(height: 12),
            AppButton(l.goPremium, onTap: onUpgrade),
          ],
        ),
      ),
    );
  }
}

class _ErrorRetry extends StatelessWidget {
  const _ErrorRetry({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return _StatePanel(
      icon: Icons.cloud_off_rounded,
      title: message,
      action: SizedBox(width: double.infinity, child: AppButton(l.retry, kind: AppBtnKind.secondary, onTap: onRetry)),
    );
  }
}
