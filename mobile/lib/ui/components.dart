// Presentational widgets for the premium redesign. Ported from
// design/flutter_wip/redesign_preview.dart, stripped of mock data: every widget
// takes plain data + callbacks and owns no app mechanics.
import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show HapticFeedback;
import 'package:google_fonts/google_fonts.dart';

import 'design.dart';

/// On-brand confirmation dialog (rounded, Manrope, symmetric buttons). Returns true if
/// the user confirms. Use instead of the stock [AlertDialog].
Future<bool> showBrandConfirm(
  BuildContext context, {
  required IconData icon,
  required String title,
  required String message,
  required String confirmLabel,
  required String cancelLabel,
  bool destructive = false,
}) async {
  final r = await showDialog<bool>(
    context: context,
    builder: (ctx) {
      final c = ctx.colors;
      final dark = Theme.of(ctx).brightness == Brightness.dark;
      final accent = destructive ? c.err : c.primary;
      return Dialog(
        backgroundColor: Colors.transparent,
        insetPadding: const EdgeInsets.symmetric(horizontal: 36),
        child: Container(
          padding: const EdgeInsets.fromLTRB(22, 24, 22, 20),
          decoration: BoxDecoration(
            color: dark ? c.header : Colors.white,
            borderRadius: BorderRadius.circular(Radii.xl),
            border: Border.all(color: c.glassBorder),
            boxShadow: [BoxShadow(color: c.shadow, blurRadius: 40, spreadRadius: -8, offset: const Offset(0, 20))],
          ),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: 56, height: 56, alignment: Alignment.center,
              decoration: BoxDecoration(shape: BoxShape.circle, color: accent.withValues(alpha: 0.14)),
              child: Icon(icon, size: 27, color: accent),
            ),
            const SizedBox(height: 16),
            Text(title, textAlign: TextAlign.center, style: h2(ctx)),
            const SizedBox(height: 8),
            Text(message, textAlign: TextAlign.center, style: body(ctx).copyWith(color: c.textSecondary, height: 1.4)),
            const SizedBox(height: 22),
            Row(children: [
              Expanded(child: AppButton(cancelLabel, kind: AppBtnKind.secondary, onTap: () => Navigator.pop(ctx, false))),
              const SizedBox(width: 10),
              Expanded(child: AppButton(confirmLabel, color: destructive ? c.err : null, onTap: () => Navigator.pop(ctx, true))),
            ]),
          ]),
        ),
      );
    },
  );
  return r ?? false;
}

/// A fully chrome-free [InputDecoration] for a `TextField` that sits INSIDE a styled well
/// (a `GlassModule` / pill / bordered `Container`). The app theme's `inputDecorationTheme`
/// paints a fill + a hairline enabled outline + a green FOCUSED outline; a field that only
/// sets `border: InputBorder.none` still inherits `enabledBorder`/`focusedBorder`, so the
/// theme draws a *second* rounded box inside the well — the "поле в поле" bug. This nulls
/// EVERY border slot and turns `filled` off, so the well owns the shape/fill and the field
/// carries only text. Use it for every in-well field (mirrors what `AuthGlassField` does).
InputDecoration bareInput({
  String? hintText,
  TextStyle? hintStyle,
  Widget? icon,
  Widget? suffixIcon,
  EdgeInsetsGeometry? contentPadding,
  bool isCollapsed = false,
  bool isDense = false,
}) =>
    InputDecoration(
      filled: false,
      isCollapsed: isCollapsed,
      isDense: isDense,
      hintText: hintText,
      hintStyle: hintStyle,
      icon: icon,
      suffixIcon: suffixIcon,
      contentPadding: contentPadding,
      border: InputBorder.none,
      enabledBorder: InputBorder.none,
      focusedBorder: InputBorder.none,
      disabledBorder: InputBorder.none,
      errorBorder: InputBorder.none,
      focusedErrorBorder: InputBorder.none,
    );

/// Content-SIZED bottom-sheet body — the single on-brand container for every modal sheet
/// (object cards, stat/achievement detail, community forms, summaries…). Same warm cream
/// gradient as the object cards, a flat fill (NO mesh blobs / blur, so it can't lag), rounded
/// top, and — crucially — it sizes to its CONTENT with a height ceiling, instead of stretching
/// to fill the sheet the way the old GradientBackground did (that left big empty areas below
/// short content). A long sheet caps at [maxHeightFactor] of the screen and scrolls.
///
/// [scrollable] (default): wrap the child in a SingleChildScrollView — for the common case of a
/// short `Column(mainAxisSize.min)`. Set it FALSE for a child that owns its own scrolling /
/// `Flexible`/`ListView` (a list or paginated sheet): the container's maxHeight then bounds that
/// child so its Flexible can flex, and the sheet still sizes to content up to the ceiling.
class CardSheet extends StatelessWidget {
  final Widget child;
  final bool scrollable;
  final double maxHeightFactor;
  const CardSheet({
    super.key,
    required this.child,
    this.scrollable = true,
    this.maxHeightFactor = 0.85,
  });

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * maxHeightFactor,
      ),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [c.bgTop, c.bgBottom],
        ),
        borderRadius: const BorderRadius.vertical(top: Radius.circular(Radii.xl)),
      ),
      child: scrollable ? SingleChildScrollView(child: child) : child,
    );
  }
}

/// Deprecated: kept as a thin alias so any un-migrated call site still renders content-sized.
/// Prefer [CardSheet]. Defaults to `scrollable: false` — its historical children own their
/// own layout (some carry a `Flexible`/`ListView`), so it must not impose a scroll wrapper.
@Deprecated('Use CardSheet — content-sized sheet with the object-card look')
class RoundedSheet extends StatelessWidget {
  final Widget child;
  const RoundedSheet({super.key, required this.child});
  @override
  Widget build(BuildContext context) => CardSheet(scrollable: false, child: child);
}

/// Wraps a tappable widget with a subtle scale-down press animation so it clearly
/// reads as pressable. Use anywhere a card/chip/icon is tapped.
class Pressable extends StatefulWidget {
  final Widget child;
  final VoidCallback? onTap;
  final double scale;
  const Pressable({super.key, required this.child, this.onTap, this.scale = 0.9});
  @override
  State<Pressable> createState() => _PressableState();
}

class _PressableState extends State<Pressable> {
  bool _down = false;
  void _set(bool v) {
    if (widget.onTap != null && _down != v) setState(() => _down = v);
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: widget.onTap,
      onTapDown: (_) => _set(true),
      onTapUp: (_) => _set(false),
      onTapCancel: () => _set(false),
      child: AnimatedScale(
        scale: _down ? widget.scale : 1,
        // A touch of overshoot on release makes the press clearly felt.
        duration: Duration(milliseconds: _down ? 110 : 240),
        curve: _down ? Curves.easeOut : Curves.easeOutBack,
        child: AnimatedOpacity(
          opacity: _down ? 0.82 : 1,
          duration: const Duration(milliseconds: 110),
          child: widget.child,
        ),
      ),
    );
  }
}

/// The login/register primary button from the mockup: fills with the brand gradient +
/// The big "sign in / create account" submit button — a live status indicator.
///
/// States:
///  • **neutral** (default) — muted grey gradient; nothing filled in yet.
///  • **ready** ([valid]) — green gradient with a glow, inviting the tap.
///  • **error** ([error] rising edge) — a single, sharp strike: the button snaps to a
///    bright red, the device buzzes (haptic) as the button jolts, one light band sweeps
///    across it, then it eases smoothly back to the resting look (green when the form is
///    valid). It is a one-shot flare, not a repeating pulse — the caller toggles [error]
///    true then false; only the false→true edge fires it.
class InteractiveAuthButton extends StatefulWidget {
  final String label;
  final bool valid;
  final bool busy;

  /// Rising edge (false→true) fires a single red flare + haptic. Steady state is ignored.
  final bool error;
  final VoidCallback? onTap;
  const InteractiveAuthButton({
    super.key,
    required this.label,
    required this.valid,
    this.busy = false,
    this.error = false,
    this.onTap,
  });

  @override
  State<InteractiveAuthButton> createState() => _InteractiveAuthButtonState();
}

class _InteractiveAuthButtonState extends State<InteractiveAuthButton>
    with SingleTickerProviderStateMixin {
  // One-shot flare: 0 = the red strike, 1 = fully settled back to the resting look.
  late final AnimationController _flare =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 760));

  static const _brightRed = Color(0xFFFF3B30);

  @override
  void initState() {
    super.initState();
    if (widget.error) _strike();
  }

  @override
  void didUpdateWidget(covariant InteractiveAuthButton old) {
    super.didUpdateWidget(old);
    // Only the false→true edge triggers a new flare (steady true is ignored).
    if (widget.error && !old.error) _strike();
  }

  void _strike() {
    HapticFeedback.heavyImpact(); // device buzz on the strike
    _flare.forward(from: 0);
  }

  @override
  void dispose() {
    _flare.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final br = BorderRadius.circular(Radii.md);

    // Resting look: green when ready, muted olive-grey otherwise.
    final restingGrad = widget.valid
        ? LinearGradient(colors: [c.primary, c.ok])
        : LinearGradient(colors: [c.textFaint, c.textSecondary]);
    final restingGlow = widget.valid ? c.primary : c.shadow;
    final restingGlowA = widget.valid ? 0.45 : 0.0;

    final content = widget.busy
        ? const SizedBox(
            width: 22, height: 22,
            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
        : Row(mainAxisSize: MainAxisSize.min, children: [
            Text(widget.label,
                style: GoogleFonts.manrope(
                    fontSize: 16, fontWeight: FontWeight.w800, color: Colors.white)),
            const SizedBox(width: 8),
            const Icon(AppIcons.arrowRight, size: 18, color: Colors.white),
          ]);

    return Pressable(
      onTap: widget.busy ? null : widget.onTap,
      child: AnimatedBuilder(
        animation: _flare,
        builder: (context, _) {
          final flaring = _flare.isAnimating;
          final t = _flare.value;
          // Colour: hold bright red briefly at the strike, then ease to the resting look.
          final ct = t < 0.26
              ? 0.0
              : Curves.easeInOut.transform(((t - 0.26) / 0.74).clamp(0.0, 1.0));
          final grad = flaring
              ? Gradient.lerp(
                  const LinearGradient(colors: [_brightRed, Color(0xFFD8352B)]),
                  restingGrad,
                  ct)!
              : restingGrad;
          final glow = flaring ? Color.lerp(_brightRed, restingGlow, ct)! : restingGlow;
          final glowA = flaring ? (0.6 * (1 - ct) + restingGlowA * ct) : restingGlowA;
          // A few damped horizontal jolts — the button "vibrating" with the device.
          final shake = flaring ? math.sin(t * math.pi * 5) * 7 * (1 - t) : 0.0;
          // A quick scale pop on the strike, settled by mid-flare.
          final pop = flaring ? 1 + 0.05 * math.sin(math.pi * (t / 0.5).clamp(0.0, 1.0)) : 1.0;

          final decoration = BoxDecoration(
            borderRadius: br,
            gradient: grad,
            boxShadow: glowA <= 0
                ? null
                : [
                    BoxShadow(
                      color: glow.withValues(alpha: glowA),
                      blurRadius: 26, spreadRadius: -6, offset: const Offset(0, 10),
                    ),
                  ],
          );

          final inner = ClipRRect(
            borderRadius: br,
            child: Stack(alignment: Alignment.center, children: [
              // Single light band sweeping across during the red strike (the "перелив").
              if (flaring)
                Positioned.fill(
                  child: IgnorePointer(
                    child: Opacity(
                      opacity: (1 - ct).clamp(0.0, 1.0),
                      child: LayoutBuilder(builder: (context, cons) {
                        final w = cons.maxWidth;
                        final p = (t / 0.6).clamp(0.0, 1.0); // one pass, done by ~60%
                        final dx = (p * 1.6 - 0.35) * w;
                        return Transform.translate(
                          offset: Offset(dx, 0),
                          child: Container(
                            width: w * 0.5,
                            decoration: BoxDecoration(
                              gradient: LinearGradient(colors: [
                                Colors.white.withValues(alpha: 0),
                                Colors.white.withValues(alpha: 0.4),
                                Colors.white.withValues(alpha: 0),
                              ]),
                            ),
                          ),
                        );
                      }),
                    ),
                  ),
                ),
              content,
            ]),
          );

          // While flaring we drive the colour per-frame (plain Container). At rest, an
          // AnimatedContainer tweens the green↔grey change smoothly as the form validates.
          final box = flaring
              ? Container(height: 56, alignment: Alignment.center, decoration: decoration, child: inner)
              : AnimatedContainer(
                  duration: Motion.med, curve: Motion.curve,
                  height: 56, alignment: Alignment.center, decoration: decoration, child: inner);

          return Transform.translate(
            offset: Offset(shake, 0),
            child: Transform.scale(scale: pop, child: box),
          );
        },
      ),
    );
  }
}

// ── swipe-to-start ───────────────────────────────────────────────────────────
/// iPhone-slide-to-unlock style start control. Fires [onComplete] on a full swipe
/// (or a tap, for accessibility). [label] is localized by the caller.
class SwipeToStart extends StatefulWidget {
  final String label;
  final VoidCallback onComplete;
  const SwipeToStart({super.key, required this.label, required this.onComplete});
  @override
  State<SwipeToStart> createState() => _SwipeToStartState();
}

class _SwipeToStartState extends State<SwipeToStart> with SingleTickerProviderStateMixin {
  double _dx = 0;
  // Repeating light sweep across the track — invites the user to drag the knob.
  late final AnimationController _shimmer =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 2000))..repeat();

  @override
  void dispose() {
    _shimmer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    const h = 84.0, knob = 72.0, pad = 6.0;
    final br = BorderRadius.circular(Radii.pill);
    return LayoutBuilder(builder: (context, cons) {
      final w = cons.maxWidth;
      final maxDx = (w - knob - pad * 2).clamp(0.0, double.infinity);
      final progress = maxDx <= 0 ? 0.0 : (_dx / maxDx).clamp(0.0, 1.0);
      return Container(
        height: h,
        decoration: BoxDecoration(
          borderRadius: br,
          gradient: LinearGradient(
            begin: Alignment.topLeft, end: Alignment.bottomRight,
            colors: [Color.lerp(c.primary, Colors.white, 0.10)!, c.primary, Color.lerp(c.primary, Colors.black, 0.36)!],
            stops: const [0, 0.5, 1],
          ),
          boxShadow: [BoxShadow(color: c.primary.withValues(alpha: .55), blurRadius: 42, spreadRadius: -6, offset: const Offset(0, 18))],
        ),
        child: ClipRRect(
          borderRadius: br,
          child: Stack(alignment: Alignment.center, children: [
            // Soft top sheen for depth.
            Positioned(
              top: 0, left: 0, right: 0, height: h * 0.5,
              child: IgnorePointer(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter, end: Alignment.bottomCenter,
                      colors: [Colors.white.withValues(alpha: 0.16), Colors.transparent],
                    ),
                  ),
                ),
              ),
            ),
            // Diagonal light band sweeping left → right.
            AnimatedBuilder(
              animation: _shimmer,
              builder: (_, __) => Positioned(
                left: -0.45 * w + _shimmer.value * (w * 1.45),
                top: 0, bottom: 0,
                child: IgnorePointer(
                  child: Transform.rotate(
                    angle: 0.35,
                    child: Container(
                      width: 120,
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          colors: [Colors.transparent, Colors.white.withValues(alpha: 0.24), Colors.transparent],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
            // Label fades out as the knob travels.
            Opacity(
              opacity: (1 - progress * 1.6).clamp(0.0, 1.0),
              child: Padding(
                padding: const EdgeInsets.only(left: knob * 0.5),
                child: Text(widget.label,
                    style: GoogleFonts.manrope(fontSize: 17, fontWeight: FontWeight.w800, letterSpacing: 0.3, color: Colors.white)),
              ),
            ),
            Positioned(
              left: pad + _dx, top: pad,
              child: GestureDetector(
                onHorizontalDragUpdate: (d) => setState(() => _dx = (_dx + d.delta.dx).clamp(0, maxDx)),
                onHorizontalDragEnd: (_) {
                  if (_dx > maxDx - 16) widget.onComplete();
                  setState(() => _dx = 0);
                },
                onTap: widget.onComplete,
                child: Container(
                  width: knob, height: knob,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: Colors.white,
                    boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.20), blurRadius: 10, offset: const Offset(0, 3))],
                  ),
                  child: Icon(AppIcons.arrowRight, size: 28, color: c.primary),
                ),
              ),
            ),
          ]),
        ),
      );
    });
  }
}

// ── status chip ────────────────────────────────────────────────────────────
/// The active-tour status pill: a coloured pulse dot + label, on glass.
class StatusChip extends StatefulWidget {
  final String label;
  final Color color;
  final bool active;
  const StatusChip({super.key, required this.label, required this.color, required this.active});
  @override
  State<StatusChip> createState() => _StatusChipState();
}

class _StatusChipState extends State<StatusChip> with SingleTickerProviderStateMixin {
  // Created eagerly in initState (not a lazy `late final`): build() only reads _c when
  // active, so a lazy field would first initialise inside dispose() — creating a ticker
  // against a deactivated element tree and crashing.
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))
      ..repeat(reverse: true);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return GlassModule(
      radius: Radii.pill,
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        FadeTransition(
          opacity: widget.active ? _c : const AlwaysStoppedAnimation(1.0),
          child: Container(width: 9, height: 9, decoration: BoxDecoration(shape: BoxShape.circle, color: widget.color)),
        ),
        const SizedBox(width: 9),
        AnimatedSwitcher(
          duration: Motion.fast,
          child: Text(widget.label,
              key: ValueKey(widget.label),
              style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800, color: c.textPrimary)),
        ),
      ]),
    );
  }
}

// ── player module ────────────────────────────────────────────────────────────
/// The active-tour bottom player: line-by-line subtitles + transport controls.
/// All actions are callbacks; disabled ones pass null.
class PlayerModule extends StatelessWidget {
  final String? title; // current place name (or null)
  final String? text; // current narration / reply
  final bool paused;
  final bool recording;
  final bool voice; // narration audio on
  final VoidCallback? onPause; // toggle pause/resume
  final VoidCallback? onStop;
  final VoidCallback? onAsk;
  final VoidCallback? onMic;
  final VoidCallback? onToggleVoice;
  const PlayerModule({
    super.key,
    this.title,
    this.text,
    required this.paused,
    required this.recording,
    required this.voice,
    this.onPause,
    this.onStop,
    this.onAsk,
    this.onMic,
    this.onToggleVoice,
  });

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget ctl(IconData i, {Color? color, VoidCallback? onTap, String? tip}) => IconButton(
          tooltip: tip,
          onPressed: onTap,
          icon: Icon(i, size: 24, color: onTap == null ? c.textFaint.withValues(alpha: .5) : (color ?? c.textSecondary)),
        );
    final hasText = (text != null && text!.isNotEmpty);
    return GlassModule(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (title != null && title!.isNotEmpty) ...[
          Text(title!, maxLines: 1, overflow: TextOverflow.ellipsis, style: h2(context)),
          const SizedBox(height: 8),
        ],
        ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.22),
          child: SingleChildScrollView(
            child: Text(
              hasText ? text! : '…',
              style: GoogleFonts.manrope(fontSize: 15, fontWeight: FontWeight.w500, height: 1.5, color: c.textPrimary),
            ),
          ),
        ),
        const SizedBox(height: 14),
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          ctl(recording ? AppIcons.stop : AppIcons.microphone,
              color: recording ? c.err : null, onTap: onMic, tip: 'mic'),
          ctl(AppIcons.stop, color: c.err, onTap: onStop, tip: 'stop'),
          // Big primary pause/resume.
          GestureDetector(
            onTap: onPause,
            child: Container(
              width: 60, height: 60,
              decoration: BoxDecoration(
                shape: BoxShape.circle, color: c.primary,
                boxShadow: [BoxShadow(color: c.primary.withValues(alpha: .45), blurRadius: 22, spreadRadius: -6, offset: const Offset(0, 10))],
              ),
              child: Icon(paused ? AppIcons.play : AppIcons.pause, size: 24, color: c.onPrimary),
            ),
          ),
          ctl(AppIcons.question, onTap: onAsk, tip: 'ask'),
          ctl(voice ? AppIcons.speakerHigh : AppIcons.speakerSlash, onTap: onToggleVoice, tip: 'mute'),
        ]),
      ]),
    );
  }
}

// ── focus picker ──────────────────────────────────────────────────────────
/// Icon chips that steer the tour's theme, all fitting one row (equal widths).
/// [items] are (code, icon); the empty code is the "auto / all" option.
class FocusPicker extends StatelessWidget {
  final String title;
  final List<({String code, IconData icon})> items;
  final String selected;
  final ValueChanged<String> onSelect;
  const FocusPicker({super.key, required this.title, required this.items, required this.selected, required this.onSelect});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return GlassModule(
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: .4, color: c.textPrimary)),
        const SizedBox(height: 12),
        Row(children: [
          for (var i = 0; i < items.length; i++) ...[
            if (i > 0) const SizedBox(width: 8),
            Expanded(
              child: Pressable(
                onTap: () => onSelect(items[i].code),
                child: AnimatedContainer(
                  duration: Motion.fast,
                  height: 46,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: items[i].code == selected ? c.primary : c.glassFill(0.06),
                    borderRadius: BorderRadius.circular(Radii.md),
                    border: items[i].code == selected ? null : Border.all(color: c.glassBorder),
                  ),
                  child: Icon(items[i].icon, size: 21, color: items[i].code == selected ? c.onPrimary : c.textSecondary),
                ),
              ),
            ),
          ],
        ]),
      ]),
    );
  }
}

// ── go premium card ──────────────────────────────────────────────────────────
class GoPremiumCard extends StatelessWidget {
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  const GoPremiumCard({super.key, required this.title, required this.subtitle, required this.onTap});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 15, 14, 15),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(Radii.lg),
          gradient: const LinearGradient(colors: [Color(0xFF181820), Color(0xFF2B2B33)]),
          boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: .35), blurRadius: 30, spreadRadius: -12, offset: const Offset(0, 14))],
        ),
        child: Row(children: [
          Icon(AppIcons.lightning, size: 22, color: c.lime),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: GoogleFonts.manrope(fontSize: 15, fontWeight: FontWeight.w800, color: Colors.white)),
              Text(subtitle, style: GoogleFonts.manrope(fontSize: 11, fontWeight: FontWeight.w600, color: Colors.white70)),
            ]),
          ),
          Container(width: 34, height: 34, decoration: BoxDecoration(shape: BoxShape.circle, color: c.lime), child: const Icon(AppIcons.caretRight, size: 16, color: Color(0xFF181820))),
        ]),
      ),
    );
  }
}

// ── settings building blocks ─────────────────────────────────────────────────
/// A tappable settings row inside a [GlassModule] section.
class SettingRow extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? value;
  final bool chevron;
  final Widget? trailing;
  final VoidCallback? onTap;
  const SettingRow({super.key, required this.icon, required this.title, this.value, this.chevron = false, this.trailing, this.onTap});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
        child: Row(children: [
          Icon(icon, size: 20, color: c.primary),
          const SizedBox(width: 14),
          Expanded(child: Text(title, style: GoogleFonts.manrope(fontSize: 14, fontWeight: FontWeight.w700, color: c.textPrimary))),
          if (value != null) Padding(padding: const EdgeInsets.only(right: 6), child: Text(value!, style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w700, color: c.textFaint))),
          if (trailing != null) trailing!,
          if ((value != null || chevron) && trailing == null) Icon(AppIcons.caretRight, size: 16, color: c.textFaint),
        ]),
      ),
    );
  }
}

class RowDivider extends StatelessWidget {
  const RowDivider({super.key});
  @override
  Widget build(BuildContext context) =>
      Divider(height: 1, thickness: 1, color: context.colors.glassBorder, indent: 16, endIndent: 16);
}

/// Small pill segmented control (generic value). Used for the theme switch.
class SegControl<T> extends StatelessWidget {
  final List<({T value, String label})> items;
  final T selected;
  final ValueChanged<T> onChanged;
  const SegControl({super.key, required this.items, required this.selected, required this.onChanged});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(Radii.pill), color: c.textFaint.withValues(alpha: .18)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        for (final it in items)
          GestureDetector(
            onTap: () => onChanged(it.value),
            child: AnimatedContainer(
              duration: Motion.fast,
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
              decoration: BoxDecoration(borderRadius: BorderRadius.circular(Radii.pill), color: it.value == selected ? c.primary : Colors.transparent),
              child: Text(it.label, style: GoogleFonts.manrope(fontSize: 11, fontWeight: FontWeight.w800, color: it.value == selected ? c.onPrimary : c.textSecondary)),
            ),
          ),
      ]),
    );
  }
}

/// Section header label above a group of settings.
class BlockLabel extends StatelessWidget {
  final String text;
  const BlockLabel(this.text, {super.key});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(2, 4, 2, 10),
        child: Text(text.toUpperCase(), style: label(context)),
      );
}

// ── profile building blocks ──────────────────────────────────────────────────
class XpBar extends StatelessWidget {
  final double value; // 0..1
  final double height;
  const XpBar({super.key, required this.value, this.height = 14});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final v = value.clamp(0.0, 1.0);
    return LayoutBuilder(builder: (context, cons) {
      final w = cons.maxWidth;
      // Fill has rounded ends; a tiny non-zero progress still shows a visible pill,
      // and a full value fills edge-to-edge.
      final fillW = v <= 0 ? 0.0 : (v >= 1 ? w : (v * w).clamp(height, w));
      return Stack(children: [
        Container(
          height: height,
          decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .22), borderRadius: BorderRadius.circular(Radii.pill)),
        ),
        AnimatedContainer(
          duration: Motion.med, curve: Motion.emphasized,
          height: height, width: fillW,
          decoration: BoxDecoration(
            gradient: LinearGradient(colors: [Color.lerp(c.primary, c.lime, .35)!, c.primary]),
            borderRadius: BorderRadius.circular(Radii.pill),
            boxShadow: v > 0 ? [BoxShadow(color: c.primary.withValues(alpha: .35), blurRadius: 8, spreadRadius: -2, offset: const Offset(0, 2))] : null,
          ),
        ),
      ]);
    });
  }
}

/// A premium marker — a small golden crown chip. Shown next to a paid user's avatar,
/// visible to them and to others (e.g. in the friends list).
class PremiumBadge extends StatelessWidget {
  final double size;
  const PremiumBadge({super.key, this.size = 26});
  @override
  Widget build(BuildContext context) {
    return Container(
      width: size, height: size, alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [Color(0xFFFFD873), Color(0xFFF2A93B)]),
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: [BoxShadow(color: const Color(0xFFF2A93B).withValues(alpha: 0.5), blurRadius: 8, spreadRadius: -1, offset: const Offset(0, 2))],
      ),
      child: Icon(Icons.workspace_premium_rounded, size: size * 0.6, color: Colors.white),
    );
  }
}

/// Profile avatar. Renders the user's [imageUrl] if given, else the bundled default
/// illustrated backpacker asset, else a drawn fallback — always inside the brand ring.
/// When [premium] is set, a crown badge is pinned to the bottom-right.
class TravelerAvatar extends StatelessWidget {
  final double size;
  final String? imageUrl; // user-chosen avatar (network); null => default asset
  final bool premium;
  const TravelerAvatar({super.key, this.size = 96, this.imageUrl, this.premium = false});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final Widget inner = _resolveImage(imageUrl, size);
    final avatar = Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [c.sage, c.primary]),
        border: Border.all(color: c.glassBorder, width: 3),
        boxShadow: [BoxShadow(color: c.primary.withValues(alpha: .35), blurRadius: 20, spreadRadius: -6, offset: const Offset(0, 8))],
      ),
      child: ClipOval(child: SizedBox.expand(child: inner)),
    );
    if (!premium) return avatar;
    return SizedBox(
      width: size, height: size,
      child: Stack(clipBehavior: Clip.none, children: [
        avatar,
        Positioned(right: -2, bottom: -2, child: PremiumBadge(size: size * 0.3)),
      ]),
    );
  }

  /// Resolve the avatar source: a `data:` URL → decoded bytes ([Image.memory]); a real
  /// URL → [Image.network]; null/blank or any decode error → the bundled default.
  static Widget _resolveImage(String? url, double size) {
    if (url == null || url.isEmpty) return _defaultAvatar(size);
    if (url.startsWith('data:')) {
      try {
        final bytes = base64Decode(url.substring(url.indexOf(',') + 1));
        return Image.memory(bytes, fit: BoxFit.cover, errorBuilder: (_, __, ___) => _defaultAvatar(size));
      } catch (_) {
        return _defaultAvatar(size);
      }
    }
    return Image.network(url, fit: BoxFit.cover, errorBuilder: (_, __, ___) => _defaultAvatar(size));
  }

  static Widget _defaultAvatar(double size) => Image.asset(
        'assets/avatar_traveler.png',
        fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => CustomPaint(painter: _BackpackerPainter(), size: Size.square(size)),
      );
}

class _BackpackerPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width;
    const cream = Color(0xFFF3ECDD);
    const tan = Color(0xFFD9B98C);
    const strap = Color(0xFFB98E5E);
    final p = Paint()..isAntiAlias = true;

    // Backpack body — a rounded pack peeking behind the left shoulder.
    p.color = tan;
    canvas.drawRRect(
      RRect.fromRectAndRadius(Rect.fromLTWH(s * 0.20, s * 0.42, s * 0.30, s * 0.34), Radius.circular(s * 0.09)),
      p,
    );
    // Pack pocket line.
    p
      ..color = strap
      ..style = PaintingStyle.stroke
      ..strokeWidth = s * 0.02;
    canvas.drawLine(Offset(s * 0.24, s * 0.60), Offset(s * 0.46, s * 0.60), p);
    p.style = PaintingStyle.fill;

    // Torso / shoulders (a capsule rising from the bottom).
    p.color = cream;
    canvas.drawRRect(
      RRect.fromRectAndRadius(Rect.fromLTWH(s * 0.30, s * 0.52, s * 0.40, s * 0.44), Radius.circular(s * 0.18)),
      p,
    );
    // Shoulder strap across the chest.
    p
      ..color = strap
      ..style = PaintingStyle.stroke
      ..strokeWidth = s * 0.045
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(Offset(s * 0.40, s * 0.55), Offset(s * 0.58, s * 0.86), p);
    p.style = PaintingStyle.fill;

    // Head.
    p.color = cream;
    canvas.drawCircle(Offset(s * 0.50, s * 0.40), s * 0.145, p);
    // Little cap.
    p.color = strap;
    final capRect = Rect.fromCircle(center: Offset(s * 0.50, s * 0.335), radius: s * 0.155);
    canvas.drawArc(capRect, 3.14159, 3.14159, false, p);
    canvas.drawRRect(
      RRect.fromRectAndRadius(Rect.fromLTWH(s * 0.345, s * 0.325, s * 0.31, s * 0.028), Radius.circular(s * 0.02)),
      p,
    );
  }

  @override
  bool shouldRepaint(covariant _BackpackerPainter oldDelegate) => false;
}

class AchievementBadge extends StatelessWidget {
  final String emoji;
  final bool locked;
  const AchievementBadge({super.key, required this.emoji, this.locked = false});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    if (locked) {
      // Muted: faint fill, dim emoji — reads clearly as "not yet earned".
      return Container(
        width: 48, height: 48, alignment: Alignment.center,
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(15), color: c.glassFill(0.03), border: Border.all(color: c.glassBorder.withValues(alpha: 0.5))),
        child: Opacity(opacity: 0.38, child: Text(emoji, style: const TextStyle(fontSize: 20))),
      );
    }
    // Earned: vibrant lime→primary tint + soft glow so it pops.
    return Container(
      width: 48, height: 48, alignment: Alignment.center,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(15),
        gradient: LinearGradient(
          begin: Alignment.topLeft, end: Alignment.bottomRight,
          colors: [c.lime.withValues(alpha: 0.28), c.primary.withValues(alpha: 0.20)],
        ),
        border: Border.all(color: c.primary.withValues(alpha: 0.45), width: 1.2),
        boxShadow: [BoxShadow(color: c.primary.withValues(alpha: 0.18), blurRadius: 12, spreadRadius: -4, offset: const Offset(0, 4))],
      ),
      child: Text(emoji, style: const TextStyle(fontSize: 22)),
    );
  }
}

// ── active-tour: Dynamic-Island status + control panel ───────────────────────

/// Status shown top-center during a tour, shaped like the Dynamic Island: a dark
/// blurred capsule with a pulsing state dot + label.
class StatusIsland extends StatefulWidget {
  const StatusIsland({super.key, required this.label, required this.color, required this.active});
  final String label;
  final Color color;
  final bool active;
  @override
  State<StatusIsland> createState() => _StatusIslandState();
}

class _StatusIslandState extends State<StatusIsland> with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))..repeat(reverse: true);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(Radii.pill),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 11),
          decoration: BoxDecoration(
            color: const Color(0xE6101012),
            borderRadius: BorderRadius.circular(Radii.pill),
            border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
            boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.35), blurRadius: 22, spreadRadius: -6, offset: const Offset(0, 10))],
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            FadeTransition(
              opacity: widget.active ? _c : const AlwaysStoppedAnimation(1.0),
              child: Container(
                width: 9, height: 9,
                decoration: BoxDecoration(
                  shape: BoxShape.circle, color: widget.color,
                  boxShadow: [BoxShadow(color: widget.color.withValues(alpha: 0.7), blurRadius: 8, spreadRadius: 1)],
                ),
              ),
            ),
            const SizedBox(width: 10),
            AnimatedSwitcher(
              duration: Motion.fast,
              child: Text(widget.label,
                  key: ValueKey(widget.label),
                  style: GoogleFonts.manrope(fontSize: 13.5, fontWeight: FontWeight.w800, color: Colors.white, letterSpacing: 0.2)),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Large round control button with a press animation. [primary] = accent gradient hero,
/// [danger] = red glyph, otherwise a neutral frosted circle.
class RoundCtlButton extends StatelessWidget {
  const RoundCtlButton({
    super.key,
    required this.icon,
    this.onTap,
    this.size = 54,
    this.primary = false,
    this.danger = false,
    this.tip,
  });
  final IconData icon;
  final VoidCallback? onTap;
  final double size;
  final bool primary;
  final bool danger;
  final String? tip;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final enabled = onTap != null;
    final fg = primary
        ? c.onPrimary
        : (danger ? c.err : (enabled ? c.textPrimary : c.textFaint.withValues(alpha: 0.5)));
    return Pressable(
      onTap: onTap,
      child: Container(
        width: size, height: size,
        alignment: Alignment.center,
        decoration: primary
            ? BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(colors: [c.primary, c.ok], begin: Alignment.topLeft, end: Alignment.bottomRight),
                boxShadow: [BoxShadow(color: c.primary.withValues(alpha: 0.45), blurRadius: 20, spreadRadius: -4, offset: const Offset(0, 8))],
              )
            : BoxDecoration(
                shape: BoxShape.circle,
                color: c.glassFill(0.06),
                border: Border.all(color: c.glassBorder),
              ),
        child: Icon(icon, size: size * 0.42, color: fg),
      ),
    );
  }
}

/// The highlighted microphone hero for barge-in ("ask by voice"). Straddles the top edge
/// of [TourControls]; pulses red while recording.
class MicButton extends StatefulWidget {
  const MicButton({super.key, required this.recording, this.onTap});
  final bool recording;
  final VoidCallback? onTap;
  @override
  State<MicButton> createState() => _MicButtonState();
}

class _MicButtonState extends State<MicButton> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse;
  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200));
    if (widget.recording) _pulse.repeat();
  }

  @override
  void didUpdateWidget(covariant MicButton old) {
    super.didUpdateWidget(old);
    if (widget.recording && !_pulse.isAnimating) {
      _pulse.repeat();
    } else if (!widget.recording && _pulse.isAnimating) {
      _pulse.stop();
      _pulse.value = 0;
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final rec = widget.recording;
    final base = rec ? c.err : c.primary;
    return Pressable(
      onTap: widget.onTap,
      child: SizedBox(
        width: 92, height: 92,
        child: Stack(alignment: Alignment.center, children: [
          if (rec)
            AnimatedBuilder(
              animation: _pulse,
              builder: (context, _) {
                final t = _pulse.value;
                return Container(
                  width: 68 + 24 * t, height: 68 + 24 * t,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: base.withValues(alpha: 0.28 * (1 - t)),
                  ),
                );
              },
            ),
          Container(
            width: 68, height: 68,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: LinearGradient(
                colors: [Color.lerp(base, Colors.white, 0.12)!, base],
                begin: Alignment.topLeft, end: Alignment.bottomRight,
              ),
              border: Border.all(color: context.colors.glassBorder, width: 3),
              boxShadow: [BoxShadow(color: base.withValues(alpha: 0.5), blurRadius: 22, spreadRadius: -2, offset: const Offset(0, 8))],
            ),
            child: Icon(rec ? AppIcons.stop : AppIcons.microphone, size: 30, color: c.onPrimary),
          ),
        ]),
      ),
    );
  }
}

/// The active-tour control panel: ~1/3 screen, white-glass (no gradient), a highlighted
/// mic hero on the top edge, scrollable subtitles, and a row of large round controls.
class TourControls extends StatelessWidget {
  const TourControls({
    super.key,
    this.title,
    this.text,
    required this.paused,
    required this.recording,
    required this.voice,
    this.onPause,
    this.onStop,
    this.onAsk,
    this.onMic,
    this.onToggleVoice,
    this.onHistory,
  });
  final String? title;
  final String? text;
  final bool paused;
  final bool recording;
  final bool voice;
  final VoidCallback? onPause, onStop, onAsk, onMic, onToggleVoice, onHistory;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final size = MediaQuery.of(context).size;
    final bottomPad = MediaQuery.of(context).padding.bottom;
    final panelH = (size.height * 0.34).clamp(240.0, 360.0);
    final hasText = text != null && text!.isNotEmpty;
    // Frosted glass: translucent enough to see the map move through it, strong blur
    // keeps the subtitles legible.
    final fill = dark ? const Color(0xB81B1E24) : const Color(0xC2FFFFFF);
    const radius = BorderRadius.vertical(top: Radius.circular(28));

    return SizedBox(
      height: panelH + 30,
      child: Stack(clipBehavior: Clip.none, alignment: Alignment.topCenter, children: [
        Positioned(
          top: 30, left: 0, right: 0, bottom: 0,
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: radius,
              boxShadow: [BoxShadow(color: c.shadow, blurRadius: 34, spreadRadius: -10, offset: const Offset(0, -6))],
            ),
            child: ClipRRect(
              borderRadius: radius,
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 34, sigmaY: 34),
                child: Container(
                  decoration: BoxDecoration(
                    color: fill,
                    borderRadius: radius,
                    border: Border(top: BorderSide(color: c.glassBorder, width: 1)),
                  ),
                  padding: EdgeInsets.fromLTRB(20, 46, 20, bottomPad + 14),
                  child: Column(mainAxisSize: MainAxisSize.max, children: [
                    if (title != null && title!.isNotEmpty) ...[
                      Text(title!, maxLines: 1, overflow: TextOverflow.ellipsis, style: h2(context)),
                      const SizedBox(height: 8),
                    ],
                    Expanded(
                      child: SingleChildScrollView(
                        child: Text(
                          hasText ? text! : '…',
                          style: GoogleFonts.manrope(fontSize: 15.5, fontWeight: FontWeight.w500, height: 1.5, color: c.textPrimary),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),
                    Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                      RoundCtlButton(icon: AppIcons.history, onTap: onHistory, size: 52),
                      RoundCtlButton(icon: voice ? AppIcons.speakerHigh : AppIcons.speakerSlash, onTap: onToggleVoice, size: 52),
                      RoundCtlButton(icon: paused ? AppIcons.play : AppIcons.pause, onTap: onPause, size: 66, primary: true),
                      RoundCtlButton(icon: AppIcons.question, onTap: onAsk, size: 52),
                      RoundCtlButton(icon: AppIcons.stop, onTap: onStop, size: 52, danger: true),
                    ]),
                  ]),
                ),
              ),
            ),
          ),
        ),
        MicButton(recording: recording, onTap: onMic),
      ]),
    );
  }
}
