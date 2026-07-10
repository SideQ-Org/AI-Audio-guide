// Separate account-creation screen, reached from the login screen's "Create account"
// button. Email + password + confirm. On success Supabase either signs the user
// straight in (if email confirmation is off) — the auth gate then swaps to the map — or
// asks them to confirm via email, which we surface as a message and pop back to login.

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import 'auth_errors.dart';
import 'auth_scaffold.dart';
import 'auth_service.dart';
import 'validators.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _auth = AuthService.instance;
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
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
    _confirm.dispose();
    super.dispose();
  }

  void _onAuth() {
    // Confirmation-off projects sign the user straight in → leave the register screen.
    if (mounted && _auth.isSignedIn) Navigator.of(context).maybePop();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (!_formKey.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await _auth.signUpWithEmail(_email.text.trim(), _password.text);
      if (!mounted) return;
      final l = AppLocalizations.of(context)!;
      if (_auth.isSignedIn) {
        Navigator.of(context).maybePop(); // straight in
      } else {
        // Email confirmation required: tell them, then return to the login screen.
        _snack(l.signUpCheckEmail);
        Navigator.of(context).maybePop();
      }
    } catch (e) {
      if (mounted) _snack(friendlyAuthError(AppLocalizations.of(context)!, e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context)!;
    return AuthScaffold(
      busy: _busy,
      glyph: Icons.person_add_alt_1_rounded,
      title: l.createAccount,
      subtitle: l.loginSubtitle,
      children: [
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
                textInputAction: TextInputAction.next,
                validator: (v) => validatePassword(l, v),
              ),
              const SizedBox(height: 12),
              AuthPasswordField(
                controller: _confirm,
                label: l.confirmPasswordLabel,
                icon: Icons.lock_reset_rounded,
                validator: (v) => validateConfirmPassword(l, v, _password.text),
                onFieldSubmitted: (_) => _submit(),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: _busy ? null : _submit,
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
          child: _busy
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : Text(l.createAccount),
        ),
      ],
    );
  }
}
