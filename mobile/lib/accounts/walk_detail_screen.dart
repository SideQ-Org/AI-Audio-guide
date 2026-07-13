// One saved walk: its narrated objects in order, each replayable offline via on-device
// TTS (the paid-tier "revisit your walk" hook). Loads /walks/{id} on open.

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:intl/intl.dart';
import 'package:latlong2/latlong.dart';

import '../l10n/app_localizations.dart';
import '../map_config.dart';
import '../ui/components.dart';
import '../ui/design.dart';
import '../ui/track_map.dart';
import 'api_client.dart';
import 'models.dart';

// code -> BCP-47 TTS tag (mirrors kLangs in main.dart; kept local to avoid coupling).
const _ttsTag = <String, String>{
  'en': 'en-US', 'ru': 'ru-RU', 'es': 'es-ES', 'fr': 'fr-FR',
  'de': 'de-DE', 'it': 'it-IT', 'pt': 'pt-BR', 'zh': 'zh-CN',
};

class WalkDetailScreen extends StatefulWidget {
  const WalkDetailScreen({
    super.key,
    required this.walkId,
    required this.title,
    this.owner = true,
    this.community = false,
    this.subtitle,
  });
  final String walkId;
  final String title;

  /// True → my own walk (can delete + share). False → a friend's shared walk.
  final bool owner;

  /// Load via the community endpoint (a friend's shared walk) instead of /walks/{id}.
  final bool community;

  /// Optional line under the title (e.g. "прошёл @anna").
  final String? subtitle;

  @override
  State<WalkDetailScreen> createState() => _WalkDetailScreenState();
}

class _WalkDetailScreenState extends State<WalkDetailScreen> {
  final FlutterTts _tts = FlutterTts();
  late Future<WalkDetail> _future;
  int? _speakingSeq;
  bool _shared = false;
  bool _sharing = false;

  Future<WalkDetail> _fetch() =>
      widget.community ? CommunityApi.walkDetail(widget.walkId) : WalkApi.getWalk(widget.walkId);

  @override
  void initState() {
    super.initState();
    _future = _fetch();
    _tts.setCompletionHandler(() {
      if (mounted) setState(() => _speakingSeq = null);
    });
  }

  Future<void> _share() async {
    final l = AppLocalizations.of(context)!;
    setState(() => _sharing = true);
    try {
      await CommunityApi.shareWalk(widget.walkId);
      if (mounted) {
        setState(() => _shared = true);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l.walkShared)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l.authErrorNetwork)));
      }
    } finally {
      if (mounted) setState(() => _sharing = false);
    }
  }

  /// Tapping a stop on the map opens its narration.
  void _showEvent(WalkDetail walk, WalkEventItem e) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _EventSheet(
        event: e,
        onReplay: () => _speak(e, walk.language),
      ),
    );
  }

  @override
  void dispose() {
    _tts.stop();
    super.dispose();
  }

  Future<void> _confirmDelete() async {
    final l = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l.deleteWalk),
        content: Text(l.deleteWalkConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l.cancel)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(ctx).colorScheme.error),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l.delete),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await _tts.stop();
    try {
      await WalkApi.deleteWalk(widget.walkId);
    } catch (_) {
      // ignore — the list reflects true server state on reload either way
    }
    if (mounted) Navigator.of(context).pop(true); // signal the list to reload
  }

  Future<void> _speak(WalkEventItem e, String lang) async {
    await _tts.stop();
    if (_speakingSeq == e.seq) {
      setState(() => _speakingSeq = null);
      return;
    }
    if ((e.narration ?? '').trim().isEmpty) return;
    try {
      await _tts.setLanguage(_ttsTag[lang] ?? 'en-US');
    } catch (_) {}
    setState(() => _speakingSeq = e.seq);
    await _tts.speak(e.narration!);
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            _DetailHeader(
              title: widget.title,
              onDelete: widget.owner ? _confirmDelete : null,
              onShare: widget.owner ? (_sharing ? null : _share) : null,
              shared: _shared,
            ),
            Expanded(
              child: FutureBuilder<WalkDetail>(
                future: _future,
                builder: (context, snap) {
                  if (snap.connectionState != ConnectionState.done) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  if (snap.hasError || !snap.hasData) {
                    return _ErrorRetry(
                      message: l.historyLoadError,
                      retry: () => setState(() => _future = _fetch()),
                    );
                  }
                  final walk = snap.data!;
                  final c = context.colors;
                  return ListView(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
                    children: [
                      _WalkHeader(walk: walk, subtitle: widget.subtitle),
                      const SizedBox(height: 12),
                      _RouteMap(walk: walk, onTapEvent: (e) => _showEvent(walk, e)),
                      if ((walk.summary ?? '').trim().isNotEmpty) ...[
                        const SizedBox(height: 12),
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
                          decoration: BoxDecoration(
                            color: c.glassFill(0.05),
                            borderRadius: BorderRadius.circular(Radii.md),
                            border: Border.all(color: c.glassBorder),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Icon(Icons.auto_awesome_rounded, size: 18, color: c.primary),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(walk.summary!.trim(),
                                    style: body(context).copyWith(height: 1.5, color: c.textSecondary)),
                              ),
                            ],
                          ),
                        ),
                      ],
                      if (walk.events.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(l.walkHistoryEmptySubtitle,
                              style: body(context).copyWith(color: c.textSecondary)),
                        )
                      else ...[
                        const SizedBox(height: 8),
                        _SummaryBlock(walk: walk),
                        const SizedBox(height: 12),
                      ],
                      const SizedBox(height: 4),
                      for (final e in walk.events)
                        _EventTile(
                          event: e,
                          speaking: _speakingSeq == e.seq,
                          onReplay: () => _speak(e, walk.language),
                        ),
                    ],
                  );
                },
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Branded detail header: back + title + share + delete (no stock AppBar).
class _DetailHeader extends StatelessWidget {
  const _DetailHeader({required this.title, this.onDelete, this.onShare, this.shared = false});
  final String title;
  final VoidCallback? onDelete; // null → hide (not my walk)
  final VoidCallback? onShare;
  final bool shared;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(6, 6, 6, 8),
      child: Row(children: [
        IconButton(
          icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
          onPressed: () => Navigator.of(context).maybePop(),
          tooltip: MaterialLocalizations.of(context).backButtonTooltip,
        ),
        Expanded(child: Text(title, style: h2(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
        if (onShare != null)
          IconButton(
            icon: Icon(shared ? Icons.check_circle_rounded : Icons.ios_share_rounded,
                color: shared ? c.primary : c.textPrimary),
            tooltip: l.walkShare,
            onPressed: shared ? null : onShare,
          ),
        if (onDelete != null)
          IconButton(
            icon: Icon(Icons.delete_outline_rounded, color: c.textFaint),
            tooltip: l.deleteWalk,
            onPressed: onDelete,
          ),
      ]),
    );
  }
}

class _WalkHeader extends StatelessWidget {
  const _WalkHeader({required this.walk, this.subtitle});
  final WalkDetail walk;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final locale = Localizations.localeOf(context).toString();
    final when = DateFormat.yMMMMd(locale).add_Hm().format(walk.startedAt.toLocal());
    final bits = <String>[
      if (walk.city != null && walk.city!.isNotEmpty) walk.city!,
      l.placesCount(walk.objectCount),
    ];
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(when, style: caption(context)),
          const SizedBox(height: 4),
          Text(bits.join('  ·  '), style: titleS(context)),
          if (subtitle != null && subtitle!.trim().isNotEmpty) ...[
            const SizedBox(height: 2),
            Text(subtitle!, style: caption(context).copyWith(color: context.colors.primary, fontWeight: FontWeight.w700)),
          ],
        ],
      ),
    );
  }
}

// Interactive map of the walk: the real GPS route (polyline) with a numbered, TAPPABLE
// pin at each narrated stop (tap → its narration). Falls back to a line through the stops
// for walks recorded before the route feature; renders nothing if there are no coords.
class _RouteMap extends StatelessWidget {
  const _RouteMap({required this.walk, this.onTapEvent});
  final WalkDetail walk;
  final void Function(WalkEventItem)? onTapEvent;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final cs = Theme.of(context).colorScheme;

    final markers = walk.events.map((e) => LatLng(e.lat, e.lon)).toList();
    final route = walk.path.length >= 2
        ? walk.path.map((p) => LatLng(p[0], p[1])).toList()
        : markers; // fallback: connect the narrated stops
    final all = <LatLng>[...route, ...markers];
    if (all.isEmpty) return const SizedBox.shrink();

    // Draw the route as one or more segments: normal stretches solid in the brand color,
    // stretches walked while the tour was PAUSED as a grey dashed line. Old walks (no
    // per-point flag) and the marker fallback render as a single solid line.
    final polylines = walk.path.length >= 2
        ? trackPolylines(walk.path, liveColor: cs.primary)
        : (route.length >= 2
            ? [Polyline(points: route, strokeWidth: 4, color: cs.primary)]
            : <Polyline>[]);

    // Interactive: pan + pinch-zoom + double-tap (so users can explore the route). Tap a
    // pin to read its narration.
    const interaction = InteractionOptions(
      flags: InteractiveFlag.pinchZoom | InteractiveFlag.drag | InteractiveFlag.doubleTapZoom,
    );
    final options = all.length == 1
        ? MapOptions(initialCenter: all.first, initialZoom: 16, interactionOptions: interaction)
        : MapOptions(
            initialCameraFit: CameraFit.bounds(
              bounds: LatLngBounds.fromPoints(all),
              padding: const EdgeInsets.all(28),
              maxZoom: 17,
            ),
            interactionOptions: interaction,
          );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(18),
        child: SizedBox(
          height: 240,
          child: FlutterMap(
            options: options,
            children: [
              TileLayer(
                urlTemplate: MapConfig.tileUrl(dark: dark),
                subdomains: MapConfig.subdomains,
                userAgentPackageName: 'com.example.ai_audio_guide',
              ),
              if (polylines.isNotEmpty) PolylineLayer(polylines: polylines),
              MarkerLayer(
                markers: [
                  for (int i = 0; i < walk.events.length; i++)
                    Marker(
                      point: LatLng(walk.events[i].lat, walk.events[i].lon),
                      width: 30,
                      height: 30,
                      child: GestureDetector(
                        onTap: onTapEvent == null ? null : () => onTapEvent!(walk.events[i]),
                        child: _NumberedPin(n: i + 1, color: cs.primary, onPrimary: cs.onPrimary),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

}

class _EventTile extends StatelessWidget {
  const _EventTile({
    required this.event,
    required this.speaking,
    required this.onReplay,
  });
  final WalkEventItem event;
  final bool speaking;
  final VoidCallback onReplay;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final hasText = (event.narration ?? '').trim().isNotEmpty;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassModule(
        padding: const EdgeInsets.fromLTRB(16, 14, 10, 14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(event.name, style: titleS(context)),
                  const SizedBox(height: 2),
                  Text(event.category, style: caption(context)),
                  if (hasText) ...[
                    const SizedBox(height: 8),
                    Text(event.narration!,
                        style: body(context).copyWith(color: c.textSecondary, height: 1.45)),
                  ],
                ],
              ),
            ),
            if (hasText)
              IconButton(
                icon: Icon(speaking ? Icons.stop_circle_rounded : Icons.play_circle_fill_rounded, size: 30),
                onPressed: onReplay,
                color: c.primary,
              ),
          ],
        ),
      ),
    );
  }
}

class _ErrorRetry extends StatelessWidget {
  const _ErrorRetry({required this.message, required this.retry});
  final String message;
  final VoidCallback retry;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return Center(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 0, 24, 80),
        child: GlassModule(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.cloud_off_rounded, size: 30, color: context.colors.primary),
              const SizedBox(height: 12),
              Text(message, textAlign: TextAlign.center, style: body(context)),
              const SizedBox(height: 16),
              SizedBox(width: double.infinity, child: AppButton(l.retry, kind: AppBtnKind.secondary, onTap: retry)),
            ],
          ),
        ),
      ),
    );
  }
}

// Numbered, tappable map pin for a narrated stop.
class _NumberedPin extends StatelessWidget {
  const _NumberedPin({required this.n, required this.color, required this.onPrimary});
  final int n;
  final Color color;
  final Color onPrimary;
  @override
  Widget build(BuildContext context) {
    return Container(
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: [BoxShadow(color: color.withValues(alpha: .5), blurRadius: 8, spreadRadius: -2)],
      ),
      child: Text('$n', style: TextStyle(color: onPrimary, fontSize: 13, fontWeight: FontWeight.w800)),
    );
  }
}

// Expandable "tour summary": all the narrations woven into one comfortable read.
class _SummaryBlock extends StatefulWidget {
  const _SummaryBlock({required this.walk});
  final WalkDetail walk;
  @override
  State<_SummaryBlock> createState() => _SummaryBlockState();
}

class _SummaryBlockState extends State<_SummaryBlock> {
  bool _open = false;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final text = widget.walk.events
        .map((e) => (e.narration ?? '').trim())
        .where((s) => s.isNotEmpty)
        .join('\n\n');
    if (text.isEmpty) return const SizedBox.shrink();
    return GlassModule(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.menu_book_rounded, size: 18, color: c.primary),
          const SizedBox(width: 8),
          Text(l.walkSummary, style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
        ]),
        const SizedBox(height: 10),
        AnimatedSize(
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: BoxConstraints(maxHeight: _open ? double.infinity : 110),
            child: Text(text,
                overflow: _open ? TextOverflow.visible : TextOverflow.fade,
                style: body(context).copyWith(color: c.textSecondary, height: 1.5)),
          ),
        ),
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton(
            onPressed: () => setState(() => _open = !_open),
            style: TextButton.styleFrom(padding: EdgeInsets.zero, minimumSize: const Size(0, 32)),
            child: Text(_open ? l.walkCollapse : l.walkExpand,
                style: TextStyle(color: c.primary, fontWeight: FontWeight.w800)),
          ),
        ),
      ]),
    );
  }
}

// Bottom sheet shown when a map stop is tapped: what was narrated there.
class _EventSheet extends StatelessWidget {
  const _EventSheet({required this.event, required this.onReplay});
  final WalkEventItem event;
  final VoidCallback onReplay;
  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final text = (event.narration ?? '').trim();
    return RoundedSheet(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(context).padding.bottom + 20),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Center(child: Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16),
              decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)))),
          Text(event.name, style: h2(context)),
          const SizedBox(height: 2),
          Text(event.category, style: caption(context)),
          const SizedBox(height: 14),
          if (text.isNotEmpty)
            Flexible(
              child: SingleChildScrollView(
                child: Text(text, style: body(context).copyWith(color: c.textSecondary, height: 1.5)),
              ),
            )
          else
            Text(l.walkHistoryEmptySubtitle, style: body(context).copyWith(color: c.textSecondary)),
          const SizedBox(height: 16),
          if (text.isNotEmpty)
            AppButton(l.walkReplay, icon: Icons.play_arrow_rounded, onTap: () {
              onReplay();
              Navigator.pop(context);
            }),
        ]),
      ),
    );
  }
}
