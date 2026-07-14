// Shared building blocks for the sign-in / create-account screens, so both read as one
// screen family and match the Profile tab's block styling: clean white frosted glass
// cards (no sheen), with faintly-recessed input wells inside them.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:image_picker/image_picker.dart';

import '../ui/components.dart';
import '../ui/design.dart';

/// Pick a profile photo from the gallery and return it as a small `data:` URL, ready to
/// drop into user_metadata (`avatar_url`). Downscaled + JPEG-compressed hard so it stays
/// a few KB — it rides inside the auth token, so it must be tiny. Null if cancelled.
Future<String?> pickAvatarDataUrl() async {
  final x = await ImagePicker().pickImage(
    source: ImageSource.gallery,
    maxWidth: 160, maxHeight: 160, imageQuality: 55,
  );
  if (x == null) return null;
  final bytes = await x.readAsBytes();
  return 'data:image/jpeg;base64,${base64Encode(bytes)}';
}

/// Circular avatar with a small camera badge — tap to pick a new one. Shows [dataUrl]
/// (or the bundled default when null).
class AuthAvatarPicker extends StatelessWidget {
  const AuthAvatarPicker({super.key, required this.dataUrl, required this.onTap, this.size = 92});
  final String? dataUrl;
  final VoidCallback onTap;
  final double size;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: SizedBox(
        width: size, height: size,
        child: Stack(clipBehavior: Clip.none, children: [
          TravelerAvatar(size: size, imageUrl: dataUrl),
          Positioned(
            right: -2, bottom: -2,
            child: Container(
              width: size * 0.34, height: size * 0.34,
              decoration: BoxDecoration(
                color: c.primary,
                shape: BoxShape.circle,
                border: Border.all(color: c.glassBorder, width: 2),
                boxShadow: [BoxShadow(color: c.primary.withValues(alpha: .4), blurRadius: 10, spreadRadius: -2)],
              ),
              child: Icon(Icons.photo_camera_rounded, size: size * 0.18, color: c.onPrimary),
            ),
          ),
        ]),
      ),
    );
  }
}

/// Card colour — identical to the Profile blocks (`_blockFill` in ui/screens.dart):
/// clean white frosted glass in light, the theme glass tint in dark.
Color authBlockFill(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? context.colors.glass
        : const Color(0x8CFFFFFF);

/// Recessed fill for an input/pill sitting inside a white card — a faint tint so it
/// reads as inset without the washed white look.
Color authWellFill(BuildContext context) =>
    context.colors.glassFill(
        Theme.of(context).brightness == Brightness.dark ? 0.10 : 0.05);

/// Hairline that stays visible on both the white card and the dark glass.
Color authWellBorder(BuildContext context) =>
    context.colors.textFaint.withValues(alpha: 0.28);

/// Inline glass field (icon + borderless input), optional show/hide eye for passwords.
class AuthGlassField extends StatefulWidget {
  const AuthGlassField({
    super.key,
    required this.controller,
    required this.icon,
    required this.hint,
    this.obscure = false,
    this.keyboard = TextInputType.text,
    this.action = TextInputAction.next,
    this.onSubmit,
    this.autofillHints,
  });

  final TextEditingController controller;
  final IconData icon;
  final String hint;
  final bool obscure;
  final TextInputType keyboard;
  final TextInputAction action;
  final VoidCallback? onSubmit;
  final Iterable<String>? autofillHints;

  @override
  State<AuthGlassField> createState() => _AuthGlassFieldState();
}

class _AuthGlassFieldState extends State<AuthGlassField> {
  late bool _hidden = widget.obscure;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Container(
      height: 54,
      padding: const EdgeInsets.symmetric(horizontal: 15),
      decoration: BoxDecoration(
        color: authWellFill(context),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: authWellBorder(context)),
      ),
      child: Row(
        children: [
          Icon(widget.icon, size: 20, color: c.textFaint),
          const SizedBox(width: 11),
          Expanded(
            child: TextField(
              controller: widget.controller,
              obscureText: _hidden,
              autocorrect: false,
              enableSuggestions: !widget.obscure,
              // Obscure fields: force the full password keyboard. Without this iOS can fall
              // back to the numeric passcode keypad (only digits) for secure fields.
              keyboardType: widget.obscure ? TextInputType.visiblePassword : widget.keyboard,
              autofillHints: widget.autofillHints,
              textInputAction: widget.action,
              cursorColor: c.primary,
              onSubmitted: widget.onSubmit == null ? null : (_) => widget.onSubmit!(),
              style: body(context).copyWith(fontSize: 15, fontWeight: FontWeight.w600),
              // The well is drawn by the parent Container; the field itself must be totally
              // chrome-free — collapsed only nulls `border`, so the theme's focused/enabled
              // outlines (a green ring) still show unless every slot is set to none.
              decoration: InputDecoration(
                isDense: true,
                isCollapsed: true,
                filled: false,
                contentPadding: EdgeInsets.zero,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                errorBorder: InputBorder.none,
                disabledBorder: InputBorder.none,
                hintText: widget.hint,
                hintStyle: body(context).copyWith(
                    fontSize: 15, fontWeight: FontWeight.w600, color: c.textFaint),
              ),
            ),
          ),
          if (widget.obscure)
            GestureDetector(
              onTap: () => setState(() => _hidden = !_hidden),
              behavior: HitTestBehavior.opaque,
              child: Padding(
                padding: const EdgeInsets.only(left: 6),
                child: Icon(
                  _hidden ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                  size: 20,
                  color: c.textFaint,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// A "G Google" / "Apple" pill inside the social glass card — matches the mockup `.social`.
class AuthSocialButton extends StatelessWidget {
  const AuthSocialButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onTap,
    this.iconSize = 22,
  });

  final IconData icon;
  final String label;
  final double iconSize;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Pressable(
      onTap: onTap,
      child: Container(
        height: 52,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: authWellFill(context),
          borderRadius: BorderRadius.circular(15),
          border: Border.all(color: authWellBorder(context)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: iconSize, color: c.textPrimary),
            const SizedBox(width: 8),
            Text(label, style: titleS(context).copyWith(fontWeight: FontWeight.w800, fontSize: 14)),
          ],
        ),
      ),
    );
  }
}

/// "или почтой" hairline divider (`.orr` in the mockup).
class AuthOrLine extends StatelessWidget {
  const AuthOrLine({super.key, required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    Widget line() => Expanded(child: Container(height: 1, color: c.glassBorder));
    return Row(children: [
      line(),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Text(label.toUpperCase(),
            style: caption(context).copyWith(fontWeight: FontWeight.w700, color: c.textFaint)),
      ),
      line(),
    ]);
  }
}

/// A compact 3-way picker for the OPTIONAL grammatical form of address the guide uses for the
/// LISTENER ("ты прошёл/прошла"). NOT identity — only how narration phrases the 2nd person.
/// '' = neutral (default), 'masculine' = «ты прошёл», 'feminine' = «ты прошла». If unset the
/// guide addresses the walker neutrally.
class AddressFormPicker extends StatelessWidget {
  final String value;
  final ValueChanged<String> onChanged;
  const AddressFormPicker({super.key, required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    const opts = <(String, String, String?)>[
      ('', 'Нейтрально', null),
      ('masculine', 'Он', '«ты прошёл»'),
      ('feminine', 'Она', '«ты прошла»'),
    ];
    return Row(children: [
      for (final (val, title, sub) in opts) ...[
        Expanded(
          child: Pressable(
            scale: 0.96,
            onTap: () => onChanged(val),
            child: AnimatedContainer(
              duration: Motion.fast,
              padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 4),
              decoration: BoxDecoration(
                color: value == val ? c.primary : c.glassFill(0.05),
                borderRadius: BorderRadius.circular(Radii.md),
                border: Border.all(color: value == val ? c.primary : c.glassBorder),
              ),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text(title,
                    textAlign: TextAlign.center,
                    style: GoogleFonts.manrope(
                        fontSize: 14.5,
                        fontWeight: FontWeight.w700,
                        color: value == val ? c.onPrimary : c.textPrimary)),
                if (sub != null) ...[
                  const SizedBox(height: 2),
                  Text(sub,
                      textAlign: TextAlign.center,
                      style: GoogleFonts.manrope(
                          fontSize: 10.5,
                          fontWeight: FontWeight.w500,
                          color: value == val
                              ? c.onPrimary.withValues(alpha: 0.82)
                              : c.textFaint)),
                ],
              ]),
            ),
          ),
        ),
        if (val != 'feminine') const SizedBox(width: 8),
      ],
    ]);
  }
}
