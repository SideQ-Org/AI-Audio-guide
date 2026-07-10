// Sign-in screen: Google / Apple (OAuth via Supabase) + email-password. Sign-in is
// mandatory — there is no guest mode. Pops itself once a session is established (when
// pushed as a route); as the root gate (isGate) the app swaps it for the map on auth.

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import 'auth_errors.dart';
import 'auth_scaffold.dart';
import 'auth_service.dart';
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
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _auth.addListener(_onAuth);
  }

  @override
  void dispose() {
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
    if (!_formKey.currentState!.validate()) return;
    await _guard(() => _auth.signInWithEmail(_email.text.trim(), _password.text));
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
    final showApple = Theme.of(context).platform == TargetPlatform.iOS ||
        Theme.of(context).platform == TargetPlatform.macOS;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return AuthScaffold(
      isGate: widget.isGate,
      busy: _busy,
      title: l.signIn,
      subtitle: l.loginSubtitle,
      children: [
        FilledButton.icon(
          onPressed: _busy ? null : () => _guard(_auth.signInWithGoogle),
          icon: const Icon(Icons.login),
          label: Text(l.continueWithGoogle),
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
        ),
        if (showApple) ...[
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _busy ? null : () => _guard(_auth.signInWithApple),
            icon: const Icon(Icons.apple),
            label: Text(l.continueWithApple),
            // Apple brand: near-black in light, white in dark — adapts to the theme.
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(48),
              backgroundColor: dark ? Colors.white : Colors.black,
              foregroundColor: dark ? Colors.black : Colors.white,
            ),
          ),
        ],
        const SizedBox(height: 20),
        _OrDivider(label: l.orSeparator),
        const SizedBox(height: 20),
        Form(
          key: _formKey,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextFormField(
                controller: _email,
                keyboardType: TextInputType.emailAddress,
                autocorrect: false,
                textInputAction: TextInputAction.next,
                validator: (v) => validateEmail(l, v),
                decoration: authDecoration(context,
                    label: l.emailLabel, icon: Icons.alternate_email_rounded),
              ),
              const SizedBox(height: 12),
              AuthPasswordField(
                controller: _password,
                label: l.passwordLabel,
                validator: (v) => validatePassword(l, v),
                onFieldSubmitted: (_) => _emailSignIn(),
              ),
            ],
          ),
        ),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton(
            onPressed: _busy ? null : _forgotPassword,
            child: Text(l.forgotPassword),
          ),
        ),
        const SizedBox(height: 4),
        FilledButton(
          onPressed: _busy ? null : _emailSignIn,
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
          child: _busy
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : Text(l.signIn),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: _busy ? null : _openRegister,
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
          child: Text(l.createAccount),
        ),
      ],
    );
  }
}

class _OrDivider extends StatelessWidget {
  const _OrDivider({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      const Expanded(child: Divider()),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Text(label, style: TextStyle(color: cs.onSurfaceVariant)),
      ),
      const Expanded(child: Divider()),
    ]);
  }
}
