import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:sensors_plus/sensors_plus.dart';

/// One fused compass reading.
class CompassReading {
  /// Direction the top of the phone points, degrees clockwise from magnetic north.
  final double headingDeg;

  /// True only when the phone is clearly being held up to view AND the reading is
  /// stable — i.e. the heading is a real *facing* we can trust for "left/right".
  /// When false the caller must fall back to the GPS course (gaze_confidence=low).
  final bool confident;

  const CompassReading(this.headingDeg, this.confident);
}

/// Tilt-compensated compass from the magnetometer + accelerometer (the device
/// has no single "heading" sensor since `flutter_compass` was dropped). Fuses the
/// two with the standard Android rotation-matrix method and only reports
/// `confident` when the phone is held up and the heading is steady — because a
/// wrong "on your right" is worse than staying neutral.
///
/// Magnetic vs true north: we report magnetic heading; the ~10° Moscow declination
/// can nudge the ahead/lateral boundary but won't flip a clear left vs right.
class CompassService {
  StreamSubscription<AccelerometerEvent>? _accSub;
  StreamSubscription<MagnetometerEvent>? _magSub;
  List<double>? _acc; // latest accelerometer sample (m/s^2)
  List<double>? _mag; // latest magnetometer sample (uT)
  final List<double> _recent = []; // recent azimuths, for a stability check
  final _ctrl = StreamController<CompassReading>.broadcast();

  Stream<CompassReading> get readings => _ctrl.stream;

  /// Magnetometer/accelerometer exist only on real mobile devices.
  static bool get supported =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);

  void start() {
    if (!supported || _magSub != null) return;
    _accSub = accelerometerEventStream().listen((e) => _acc = [e.x, e.y, e.z]);
    _magSub = magnetometerEventStream().listen((e) {
      _mag = [e.x, e.y, e.z];
      _emit();
    });
  }

  void _emit() {
    final a = _acc, m = _mag;
    if (a == null || m == null) return;
    final az = _azimuth(a, m);
    if (az == null) return; // degenerate geometry (phone aligned with field)
    _recent.add(az);
    if (_recent.length > 8) _recent.removeAt(0);
    final stable = _recent.length >= 5 && _spread(_recent) < 15.0;
    _ctrl.add(CompassReading(az, stable && _heldUp(a)));
  }

  /// Android getRotationMatrix + getOrientation: azimuth = atan2(Hy, My).
  static double? _azimuth(List<double> a, List<double> m) {
    final ax = a[0], ay = a[1], az = a[2];
    final ex = m[0], ey = m[1], ez = m[2];
    var hx = ey * az - ez * ay;
    var hy = ez * ax - ex * az;
    var hz = ex * ay - ey * ax;
    final normH = sqrt(hx * hx + hy * hy + hz * hz);
    if (normH < 0.1) return null;
    hx /= normH;
    hy /= normH;
    hz /= normH;
    final invA = 1.0 / sqrt(ax * ax + ay * ay + az * az);
    final nax = ax * invA, naz = az * invA;
    final my = naz * hx - nax * hz; // M = A x H, only My needed for azimuth
    final deg = atan2(hy, my) * 180.0 / pi;
    return (deg + 360.0) % 360.0;
  }

  /// "Held up to view": the screen is tilted well off horizontal (not lying flat
  /// in a pocket/on a table). flatness = |gravity·z| / |gravity|, 1 = flat.
  static bool _heldUp(List<double> a) {
    final norm = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
    if (norm < 1e-3) return false;
    return (a[2] / norm).abs() < 0.7; // tilted at least ~45° from flat
  }

  /// Angular spread of a small window of bearings (handles the 360/0 wrap).
  static double _spread(List<double> xs) {
    var mn = xs[0], mx = xs[0];
    for (final x in xs) {
      if (x < mn) mn = x;
      if (x > mx) mx = x;
    }
    final s = mx - mn;
    return s > 180.0 ? 360.0 - s : s;
  }

  void stop() {
    _accSub?.cancel();
    _magSub?.cancel();
    _accSub = null;
    _magSub = null;
    _acc = null;
    _mag = null;
    _recent.clear();
  }

  void dispose() {
    stop();
    _ctrl.close();
  }
}
