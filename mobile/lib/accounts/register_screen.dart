// Create-account screen (premium redesign — same glass family as login_screen.dart).
//
// Two phases in one screen:
//  1. FORM — avatar (optional), nickname, email, password + confirm, birthday (optional,
//     kept only for a future birthday greeting), and a "get Premium now" toggle.
//  2. OTP  — Supabase requires email confirmation, so after sign-up we collect the
//     6-digit code from the email and verify it. On success the session is established,
//     the chosen profile fields are saved, Premium is purchased (stub) if requested, and
//     the auth gate swaps to the app.

import 'dart:async';

import 'package:flutter/material.dart';

import '../billing/billing_service.dart';
import '../l10n/app_localizations.dart';
import '../ui/components.dart';
import '../ui/design.dart';
import '../ui/wheel_picker.dart';
import 'auth_errors.dart';
import 'auth_service.dart';
import 'auth_widgets.dart';
import 'validators.dart';

enum _Phase { form, otp }

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _auth = AuthService.instance;
  final _nick = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _code = TextEditingController();

  _Phase _phase = _Phase.form;
  String? _avatar; // data: URL, optional
  DateTime? _birthday; // optional
  String _addressForm = ''; // optional form of address: '' neutral | masculine | feminine
  bool _wantPremium = false;

  bool _busy = false;
  bool _valid = false;
  bool _error = false;
  Timer? _errTimer;

  @override
  void initState() {
    super.initState();
    for (final c in [_nick, _email, _password, _confirm, _code]) {
      c.addListener(_revalidate);
    }
  }

  void _revalidate() {
    final bool v;
    if (_phase == _Phase.form) {
      final e = _email.text.trim();
      v = _nick.text.trim().isNotEmpty &&
          e.contains('@') &&
          e.contains('.') &&
          _password.text.length >= 6 &&
          _confirm.text == _password.text;
    } else {
      v = _code.text.trim().length >= 6;
    }
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
    for (final c in [_nick, _email, _password, _confirm, _code]) {
      c.dispose();
    }
    super.dispose();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  void _flashError() {
    _errTimer?.cancel();
    setState(() => _error = true);
    _errTimer = Timer(const Duration(milliseconds: 850), () {
      if (mounted) setState(() => _error = false);
    });
  }

  String? get _birthdayIso => _birthday == null
      ? null
      : '${_birthday!.year.toString().padLeft(4, '0')}-'
          '${_birthday!.month.toString().padLeft(2, '0')}-'
          '${_birthday!.day.toString().padLeft(2, '0')}';

  Future<void> _pickAvatar() async {
    try {
      final url = await pickAvatarDataUrl();
      if (url != null && mounted) setState(() => _avatar = url);
    } catch (e) {
      _snack('$e');
    }
  }

  Future<void> _pickBirthday() async {
    final picked = await showAppDatePicker(
      context,
      initial: _birthday,
      title: AppLocalizations.of(context)!.birthdayLabel,
    );
    if (picked != null && mounted) setState(() => _birthday = picked);
  }

  // ── phase 1: create the account, then move to the code step ──
  Future<void> _submitForm() async {
    FocusScope.of(context).unfocus();
    final l = AppLocalizations.of(context)!;
    final err = validateEmail(l, _email.text.trim()) ??
        validatePassword(l, _password.text) ??
        validateConfirmPassword(l, _confirm.text, _password.text);
    if (_nick.text.trim().isEmpty || err != null) {
      _snack(err ?? l.nickLabel);
      _flashError();
      return;
    }
    setState(() => _busy = true);
    try {
      await _auth.signUpWithEmail(
        _email.text.trim(),
        _password.text,
        nick: _nick.text.trim(),
        birthdayIso: _birthdayIso,
        avatarUrl: _avatar,
        addressForm: _addressForm,
      );
      if (!mounted) return;
      // Confirmation-off projects sign the user straight in; otherwise go collect the code.
      if (_auth.isSignedIn) {
        await _finishSignedIn();
      } else {
        setState(() {
          _phase = _Phase.otp;
          _valid = false;
        });
      }
    } catch (e) {
      if (mounted) {
        _snack(friendlyAuthError(l, e));
        _flashError();
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ── phase 2: verify the emailed code ──
  Future<void> _verify() async {
    FocusScope.of(context).unfocus();
    final l = AppLocalizations.of(context)!;
    if (_code.text.trim().length < 6) {
      _snack(l.otpInvalid);
      _flashError();
      return;
    }
    setState(() => _busy = true);
    try {
      await _auth.verifySignupOtp(_email.text.trim(), _code.text);
      if (!mounted) return;
      await _finishSignedIn();
    } catch (e) {
      if (mounted) {
        // A rejected code is almost always wrong/expired — say so plainly.
        final msg = friendlyAuthError(l, e);
        _snack(msg == l.authErrorNetwork ? msg : l.otpInvalid);
        _flashError();
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// After a session exists: mirror the nick to the durable row and buy Premium (stub)
  /// if the user asked for it. The gate/_onAuth then swaps to the app.
  Future<void> _finishSignedIn() async {
    try {
      await _auth.updateProfile(nick: _nick.text.trim());
    } catch (_) {/* best-effort mirror */}
    if (_wantPremium) {
      try {
        await BillingService.instance.buy(kProductMonthly);
      } catch (_) {/* premium can be retried from the paywall later */}
    }
    if (mounted) Navigator.of(context).maybePop();
  }

  Future<void> _resend() async {
    final l = AppLocalizations.of(context)!;
    try {
      await _auth.resendSignupOtp(_email.text.trim());
      _snack(l.otpResent);
    } catch (e) {
      _snack(friendlyAuthError(l, e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
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
                child: AnimatedSwitcher(
                  duration: Motion.med,
                  switchInCurve: Motion.emphasized,
                  child: _phase == _Phase.form ? _buildForm(c) : _buildOtp(c),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _backButton(AppColors c, VoidCallback onTap) => Align(
        alignment: Alignment.centerLeft,
        child: IconButton(
          onPressed: onTap,
          icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
        ),
      );

  // ── FORM ──────────────────────────────────────────────────────────────────
  Widget _buildForm(AppColors c) {
    final l = AppLocalizations.of(context)!;
    final canPop = Navigator.of(context).canPop();
    return Column(
      key: const ValueKey('form'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (canPop) _backButton(c, () => Navigator.of(context).maybePop()),
        const SizedBox(height: 8),
        // welcome
        GlassModule(
          radius: Radii.xl,
          fill: authBlockFill(context),
          sheen: false,
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l.createAccount, style: h1(context).copyWith(fontSize: 26, letterSpacing: -0.6)),
              const SizedBox(height: 8),
              Text(l.registerSubtitle,
                  style: body(context).copyWith(
                      fontSize: 14, fontWeight: FontWeight.w600, height: 1.4, color: c.textSecondary)),
            ],
          ),
        ),
        const SizedBox(height: Gap.lg),
        // avatar
        Center(
          child: Column(children: [
            AuthAvatarPicker(dataUrl: _avatar, onTap: _pickAvatar),
            const SizedBox(height: 8),
            Text(l.avatarChoose, style: caption(context)),
          ]),
        ),
        const SizedBox(height: Gap.lg),
        // fields
        GlassModule(
          radius: Radii.xl,
          fill: authBlockFill(context),
          sheen: false,
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AuthGlassField(
                controller: _nick,
                icon: Icons.person_outline_rounded,
                hint: l.nickLabel,
                action: TextInputAction.next,
              ),
              const SizedBox(height: 10),
              AuthGlassField(
                controller: _email,
                icon: Icons.mail_outline_rounded,
                hint: l.emailLabel,
                keyboard: TextInputType.emailAddress,
                action: TextInputAction.next,
                autofillHints: const [AutofillHints.email],
              ),
              const SizedBox(height: 10),
              AuthGlassField(
                controller: _password,
                icon: Icons.lock_outline_rounded,
                hint: l.passwordLabel,
                obscure: true,
                action: TextInputAction.next,
                autofillHints: const [AutofillHints.newPassword],
              ),
              const SizedBox(height: 10),
              AuthGlassField(
                controller: _confirm,
                icon: Icons.lock_reset_rounded,
                hint: l.confirmPasswordLabel,
                obscure: true,
                action: TextInputAction.next,
                autofillHints: const [AutofillHints.newPassword],
              ),
              const SizedBox(height: 10),
              _birthdayField(c, l),
            ],
          ),
        ),
        const SizedBox(height: Gap.md),
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Text('Как к вам обращаться (необязательно)', style: label(context)),
        ),
        AddressFormPicker(
          value: _addressForm,
          onChanged: (v) => setState(() => _addressForm = v),
        ),
        const SizedBox(height: Gap.md),
        _premiumToggle(c, l),
        const SizedBox(height: Gap.lg),
        InteractiveAuthButton(
          label: l.createAccount,
          valid: _valid,
          busy: _busy,
          error: _error,
          onTap: _submitForm,
        ),
        const SizedBox(height: Gap.lg),
        Center(
          child: Pressable(
            onTap: () => Navigator.of(context).maybePop(),
            child: Text.rich(TextSpan(
              style: body(context).copyWith(fontSize: 13, fontWeight: FontWeight.w600, color: c.textSecondary),
              children: [
                TextSpan(text: '${l.haveAccount} '),
                TextSpan(text: l.signIn, style: TextStyle(color: c.primary, fontWeight: FontWeight.w800)),
              ],
            )),
          ),
        ),
      ],
    );
  }

  Widget _birthdayField(AppColors c, AppLocalizations l) {
    final label = _birthday == null
        ? l.birthdayOptional
        : MaterialLocalizations.of(context).formatMediumDate(_birthday!);
    return Pressable(
      onTap: _pickBirthday,
      child: Container(
        height: 54,
        padding: const EdgeInsets.symmetric(horizontal: 15),
        decoration: BoxDecoration(
          color: authWellFill(context),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: authWellBorder(context)),
        ),
        child: Row(children: [
          Icon(Icons.cake_outlined, size: 20, color: c.textFaint),
          const SizedBox(width: 11),
          Expanded(
            child: Text(label,
                style: body(context).copyWith(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                    color: _birthday == null ? c.textFaint : c.textPrimary)),
          ),
          Icon(Icons.chevron_right_rounded, size: 20, color: c.textFaint),
        ]),
      ),
    );
  }

  Widget _premiumToggle(AppColors c, AppLocalizations l) {
    return GlassModule(
      radius: Radii.xl,
      fill: authBlockFill(context),
      sheen: false,
      padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
      child: Row(children: [
        Container(
          width: 40, height: 40,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: LinearGradient(colors: [c.lime, c.primary]),
          ),
          child: Icon(Icons.workspace_premium_rounded, size: 22, color: c.onPrimary),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l.registerPremiumTitle, style: titleS(context).copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 2),
              Text(l.registerPremiumSub, style: caption(context)),
            ],
          ),
        ),
        Switch(
          value: _wantPremium,
          activeThumbColor: c.onPrimary,
          activeTrackColor: c.primary,
          onChanged: (v) => setState(() => _wantPremium = v),
        ),
      ]),
    );
  }

  // ── OTP ───────────────────────────────────────────────────────────────────
  Widget _buildOtp(AppColors c) {
    final l = AppLocalizations.of(context)!;
    return Column(
      key: const ValueKey('otp'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _backButton(c, () {
          setState(() {
            _phase = _Phase.form;
            _code.clear();
            _valid = false;
          });
        }),
        const SizedBox(height: 8),
        GlassModule(
          radius: Radii.xl,
          fill: authBlockFill(context),
          sheen: false,
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Icon(Icons.mark_email_read_outlined, size: 26, color: c.primary),
                const SizedBox(width: 10),
                Expanded(child: Text(l.otpTitle, style: h1(context).copyWith(fontSize: 24, letterSpacing: -0.5))),
              ]),
              const SizedBox(height: 10),
              Text(l.otpSentTo(_email.text.trim()),
                  style: body(context).copyWith(
                      fontSize: 14, fontWeight: FontWeight.w600, height: 1.4, color: c.textSecondary)),
            ],
          ),
        ),
        const SizedBox(height: Gap.lg),
        GlassModule(
          radius: Radii.xl,
          fill: authBlockFill(context),
          sheen: false,
          padding: const EdgeInsets.all(14),
          child: AuthGlassField(
            controller: _code,
            icon: Icons.pin_outlined,
            hint: l.otpCodeLabel,
            keyboard: TextInputType.number,
            action: TextInputAction.done,
            onSubmit: _verify,
            autofillHints: const [AutofillHints.oneTimeCode],
          ),
        ),
        const SizedBox(height: Gap.lg),
        InteractiveAuthButton(
          label: l.otpConfirm,
          valid: _valid,
          busy: _busy,
          error: _error,
          onTap: _verify,
        ),
        const SizedBox(height: Gap.md),
        Center(
          child: Pressable(
            onTap: _resend,
            child: Text(l.otpResend,
                style: body(context).copyWith(
                    fontSize: 13, fontWeight: FontWeight.w700, color: c.primary)),
          ),
        ),
      ],
    );
  }
}
