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
      appBar: AppBar(title: Text(l.walkHistory)),
      body: _body(l),
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
            // One extra leading item for the upgrade banner when the free cap is hit.
            itemCount: walks.length + (showUpgrade ? 1 : 0),
            itemBuilder: (_, i) {
              if (showUpgrade && i == 0) {
                return _UpgradeBanner(
                  count: profile!.walkLimit ?? walks.length,
                  onUpgrade: widget.onUpgrade!,
                );
              }
              final w = walks[i - (showUpgrade ? 1 : 0)];
              return _WalkTile(
                walk: w,
                onTap: () async {
                  final deleted = await Navigator.of(context).push<bool>(
                    MaterialPageRoute<bool>(
                      builder: (_) => WalkDetailScreen(walkId: w.id, title: _walkTitle(l, w)),
                    ),
                  );
                  if (deleted == true) _reload(refreshProfile: true);
                },
                onDelete: () => _delete(w),
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

class _WalkTile extends StatelessWidget {
  const _WalkTile({required this.walk, required this.onTap, required this.onDelete});
  final WalkSummary walk;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final locale = Localizations.localeOf(context).toString();
    final when = DateFormat.yMMMd(locale).add_Hm().format(walk.startedAt.toLocal());
    final title = _WalkHistoryScreenState._walkTitle(l, walk);
    return Dismissible(
      key: ValueKey(walk.id),
      direction: DismissDirection.endToStart,
      confirmDismiss: (_) async {
        onDelete();
        return false; // deletion + reload handled by onDelete
      },
      background: Container(
        color: cs.errorContainer,
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        child: Icon(Icons.delete_outline, color: cs.onErrorContainer),
      ),
      child: ListTile(
        leading: const Icon(Icons.route_rounded),
        title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text('$when  ·  ${l.placesCount(walk.objectCount)}'),
        // Explicit delete button (the swipe-to-delete stays as a shortcut).
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              icon: const Icon(Icons.delete_outline),
              color: cs.onSurfaceVariant,
              tooltip: l.deleteWalk,
              onPressed: onDelete,
            ),
            Icon(Icons.chevron_right, color: cs.onSurfaceVariant),
          ],
        ),
        onTap: onTap,
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.title, required this.subtitle});
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.route_rounded, size: 64, color: cs.onSurfaceVariant),
            const SizedBox(height: 18),
            Text(title,
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                textAlign: TextAlign.center),
            const SizedBox(height: 8),
            Text(subtitle,
                style: TextStyle(fontSize: 14, height: 1.5, color: cs.onSurfaceVariant),
                textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}

class _SignInPrompt extends StatelessWidget {
  const _SignInPrompt({required this.onSignIn, required this.prompt, required this.cta});
  final VoidCallback onSignIn;
  final String prompt;
  final String cta;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.lock_outline_rounded, size: 60, color: cs.onSurfaceVariant),
            const SizedBox(height: 18),
            Text(prompt,
                style: const TextStyle(fontSize: 16, height: 1.4),
                textAlign: TextAlign.center),
            const SizedBox(height: 20),
            FilledButton(onPressed: onSignIn, child: Text(cta)),
          ],
        ),
      ),
    );
  }
}

class _UpgradeBanner extends StatelessWidget {
  const _UpgradeBanner({required this.count, required this.onUpgrade});
  final int count;
  final VoidCallback onUpgrade;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: cs.primary.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: cs.primary.withValues(alpha: 0.25)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Icon(Icons.workspace_premium_rounded, color: cs.primary, size: 22),
            const SizedBox(width: 10),
            Expanded(
              child: Text(l.historyFullTitle,
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            ),
          ]),
          const SizedBox(height: 6),
          Text(l.historyFullBody(count),
              style: TextStyle(fontSize: 13.5, height: 1.4, color: cs.onSurfaceVariant)),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton(onPressed: onUpgrade, child: Text(l.goPremium)),
          ),
        ],
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
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(message),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: onRetry, child: Text(l.retry)),
        ],
      ),
    );
  }
}
