// Sign-in screen (premium redesign — matches design/mockup.html "login").
// Glass welcome card → Google/Apple in a glass card with an inline "create account"
// link → "or with email" divider → inline email/password fields → the big indicator
// button. Sign-in is mandatory (no guest mode): as the root gate the app swaps this for
// the map on auth; when pushed as a route it pops itself once a session exists.
//
// The submit button is a live status light (see [InteractiveAuthButton]): grey until the
// form is fillable, green when it is, and — on a rejected sign-in — it flares red, pulses
// and shimmers for a couple of seconds while a "forgot password?" link fades in.

import 'dart:async';

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../ui/components.dart';
import '../ui/design.dart';
import 'auth_errors.dart';
import 'auth_service.dart';
import 'auth_widgets.dart';
import 'register_screen.dart';
import 'validators.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, this.isGate = false});

  /// True when this is the app's mandatory sign-in gate (the root, not a pushed
  /// route): no back button and no way past it without authenticating.
  final bool isGate;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _auth = AuthService.instance;
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  bool _valid = false; // form is fillable → button goes green

  // Set once the first sign-in attempt is rejected: reveals "forgot password?" and
  // flares the button red. [_errTimer] clears the flare after a beat.
  bool _error = false;
  bool _wrongOnce = false;
  Timer? _errTimer;

  @override
  void initState() {
    super.initState();
    _auth.addListener(_onAuth);
    _email.addListener(_revalidate);
    _password.addListener(_revalidate);
  }

  void _revalidate() {
    final e = _email.text.trim();
    final v = e.contains('@') && e.contains('.') && _password.text.length >= 6;
    // Any edit also calms a red flare — the user is fixing it.
    if (v != _valid || _error) {
      setState(() {
        _valid = v;
        _error = false;
      });
    }
  }

  @override
  void dispose() {
    _errTimer?.cancel();
    _auth.removeListener(_onAuth);
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  void _onAuth() {
    // OAuth returns via deep link -> auth state flips -> close the screen.
    if (mounted && _auth.isSignedIn) Navigator.of(context).maybePop();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  /// Flare the button red + reveal "forgot password?", then relax after a couple seconds.
  void _flashError() {
    _errTimer?.cancel();
    setState(() {
      _error = true;
      _wrongOnce = true;
    });
    // Drop the trigger shortly after the one-shot flare so a repeat failure can re-fire it.
    _errTimer = Timer(const Duration(milliseconds: 850), () {
      if (mounted) setState(() => _error = false);
    });
  }

  Future<void> _guard(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted && _auth.isSignedIn) Navigator.of(context).maybePop();
    } catch (e) {
      if (mounted) _snack(friendlyAuthError(AppLocalizations.of(context)!, e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _emailSignIn() async {
    FocusScope.of(context).unfocus();
    final l = AppLocalizations.of(context)!;
    final emailErr = validateEmail(l, _email.text.trim());
    final passErr = validatePassword(l, _password.text);
    if (emailErr != null || passErr != null) {
      _snack(emailErr ?? passErr!);
      _flashError();
      return;
    }
    setState(() => _busy = true);
    try {
      await _auth.signInWithEmail(_email.text.trim(), _password.text);
      if (mounted && _auth.isSignedIn) Navigator.of(context).maybePop();
    } catch (e) {
      if (mounted) {
        // A rejected sign-in is almost always bad credentials — say so explicitly so the
        // user doesn't read a vague "something went wrong" as a connectivity problem.
        // Only a genuine network failure keeps its own (network) wording.
        final msg = friendlyAuthError(l, e);
        _snack(msg == l.authErrorNetwork ? msg : l.authErrorInvalidCredentials);
        _flashError();
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _openRegister() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const RegisterScreen()),
    );
  }

  Future<void> _forgotPassword() async {
    final l = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: _email.text.trim());
    final email = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l.resetPasswordTitle),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.emailAddress,
          autocorrect: false,
          decoration: InputDecoration(
            labelText: l.emailLabel,
            hintText: l.resetPasswordHint,
          ),
          onSubmitted: (v) => Navigator.pop(ctx, v.trim()),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: Text(l.resetPasswordSend),
          ),
        ],
      ),
    );
    if (email == null || email.isEmpty) return;
    if (validateEmail(l, email) != null) {
      _snack(l.emailInvalid);
      return;
    }
    try {
      await _auth.sendPasswordReset(email);
      _snack(l.resetEmailSent);
    } catch (e) {
      _snack(friendlyAuthError(l, e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    final c = context.colors;
    final showApple = Theme.of(context).platform == TargetPlatform.iOS ||
        Theme.of(context).platform == TargetPlatform.macOS;
    final canPop = !widget.isGate && Navigator.of(context).canPop();
    // Keyboard up → collapse the welcome card and tighten the gaps so the fields and
    // the submit button lift clear of the keyboard instead of hiding behind it.
    final compact = MediaQuery.of(context).viewInsets.bottom > 0;

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: AbsorbPointer(
            absorbing: _busy,
            child: AnimatedOpacity(
              opacity: _busy ? 0.7 : 1,
              duration: Motion.fast,
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (canPop)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: IconButton(
                          onPressed: () => Navigator.of(context).maybePop(),
                          icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
                        ),
                      ),
                    SizedBox(height: compact ? 0 : 8),
                    // ── welcome (collapses when the keyboard is up) ─────────────
                    AnimatedSize(
                      duration: Motion.med,
                      curve: Motion.emphasized,
                      alignment: Alignment.topCenter,
                      child: GlassModule(
                        radius: Radii.xl,
                        fill: authBlockFill(context),
                        sheen: false,
                        padding: EdgeInsets.fromLTRB(22, compact ? 14 : 22, 22, compact ? 14 : 22),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            AnimatedDefaultTextStyle(
                              duration: Motion.med,
                              curve: Motion.emphasized,
                              style: h1(context).copyWith(fontSize: compact ? 20 : 26, letterSpacing: -0.6),
                              child: Text(l.loginWelcomeTitle),
                            ),
                            if (!compact) ...[
                              const SizedBox(height: 8),
                              Text(
                                l.loginWelcomeSubtitle,
                                style: body(context).copyWith(
                                    fontSize: 14, fontWeight: FontWeight.w600, height: 1.4, color: c.textSecondary),
                              ),
                            ],
                          ],
                        ),
                      ),
                    ),
                    SizedBox(height: compact ? Gap.md : Gap.lg),
                    // ── social + create account ─────────────────────────────
                    GlassModule(
                      radius: Radii.xl,
                      fill: authBlockFill(context),
                      sheen: false,
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(children: [
                            Expanded(
                              child: AuthSocialButton(
                                icon: Icons.g_mobiledata_rounded,
                                iconSize: 30,
                                label: 'Google',
                                onTap: () => _guard(_auth.signInWithGoogle),
                              ),
                            ),
                            if (showApple) ...[
                              const SizedBox(width: 10),
                              Expanded(
                                child: AuthSocialButton(
                                  icon: Icons.apple,
                                  iconSize: 22,
                                  label: 'Apple',
                                  onTap: () => _guard(_auth.signInWithApple),
                                ),
                              ),
                            ],
                          ]),
                          const SizedBox(height: 14),
                          Center(
                            child: Pressable(
                              onTap: _openRegister,
                              child: Text.rich(
                                TextSpan(
                                  style: body(context).copyWith(
                                      fontSize: 13, fontWeight: FontWeight.w600, color: c.textSecondary),
                                  children: [
                                    TextSpan(text: '${l.loginNewHere} '),
                                    TextSpan(
                                      text: l.createAccount,
                                      style: TextStyle(color: c.primary, fontWeight: FontWeight.w800),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    SizedBox(height: compact ? Gap.sm : Gap.md),
                    AuthOrLine(label: l.orWithEmail),
                    SizedBox(height: compact ? Gap.sm : Gap.md),
                    // ── email + password ────────────────────────────────────
                    GlassModule(
                      radius: Radii.xl,
                      fill: authBlockFill(context),
                      sheen: false,
                      padding: const EdgeInsets.all(14),
                      // AutofillGroup: lets iOS treat email+password as one credential set, so
                      // the autofill accessory doesn't eat the first keystroke of the password.
                      child: AutofillGroup(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            AuthGlassField(
                              controller: _email,
                              icon: Icons.mail_outline_rounded,
                              hint: l.emailLabel,
                              keyboard: TextInputType.emailAddress,
                              action: TextInputAction.next,
                              autofillHints: const [AutofillHints.username, AutofillHints.email],
                            ),
                            const SizedBox(height: 10),
                            AuthGlassField(
                              controller: _password,
                              icon: Icons.lock_outline_rounded,
                              hint: l.passwordLabel,
                              obscure: true,
                              action: TextInputAction.done,
                              onSubmit: _emailSignIn,
                              autofillHints: const [AutofillHints.password],
                            ),
                          ],
                        ),
                      ),
                    ),
                    // ── forgot password (revealed after a rejected attempt) ──
                    AnimatedSize(
                      duration: Motion.med,
                      curve: Motion.emphasized,
                      child: _wrongOnce
                          ? Align(
                              alignment: Alignment.centerRight,
                              child: TextButton(
                                onPressed: _forgotPassword,
                                child: Text(l.forgotPassword,
                                    style: TextStyle(color: c.primary, fontWeight: FontWeight.w700)),
                              ),
                            )
                          : const SizedBox(width: double.infinity),
                    ),
                    SizedBox(height: _wrongOnce ? 4 : Gap.lg),
                    // ── submit indicator ────────────────────────────────────
                    InteractiveAuthButton(
                      label: l.signIn,
                      valid: _valid,
                      busy: _busy,
                      error: _error,
                      onTap: _emailSignIn,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
