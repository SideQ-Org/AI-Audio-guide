// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Italian (`it`).
class AppLocalizationsIt extends AppLocalizations {
  AppLocalizationsIt([String locale = 'it']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Ti racconto i luoghi intorno a te';

  @override
  String get bgNotifPaused => 'Tour in pausa';

  @override
  String get bgPause => 'Pausa';

  @override
  String get bgResume => 'Riprendi';

  @override
  String get connect => 'Connetti';

  @override
  String get disconnect => 'Disconnetti';

  @override
  String get startWalk => 'Passeggiata';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Stop';

  @override
  String get ask => 'Chiedi';

  @override
  String get askHint => 'Chiedi alla guida… (es. salta i negozi)';

  @override
  String get micAsk => 'Chiedi a voce';

  @override
  String get micStop => 'Ferma e invia';

  @override
  String get clearFeed => 'Cancella il registro';

  @override
  String get voiceOn => 'Narrazione attiva';

  @override
  String get voiceOff => 'Narrazione disattivata';

  @override
  String get language => 'Lingua';

  @override
  String get settings => 'Impostazioni';

  @override
  String get history => 'Cronologia';

  @override
  String get simulatedWalk => 'Passeggiata simulata (demo)';

  @override
  String get compassNorth => 'Orienta a nord';

  @override
  String get emptyHint =>
      'Tocca «Passeggiata».\nLa guida ti racconterà i luoghi intorno a te.';

  @override
  String get following => 'Ti sto seguendo';

  @override
  String get freeBrowse => 'Esplorazione libera — tocca per seguire';

  @override
  String get appearance => 'Aspetto';

  @override
  String get themeSystem => 'Sistema';

  @override
  String get themeLight => 'Chiaro';

  @override
  String get themeDark => 'Scuro';

  @override
  String get themeTopic => 'Tema del tour';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'Storia';

  @override
  String get themeArchitecture => 'Architettura';

  @override
  String get themePeople => 'Persone';

  @override
  String get themeCulture => 'Cultura';

  @override
  String get themeLegends => 'Leggende';

  @override
  String get route => 'Percorso';

  @override
  String get walkHistory => 'Cronologia delle passeggiate';

  @override
  String get walkHistoryEmptyTitle => 'Ancora nessuna passeggiata';

  @override
  String get walkHistoryEmptySubtitle =>
      'Le tue passeggiate passate appariranno qui quando arriveranno gli account.';

  @override
  String get nearbyHint => 'Avvicinati e la guida te ne parlerà.';

  @override
  String get zoomIn => 'Ingrandisci';

  @override
  String get zoomOut => 'Riduci';

  @override
  String get chipReconnecting => 'riconnessione…';

  @override
  String get chipNotConnected => 'non connesso';

  @override
  String get chipSpeaking => 'parla';

  @override
  String get chipScoring => 'analisi';

  @override
  String get chipNarrating => 'racconto';

  @override
  String get chipSwitching => 'cambio';

  @override
  String get chipListening => 'ascolta';

  @override
  String get chipAnswering => 'risponde';

  @override
  String get chipExpanding => 'amplia il raggio';

  @override
  String get chipReady => 'pronto';

  @override
  String get chipError => 'fonte non disponibile';

  @override
  String get chipOffline => 'offline';

  @override
  String metaConnectionLost(int seconds) {
    return 'connessione persa, riconnessione tra ${seconds}s…';
  }

  @override
  String get metaGeoDisabled =>
      'La geolocalizzazione è disattivata nel sistema';

  @override
  String get metaGeoNoPermission => 'Nessun permesso di geolocalizzazione';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS non disponibile su questa piattaforma: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'GPS reale attivo';

  @override
  String get metaMicNoPermission => 'Nessun accesso al microfono';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'la voce per $lang non è disponibile su questo dispositivo';
  }

  @override
  String get signIn => 'Accedi';

  @override
  String get signOut => 'Esci';

  @override
  String signedInAs(String email) {
    return 'Accesso come $email';
  }

  @override
  String get loginSubtitle =>
      'Accedi per salvare le tue passeggiate e rivederle.';

  @override
  String get continueWithGoogle => 'Continua con Google';

  @override
  String get continueWithApple => 'Continua con Apple';

  @override
  String get emailLabel => 'E-mail';

  @override
  String get passwordLabel => 'Password';

  @override
  String get createAccount => 'Crea account';

  @override
  String get continueAsGuest => 'Continua come ospite';

  @override
  String get orSeparator => 'oppure';

  @override
  String authFailed(String error) {
    return 'Accesso non riuscito: $error';
  }

  @override
  String get signUpCheckEmail =>
      'Controlla la tua e-mail per confermare l\'account.';

  @override
  String get historySignInPrompt => 'Accedi per vedere le passeggiate salvate.';

  @override
  String get historyLoadError => 'Impossibile caricare le passeggiate.';

  @override
  String get retry => 'Riprova';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count luoghi',
      one: '1 luogo',
      zero: 'Nessun luogo',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Elimina passeggiata';

  @override
  String get deleteWalkConfirm =>
      'Eliminare questa passeggiata? Non è reversibile.';

  @override
  String get delete => 'Elimina';

  @override
  String get cancel => 'Annulla';

  @override
  String get deleteAccount => 'Elimina account';

  @override
  String get deleteAccountConfirm =>
      'Eliminare definitivamente il tuo account e tutte le passeggiate salvate? Non è reversibile.';

  @override
  String get goPremium => 'Passa a Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'Il meglio delle tue passeggiate';

  @override
  String get premiumModel => 'Narrazione più ricca e di qualità superiore';

  @override
  String get premiumNoAds => 'Nessuna pubblicità';

  @override
  String get premiumUnlimitedTours => 'Tour illimitati ogni giorno';

  @override
  String get premiumUnlimitedSaves => 'Passeggiate salvate illimitate';

  @override
  String get premiumMonthly => 'Mensile';

  @override
  String get premiumYearly => 'Annuale';

  @override
  String get premiumRestore => 'Ripristina acquisti';

  @override
  String get manageSubscription => 'Gestisci abbonamento';

  @override
  String get premiumActive => 'Premium attivo';

  @override
  String get historyFullTitle => 'La cronologia è piena';

  @override
  String historyFullBody(int count) {
    return 'Gli account gratuiti conservano le tue ultime $count passeggiate. Passa a Premium per una cronologia illimitata.';
  }

  @override
  String get dailyLimitTitle => 'Tour gratuiti esauriti per oggi';

  @override
  String dailyLimitBody(int count) {
    return 'Gli account gratuiti hanno $count tour al giorno. Passa a Premium per tour illimitati — e senza pubblicità.';
  }

  @override
  String get confirmPasswordLabel => 'Conferma password';

  @override
  String get emailRequired => 'Inserisci la tua email';

  @override
  String get emailInvalid => 'Inserisci un\'email valida';

  @override
  String get passwordRequired => 'Inserisci la password';

  @override
  String passwordTooShort(int count) {
    return 'La password deve avere almeno $count caratteri';
  }

  @override
  String get passwordsDontMatch => 'Le password non coincidono';

  @override
  String get forgotPassword => 'Password dimenticata?';

  @override
  String get resetPasswordTitle => 'Reimposta password';

  @override
  String get resetPasswordHint => 'Inserisci l\'email del tuo account';

  @override
  String get resetPasswordSend => 'Invia link';

  @override
  String get resetEmailSent =>
      'Link per reimpostare la password inviato. Controlla l\'email.';

  @override
  String get authErrorInvalidCredentials => 'Email o password errati.';

  @override
  String get authErrorEmailInUse => 'Questa email è già registrata.';

  @override
  String get authErrorWeakPassword => 'Scegli una password più sicura.';

  @override
  String get authErrorRateLimited => 'Troppi tentativi. Riprova più tardi.';

  @override
  String get authErrorNetwork =>
      'Errore di rete. Controlla la connessione e riprova.';

  @override
  String get authErrorGeneric => 'Qualcosa è andato storto. Riprova.';

  @override
  String get cancelSubscription => 'Annulla abbonamento';

  @override
  String get bgFinish => 'Termina';
}
