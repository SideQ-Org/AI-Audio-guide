// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Telling you about places around you';

  @override
  String get bgNotifPaused => 'Tour paused';

  @override
  String get bgPause => 'Pause';

  @override
  String get bgResume => 'Resume';

  @override
  String get connect => 'Connect';

  @override
  String get disconnect => 'Disconnect';

  @override
  String get startWalk => 'Walk';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Stop';

  @override
  String get ask => 'Ask';

  @override
  String get askHint => 'Ask the guide… (e.g. skip shops)';

  @override
  String get micAsk => 'Ask by voice';

  @override
  String get micStop => 'Stop and send';

  @override
  String get clearFeed => 'Clear feed';

  @override
  String get voiceOn => 'Narration on';

  @override
  String get voiceOff => 'Narration off';

  @override
  String get language => 'Language';

  @override
  String get settings => 'Settings';

  @override
  String get history => 'History';

  @override
  String get simulatedWalk => 'Simulated walk (demo)';

  @override
  String get compassNorth => 'Orient north';

  @override
  String get emptyHint =>
      'Tap “Walk”.\nThe guide will tell you about places around you.';

  @override
  String get following => 'Following you';

  @override
  String get freeBrowse => 'Free browse — tap to follow';

  @override
  String get appearance => 'Appearance';

  @override
  String get themeSystem => 'System';

  @override
  String get themeLight => 'Light';

  @override
  String get themeDark => 'Dark';

  @override
  String get themeTopic => 'Tour theme';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'History';

  @override
  String get themeArchitecture => 'Architecture';

  @override
  String get themePeople => 'People';

  @override
  String get themeCulture => 'Culture';

  @override
  String get themeLegends => 'Legends';

  @override
  String get route => 'Route';

  @override
  String get walkHistory => 'Walk history';

  @override
  String get walkHistoryEmptyTitle => 'No walks yet';

  @override
  String get walkHistoryEmptySubtitle =>
      'Your past walks will appear here once accounts arrive.';

  @override
  String get nearbyHint => 'Walk closer and the guide will tell you about it.';

  @override
  String get zoomIn => 'Zoom in';

  @override
  String get zoomOut => 'Zoom out';

  @override
  String get chipReconnecting => 'reconnecting…';

  @override
  String get chipNotConnected => 'not connected';

  @override
  String get chipSpeaking => 'speaking';

  @override
  String get chipScoring => 'analysing';

  @override
  String get chipNarrating => 'narrating';

  @override
  String get chipSwitching => 'switching';

  @override
  String get chipListening => 'listening';

  @override
  String get chipAnswering => 'answering';

  @override
  String get chipExpanding => 'expanding radius';

  @override
  String get chipReady => 'ready';

  @override
  String get chipError => 'source unavailable';

  @override
  String get chipOffline => 'offline';

  @override
  String metaConnectionLost(int seconds) {
    return 'Connection lost, reconnecting in ${seconds}s…';
  }

  @override
  String get metaGeoDisabled => 'Location is turned off in system settings';

  @override
  String get metaGeoNoPermission => 'No location permission';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS unavailable on this platform: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'Real GPS on';

  @override
  String get metaMicNoPermission => 'No microphone access';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'Voice for $lang is unavailable on this device';
  }

  @override
  String get signIn => 'Sign in';

  @override
  String get signOut => 'Sign out';

  @override
  String signedInAs(String email) {
    return 'Signed in as $email';
  }

  @override
  String get loginSubtitle => 'Sign in to save your walks and revisit them.';

  @override
  String get continueWithGoogle => 'Continue with Google';

  @override
  String get continueWithApple => 'Continue with Apple';

  @override
  String get emailLabel => 'Email';

  @override
  String get passwordLabel => 'Password';

  @override
  String get createAccount => 'Create account';

  @override
  String get continueAsGuest => 'Continue as guest';

  @override
  String get orSeparator => 'or';

  @override
  String authFailed(String error) {
    return 'Sign-in failed: $error';
  }

  @override
  String get signUpCheckEmail => 'Check your email to confirm your account.';

  @override
  String get historySignInPrompt => 'Sign in to see your saved walks.';

  @override
  String get historyLoadError => 'Couldn\'t load your walks.';

  @override
  String get retry => 'Retry';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count places',
      one: '1 place',
      zero: 'No places',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Delete walk';

  @override
  String get deleteWalkConfirm => 'Delete this walk? This can\'t be undone.';

  @override
  String get delete => 'Delete';

  @override
  String get cancel => 'Cancel';

  @override
  String get deleteAccount => 'Delete account';

  @override
  String get deleteAccountConfirm =>
      'Permanently delete your account and all saved walks? This can\'t be undone.';

  @override
  String get goPremium => 'Go Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'The best of your walks';

  @override
  String get premiumModel => 'Richer, higher-quality narration';

  @override
  String get premiumNoAds => 'No ads';

  @override
  String get premiumUnlimitedTours => 'Unlimited tours every day';

  @override
  String get premiumUnlimitedSaves => 'Unlimited saved walks';

  @override
  String get premiumMonthly => 'Monthly';

  @override
  String get premiumYearly => 'Yearly';

  @override
  String get premiumRestore => 'Restore purchases';

  @override
  String get manageSubscription => 'Manage subscription';

  @override
  String get premiumActive => 'Premium active';

  @override
  String get historyFullTitle => 'History is full';

  @override
  String historyFullBody(int count) {
    return 'Free accounts keep your latest $count walks. Go Premium for unlimited history.';
  }

  @override
  String get dailyLimitTitle => 'Out of free tours today';

  @override
  String dailyLimitBody(int count) {
    return 'Free accounts get $count tours a day. Go Premium for unlimited tours — and no ads.';
  }

  @override
  String get confirmPasswordLabel => 'Confirm password';

  @override
  String get emailRequired => 'Enter your email';

  @override
  String get emailInvalid => 'Enter a valid email address';

  @override
  String get passwordRequired => 'Enter your password';

  @override
  String passwordTooShort(int count) {
    return 'Password must be at least $count characters';
  }

  @override
  String get passwordsDontMatch => 'Passwords don\'t match';

  @override
  String get forgotPassword => 'Forgot password?';

  @override
  String get resetPasswordTitle => 'Reset password';

  @override
  String get resetPasswordHint => 'Enter your account email';

  @override
  String get resetPasswordSend => 'Send link';

  @override
  String get resetEmailSent => 'Password reset link sent. Check your email.';

  @override
  String get authErrorInvalidCredentials => 'Wrong email or password.';

  @override
  String get authErrorEmailInUse => 'That email is already registered.';

  @override
  String get authErrorWeakPassword => 'Please choose a stronger password.';

  @override
  String get authErrorRateLimited =>
      'Too many attempts. Please try again later.';

  @override
  String get authErrorNetwork =>
      'Network error. Check your connection and try again.';

  @override
  String get authErrorGeneric => 'Something went wrong. Please try again.';

  @override
  String get cancelSubscription => 'Cancel subscription';

  @override
  String get bgFinish => 'Finish';
}
