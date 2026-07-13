// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for German (`de`).
class AppLocalizationsDe extends AppLocalizations {
  AppLocalizationsDe([String locale = 'de']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Ich erzähle dir von Orten in deiner Nähe';

  @override
  String get bgNotifPaused => 'Tour pausiert';

  @override
  String get bgPause => 'Pause';

  @override
  String get bgResume => 'Fortsetzen';

  @override
  String get connect => 'Verbinden';

  @override
  String get disconnect => 'Trennen';

  @override
  String get startWalk => 'Spaziergang';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Stopp';

  @override
  String get ask => 'Fragen';

  @override
  String get askHint => 'Frag den Guide… (z. B. Läden überspringen)';

  @override
  String get micAsk => 'Per Sprache fragen';

  @override
  String get micStop => 'Stoppen und senden';

  @override
  String get clearFeed => 'Verlauf löschen';

  @override
  String get voiceOn => 'Sprachausgabe an';

  @override
  String get voiceOff => 'Sprachausgabe aus';

  @override
  String get language => 'Sprache';

  @override
  String get settings => 'Einstellungen';

  @override
  String get history => 'Verlauf';

  @override
  String get simulatedWalk => 'Simulierter Spaziergang (Demo)';

  @override
  String get compassNorth => 'Nach Norden ausrichten';

  @override
  String get emptyHint =>
      'Tippe auf „Spaziergang“.\nDer Guide erzählt dir von Orten in deiner Nähe.';

  @override
  String get following => 'Folge dir';

  @override
  String get freeBrowse => 'Freie Ansicht – tippen zum Folgen';

  @override
  String get appearance => 'Darstellung';

  @override
  String get themeSystem => 'System';

  @override
  String get themeLight => 'Hell';

  @override
  String get themeDark => 'Dunkel';

  @override
  String get themeTopic => 'Tour-Thema';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'Geschichte';

  @override
  String get themeArchitecture => 'Architektur';

  @override
  String get themePeople => 'Menschen';

  @override
  String get themeCulture => 'Kultur';

  @override
  String get themeLegends => 'Legenden';

  @override
  String get route => 'Route';

  @override
  String get walkHistory => 'Spaziergangsverlauf';

  @override
  String get walkHistoryEmptyTitle => 'Noch keine Spaziergänge';

  @override
  String get walkHistoryEmptySubtitle =>
      'Deine bisherigen Spaziergänge erscheinen hier, sobald es Konten gibt.';

  @override
  String get nearbyHint => 'Geh näher heran, und der Guide erzählt dir davon.';

  @override
  String get zoomIn => 'Vergrößern';

  @override
  String get zoomOut => 'Verkleinern';

  @override
  String get chipReconnecting => 'Wiederverbindung…';

  @override
  String get chipNotConnected => 'nicht verbunden';

  @override
  String get chipSpeaking => 'spricht';

  @override
  String get chipScoring => 'analysiert';

  @override
  String get chipNarrating => 'erzählt';

  @override
  String get chipSwitching => 'wechselt';

  @override
  String get chipListening => 'hört zu';

  @override
  String get chipAnswering => 'antwortet';

  @override
  String get chipExpanding => 'erweitert Radius';

  @override
  String get chipReady => 'bereit';

  @override
  String get chipError => 'Quelle nicht verfügbar';

  @override
  String get chipOffline => 'offline';

  @override
  String get chipPaused => 'paused';

  @override
  String get tourLogTitle => 'Walk journal';

  @override
  String get tourLogEmpty => 'Places I tell you about will appear here.';

  @override
  String get tourAskVoice => 'Ask by voice';

  @override
  String get summaryTitle => 'Walk complete';

  @override
  String get summaryDiscardTitle => 'Walk not saved';

  @override
  String get summaryDiscardNote =>
      'Under 10 minutes — not recorded, just reset.';

  @override
  String get summaryDuration => 'Duration';

  @override
  String get summaryDistance => 'Distance';

  @override
  String get summaryPlaces => 'Places';

  @override
  String get summaryTold => 'What we covered';

  @override
  String get summaryDone => 'Done';

  @override
  String get unitMin => 'min';

  @override
  String get unitHr => 'h';

  @override
  String get unitKm => 'km';

  @override
  String get unitM => 'm';

  @override
  String metaConnectionLost(int seconds) {
    return 'Verbindung verloren, Wiederverbindung in ${seconds}s…';
  }

  @override
  String get metaGeoDisabled => 'Standort ist im System deaktiviert';

  @override
  String get metaGeoNoPermission => 'Keine Standortberechtigung';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS auf dieser Plattform nicht verfügbar: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'Echtes GPS an';

  @override
  String get metaMicNoPermission => 'Kein Mikrofonzugriff';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'Stimme für $lang ist auf diesem Gerät nicht verfügbar';
  }

  @override
  String get signIn => 'Anmelden';

  @override
  String get signOut => 'Abmelden';

  @override
  String signedInAs(String email) {
    return 'Angemeldet als $email';
  }

  @override
  String get loginSubtitle =>
      'Melde dich an, um deine Spaziergänge zu speichern und erneut anzusehen.';

  @override
  String get loginWelcomeTitle => 'Welcome back';

  @override
  String get loginWelcomeSubtitle =>
      'Open the app and just walk — your guide tells you what\'s around.';

  @override
  String get loginNewHere => 'New here?';

  @override
  String get registerSubtitle =>
      'Create an account to save your walks and revisit them.';

  @override
  String get haveAccount => 'Already have an account?';

  @override
  String get nickLabel => 'Nickname';

  @override
  String get birthdayLabel => 'Birthday';

  @override
  String get birthdayOptional => 'Birthday · optional';

  @override
  String get avatarChoose => 'Add a photo · optional';

  @override
  String get registerPremiumTitle => 'Get Premium right away';

  @override
  String get registerPremiumSub => 'The whole guide, no limits.';

  @override
  String get otpTitle => 'Confirm your email';

  @override
  String otpSentTo(String email) {
    return 'We sent a 6-digit code to $email. Enter it below to finish.';
  }

  @override
  String get otpCodeLabel => 'Code from email';

  @override
  String get otpConfirm => 'Confirm';

  @override
  String get otpResend => 'Send the code again';

  @override
  String get otpResent => 'Code sent again';

  @override
  String get otpInvalid => 'Wrong or expired code.';

  @override
  String get orWithEmail => 'or with email';

  @override
  String get continueWithGoogle => 'Mit Google fortfahren';

  @override
  String get continueWithApple => 'Mit Apple fortfahren';

  @override
  String get emailLabel => 'E-Mail';

  @override
  String get passwordLabel => 'Passwort';

  @override
  String get createAccount => 'Konto erstellen';

  @override
  String get continueAsGuest => 'Als Gast fortfahren';

  @override
  String get orSeparator => 'oder';

  @override
  String authFailed(String error) {
    return 'Anmeldung fehlgeschlagen: $error';
  }

  @override
  String get signUpCheckEmail => 'Bestätige dein Konto über die E-Mail.';

  @override
  String get historySignInPrompt =>
      'Melde dich an, um deine gespeicherten Spaziergänge zu sehen.';

  @override
  String get historyLoadError => 'Spaziergänge konnten nicht geladen werden.';

  @override
  String get retry => 'Erneut versuchen';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Orte',
      one: '1 Ort',
      zero: 'Keine Orte',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Spaziergang löschen';

  @override
  String get walkShare => 'Share route';

  @override
  String get walkShared => 'Route shared with friends';

  @override
  String get walkSummary => 'Tour summary';

  @override
  String get walkExpand => 'Read more';

  @override
  String get walkCollapse => 'Show less';

  @override
  String get walkReplay => 'Play again';

  @override
  String get deleteWalkConfirm =>
      'Diesen Spaziergang löschen? Kann nicht rückgängig gemacht werden.';

  @override
  String get delete => 'Löschen';

  @override
  String get cancel => 'Abbrechen';

  @override
  String get deleteAccount => 'Konto löschen';

  @override
  String get deleteAccountConfirm =>
      'Dein Konto und alle gespeicherten Spaziergänge dauerhaft löschen? Kann nicht rückgängig gemacht werden.';

  @override
  String get goPremium => 'Premium holen';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'Das Beste aus deinen Spaziergängen';

  @override
  String get premiumModel => 'Reichere Erzählung in höherer Qualität';

  @override
  String get premiumNoAds => 'Keine Werbung';

  @override
  String get premiumUnlimitedTours => 'Unbegrenzte Touren jeden Tag';

  @override
  String get premiumUnlimitedSaves => 'Unbegrenzt gespeicherte Spaziergänge';

  @override
  String get premiumMonthly => 'Monatlich';

  @override
  String get premiumYearly => 'Jährlich';

  @override
  String get premiumRestore => 'Käufe wiederherstellen';

  @override
  String get manageSubscription => 'Abo verwalten';

  @override
  String get premiumActive => 'Premium aktiv';

  @override
  String get historyFullTitle => 'Verlauf ist voll';

  @override
  String historyFullBody(int count) {
    return 'Kostenlose Konten behalten deine letzten $count Spaziergänge. Hol dir Premium für unbegrenzten Verlauf.';
  }

  @override
  String get dailyLimitTitle => 'Heute keine kostenlosen Touren mehr';

  @override
  String dailyLimitBody(int count) {
    return 'Kostenlose Konten erhalten $count Touren pro Tag. Hol dir Premium für unbegrenzte Touren — und keine Werbung.';
  }

  @override
  String get confirmPasswordLabel => 'Passwort bestätigen';

  @override
  String get emailRequired => 'Gib deine E-Mail ein';

  @override
  String get emailInvalid => 'Gib eine gültige E-Mail-Adresse ein';

  @override
  String get passwordRequired => 'Gib dein Passwort ein';

  @override
  String passwordTooShort(int count) {
    return 'Das Passwort muss mindestens $count Zeichen haben';
  }

  @override
  String get passwordsDontMatch => 'Die Passwörter stimmen nicht überein';

  @override
  String get forgotPassword => 'Passwort vergessen?';

  @override
  String get resetPasswordTitle => 'Passwort zurücksetzen';

  @override
  String get resetPasswordHint => 'Gib die E-Mail deines Kontos ein';

  @override
  String get resetPasswordSend => 'Link senden';

  @override
  String get resetEmailSent =>
      'Link zum Zurücksetzen gesendet. Prüfe deine E-Mails.';

  @override
  String get authErrorInvalidCredentials =>
      'Falsche E-Mail oder falsches Passwort.';

  @override
  String get authErrorEmailInUse => 'Diese E-Mail ist bereits registriert.';

  @override
  String get authErrorWeakPassword => 'Bitte wähle ein stärkeres Passwort.';

  @override
  String get authErrorRateLimited =>
      'Zu viele Versuche. Bitte später erneut versuchen.';

  @override
  String get authErrorNetwork =>
      'Netzwerkfehler. Prüfe deine Verbindung und versuch es erneut.';

  @override
  String get authErrorGeneric =>
      'Etwas ist schiefgelaufen. Bitte versuch es erneut.';

  @override
  String get cancelSubscription => 'Abo kündigen';

  @override
  String get bgFinish => 'Beenden';

  @override
  String get greetMorning => 'Guten Morgen,';

  @override
  String get greetAfternoon => 'Guten Tag,';

  @override
  String get greetEvening => 'Guten Abend,';

  @override
  String get greetNight => 'Gute Nacht,';

  @override
  String get homePrompt => 'wohin heute?';

  @override
  String get homeGuest => 'Reisender';

  @override
  String get swipeToStart => 'Los geht\'s';

  @override
  String get tabHome => 'Start';

  @override
  String get tabCommunity => 'Community';

  @override
  String get tabProfile => 'Profil';

  @override
  String get themeLabel => 'Thema';

  @override
  String get focusTitle => 'FOKUS AUF';

  @override
  String get premiumTrial => '1 Woche gratis';

  @override
  String profileLevelN(int n) {
    return 'Level $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return 'bis Level $level · $xp XP';
  }

  @override
  String get profileAtMax => 'Höchstes Level erreicht';

  @override
  String get close => 'Close';

  @override
  String get communityGuest => 'Sign in to see friends, routes and challenges.';

  @override
  String get communityChallenges => 'Challenges';

  @override
  String get communityCreateChallenge => 'Create challenge';

  @override
  String get communityNoChallenges => 'No active challenges yet.';

  @override
  String get communityFriendsRoutes => 'Friends\' routes';

  @override
  String get communityFriends => 'Friends';

  @override
  String get communityAddFriend => 'Add';

  @override
  String get communityWalkingNow => 'walking now';

  @override
  String get communityNoFriends => 'No one yet — add friends by handle.';

  @override
  String get communityRequests => 'Friend requests';

  @override
  String get communityAccept => 'Accept';

  @override
  String get communityDecline => 'Decline';

  @override
  String get communityJoin => 'Join';

  @override
  String get communityPickHandleTitle => 'Pick a handle';

  @override
  String get communityPickHandleBody => 'Friends use it to find you.';

  @override
  String get communityHandleField => 'handle';

  @override
  String get communityHandleSave => 'Save';

  @override
  String get communityHandleTaken => 'Handle taken or invalid';

  @override
  String get communityRequestSent => 'Request sent';

  @override
  String get communitySearchHandle => 'Search by handle';

  @override
  String get communitySendRequest => 'Add';

  @override
  String get communityChallengeTitle => 'Title';

  @override
  String get communityMetric => 'Metric';

  @override
  String get communityMetricDistance => 'Distance';

  @override
  String get communityMetricPlaces => 'Places';

  @override
  String get communityMetricDistricts => 'Districts';

  @override
  String get communityGoalLabel => 'Goal';

  @override
  String get communityDaysLabel => 'Days';

  @override
  String get communityLeaderboard => 'Leaderboard';

  @override
  String get communityNoParticipants => 'No participants yet.';

  @override
  String communityRankPlace(int rank) {
    return '#$rank';
  }

  @override
  String communityGoalKm(int km) {
    return '$km km';
  }

  @override
  String communityGoalPlaces(int count) {
    return '$count places';
  }

  @override
  String communityGoalDistricts(int count) {
    return '$count districts';
  }

  @override
  String feedWalked(String name) {
    return '$name went for a walk';
  }

  @override
  String feedWalkedIn(String name, String city) {
    return '$name walked in $city';
  }

  @override
  String feedStreak(String name, int days) {
    return '$name — $days-day streak';
  }

  @override
  String feedBadge(String name, String badge) {
    return '$name earned “$badge”';
  }

  @override
  String feedChallenge(String name) {
    return '$name started a challenge';
  }

  @override
  String get communityCoWalk => 'Walk together';

  @override
  String get communityCoWalkSub => 'Live session with a friend';

  @override
  String get communityCoWalkActive => 'Walking together';

  @override
  String get communityCoWalkWaiting => 'Waiting for a friend…';

  @override
  String get communityCoWalkLeave => 'Leave';

  @override
  String get communityCoWalkExplain =>
      'Create a code and share it, or enter a friend\'s code — you\'ll see each other live on the map.';

  @override
  String get communityCoWalkCreate => 'Create a room';

  @override
  String get communityCoWalkOrJoin => 'or join';

  @override
  String get communityCoWalkJoin => 'Join';

  @override
  String get communityCoWalkEnterCode => 'code';

  @override
  String get communityMyRoutes => 'My routes';

  @override
  String get communitySeeAll => 'All';

  @override
  String get communityNoRoutes => 'No walks yet.';

  @override
  String get communityWhatsNew => 'New: friends, challenges and co-walks';

  @override
  String get communityTogether => 'Together';

  @override
  String get communityGroupStreak => 'Group streak';

  @override
  String get communityGroupStreakSub => 'Keep a streak with friends';

  @override
  String get communityTeamChallenge => 'Team challenge';

  @override
  String get communityTeamChallengeSub => 'Compete with friends';

  @override
  String get communityGroupStreakPick => 'Pick friends for the shared streak';

  @override
  String get communityGroupStreakEmpty =>
      'Add friends first to start a group streak.';

  @override
  String communityGroupStreakDays(int days) {
    return '$days days together';
  }

  @override
  String xpValue(int n) {
    return '$n XP';
  }

  @override
  String get achievements => 'ERFOLGE';

  @override
  String get friends => 'Freunde';

  @override
  String get invite => 'Einladen';

  @override
  String get friendsSoon => 'Freunde kommen bald';

  @override
  String get statsSoon => 'Deine Statistiken erscheinen hier';

  @override
  String get communitySoonTitle => 'Community kommt bald';

  @override
  String get communitySoonBody =>
      'Freunde, geteilte Routen und Challenges leben hier.';

  @override
  String get sectionAccount => 'Konto';

  @override
  String get sectionDeveloper => 'Entwickler';
}
