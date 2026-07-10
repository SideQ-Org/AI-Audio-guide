// Form-field validators for the auth screens. Return null when valid, or a localized
// message to display inline under the field.

import '../l10n/app_localizations.dart';

// Deliberately permissive: catches obvious typos (missing @ / domain) without rejecting
// valid-but-unusual addresses. The backend / Supabase is the real authority.
final _emailRe = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');

const kMinPasswordLength = 6;

String? validateEmail(AppLocalizations l, String? value) {
  final v = value?.trim() ?? '';
  if (v.isEmpty) return l.emailRequired;
  if (!_emailRe.hasMatch(v)) return l.emailInvalid;
  return null;
}

String? validatePassword(AppLocalizations l, String? value) {
  final v = value ?? '';
  if (v.isEmpty) return l.passwordRequired;
  if (v.length < kMinPasswordLength) return l.passwordTooShort(kMinPasswordLength);
  return null;
}

String? validateConfirmPassword(AppLocalizations l, String? value, String password) {
  final v = value ?? '';
  if (v.isEmpty) return l.passwordRequired;
  if (v != password) return l.passwordsDontMatch;
  return null;
}
