import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_de.dart';
import 'app_localizations_en.dart';
import 'app_localizations_es.dart';
import 'app_localizations_fr.dart';
import 'app_localizations_it.dart';
import 'app_localizations_pt.dart';
import 'app_localizations_ru.dart';
import 'app_localizations_zh.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
      : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations? of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations);
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
    delegate,
    GlobalMaterialLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
  ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('de'),
    Locale('en'),
    Locale('es'),
    Locale('fr'),
    Locale('it'),
    Locale('pt'),
    Locale('ru'),
    Locale('zh')
  ];

  /// No description provided for @bgNotifTitle.
  ///
  /// In en, this message translates to:
  /// **'AI Audio Guide'**
  String get bgNotifTitle;

  /// No description provided for @bgNotifText.
  ///
  /// In en, this message translates to:
  /// **'Telling you about places around you'**
  String get bgNotifText;

  /// No description provided for @bgNotifPaused.
  ///
  /// In en, this message translates to:
  /// **'Tour paused'**
  String get bgNotifPaused;

  /// No description provided for @bgPause.
  ///
  /// In en, this message translates to:
  /// **'Pause'**
  String get bgPause;

  /// No description provided for @bgResume.
  ///
  /// In en, this message translates to:
  /// **'Resume'**
  String get bgResume;

  /// No description provided for @connect.
  ///
  /// In en, this message translates to:
  /// **'Connect'**
  String get connect;

  /// No description provided for @disconnect.
  ///
  /// In en, this message translates to:
  /// **'Disconnect'**
  String get disconnect;

  /// No description provided for @startWalk.
  ///
  /// In en, this message translates to:
  /// **'Walk'**
  String get startWalk;

  /// No description provided for @startGps.
  ///
  /// In en, this message translates to:
  /// **'GPS'**
  String get startGps;

  /// No description provided for @stop.
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get stop;

  /// No description provided for @ask.
  ///
  /// In en, this message translates to:
  /// **'Ask'**
  String get ask;

  /// No description provided for @askHint.
  ///
  /// In en, this message translates to:
  /// **'Ask the guide… (e.g. skip shops)'**
  String get askHint;

  /// No description provided for @micAsk.
  ///
  /// In en, this message translates to:
  /// **'Ask by voice'**
  String get micAsk;

  /// No description provided for @micStop.
  ///
  /// In en, this message translates to:
  /// **'Stop and send'**
  String get micStop;

  /// No description provided for @clearFeed.
  ///
  /// In en, this message translates to:
  /// **'Clear feed'**
  String get clearFeed;

  /// No description provided for @voiceOn.
  ///
  /// In en, this message translates to:
  /// **'Narration on'**
  String get voiceOn;

  /// No description provided for @voiceOff.
  ///
  /// In en, this message translates to:
  /// **'Narration off'**
  String get voiceOff;

  /// No description provided for @language.
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get language;

  /// No description provided for @settings.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get settings;

  /// No description provided for @history.
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get history;

  /// No description provided for @simulatedWalk.
  ///
  /// In en, this message translates to:
  /// **'Simulated walk (demo)'**
  String get simulatedWalk;

  /// No description provided for @compassNorth.
  ///
  /// In en, this message translates to:
  /// **'Orient north'**
  String get compassNorth;

  /// No description provided for @emptyHint.
  ///
  /// In en, this message translates to:
  /// **'Tap “Walk”.\nThe guide will tell you about places around you.'**
  String get emptyHint;

  /// No description provided for @following.
  ///
  /// In en, this message translates to:
  /// **'Following you'**
  String get following;

  /// No description provided for @freeBrowse.
  ///
  /// In en, this message translates to:
  /// **'Free browse — tap to follow'**
  String get freeBrowse;

  /// No description provided for @appearance.
  ///
  /// In en, this message translates to:
  /// **'Appearance'**
  String get appearance;

  /// No description provided for @themeSystem.
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get themeSystem;

  /// No description provided for @themeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get themeLight;

  /// No description provided for @themeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get themeDark;

  /// No description provided for @themeTopic.
  ///
  /// In en, this message translates to:
  /// **'Tour theme'**
  String get themeTopic;

  /// No description provided for @themeAuto.
  ///
  /// In en, this message translates to:
  /// **'Auto'**
  String get themeAuto;

  /// No description provided for @themeHistory.
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get themeHistory;

  /// No description provided for @themeArchitecture.
  ///
  /// In en, this message translates to:
  /// **'Architecture'**
  String get themeArchitecture;

  /// No description provided for @themePeople.
  ///
  /// In en, this message translates to:
  /// **'People'**
  String get themePeople;

  /// No description provided for @themeCulture.
  ///
  /// In en, this message translates to:
  /// **'Culture'**
  String get themeCulture;

  /// No description provided for @themeLegends.
  ///
  /// In en, this message translates to:
  /// **'Legends'**
  String get themeLegends;

  /// No description provided for @route.
  ///
  /// In en, this message translates to:
  /// **'Route'**
  String get route;

  /// No description provided for @walkHistory.
  ///
  /// In en, this message translates to:
  /// **'Walk history'**
  String get walkHistory;

  /// No description provided for @walkHistoryEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No walks yet'**
  String get walkHistoryEmptyTitle;

  /// No description provided for @walkHistoryEmptySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Your past walks will appear here once accounts arrive.'**
  String get walkHistoryEmptySubtitle;

  /// No description provided for @nearbyHint.
  ///
  /// In en, this message translates to:
  /// **'Walk closer and the guide will tell you about it.'**
  String get nearbyHint;

  /// No description provided for @zoomIn.
  ///
  /// In en, this message translates to:
  /// **'Zoom in'**
  String get zoomIn;

  /// No description provided for @zoomOut.
  ///
  /// In en, this message translates to:
  /// **'Zoom out'**
  String get zoomOut;

  /// No description provided for @chipReconnecting.
  ///
  /// In en, this message translates to:
  /// **'reconnecting…'**
  String get chipReconnecting;

  /// No description provided for @chipNotConnected.
  ///
  /// In en, this message translates to:
  /// **'not connected'**
  String get chipNotConnected;

  /// No description provided for @chipSpeaking.
  ///
  /// In en, this message translates to:
  /// **'speaking'**
  String get chipSpeaking;

  /// No description provided for @chipScoring.
  ///
  /// In en, this message translates to:
  /// **'analysing'**
  String get chipScoring;

  /// No description provided for @chipNarrating.
  ///
  /// In en, this message translates to:
  /// **'narrating'**
  String get chipNarrating;

  /// No description provided for @chipSwitching.
  ///
  /// In en, this message translates to:
  /// **'switching'**
  String get chipSwitching;

  /// No description provided for @chipListening.
  ///
  /// In en, this message translates to:
  /// **'listening'**
  String get chipListening;

  /// No description provided for @chipAnswering.
  ///
  /// In en, this message translates to:
  /// **'answering'**
  String get chipAnswering;

  /// No description provided for @chipExpanding.
  ///
  /// In en, this message translates to:
  /// **'expanding radius'**
  String get chipExpanding;

  /// No description provided for @chipReady.
  ///
  /// In en, this message translates to:
  /// **'ready'**
  String get chipReady;

  /// No description provided for @chipError.
  ///
  /// In en, this message translates to:
  /// **'source unavailable'**
  String get chipError;

  /// No description provided for @chipOffline.
  ///
  /// In en, this message translates to:
  /// **'offline'**
  String get chipOffline;

  /// No description provided for @metaConnectionLost.
  ///
  /// In en, this message translates to:
  /// **'Connection lost, reconnecting in {seconds}s…'**
  String metaConnectionLost(int seconds);

  /// No description provided for @metaGeoDisabled.
  ///
  /// In en, this message translates to:
  /// **'Location is turned off in system settings'**
  String get metaGeoDisabled;

  /// No description provided for @metaGeoNoPermission.
  ///
  /// In en, this message translates to:
  /// **'No location permission'**
  String get metaGeoNoPermission;

  /// No description provided for @metaGpsUnavailable.
  ///
  /// In en, this message translates to:
  /// **'GPS unavailable on this platform: {error}'**
  String metaGpsUnavailable(String error);

  /// No description provided for @metaGpsError.
  ///
  /// In en, this message translates to:
  /// **'GPS: {error}'**
  String metaGpsError(String error);

  /// No description provided for @metaRealGpsOn.
  ///
  /// In en, this message translates to:
  /// **'Real GPS on'**
  String get metaRealGpsOn;

  /// No description provided for @metaMicNoPermission.
  ///
  /// In en, this message translates to:
  /// **'No microphone access'**
  String get metaMicNoPermission;

  /// No description provided for @metaVoiceUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Voice for {lang} is unavailable on this device'**
  String metaVoiceUnavailable(String lang);

  /// No description provided for @signIn.
  ///
  /// In en, this message translates to:
  /// **'Sign in'**
  String get signIn;

  /// No description provided for @signOut.
  ///
  /// In en, this message translates to:
  /// **'Sign out'**
  String get signOut;

  /// No description provided for @signedInAs.
  ///
  /// In en, this message translates to:
  /// **'Signed in as {email}'**
  String signedInAs(String email);

  /// No description provided for @loginSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Sign in to save your walks and revisit them.'**
  String get loginSubtitle;

  /// No description provided for @continueWithGoogle.
  ///
  /// In en, this message translates to:
  /// **'Continue with Google'**
  String get continueWithGoogle;

  /// No description provided for @continueWithApple.
  ///
  /// In en, this message translates to:
  /// **'Continue with Apple'**
  String get continueWithApple;

  /// No description provided for @emailLabel.
  ///
  /// In en, this message translates to:
  /// **'Email'**
  String get emailLabel;

  /// No description provided for @passwordLabel.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get passwordLabel;

  /// No description provided for @createAccount.
  ///
  /// In en, this message translates to:
  /// **'Create account'**
  String get createAccount;

  /// No description provided for @continueAsGuest.
  ///
  /// In en, this message translates to:
  /// **'Continue as guest'**
  String get continueAsGuest;

  /// No description provided for @orSeparator.
  ///
  /// In en, this message translates to:
  /// **'or'**
  String get orSeparator;

  /// No description provided for @authFailed.
  ///
  /// In en, this message translates to:
  /// **'Sign-in failed: {error}'**
  String authFailed(String error);

  /// No description provided for @signUpCheckEmail.
  ///
  /// In en, this message translates to:
  /// **'Check your email to confirm your account.'**
  String get signUpCheckEmail;

  /// No description provided for @historySignInPrompt.
  ///
  /// In en, this message translates to:
  /// **'Sign in to see your saved walks.'**
  String get historySignInPrompt;

  /// No description provided for @historyLoadError.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your walks.'**
  String get historyLoadError;

  /// No description provided for @retry.
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get retry;

  /// No description provided for @placesCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =0{No places} =1{1 place} other{{count} places}}'**
  String placesCount(int count);

  /// No description provided for @deleteWalk.
  ///
  /// In en, this message translates to:
  /// **'Delete walk'**
  String get deleteWalk;

  /// No description provided for @deleteWalkConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete this walk? This can\'t be undone.'**
  String get deleteWalkConfirm;

  /// No description provided for @delete.
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get delete;

  /// No description provided for @cancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get cancel;

  /// No description provided for @deleteAccount.
  ///
  /// In en, this message translates to:
  /// **'Delete account'**
  String get deleteAccount;

  /// No description provided for @deleteAccountConfirm.
  ///
  /// In en, this message translates to:
  /// **'Permanently delete your account and all saved walks? This can\'t be undone.'**
  String get deleteAccountConfirm;

  /// No description provided for @goPremium.
  ///
  /// In en, this message translates to:
  /// **'Go Premium'**
  String get goPremium;

  /// No description provided for @premiumTitle.
  ///
  /// In en, this message translates to:
  /// **'AI Guide Premium'**
  String get premiumTitle;

  /// No description provided for @premiumTagline.
  ///
  /// In en, this message translates to:
  /// **'The best of your walks'**
  String get premiumTagline;

  /// No description provided for @premiumModel.
  ///
  /// In en, this message translates to:
  /// **'Richer, higher-quality narration'**
  String get premiumModel;

  /// No description provided for @premiumNoAds.
  ///
  /// In en, this message translates to:
  /// **'No ads'**
  String get premiumNoAds;

  /// No description provided for @premiumUnlimitedTours.
  ///
  /// In en, this message translates to:
  /// **'Unlimited tours every day'**
  String get premiumUnlimitedTours;

  /// No description provided for @premiumUnlimitedSaves.
  ///
  /// In en, this message translates to:
  /// **'Unlimited saved walks'**
  String get premiumUnlimitedSaves;

  /// No description provided for @premiumMonthly.
  ///
  /// In en, this message translates to:
  /// **'Monthly'**
  String get premiumMonthly;

  /// No description provided for @premiumYearly.
  ///
  /// In en, this message translates to:
  /// **'Yearly'**
  String get premiumYearly;

  /// No description provided for @premiumRestore.
  ///
  /// In en, this message translates to:
  /// **'Restore purchases'**
  String get premiumRestore;

  /// No description provided for @manageSubscription.
  ///
  /// In en, this message translates to:
  /// **'Manage subscription'**
  String get manageSubscription;

  /// No description provided for @premiumActive.
  ///
  /// In en, this message translates to:
  /// **'Premium active'**
  String get premiumActive;

  /// No description provided for @historyFullTitle.
  ///
  /// In en, this message translates to:
  /// **'History is full'**
  String get historyFullTitle;

  /// No description provided for @historyFullBody.
  ///
  /// In en, this message translates to:
  /// **'Free accounts keep your latest {count} walks. Go Premium for unlimited history.'**
  String historyFullBody(int count);

  /// No description provided for @dailyLimitTitle.
  ///
  /// In en, this message translates to:
  /// **'Out of free tours today'**
  String get dailyLimitTitle;

  /// No description provided for @dailyLimitBody.
  ///
  /// In en, this message translates to:
  /// **'Free accounts get {count} tours a day. Go Premium for unlimited tours — and no ads.'**
  String dailyLimitBody(int count);

  /// No description provided for @confirmPasswordLabel.
  ///
  /// In en, this message translates to:
  /// **'Confirm password'**
  String get confirmPasswordLabel;

  /// No description provided for @emailRequired.
  ///
  /// In en, this message translates to:
  /// **'Enter your email'**
  String get emailRequired;

  /// No description provided for @emailInvalid.
  ///
  /// In en, this message translates to:
  /// **'Enter a valid email address'**
  String get emailInvalid;

  /// No description provided for @passwordRequired.
  ///
  /// In en, this message translates to:
  /// **'Enter your password'**
  String get passwordRequired;

  /// No description provided for @passwordTooShort.
  ///
  /// In en, this message translates to:
  /// **'Password must be at least {count} characters'**
  String passwordTooShort(int count);

  /// No description provided for @passwordsDontMatch.
  ///
  /// In en, this message translates to:
  /// **'Passwords don\'t match'**
  String get passwordsDontMatch;

  /// No description provided for @forgotPassword.
  ///
  /// In en, this message translates to:
  /// **'Forgot password?'**
  String get forgotPassword;

  /// No description provided for @resetPasswordTitle.
  ///
  /// In en, this message translates to:
  /// **'Reset password'**
  String get resetPasswordTitle;

  /// No description provided for @resetPasswordHint.
  ///
  /// In en, this message translates to:
  /// **'Enter your account email'**
  String get resetPasswordHint;

  /// No description provided for @resetPasswordSend.
  ///
  /// In en, this message translates to:
  /// **'Send link'**
  String get resetPasswordSend;

  /// No description provided for @resetEmailSent.
  ///
  /// In en, this message translates to:
  /// **'Password reset link sent. Check your email.'**
  String get resetEmailSent;

  /// No description provided for @authErrorInvalidCredentials.
  ///
  /// In en, this message translates to:
  /// **'Wrong email or password.'**
  String get authErrorInvalidCredentials;

  /// No description provided for @authErrorEmailInUse.
  ///
  /// In en, this message translates to:
  /// **'That email is already registered.'**
  String get authErrorEmailInUse;

  /// No description provided for @authErrorWeakPassword.
  ///
  /// In en, this message translates to:
  /// **'Please choose a stronger password.'**
  String get authErrorWeakPassword;

  /// No description provided for @authErrorRateLimited.
  ///
  /// In en, this message translates to:
  /// **'Too many attempts. Please try again later.'**
  String get authErrorRateLimited;

  /// No description provided for @authErrorNetwork.
  ///
  /// In en, this message translates to:
  /// **'Network error. Check your connection and try again.'**
  String get authErrorNetwork;

  /// No description provided for @authErrorGeneric.
  ///
  /// In en, this message translates to:
  /// **'Something went wrong. Please try again.'**
  String get authErrorGeneric;

  /// No description provided for @cancelSubscription.
  ///
  /// In en, this message translates to:
  /// **'Cancel subscription'**
  String get cancelSubscription;

  /// No description provided for @bgFinish.
  ///
  /// In en, this message translates to:
  /// **'Finish'**
  String get bgFinish;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) => <String>[
        'de',
        'en',
        'es',
        'fr',
        'it',
        'pt',
        'ru',
        'zh'
      ].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'de':
      return AppLocalizationsDe();
    case 'en':
      return AppLocalizationsEn();
    case 'es':
      return AppLocalizationsEs();
    case 'fr':
      return AppLocalizationsFr();
    case 'it':
      return AppLocalizationsIt();
    case 'pt':
      return AppLocalizationsPt();
    case 'ru':
      return AppLocalizationsRu();
    case 'zh':
      return AppLocalizationsZh();
  }

  throw FlutterError(
      'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
      'an issue with the localizations generation tool. Please file an issue '
      'on GitHub with a reproducible sample app and the gen-l10n configuration '
      'that was used.');
}
