import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:sensors_plus/sensors_plus.dart';

/// One fused compass reading.
class CompassReading {
  /// Direction the top of the phone points, degrees clockwise from magnetic north.
  /// Circular-EMA smoothed (~350 ms time constant) — steady enough for a map arrow.
  final double headingDeg;

  /// True when the reading is TRUSTWORTHY: the azimuth has been steady over the
  /// last ~2 s AND the magnetic-field magnitude looks like the ambient geomagnetic
  /// field (not a metal fence / tram / magnet case). Works in ANY phone attitude —
  /// tilt is compensated, so a phone held flat in the hand still yields a facing.
  /// When false the caller must fall back to the GPS course (gaze_confidence=low).
  final bool confident;

  const CompassReading(this.headingDeg, this.confident);
}

/// Tilt-compensated compass from the magnetometer + accelerometer (the device
/// has no single "heading" sensor since `flutter_compass` was dropped). Uses the
/// standard Android rotation-matrix method (H = M×A, azimuth from the horizontal
/// field components), which is valid at any roll/pitch — NOT only "held up".
///
/// Confidence is decided by SIGNAL QUALITY, not phone posture:
/// - stability: the raw azimuth's circular spread over the last ~2 s stays small
///   (a swinging/pocketed phone or magnetic disturbance jitters far above it);
/// - field magnitude: |B| within the plausible geomagnetic range (~25–65 µT) —
///   rebar, vehicles and magnets push it far outside.
///
/// Magnetic vs true north: we report MAGNETIC heading here. The consumer
/// (main.dart) auto-learns the offset to the true-north GPS course (local
/// declination + device bias) and subtracts it before using a compass heading, so
/// the compass and the GPS course share one reference frame (the ~10° Moscow
/// declination no longer shifts the ahead/lateral boundary between standing and walking).
class CompassService {
  StreamSubscription<AccelerometerEvent>? _accSub;
  StreamSubscription<MagnetometerEvent>? _magSub;
  List<double>? _grav; // low-pass-filtered accelerometer ≈ gravity (m/s^2)
  List<double>? _mag; // latest magnetometer sample (µT)
  double _fieldUt = 0; // latest |B| magnitude (µT), for the disturbance check
  // Circular EMA of the azimuth (sin/cos components — no 0/360 seam artifacts).
  double? _smSin, _smCos;
  DateTime? _lastAt;
  // Timestamped RAW azimuths of the last ~2 s, for the stability check.
  final List<(DateTime, double)> _recent = [];
  final _ctrl = StreamController<CompassReading>.broadcast();

  // Responsiveness ~300–500 ms: EMA time constant of the reported heading.
  static const double _tauMs = 350.0;
  static const int _windowMs = 2000; // stability lookback
  static const double _stableSpreadDeg = 14.0; // max deviation from the circular mean
  static const double _fieldMinUt = 25.0, _fieldMaxUt = 65.0; // plausible geomagnetic |B|
  static const double _gravLpAlpha = 0.25; // accelerometer→gravity low-pass (shake filter)

  Stream<CompassReading> get readings => _ctrl.stream;

  /// Magnetometer/accelerometer exist only on real mobile devices.
  static bool get supported =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);

  void start() {
    if (!supported || _magSub != null) return;
    // uiInterval (~60 ms) so the EMA has enough samples for a ~350 ms response;
    // runs only for the duration of an active tour (started/stopped with GPS).
    _accSub = accelerometerEventStream(samplingPeriod: SensorInterval.uiInterval)
        .listen((e) {
      final g = _grav;
      _grav = g == null
          ? [e.x, e.y, e.z]
          : [
              g[0] + _gravLpAlpha * (e.x - g[0]),
              g[1] + _gravLpAlpha * (e.y - g[1]),
              g[2] + _gravLpAlpha * (e.z - g[2]),
            ];
    });
    _magSub = magnetometerEventStream(samplingPeriod: SensorInterval.uiInterval)
        .listen((e) {
      _mag = [e.x, e.y, e.z];
      _fieldUt = sqrt(e.x * e.x + e.y * e.y + e.z * e.z);
      _emit();
    });
  }

  void _emit() {
    final a = _grav, m = _mag;
    if (a == null || m == null) return;
    final az = _azimuth(a, m);
    if (az == null) return; // degenerate geometry (field ~parallel to gravity)
    final now = DateTime.now();

    // Circular EMA (per-sample alpha derived from the actual dt, so the ~350 ms
    // responsiveness holds regardless of the sensor rate).
    final dtMs = _lastAt == null
        ? _tauMs
        : now.difference(_lastAt!).inMilliseconds.clamp(1, 2000).toDouble();
    _lastAt = now;
    final alpha = 1 - exp(-dtMs / _tauMs);
    final rad = az * pi / 180.0;
    if (_smSin == null || _smCos == null) {
      _smSin = sin(rad);
      _smCos = cos(rad);
    } else {
      _smSin = _smSin! + alpha * (sin(rad) - _smSin!);
      _smCos = _smCos! + alpha * (cos(rad) - _smCos!);
    }
    final heading = (atan2(_smSin!, _smCos!) * 180.0 / pi + 360.0) % 360.0;

    // Stability over the last ~2 s of RAW azimuths (the smoothed value would
    // flatter itself). Enough samples + enough time span + small circular spread.
    _recent.add((now, az));
    _recent.removeWhere(
        (r) => now.difference(r.$1).inMilliseconds > _windowMs);
    final spanMs = _recent.length < 2
        ? 0
        : now.difference(_recent.first.$1).inMilliseconds;
    final stable = _recent.length >= 6 &&
        spanMs >= 1000 &&
        _circularSpread(_recent) < _stableSpreadDeg;
    final fieldOk = _fieldUt >= _fieldMinUt && _fieldUt <= _fieldMaxUt;
    _ctrl.add(CompassReading(heading, stable && fieldOk));
  }

  /// Android getRotationMatrix + getOrientation: H = M×A (horizontal east axis),
  /// azimuth = atan2(Hy, My). Tilt-compensated by construction — `a` must be the
  /// gravity estimate (low-passed accelerometer), any phone attitude is fine.
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
    final normA = sqrt(ax * ax + ay * ay + az * az);
    if (normA < 1e-3) return null;
    final invA = 1.0 / normA;
    final nax = ax * invA, naz = az * invA;
    final my = naz * hx - nax * hz; // M = A x H, only My needed for azimuth
    final deg = atan2(hy, my) * 180.0 / pi;
    return (deg + 360.0) % 360.0;
  }

  /// Max angular deviation from the circular mean of a window of bearings
  /// (wrap-safe: works the same across the 0/360 seam).
  static double _circularSpread(List<(DateTime, double)> xs) {
    var ss = 0.0, cc = 0.0;
    for (final x in xs) {
      final r = x.$2 * pi / 180.0;
      ss += sin(r);
      cc += cos(r);
    }
    final mean = atan2(ss, cc) * 180.0 / pi;
    var mx = 0.0;
    for (final x in xs) {
      var d = (x.$2 - mean).abs() % 360.0;
      if (d > 180.0) d = 360.0 - d;
      if (d > mx) mx = d;
    }
    return mx;
  }

  void stop() {
    _accSub?.cancel();
    _magSub?.cancel();
    _accSub = null;
    _magSub = null;
    _grav = null;
    _mag = null;
    _fieldUt = 0;
    _smSin = null;
    _smCos = null;
    _lastAt = null;
    _recent.clear();
  }

  void dispose() {
    stop();
    _ctrl.close();
  }
}
