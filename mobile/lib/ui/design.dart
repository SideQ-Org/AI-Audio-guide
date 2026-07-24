// AI Audio Guide — design system (premium redesign).
// Single source of truth: tokens + theme + core liquid-glass components.
// Palette/typography follow design/DESIGN_SPEC.md (v3, approved).
import 'dart:io' show Platform;
import 'dart:ui' show ImageFilter;

import 'package:flutter/foundation.dart'
    show TargetPlatform, defaultTargetPlatform, kIsWeb;
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

// ── tokens ──────────────────────────────────────────────────────────────────
abstract final class Gap {
  static const double xs = 4,
      sm = 8,
      md = 12,
      lg = 16,
      xl = 20,
      xxl = 24,
      xxxl = 32;
}

abstract final class Radii {
  static const double sm = 12, md = 16, lg = 22, xl = 28, pill = 999;
}

abstract final class Motion {
  static const fast = Duration(milliseconds: 180);
  static const med = Duration(milliseconds: 320);
  static const slow = Duration(milliseconds: 520);
  static const curve = Curves.easeInOutCubic;
  static const emphasized = Cubic(0.2, 0.8, 0.2, 1.0);

  // ── unified interaction/entrance tokens (premium: short, easeOutCubic, no bounce) ──
  /// Press-down feedback on tappables (scale + dim).
  static const press = Duration(milliseconds: 120);

  /// Release/settle after a press.
  static const release = Duration(milliseconds: 200);

  /// One-shot entrance of a block/sheet content (fade + small slide).
  static const enter = Duration(milliseconds: 280);

  /// Per-item step for staggered list/section entrances.
  static const stagger = Duration(milliseconds: 35);

  /// Entrance/exit curve — decelerating, no overshoot.
  static const easeOut = Curves.easeOutCubic;
}

/// Accessibility: true when the OS asks to minimize animation
/// (`MediaQuery.disableAnimations`). Entrance/press animations should collapse
/// to their end state when this is set.
bool reduceMotion(BuildContext context) =>
    MediaQuery.maybeDisableAnimationsOf(context) ?? false;

// Icon set for the redesign. Uses Material's rounded family (already the app's idiom)
// — phosphor_flutter is incompatible with the current Flutter SDK (it extends the now
// `final` IconData class), so we map the same semantic names onto Material icons.
abstract final class AppIcons {
  static const home = Icons.home_rounded;
  static const community = Icons.groups_rounded;
  static const profile = Icons.person_rounded;
  static const settings = Icons.tune_rounded;
  static const history = Icons.history_rounded;
  static const moon = Icons.dark_mode_rounded;
  static const sun = Icons.light_mode_rounded;
  static const lightning = Icons.bolt_rounded;
  static const arrowRight = Icons.arrow_forward_rounded;
  static const caretRight = Icons.chevron_right_rounded;
  static const play = Icons.play_arrow_rounded;
  static const pause = Icons.pause_rounded;
  static const stop = Icons.stop_rounded;
  static const rewind = Icons.fast_rewind_rounded;
  static const dots = Icons.more_horiz_rounded;
  static const question = Icons.chat_bubble_outline_rounded;
  static const microphone = Icons.mic_rounded;
  static const speakerHigh = Icons.volume_up_rounded;
  static const speakerSlash = Icons.volume_off_rounded;
  static const plus = Icons.add_rounded;
  static const trophy = Icons.emoji_events_rounded;
  static const flag = Icons.flag_rounded;
  static const megaphone = Icons.campaign_rounded;
  static const mountains = Icons.landscape_rounded;
  static const list = Icons.list_rounded;
  static const bank = Icons.account_balance_rounded;
  static const globe = Icons.language_rounded;
  static const map = Icons.map_rounded;
  static const signOut = Icons.logout_rounded;
  static const userPlus = Icons.person_add_alt_1_rounded;
  static const usersThree = Icons.groups_rounded;
}

// Accent colours for map-object categories — part of the design system so map pins, object
// cards and the activated card all tint from ONE source. Muted, earthy tones that sit on the
// glass/mesh background in both themes (a hair desaturated vs. pure hues to stay on-palette).
abstract final class Cat {
  static const culture =
      Color(0xFFD59448); // warm ochre — museums, monuments, worship, art
  static const nature =
      Color(0xFF3C9E77); // sage green — parks, forests, terrain
  static const water =
      Color(0xFF4E8FD1); // muted blue — water, rivers, fountains
  static const civic =
      Color(0xFF7E93A8); // slate — civic, transport, structures
  static const everyday =
      Color(0xFF9AA6A0); // faint sage-slate — shops, cafes, plain buildings
}

// ── colours (ThemeExtension) ───────────────────────────────────────────────
@immutable
class AppColors extends ThemeExtension<AppColors> {
  final Color bgTop, bgBottom, meshA, meshB, meshC;
  final Color glass, glassBorder, glassHi, header, shadow;
  final Color primary, onPrimary, lime, sage, ok, err, glow;
  final Color textPrimary, textSecondary, textFaint;

  const AppColors({
    required this.bgTop,
    required this.bgBottom,
    required this.meshA,
    required this.meshB,
    required this.meshC,
    required this.glass,
    required this.glassBorder,
    required this.glassHi,
    required this.header,
    required this.shadow,
    required this.primary,
    required this.onPrimary,
    required this.lime,
    required this.sage,
    required this.ok,
    required this.err,
    required this.glow,
    required this.textPrimary,
    required this.textSecondary,
    required this.textFaint,
  });

  static const light = AppColors(
    bgTop: Color(0xFFF6F2EA), bgBottom: Color(0xFFEBE1D2),
    meshA: Color(0x66C4E26B), meshB: Color(0x808FA99B),
    meshC: Color(0x8CEBCDA5),
    // Warm translucent glass — reads as a distinct block over the frosted map but still
    // lets it show through.
    glass: Color(0x94EDE6D8), glassBorder: Color(0xC2FFFFFF),
    glassHi: Color(0x99FFFFFF),
    header: Color(0xFFEBE4D5), shadow: Color(0x382D3C32),
    primary: Color(0xFF35674E), onPrimary: Color(0xFFFFFFFF),
    lime: Color(0xFFC4E26B),
    sage: Color(0xFF8FA99B), ok: Color(0xFF3FA574), err: Color(0xFFDE6A60),
    glow: Color(0xFF35674E),
    textPrimary: Color(0xFF20241C), textSecondary: Color(0xFF5C6157),
    textFaint: Color(0xFF98A091),
  );

  static const dark = AppColors(
    bgTop: Color(0xFF413E5C),
    bgBottom: Color(0xFF161520), // top lighter (approved)
    meshA: Color(0x66B496E1), meshB: Color(0x4D96BEE1),
    meshC: Color(0x33B496E1),
    glass: Color(0x6646445C), glassBorder: Color(0x29FFFFFF),
    glassHi: Color(0x21FFFFFF),
    header: Color(0xFF343149), shadow: Color(0x80000000),
    primary: Color(0xFF7FC79A), onPrimary: Color(0xFF0E1F16),
    lime: Color(0xFFC4E26B),
    sage: Color(0xFF8FA99B), ok: Color(0xFF5FC08C), err: Color(0xFFE88379),
    glow: Color(0xFFB07CE8),
    textPrimary: Color(0xFFEFEDF6), textSecondary: Color(0xFFABA8BC),
    textFaint: Color(0xFF87839A),
  );

  Color get onGlass => textPrimary; // ink over glass
  Color glassFill(double a) =>
      textPrimary.withValues(alpha: a); // subtle tinted fill on glass

  @override
  AppColors copyWith() => this;

  @override
  AppColors lerp(covariant AppColors? other, double t) {
    if (other == null) return this;
    Color l(Color a, Color b) => Color.lerp(a, b, t)!;
    return AppColors(
      bgTop: l(bgTop, other.bgTop),
      bgBottom: l(bgBottom, other.bgBottom),
      meshA: l(meshA, other.meshA),
      meshB: l(meshB, other.meshB),
      meshC: l(meshC, other.meshC),
      glass: l(glass, other.glass),
      glassBorder: l(glassBorder, other.glassBorder),
      glassHi: l(glassHi, other.glassHi),
      header: l(header, other.header),
      shadow: l(shadow, other.shadow),
      primary: l(primary, other.primary),
      onPrimary: l(onPrimary, other.onPrimary),
      lime: l(lime, other.lime),
      sage: l(sage, other.sage),
      ok: l(ok, other.ok),
      err: l(err, other.err),
      glow: l(glow, other.glow),
      textPrimary: l(textPrimary, other.textPrimary),
      textSecondary: l(textSecondary, other.textSecondary),
      textFaint: l(textFaint, other.textFaint),
    );
  }
}

extension AppColorsX on BuildContext {
  AppColors get colors => Theme.of(this).extension<AppColors>()!;
}

bool get _simulatorSafeVisuals =>
    !kIsWeb &&
    defaultTargetPlatform == TargetPlatform.iOS &&
    Platform.environment.containsKey('SIMULATOR_DEVICE_NAME');

ThemeData buildTheme(Brightness b) {
  final c = b == Brightness.dark ? AppColors.dark : AppColors.light;
  final base = ThemeData(brightness: b, useMaterial3: true);
  final text = GoogleFonts.manropeTextTheme(base.textTheme)
      .apply(bodyColor: c.textPrimary, displayColor: c.textPrimary);
  return base.copyWith(
    scaffoldBackgroundColor: c.bgBottom,
    colorScheme: ColorScheme.fromSeed(seedColor: c.primary, brightness: b)
        .copyWith(surface: c.header, primary: c.primary),
    textTheme: text,
    extensions: [c],
    splashColor: c.primary.withValues(alpha: 0.08),
    highlightColor: c.primary.withValues(alpha: 0.05),
  );
}

// Manrope text helpers (bold, no serifs — approved).
TextStyle display(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 28,
    fontWeight: FontWeight.w800,
    height: 1.12,
    letterSpacing: -1,
    color: ctx.colors.textPrimary);
TextStyle h1(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 24,
    fontWeight: FontWeight.w800,
    letterSpacing: -.5,
    color: ctx.colors.textPrimary);
TextStyle h2(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 20,
    fontWeight: FontWeight.w800,
    letterSpacing: -.4,
    color: ctx.colors.textPrimary);
TextStyle titleS(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 16, fontWeight: FontWeight.w700, color: ctx.colors.textPrimary);
TextStyle body(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 15, fontWeight: FontWeight.w500, color: ctx.colors.textPrimary);
TextStyle label(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 12,
    fontWeight: FontWeight.w800,
    letterSpacing: .4,
    color: ctx.colors.textFaint);
TextStyle caption(BuildContext ctx) => GoogleFonts.manrope(
    fontSize: 12,
    fontWeight: FontWeight.w600,
    letterSpacing: .2,
    color: ctx.colors.textFaint);

// ── core components ────────────────────────────────────────────────────────

/// Full-screen soft mesh gradient background (not a flat colour).
class GradientBackground extends StatelessWidget {
  final Widget child;
  const GradientBackground({super.key, required this.child});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget blob(double size, Color col) => IgnorePointer(
          child: Container(
            width: size,
            height: size,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(colors: [col, col.withValues(alpha: 0)]),
            ),
          ),
        );
    return AnimatedContainer(
      duration: Motion.slow,
      curve: Motion.curve,
      decoration: BoxDecoration(
        gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [c.bgTop, c.bgBottom]),
      ),
      child: Stack(children: [
        Positioned(top: -80, left: -60, child: blob(300, c.meshA)),
        Positioned(top: -50, right: -80, child: blob(320, c.meshB)),
        Positioned(bottom: -100, right: -40, child: blob(360, c.meshC)),
        Positioned.fill(child: child),
      ]),
    );
  }
}

/// The signature liquid-glass panel: blur + translucent fill + hairline + soft
/// shadow + a subtle top-left highlight sheen. The background shows through.
class GlassModule extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final double radius;
  final double blur;

  /// Override the translucent fill (defaults to the theme `glass` token).
  final Color? fill;

  /// The diagonal top-left highlight. Turn OFF for flat, one-tone panels.
  final bool sheen;

  const GlassModule({
    super.key,
    required this.child,
    this.padding,
    this.radius = Radii.lg,
    this.blur = 24,
    this.fill,
    this.sheen = true,
  });

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final br = BorderRadius.circular(radius);
    final content = Container(
      decoration: BoxDecoration(
        color: fill ?? c.glass,
        borderRadius: br,
        border: Border.all(color: c.glassBorder, width: 1),
      ),
      child: Stack(
        children: [
          if (sheen)
            Positioned.fill(
              child: IgnorePointer(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    borderRadius: br,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [c.glassHi, Colors.transparent],
                      stops: const [0, 0.5],
                    ),
                  ),
                ),
              ),
            ),
          Padding(padding: padding ?? EdgeInsets.zero, child: child),
        ],
      ),
    );
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: br,
        boxShadow: _simulatorSafeVisuals
            ? null
            : [
                BoxShadow(
                  color: c.shadow,
                  blurRadius: 34,
                  spreadRadius: -14,
                  offset: const Offset(0, 18),
                ),
                BoxShadow(
                  color: c.shadow,
                  blurRadius: 8,
                  spreadRadius: -5,
                  offset: const Offset(0, 3),
                ),
              ],
      ),
      child: ClipRRect(
        borderRadius: br,
        child: _simulatorSafeVisuals
            ? content
            : BackdropFilter(
                filter: ImageFilter.blur(sigmaX: blur, sigmaY: blur),
                child: content,
              ),
      ),
    );
  }
}

enum AppBtnKind { primary, secondary, ghost }

class AppButton extends StatelessWidget {
  final String label;
  final IconData? icon;
  final IconData? trailing;
  final VoidCallback? onTap;
  final AppBtnKind kind;
  final double height;
  final Color? color; // overrides the primary fill (e.g. destructive red)
  const AppButton(this.label,
      {super.key,
      this.icon,
      this.trailing,
      this.onTap,
      this.kind = AppBtnKind.primary,
      this.height = 52,
      this.color});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final bool primary = kind == AppBtnKind.primary;
    final Color accent = color ?? c.primary;
    final Color bg = primary ? accent : c.glassFill(0.06);
    final Color fg = primary ? c.onPrimary : c.textPrimary;
    return Material(
      color: bg,
      borderRadius: BorderRadius.circular(Radii.md),
      elevation: 0,
      child: InkWell(
        borderRadius: BorderRadius.circular(Radii.md),
        onTap: onTap,
        child: Container(
          height: height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(Radii.md),
            border: primary ? null : Border.all(color: c.glassBorder, width: 1),
            boxShadow: primary
                ? [
                    BoxShadow(
                        color: accent.withValues(alpha: 0.45),
                        blurRadius: 22,
                        spreadRadius: -8,
                        offset: const Offset(0, 10))
                  ]
                : null,
          ),
          alignment: Alignment.center,
          child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (icon != null) ...[
                  Icon(icon, size: 18, color: fg),
                  const SizedBox(width: 8)
                ],
                Text(label,
                    style: GoogleFonts.manrope(
                        fontSize: 15, fontWeight: FontWeight.w800, color: fg)),
                if (trailing != null) ...[
                  const SizedBox(width: 8),
                  Icon(trailing, size: 18, color: fg)
                ],
              ]),
        ),
      ),
    );
  }
}

/// Floating pill tab bar (TG-style), 4 tabs. Icons only — no labels.
class FloatingTabBar extends StatelessWidget {
  final int index;
  final ValueChanged<int> onChanged;
  const FloatingTabBar(
      {super.key, required this.index, required this.onChanged});
  static const _icons = [
    AppIcons.home,
    AppIcons.community,
    AppIcons.profile,
    AppIcons.settings,
  ];

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final br = BorderRadius.circular(Radii.pill);
    final content = Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: c.glass,
        borderRadius: br,
        border: Border.all(color: c.glassBorder, width: 1),
      ),
      child: LayoutBuilder(
        builder: (context, cons) {
          final tabW = cons.maxWidth / _icons.length;
          return SizedBox(
            height: 48,
            child: Stack(
              children: [
                AnimatedPositioned(
                  duration: Motion.med,
                  curve: Motion.emphasized,
                  left: index * tabW,
                  top: 0,
                  bottom: 0,
                  width: tabW,
                  child: Container(
                    decoration: BoxDecoration(
                      color: c.primary,
                      borderRadius: BorderRadius.circular(Radii.pill),
                      boxShadow: _simulatorSafeVisuals
                          ? null
                          : [
                              BoxShadow(
                                color: c.primary.withValues(alpha: .4),
                                blurRadius: 16,
                                spreadRadius: -6,
                                offset: const Offset(0, 6),
                              ),
                            ],
                    ),
                  ),
                ),
                Row(
                  children: [
                    for (var i = 0; i < _icons.length; i++)
                      Expanded(
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onTap: () => onChanged(i),
                          child: Center(
                            child: AnimatedScale(
                              duration: Motion.med,
                              curve: Motion.emphasized,
                              scale: i == index ? 1.08 : 1,
                              child: Icon(
                                _icons[i],
                                size: 24,
                                color:
                                    i == index ? c.onPrimary : c.textSecondary,
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ],
            ),
          );
        },
      ),
    );
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: br,
        boxShadow: _simulatorSafeVisuals
            ? null
            : [
                BoxShadow(
                  color: c.shadow,
                  blurRadius: 34,
                  spreadRadius: -14,
                  offset: const Offset(0, 16),
                ),
              ],
      ),
      child: ClipRRect(
        borderRadius: br,
        child: _simulatorSafeVisuals
            ? content
            : BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 26, sigmaY: 26),
                child: content,
              ),
      ),
    );
  }
}
