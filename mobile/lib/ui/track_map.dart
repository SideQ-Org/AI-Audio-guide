// Shared GPS-track rendering: the polyline builder + a compact map widget, used by the live
// map (main.dart), the end-of-walk summary, the history-list preview and the walk detail — one
// source of truth so the track looks identical everywhere.
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../map_config.dart';

// Grey dashed styling for the stretch walked while the tour was PAUSED.
const _pausedColor = Color(0xFF9AA0A6);

/// Build polylines for a GPS route `[[lat, lon(, paused)], ...]`: a soft wide "glow" under a
/// crisp brand-coloured line, with paused stretches drawn as a grey dashed segment. Each edge
/// i->i+1 takes the paused flag of its destination point; consecutive same-flag edges join into
/// one line and the boundary vertex is shared so the route stays visually continuous.
///
/// `walked: true` switches to the navigator "already passed" style — the WHOLE track renders as
/// a grey dashed line with no glow (used on the LIVE map, where the green is reserved for the
/// route ahead). The completed-walk views (history / summary) keep the default green track.
List<Polyline> trackPolylines(
  List<List<double>> path, {
  required Color liveColor,
  double strokeWidth = 4,
  bool glow = true,
  bool walked = false,
}) {
  if (path.length < 2) return const [];
  bool pausedAt(int i) => path[i].length > 2 && path[i][2] == 1.0;
  final segments = <({List<LatLng> pts, bool paused})>[];
  var i = 0;
  while (i < path.length - 1) {
    final paused = pausedAt(i + 1);
    final pts = <LatLng>[LatLng(path[i][0], path[i][1])];
    var j = i;
    while (j < path.length - 1 && pausedAt(j + 1) == paused) {
      j++;
      pts.add(LatLng(path[j][0], path[j][1]));
    }
    segments.add((pts: pts, paused: paused));
    i = j;
  }
  final out = <Polyline>[];
  // Glow pass first (under everything), only for live stretches — skipped entirely in the
  // "walked/passed" navigator style (a flat grey dashed line, no glow).
  if (glow && !walked) {
    for (final s in segments) {
      if (!s.paused) {
        out.add(Polyline(
          points: s.pts,
          strokeWidth: strokeWidth * 2.4,
          color: liveColor.withValues(alpha: 0.22),
        ));
      }
    }
  }
  // Crisp pass on top. In `walked` mode every segment is the grey dashed "passed" style.
  for (final s in segments) {
    final passed = s.paused || walked;
    out.add(Polyline(
      points: s.pts,
      strokeWidth: strokeWidth,
      color: passed ? _pausedColor : liveColor,
      pattern: passed
          ? StrokePattern.dashed(segments: const [8, 6])
          : const StrokePattern.solid(),
    ));
  }
  return out;
}

/// A compact map that draws a GPS track fitted to its bounds. `interactive: false` (the default)
/// makes a non-pannable thumbnail for the history list / summary; the detail passes `true`.
class TrackMap extends StatelessWidget {
  const TrackMap({
    super.key,
    required this.path,
    this.height = 160,
    this.width,
    this.interactive = false,
    this.markers = const [],
    this.borderRadius = 18,
    this.strokeWidth = 4,
    this.padding = 12,
  });

  final List<List<double>> path;
  final double height;
  final double? width;
  final bool interactive;
  final List<Marker> markers;
  final double borderRadius;
  final double strokeWidth;
  final double padding;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final cs = Theme.of(context).colorScheme;
    final pts = [for (final p in path) if (p.length >= 2) LatLng(p[0], p[1])];
    final all = <LatLng>[...pts, for (final m in markers) m.point];
    if (all.isEmpty) return const SizedBox.shrink();

    final polylines = trackPolylines(path, liveColor: cs.primary, strokeWidth: strokeWidth);
    final interaction = interactive
        ? const InteractionOptions(
            flags: InteractiveFlag.pinchZoom |
                InteractiveFlag.drag |
                InteractiveFlag.doubleTapZoom,
          )
        : const InteractionOptions(flags: InteractiveFlag.none);
    final options = all.length == 1
        ? MapOptions(initialCenter: all.first, initialZoom: 16, interactionOptions: interaction)
        : MapOptions(
            initialCameraFit: CameraFit.bounds(
              bounds: LatLngBounds.fromPoints(all),
              padding: EdgeInsets.all(padding),
              maxZoom: 17,
            ),
            interactionOptions: interaction,
          );

    return ClipRRect(
      borderRadius: BorderRadius.circular(borderRadius),
      child: SizedBox(
        height: height,
        width: width,
        // A non-interactive thumbnail must let taps fall through to the parent (open detail).
        child: AbsorbPointer(
          absorbing: !interactive,
          child: FlutterMap(
            options: options,
            children: [
              MapConfig.buildTileLayer(
                dark: dark,
                userAgentPackageName: 'com.example.ai_audio_guide',
                panBuffer: 0,
                keepBuffer: 2,
              ),
              if (polylines.isNotEmpty) PolylineLayer(polylines: polylines),
              if (markers.isNotEmpty) MarkerLayer(markers: markers),
            ],
          ),
        ),
      ),
    );
  }
}
