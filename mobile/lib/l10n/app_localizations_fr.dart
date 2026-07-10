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
}
