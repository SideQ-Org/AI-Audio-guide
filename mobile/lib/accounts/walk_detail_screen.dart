// One saved walk: its narrated objects in order, each replayable offline via on-device
// TTS (the paid-tier "revisit your walk" hook). Loads /walks/{id} on open.

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:intl/intl.dart';
import 'package:latlong2/latlong.dart';

import '../l10n/app_localizations.dart';
import '../map_config.dart';
import 'api_client.dart';
import 'models.dart';

// code -> BCP-47 TTS tag (mirrors kLangs in main.dart; kept local to avoid coupling).
const _ttsTag = <String, String>{
  'en': 'en-US', 'ru': 'ru-RU', 'es': 'es-ES', 'fr': 'fr-FR',
  'de': 'de-DE', 'it': 'it-IT', 'pt': 'pt-BR', 'zh': 'zh-CN',
};

class WalkDetailScreen extends StatefulWidget {
  const WalkDetailScreen({super.key, required this.walkId, required this.title});
  final String walkId;
  final String title;

  @override
  State<WalkDetailScreen> createState() => _WalkDetailScreenState();
}

class _WalkDetailScreenState extends State<WalkDetailScreen> {
  final FlutterTts _tts = FlutterTts();
  late Future<WalkDetail> _future;
  int? _speakingSeq;

  @override
  void initState() {
    super.initState();
    _future = WalkApi.getWalk(widget.walkId);
    _tts.setCompletionHandler(() {
      if (mounted) setState(() => _speakingSeq = null);
    });
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
      appBar: AppBar(
        title: Text(widget.title),
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_outline),
            tooltip: l.deleteWalk,
            onPressed: _confirmDelete,
          ),
        ],
      ),
      body: FutureBuilder<WalkDetail>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError || !snap.hasData) {
            return _ErrorRetry(
              message: l.historyLoadError,
              retry: () => setState(() => _future = WalkApi.getWalk(widget.walkId)),
            );
          }
          final walk = snap.data!;
          final cs = Theme.of(context).colorScheme;
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
            children: [
              _WalkHeader(walk: walk),
              const SizedBox(height: 8),
              _RouteMap(walk: walk),
              if (walk.events.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(l.walkHistoryEmptySubtitle,
                      style: TextStyle(color: cs.onSurfaceVariant)),
                ),
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
    );
  }
}

class _WalkHeader extends StatelessWidget {
  const _WalkHeader({required this.walk});
  final WalkDetail walk;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final locale = Localizations.localeOf(context).toString();
    final when = DateFormat.yMMMMd(locale).add_Hm().format(walk.startedAt.toLocal());
    final bits = <String>[
      if (walk.city != null && walk.city!.isNotEmpty) walk.city!,
      l.placesCount(walk.objectCount),
    ];
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(when, style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
          const SizedBox(height: 4),
          Text(bits.join('  ·  '),
              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

// A small, non-interactive map of the walk: the real GPS route (polyline) with a pin at
// each narrated stop. Falls back to a line through the stops for walks recorded before
// the route feature; renders nothing if there are no coordinates at all.
class _RouteMap extends StatelessWidget {
  const _RouteMap({required this.walk});
  final WalkDetail walk;

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
        ? _segmentedPolylines(walk.path, cs.primary)
        : (route.length >= 2
            ? [Polyline(points: route, strokeWidth: 4, color: cs.primary)]
            : <Polyline>[]);

    final options = all.length == 1
        ? MapOptions(
            initialCenter: all.first,
            initialZoom: 16,
            interactionOptions: const InteractionOptions(flags: InteractiveFlag.none),
          )
        : MapOptions(
            initialCameraFit: CameraFit.bounds(
              bounds: LatLngBounds.fromPoints(all),
              padding: const EdgeInsets.all(28),
              maxZoom: 17,
            ),
            interactionOptions: const InteractionOptions(flags: InteractiveFlag.none),
          );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(18),
        child: SizedBox(
          height: 220,
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
                  for (final p in markers)
                    Marker(
                      point: p,
                      width: 26,
                      height: 26,
                      child: Icon(Icons.location_on, color: cs.primary, size: 26),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  // Grey dashed styling for the stretch walked while the tour was paused.
  static const _pausedColor = Color(0xFF9AA0A6);

  // Split the flagged path ([lat, lon, paused]) into contiguous runs of same paused
  // state and emit one Polyline per run. Each edge i->i+1 takes the paused flag of its
  // destination point; consecutive same-flag edges join into one line, and the boundary
  // vertex is shared between runs so the route stays visually continuous.
  static List<Polyline> _segmentedPolylines(List<List<double>> path, Color liveColor) {
    bool pausedAt(int i) => path[i].length > 2 && path[i][2] == 1.0;
    final out = <Polyline>[];
    var i = 0;
    while (i < path.length - 1) {
      final paused = pausedAt(i + 1);
      final pts = <LatLng>[LatLng(path[i][0], path[i][1])];
      var j = i;
      while (j < path.length - 1 && pausedAt(j + 1) == paused) {
        j++;
        pts.add(LatLng(path[j][0], path[j][1]));
      }
      out.add(Polyline(
        points: pts,
        strokeWidth: 4,
        color: paused ? _pausedColor : liveColor,
        pattern: paused
            ? StrokePattern.dashed(segments: const [8, 6])
            : const StrokePattern.solid(),
      ));
      i = j;
    }
    return out;
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
    final cs = Theme.of(context).colorScheme;
    final hasText = (event.narration ?? '').trim().isNotEmpty;
    return Card(
      elevation: 0,
      color: cs.surfaceContainerHighest.withValues(alpha: 0.4),
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(event.name,
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
                  Text(event.category,
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  if (hasText) ...[
                    const SizedBox(height: 6),
                    Text(event.narration!,
                        style: TextStyle(fontSize: 13, height: 1.4, color: cs.onSurface)),
                  ],
                ],
              ),
            ),
            if (hasText)
              IconButton(
                icon: Icon(speaking ? Icons.stop_circle_outlined : Icons.play_circle_outline),
                onPressed: onReplay,
                color: cs.primary,
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
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(message),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: retry, child: Text(l.retry)),
        ],
      ),
    );
  }
}
