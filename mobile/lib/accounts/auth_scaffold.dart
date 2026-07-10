// Shared chrome for the sign-in / create-account screens: a gradient brand hero over
// a rounded "card" body, plus themed input helpers. Entirely theme-driven — colours
// come from Theme.of(context).colorScheme (whose primary IS the app accent), so both
// screens follow the system light/dark theme with no hardcoded palette.

import 'package:flutter/material.dart';

/// A deeper tail for the hero gradient, derived from the accent so it works in both
/// themes without pulling in main.dart's private brand constants.
Color _accentDeep(ColorScheme cs) =>
    Color.lerp(cs.primary, Colors.black, 0.22) ?? cs.primary;

/// Gradient hero (glyph + title + subtitle) above a rounded card holding [children].
class AuthScaffold extends StatelessWidget {
  const AuthScaffold({
    super.key,
    required this.title,
    required this.subtitle,
    required this.children,
    this.glyph = Icons.explore_rounded,
    this.isGate = false,
    this.busy = false,
  });

  final String title;
  final String subtitle;
  final List<Widget> children;
  final IconData glyph;

  /// Root mandatory gate: no back button.
  final bool isGate;

  /// Blocks input + dims the card while an auth call is in flight.
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final canPop = !isGate && Navigator.of(context).canPop();
    // When the keyboard is up, collapse the hero so both input fields stay in view.
    final compact = MediaQuery.of(context).viewInsets.bottom > 0;
    return Scaffold(
      body: AbsorbPointer(
        absorbing: busy,
        child: CustomScrollView(
          slivers: [
            SliverToBoxAdapter(
              child: _Hero(
                glyph: glyph,
                title: title,
                subtitle: subtitle,
                compact: compact,
                onBack: canPop ? () => Navigator.of(context).maybePop() : null,
              ),
            ),
            SliverFillRemaining(
              hasScrollBody: false,
              child: Transform.translate(
                // Overlap the hero's rounded base for a layered, card-on-hero look.
                offset: const Offset(0, -24),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
                  child: Container(
                    padding: const EdgeInsets.fromLTRB(20, 24, 20, 24),
                    decoration: BoxDecoration(
                      color: cs.surface,
                      borderRadius: BorderRadius.circular(24),
                      border: Border.all(color: cs.outlineVariant),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.10),
                          blurRadius: 24,
                          spreadRadius: -8,
                          offset: const Offset(0, 12),
                        ),
                      ],
                    ),
                    child: AnimatedOpacity(
                      opacity: busy ? 0.6 : 1,
                      duration: const Duration(milliseconds: 150),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        mainAxisSize: MainAxisSize.min,
                        children: children,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({
    required this.glyph,
    required this.title,
    required this.subtitle,
    required this.compact,
    this.onBack,
  });

  final IconData glyph;
  final String title;
  final String subtitle;

  /// Collapsed state (keyboard open): smaller glyph/title, no subtitle, tighter padding.
  final bool compact;
  final VoidCallback? onBack;

  static const _anim = Duration(milliseconds: 250);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final top = MediaQuery.of(context).padding.top;
    // AnimatedSize smoothly tweens the hero's height as padding/glyph/subtitle change.
    return AnimatedSize(
      duration: _anim,
      curve: Curves.easeOut,
      alignment: Alignment.topCenter,
      child: Container(
        width: double.infinity,
        padding: EdgeInsets.fromLTRB(24, top + (compact ? 6 : 16), 24, compact ? 16 : 44),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [cs.primary, _accentDeep(cs)],
          ),
          borderRadius: const BorderRadius.vertical(bottom: Radius.circular(30)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (onBack != null)
              SizedBox(
                height: 40,
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: IconButton(
                    onPressed: onBack,
                    icon: Icon(Icons.arrow_back_rounded, color: cs.onPrimary),
                    tooltip: MaterialLocalizations.of(context).backButtonTooltip,
                  ),
                ),
              ),
            SizedBox(height: compact ? 0 : 4),
            AnimatedContainer(
              duration: _anim,
              curve: Curves.easeOut,
              width: compact ? 44 : 60,
              height: compact ? 44 : 60,
              decoration: BoxDecoration(
                color: cs.onPrimary.withValues(alpha: 0.16),
                borderRadius: BorderRadius.circular(compact ? 14 : 18),
              ),
              child: Icon(glyph, color: cs.onPrimary, size: compact ? 24 : 32),
            ),
            SizedBox(height: compact ? 10 : 18),
            AnimatedDefaultTextStyle(
              duration: _anim,
              curve: Curves.easeOut,
              style: TextStyle(
                color: cs.onPrimary,
                fontSize: compact ? 22 : 26,
                fontWeight: FontWeight.w800,
                letterSpacing: -0.5,
              ),
              child: Text(title),
            ),
            // Subtitle only in the expanded state; its removal collapses the hero further.
            if (!compact) ...[
              const SizedBox(height: 6),
              Text(
                subtitle,
                style: TextStyle(
                  color: cs.onPrimary.withValues(alpha: 0.88),
                  fontSize: 14.5,
                  height: 1.4,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// A consistent, theme-aware input decoration (filled, rounded, prefix icon).
InputDecoration authDecoration(
  BuildContext context, {
  required String label,
  IconData? icon,
  Widget? suffixIcon,
}) {
  final cs = Theme.of(context).colorScheme;
  OutlineInputBorder border(Color c, [double w = 1]) => OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: c, width: w),
      );
  return InputDecoration(
    labelText: label,
    prefixIcon: icon == null ? null : Icon(icon),
    suffixIcon: suffixIcon,
    filled: true,
    fillColor: cs.onSurface.withValues(alpha: 0.04),
    border: border(cs.outlineVariant),
    enabledBorder: border(cs.outlineVariant),
    focusedBorder: border(cs.primary, 1.6),
    errorBorder: border(cs.error),
    focusedErrorBorder: border(cs.error, 1.6),
  );
}

/// Password field with a show/hide eye toggle, styled via [authDecoration].
class AuthPasswordField extends StatefulWidget {
  const AuthPasswordField({
    super.key,
    required this.controller,
    required this.label,
    this.icon = Icons.lock_outline_rounded,
    this.validator,
    this.textInputAction = TextInputAction.done,
    this.onFieldSubmitted,
  });

  final TextEditingController controller;
  final String label;
  final IconData icon;
  final String? Function(String?)? validator;
  final TextInputAction textInputAction;
  final void Function(String)? onFieldSubmitted;

  @override
  State<AuthPasswordField> createState() => _AuthPasswordFieldState();
}

class _AuthPasswordFieldState extends State<AuthPasswordField> {
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: widget.controller,
      obscureText: _obscure,
      autocorrect: false,
      enableSuggestions: false,
      textInputAction: widget.textInputAction,
      onFieldSubmitted: widget.onFieldSubmitted,
      validator: widget.validator,
      decoration: authDecoration(
        context,
        label: widget.label,
        icon: widget.icon,
        suffixIcon: IconButton(
          onPressed: () => setState(() => _obscure = !_obscure),
          icon: Icon(_obscure
              ? Icons.visibility_outlined
              : Icons.visibility_off_outlined),
        ),
      ),
    );
  }
}
