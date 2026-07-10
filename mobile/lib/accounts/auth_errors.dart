// Turn raw Supabase/network auth errors into short, human-readable, localized messages
// instead of dumping exception strings at the user.

import 'dart:async';

import 'package:supabase_flutter/supabase_flutter.dart';

import '../l10n/app_localizations.dart';

String friendlyAuthError(AppLocalizations l, Object error) {
  // Note: no dart:io import — this file is compiled for web too. Socket/HTTP failures
  // surface as TimeoutException or via the message substring checks below.
  if (error is TimeoutException) {
    return l.authErrorNetwork;
  }
  final raw = error.toString().toLowerCase();
  if (raw.contains('socketexception') ||
      raw.contains('failed host lookup') ||
      raw.contains('clientexception') ||
      raw.contains('connection')) {
    return l.authErrorNetwork;
  }
  if (error is AuthException) {
    final code = error.code?.toLowerCase() ?? '';
    final msg = error.message.toLowerCase();
    bool has(String s) => code.contains(s) || msg.contains(s);

    if (has('invalid') && (has('credential') || has('login') || has('password'))) {
      return l.authErrorInvalidCredentials;
    }
    if (has('already') || has('exists') || has('registered')) {
      return l.authErrorEmailInUse;
    }
    if (has('weak') || (has('password') && has('should'))) {
      return l.authErrorWeakPassword;
    }
    if (has('rate') || has('too many')) {
      return l.authErrorRateLimited;
    }
    if (has('network') || has('connection') || has('timeout')) {
      return l.authErrorNetwork;
    }
    // A real, specific Supabase message is usually clearer than a generic fallback.
    return error.message;
  }
  return l.authErrorGeneric;
}
