// Shared GPS-track rendering: the polyline builder + a compact map widget, used by the live
// map (main.dart), the end-of-walk summary, the history-list preview and the walk detail — one
// source of truth so the track looks identical everywhere.
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../map_config.dart';

// Grey dashed styling for the stretch walked while the tour was PAUSED.
const _pausedColor = Color(0xFF9AA0A6);

/// A tiny, tile-free route thumbnail (CustomPainter) for the history list — a crisp scaled
/// polyline on the card background. No map tiles and no glow, so it never smears at small sizes
/// the way a mini FlutterMap does; the aspect is preserved so the shape isn't distorted.
class TrackThumb extends StatelessWidget {
  const TrackThumb({
    super.key,
    required this.path,
    this.size = 62,
    this.strokeWidth = 2.2,
    this.padding = 9,
  });

  final List<List<double>> path;
  final double size;
  final double strokeWidth;
  final double padding;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _TrackThumbPainter(path, cs.primary, strokeWidth, padding)),
    );
  }
}

class _TrackThumbPainter extends CustomPainter {
  _TrackThumbPainter(this.path, this.color, this.strokeWidth, this.padding);

  final List<List<double>> path;
  final Color color;
  final double strokeWidth;
  final double padding;

  @override
  void paint(Canvas canvas, Size size) {
    final pts = [for (final p in path) if (p.length >= 2) p];
    if (pts.length < 2) return;
    // Equirectangular projection (x = lon·cos(latMid), y = -lat) so the shape keeps its real
    // aspect; then scale-to-fit the box uniformly (no horizontal stretch).
    final latMid = pts.map((p) => p[0]).reduce((a, b) => a + b) / pts.length;
    final k = math.cos(latMid * math.pi / 180).abs();
    final xs = [for (final p in pts) p[1] * k];
    final ys = [for (final p in pts) -p[0]];
    final minX = xs.reduce(math.min), maxX = xs.reduce(math.max);
    final minY = ys.reduce(math.min), maxY = ys.reduce(math.max);
    final spanX = maxX - minX, spanY = maxY - minY;
    final availW = size.width - 2 * padding, availH = size.height - 2 * padding;
    final sx = spanX > 1e-9 ? availW / spanX : double.infinity;
    final sy = spanY > 1e-9 ? availH / spanY : double.infinity;
    var s = math.min(sx, sy);
    if (!s.isFinite) s = 1.0;
    final offX = padding + (availW - spanX * s) / 2 - minX * s;
    final offY = padding + (availH - spanY * s) / 2 - minY * s;
    Offset proj(int i) => Offset(xs[i] * s + offX, ys[i] * s + offY);

    final live = Paint()
      ..color = color
      ..strokeWidth = strokeWidth
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final paused = Paint()
      ..color = _pausedColor
      ..strokeWidth = strokeWidth
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    for (var i = 0; i < pts.length - 1; i++) {
      final isPaused = pts[i + 1].length > 2 && pts[i + 1][2] == 1.0;
      canvas.drawLine(proj(i), proj(i + 1), isPaused ? paused : live);
    }
    // Start (faint) + end (solid) dots so direction reads at a glance.
    canvas.drawCircle(proj(0), strokeWidth * 1.1, Paint()..color = color.withValues(alpha: 0.45));
    canvas.drawCircle(proj(pts.length - 1), strokeWidth * 1.3, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_TrackThumbPainter old) =>
      !identical(old.path, path) || old.color != color;
}

/// Build polylines for a GPS route `[[lat, lon(, paused)], ...]`: a soft wide "glow" under a
/// crisp brand-coloured line, with paused stretches drawn as a grey dashed segment. Each edge
/// i->i+1 takes the paused flag of its destination point; consecutive same-flag edges join into
/// one line and the boundary vertex is shared so the route stays visually continuous.
List<Polyline> trackPolylines(
  List<List<double>> path, {
  required Color liveColor,
  double strokeWidth = 4,
  bool glow = true,
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
  // Glow pass first (under everything), only for live stretches.
  if (glow) {
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
  // Crisp pass on top.
  for (final s in segments) {
    out.add(Polyline(
      points: s.pts,
      strokeWidth: strokeWidth,
      color: s.paused ? _pausedColor : liveColor,
      pattern: s.paused
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
              TileLayer(
                urlTemplate: MapConfig.tileUrl(dark: dark),
                subdomains: MapConfig.subdomains,
                userAgentPackageName: 'com.example.ai_audio_guide',
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
