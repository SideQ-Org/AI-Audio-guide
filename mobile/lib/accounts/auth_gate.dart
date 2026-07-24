// The root auth gate with a "tear" reveal. While [showLogin] is true it shows the
// login screen; when it flips to false (a session was established) the login screen is
// snapshotted and splits along its middle — the top half slides up, the bottom half
// slides down — uncovering the app underneath. Falls back to an instant swap if the
// snapshot can't be captured.

import 'dart:ui' as ui;

import 'package:flutter/foundation.dart'
    show TargetPlatform, defaultTargetPlatform, kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';

import 'login_screen.dart';

class AuthGate extends StatefulWidget {
  const AuthGate({super.key, required this.showLogin, required this.home});

  final bool showLogin;
  final Widget home;

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate>
    with SingleTickerProviderStateMixin {
  final GlobalKey _loginKey = GlobalKey();

  bool get _skipTearOnThisPlatform =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  // Created eagerly in initState (NOT a lazy `late final`): a lazy controller would be
  // built on first access, and if no tear ever runs, that first access is dispose() —
  // creating a ticker while the element is deactivated throws.
  late final AnimationController _tear;

  // The login snapshot being torn apart. While non-null we render [widget.home] with the
  // two split halves on top.
  ui.Image? _snap;

  @override
  void initState() {
    super.initState();
    _tear = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 720));
  }

  @override
  void didUpdateWidget(covariant AuthGate old) {
    super.didUpdateWidget(old);
    // Login → app edge: start the tear (snapshot the login we were just showing).
    // Android gets an instant swap: Samsung devices were intermittently landing on a
    // grey screen on the first login while the tear/reveal transition was trying to
    // uncover HomePage underneath.
    if (old.showLogin &&
        !widget.showLogin &&
        _snap == null &&
        !_skipTearOnThisPlatform) {
      _beginTear();
    }
  }

  void _beginTear() {
    final boundary =
        _loginKey.currentContext?.findRenderObject() as RenderRepaintBoundary?;
    // Skip (→ instant swap) if the login isn't mounted. Whether it has already been painted is
    // checked implicitly by the synchronous snapshot below; on some Samsung builds the
    // `debugNeedsPaint` getter itself trips a LateInitializationError during the first auth
    // transition, leaving the app stuck behind the spinner.
    if (boundary == null) return;
    final dpr = MediaQuery.maybeOf(context)?.devicePixelRatio ?? 2.0;
    final ui.Image img;
    try {
      // Synchronous grab — no async gap where the app flashes in before the overlay.
      img = boundary.toImageSync(pixelRatio: dpr);
    } catch (_) {
      return; // any failure → instant swap, never a red error frame
    }
    setState(() => _snap = img);
    _tear.forward(from: 0).whenComplete(() {
      if (!mounted) return;
      setState(() {
        _snap?.dispose();
        _snap = null;
      });
    });
  }

  @override
  void dispose() {
    _snap?.dispose();
    _tear.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Still showing login.
    if (widget.showLogin) {
      return RepaintBoundary(
          key: _loginKey, child: const LoginScreen(isGate: true));
    }

    // Signed in. Reveal the app; overlay the tearing halves while the animation runs.
    return Stack(children: [
      widget.home,
      if (_snap != null)
        Positioned.fill(
          child: IgnorePointer(
            child: AnimatedBuilder(
              animation: _tear,
              builder: (context, _) =>
                  CustomPaint(painter: _TearPainter(_snap!, _tear.value)),
            ),
          ),
        ),
    ]);
  }
}

class _TearPainter extends CustomPainter {
  _TearPainter(this.image, this.t);
  final ui.Image image;
  final double t;

  // Split roughly where the login's "или почтой" divider sits.
  static const double _splitFrac = 0.52;

  @override
  void paint(Canvas canvas, Size size) {
    final imgW = image.width.toDouble();
    final imgH = image.height.toDouble();
    final splitY = size.height * _splitFrac;
    final srcSplit = imgH * _splitFrac;
    final ease = Curves.easeInCubic.transform(t.clamp(0.0, 1.0));
    final up = ease * splitY; // top half rises fully off the top
    final down =
        ease * (size.height - splitY); // bottom half drops fully off the bottom
    final paint = Paint()..filterQuality = FilterQuality.low;

    // Top half.
    canvas.drawImageRect(
      image,
      Rect.fromLTWH(0, 0, imgW, srcSplit),
      Rect.fromLTWH(0, -up, size.width, splitY),
      paint,
    );
    // Bottom half.
    canvas.drawImageRect(
      image,
      Rect.fromLTWH(0, srcSplit, imgW, imgH - srcSplit),
      Rect.fromLTWH(0, splitY + down, size.width, size.height - splitY),
      paint,
    );
  }

  @override
  bool shouldRepaint(_TearPainter old) => old.t != t || old.image != image;
}
