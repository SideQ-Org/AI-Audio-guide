// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for French (`fr`).
class AppLocalizationsFr extends AppLocalizations {
  AppLocalizationsFr([String locale = 'fr']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Je vous raconte les lieux autour de vous';

  @override
  String get bgNotifPaused => 'Visite en pause';

  @override
  String get bgPause => 'Pause';

  @override
  String get bgResume => 'Reprendre';

  @override
  String get connect => 'Connecter';

  @override
  String get disconnect => 'Déconnecter';

  @override
  String get startWalk => 'Balade';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Arrêter';

  @override
  String get ask => 'Demander';

  @override
  String get askHint => 'Demandez au guide… (p. ex. ignore les boutiques)';

  @override
  String get micAsk => 'Demander à la voix';

  @override
  String get micStop => 'Arrêter et envoyer';

  @override
  String get clearFeed => 'Effacer le journal';

  @override
  String get voiceOn => 'Narration activée';

  @override
  String get voiceOff => 'Narration désactivée';

  @override
  String get language => 'Langue';

  @override
  String get settings => 'Réglages';

  @override
  String get history => 'Historique';

  @override
  String get simulatedWalk => 'Balade simulée (démo)';

  @override
  String get compassNorth => 'Orienter au nord';

  @override
  String get emptyHint =>
      'Appuyez sur « Balade ».\nLe guide vous parlera des lieux autour de vous.';

  @override
  String get following => 'Je vous suis';

  @override
  String get freeBrowse => 'Navigation libre — appuyez pour suivre';

  @override
  String get appearance => 'Apparence';

  @override
  String get themeSystem => 'Système';

  @override
  String get themeLight => 'Clair';

  @override
  String get themeDark => 'Sombre';

  @override
  String get themeTopic => 'Thème de la visite';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'Histoire';

  @override
  String get themeArchitecture => 'Architecture';

  @override
  String get themePeople => 'Personnages';

  @override
  String get themeCulture => 'Culture';

  @override
  String get themeLegends => 'Légendes';

  @override
  String get route => 'Itinéraire';

  @override
  String get walkHistory => 'Historique des balades';

  @override
  String get walkHistoryEmptyTitle => 'Aucune balade pour l\'instant';

  @override
  String get walkHistoryEmptySubtitle =>
      'Vos balades passées apparaîtront ici une fois les comptes disponibles.';

  @override
  String get nearbyHint => 'Approchez-vous et le guide vous en parlera.';

  @override
  String get zoomIn => 'Zoom avant';

  @override
  String get zoomOut => 'Zoom arrière';

  @override
  String get chipReconnecting => 'reconnexion…';

  @override
  String get chipNotConnected => 'non connecté';

  @override
  String get chipSpeaking => 'parle';

  @override
  String get chipScoring => 'analyse';

  @override
  String get chipNarrating => 'récit';

  @override
  String get chipSwitching => 'changement';

  @override
  String get chipListening => 'écoute';

  @override
  String get chipAnswering => 'réponse';

  @override
  String get chipExpanding => 'élargit le rayon';

  @override
  String get chipReady => 'prêt';

  @override
  String get chipError => 'source indisponible';

  @override
  String get chipOffline => 'hors ligne';

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
    return 'connexion perdue, reconnexion dans ${seconds}s…';
  }

  @override
  String get metaGeoDisabled =>
      'La localisation est désactivée dans le système';

  @override
  String get metaGeoNoPermission => 'Pas d\'autorisation de localisation';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS indisponible sur cette plateforme : $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS : $error';
  }

  @override
  String get metaRealGpsOn => 'GPS réel activé';

  @override
  String get metaMicNoPermission => 'Pas d\'accès au microphone';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'la voix pour $lang n\'est pas disponible sur cet appareil';
  }

  @override
  String get signIn => 'Se connecter';

  @override
  String get signOut => 'Se déconnecter';

  @override
  String signedInAs(String email) {
    return 'Connecté en tant que $email';
  }

  @override
  String get loginSubtitle =>
      'Connectez-vous pour enregistrer vos balades et les revoir.';

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
  String get continueWithGoogle => 'Continuer avec Google';

  @override
  String get continueWithApple => 'Continuer avec Apple';

  @override
  String get emailLabel => 'E-mail';

  @override
  String get passwordLabel => 'Mot de passe';

  @override
  String get createAccount => 'Créer un compte';

  @override
  String get continueAsGuest => 'Continuer en tant qu\'invité';

  @override
  String get orSeparator => 'ou';

  @override
  String authFailed(String error) {
    return 'Échec de la connexion : $error';
  }

  @override
  String get signUpCheckEmail =>
      'Vérifiez votre e-mail pour confirmer votre compte.';

  @override
  String get historySignInPrompt =>
      'Connectez-vous pour voir vos balades enregistrées.';

  @override
  String get historyLoadError => 'Impossible de charger vos balades.';

  @override
  String get retry => 'Réessayer';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count lieux',
      one: '1 lieu',
      zero: 'Aucun lieu',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Supprimer la balade';

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
      'Supprimer cette balade ? Action irréversible.';

  @override
  String get delete => 'Supprimer';

  @override
  String get cancel => 'Annuler';

  @override
  String get deleteAccount => 'Supprimer le compte';

  @override
  String get deleteAccountConfirm =>
      'Supprimer définitivement votre compte et toutes les balades enregistrées ? Action irréversible.';

  @override
  String get goPremium => 'Passer à Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'Le meilleur de vos balades';

  @override
  String get premiumModel => 'Une narration plus riche et de meilleure qualité';

  @override
  String get premiumNoAds => 'Sans publicité';

  @override
  String get premiumUnlimitedTours => 'Visites illimitées chaque jour';

  @override
  String get premiumUnlimitedSaves => 'Balades enregistrées illimitées';

  @override
  String get premiumMonthly => 'Mensuel';

  @override
  String get premiumYearly => 'Annuel';

  @override
  String get premiumRestore => 'Restaurer les achats';

  @override
  String get manageSubscription => 'Gérer l\'abonnement';

  @override
  String get premiumActive => 'Premium actif';

  @override
  String get historyFullTitle => 'L\'historique est plein';

  @override
  String historyFullBody(int count) {
    return 'Les comptes gratuits conservent vos $count dernières balades. Passez à Premium pour un historique illimité.';
  }

  @override
  String get dailyLimitTitle => 'Plus de visites gratuites aujourd\'hui';

  @override
  String dailyLimitBody(int count) {
    return 'Les comptes gratuits ont $count visites par jour. Passez à Premium pour des visites illimitées — et sans publicité.';
  }

  @override
  String get confirmPasswordLabel => 'Confirmer le mot de passe';

  @override
  String get emailRequired => 'Saisis ton e-mail';

  @override
  String get emailInvalid => 'Saisis une adresse e-mail valide';

  @override
  String get passwordRequired => 'Saisis ton mot de passe';

  @override
  String passwordTooShort(int count) {
    return 'Le mot de passe doit comporter au moins $count caractères';
  }

  @override
  String get passwordsDontMatch => 'Les mots de passe ne correspondent pas';

  @override
  String get forgotPassword => 'Mot de passe oublié ?';

  @override
  String get resetPasswordTitle => 'Réinitialiser le mot de passe';

  @override
  String get resetPasswordHint => 'Saisis l\'e-mail de ton compte';

  @override
  String get resetPasswordSend => 'Envoyer le lien';

  @override
  String get resetEmailSent =>
      'Lien de réinitialisation envoyé. Vérifie tes e-mails.';

  @override
  String get authErrorInvalidCredentials => 'E-mail ou mot de passe incorrect.';

  @override
  String get authErrorEmailInUse => 'Cet e-mail est déjà enregistré.';

  @override
  String get authErrorWeakPassword => 'Choisis un mot de passe plus robuste.';

  @override
  String get authErrorRateLimited => 'Trop de tentatives. Réessaie plus tard.';

  @override
  String get authErrorNetwork =>
      'Erreur réseau. Vérifie ta connexion et réessaie.';

  @override
  String get authErrorGeneric => 'Une erreur est survenue. Réessaie.';

  @override
  String get cancelSubscription => 'Annuler l\'abonnement';

  @override
  String get bgFinish => 'Terminer';

  @override
  String get greetMorning => 'Bonjour,';

  @override
  String get greetAfternoon => 'Bon après-midi,';

  @override
  String get greetEvening => 'Bonsoir,';

  @override
  String get greetNight => 'Bonne nuit,';

  @override
  String get homePrompt => 'on va où aujourd\'hui ?';

  @override
  String get homeGuest => 'Voyageur';

  @override
  String get swipeToStart => 'C\'est parti';

  @override
  String get tabHome => 'Accueil';

  @override
  String get tabCommunity => 'Communauté';

  @override
  String get tabProfile => 'Profil';

  @override
  String get themeLabel => 'Thème';

  @override
  String get focusTitle => 'SE CONCENTRER SUR';

  @override
  String get premiumTrial => '1 semaine gratuite';

  @override
  String profileLevelN(int n) {
    return 'Niveau $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return 'vers le niveau $level · $xp XP';
  }

  @override
  String get profileAtMax => 'Niveau maximal atteint';

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
  String get achievements => 'SUCCÈS';

  @override
  String get friends => 'Amis';

  @override
  String get invite => 'Inviter';

  @override
  String get friendsSoon => 'Les amis arrivent bientôt';

  @override
  String get statsSoon => 'Vos statistiques apparaîtront ici';

  @override
  String get communitySoonTitle => 'La communauté arrive bientôt';

  @override
  String get communitySoonBody =>
      'Amis, itinéraires partagés et défis vivront ici.';

  @override
  String get sectionAccount => 'Compte';

  @override
  String get sectionDeveloper => 'Développeur';
}
