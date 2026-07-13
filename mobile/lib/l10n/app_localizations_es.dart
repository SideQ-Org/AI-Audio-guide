// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Spanish Castilian (`es`).
class AppLocalizationsEs extends AppLocalizations {
  AppLocalizationsEs([String locale = 'es']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Te cuento sobre los lugares a tu alrededor';

  @override
  String get bgNotifPaused => 'Excursión en pausa';

  @override
  String get bgPause => 'Pausar';

  @override
  String get bgResume => 'Reanudar';

  @override
  String get connect => 'Conectar';

  @override
  String get disconnect => 'Desconectar';

  @override
  String get startWalk => 'Paseo';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Parar';

  @override
  String get ask => 'Preguntar';

  @override
  String get askHint => 'Pregunta al guía… (p. ej. omite las tiendas)';

  @override
  String get micAsk => 'Preguntar por voz';

  @override
  String get micStop => 'Detener y enviar';

  @override
  String get clearFeed => 'Borrar el registro';

  @override
  String get voiceOn => 'Narración activada';

  @override
  String get voiceOff => 'Narración desactivada';

  @override
  String get language => 'Idioma';

  @override
  String get settings => 'Ajustes';

  @override
  String get history => 'Historial';

  @override
  String get simulatedWalk => 'Paseo simulado (demo)';

  @override
  String get compassNorth => 'Orientar al norte';

  @override
  String get emptyHint =>
      'Pulsa «Paseo».\nEl guía te hablará de los lugares a tu alrededor.';

  @override
  String get following => 'Siguiéndote';

  @override
  String get freeBrowse => 'Exploración libre: toca para seguir';

  @override
  String get appearance => 'Apariencia';

  @override
  String get themeSystem => 'Sistema';

  @override
  String get themeLight => 'Claro';

  @override
  String get themeDark => 'Oscuro';

  @override
  String get themeTopic => 'Tema del recorrido';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'Historia';

  @override
  String get themeArchitecture => 'Arquitectura';

  @override
  String get themePeople => 'Personas';

  @override
  String get themeCulture => 'Cultura';

  @override
  String get themeLegends => 'Leyendas';

  @override
  String get route => 'Ruta';

  @override
  String get walkHistory => 'Historial de paseos';

  @override
  String get walkHistoryEmptyTitle => 'Aún no hay paseos';

  @override
  String get walkHistoryEmptySubtitle =>
      'Tus paseos anteriores aparecerán aquí cuando lleguen las cuentas.';

  @override
  String get nearbyHint => 'Acércate y el guía te hablará de ello.';

  @override
  String get zoomIn => 'Acercar';

  @override
  String get zoomOut => 'Alejar';

  @override
  String get chipReconnecting => 'reconectando…';

  @override
  String get chipNotConnected => 'sin conexión';

  @override
  String get chipSpeaking => 'hablando';

  @override
  String get chipScoring => 'analizando';

  @override
  String get chipNarrating => 'narrando';

  @override
  String get chipSwitching => 'cambiando';

  @override
  String get chipListening => 'escuchando';

  @override
  String get chipAnswering => 'respondiendo';

  @override
  String get chipExpanding => 'ampliando radio';

  @override
  String get chipReady => 'listo';

  @override
  String get chipError => 'fuente no disponible';

  @override
  String get chipOffline => 'sin conexión';

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
    return 'conexión perdida, reconectando en ${seconds}s…';
  }

  @override
  String get metaGeoDisabled => 'La ubicación está desactivada en el sistema';

  @override
  String get metaGeoNoPermission => 'Sin permiso de ubicación';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS no disponible en esta plataforma: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'GPS real activado';

  @override
  String get metaMicNoPermission => 'Sin acceso al micrófono';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'la voz para $lang no está disponible en este dispositivo';
  }

  @override
  String get signIn => 'Iniciar sesión';

  @override
  String get signOut => 'Cerrar sesión';

  @override
  String signedInAs(String email) {
    return 'Sesión iniciada como $email';
  }

  @override
  String get loginSubtitle =>
      'Inicia sesión para guardar tus paseos y volver a verlos.';

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
  String get continueWithGoogle => 'Continuar con Google';

  @override
  String get continueWithApple => 'Continuar con Apple';

  @override
  String get emailLabel => 'Correo electrónico';

  @override
  String get passwordLabel => 'Contraseña';

  @override
  String get createAccount => 'Crear cuenta';

  @override
  String get continueAsGuest => 'Continuar como invitado';

  @override
  String get orSeparator => 'o';

  @override
  String authFailed(String error) {
    return 'Error al iniciar sesión: $error';
  }

  @override
  String get signUpCheckEmail => 'Revisa tu correo para confirmar tu cuenta.';

  @override
  String get historySignInPrompt =>
      'Inicia sesión para ver tus paseos guardados.';

  @override
  String get historyLoadError => 'No se pudieron cargar tus paseos.';

  @override
  String get retry => 'Reintentar';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count lugares',
      one: '1 lugar',
      zero: 'Sin lugares',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Eliminar paseo';

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
  String get deleteWalkConfirm => '¿Eliminar este paseo? No se puede deshacer.';

  @override
  String get delete => 'Eliminar';

  @override
  String get cancel => 'Cancelar';

  @override
  String get deleteAccount => 'Eliminar cuenta';

  @override
  String get deleteAccountConfirm =>
      '¿Eliminar permanentemente tu cuenta y todos los paseos guardados? No se puede deshacer.';

  @override
  String get goPremium => 'Hazte Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'Lo mejor de tus paseos';

  @override
  String get premiumModel => 'Narración más rica y de mayor calidad';

  @override
  String get premiumNoAds => 'Sin anuncios';

  @override
  String get premiumUnlimitedTours => 'Tours ilimitados cada día';

  @override
  String get premiumUnlimitedSaves => 'Paseos guardados ilimitados';

  @override
  String get premiumMonthly => 'Mensual';

  @override
  String get premiumYearly => 'Anual';

  @override
  String get premiumRestore => 'Restaurar compras';

  @override
  String get manageSubscription => 'Gestionar suscripción';

  @override
  String get premiumActive => 'Premium activo';

  @override
  String get historyFullTitle => 'El historial está lleno';

  @override
  String historyFullBody(int count) {
    return 'Las cuentas gratuitas guardan tus últimos $count paseos. Hazte Premium para un historial ilimitado.';
  }

  @override
  String get dailyLimitTitle => 'Sin tours gratis por hoy';

  @override
  String dailyLimitBody(int count) {
    return 'Las cuentas gratuitas tienen $count tours al día. Hazte Premium para tours ilimitados — y sin anuncios.';
  }

  @override
  String get confirmPasswordLabel => 'Confirmar contraseña';

  @override
  String get emailRequired => 'Introduce tu correo';

  @override
  String get emailInvalid => 'Introduce un correo válido';

  @override
  String get passwordRequired => 'Introduce tu contraseña';

  @override
  String passwordTooShort(int count) {
    return 'La contraseña debe tener al menos $count caracteres';
  }

  @override
  String get passwordsDontMatch => 'Las contraseñas no coinciden';

  @override
  String get forgotPassword => '¿Olvidaste tu contraseña?';

  @override
  String get resetPasswordTitle => 'Restablecer contraseña';

  @override
  String get resetPasswordHint => 'Introduce el correo de tu cuenta';

  @override
  String get resetPasswordSend => 'Enviar enlace';

  @override
  String get resetEmailSent =>
      'Enlace de restablecimiento enviado. Revisa tu correo.';

  @override
  String get authErrorInvalidCredentials => 'Correo o contraseña incorrectos.';

  @override
  String get authErrorEmailInUse => 'Ese correo ya está registrado.';

  @override
  String get authErrorWeakPassword => 'Elige una contraseña más segura.';

  @override
  String get authErrorRateLimited =>
      'Demasiados intentos. Inténtalo más tarde.';

  @override
  String get authErrorNetwork =>
      'Error de red. Comprueba tu conexión e inténtalo de nuevo.';

  @override
  String get authErrorGeneric => 'Algo salió mal. Inténtalo de nuevo.';

  @override
  String get cancelSubscription => 'Cancelar suscripción';

  @override
  String get bgFinish => 'Finalizar';

  @override
  String get greetMorning => 'Buenos días,';

  @override
  String get greetAfternoon => 'Buenas tardes,';

  @override
  String get greetEvening => 'Buenas noches,';

  @override
  String get greetNight => 'Buenas noches,';

  @override
  String get homePrompt => '¿a dónde hoy?';

  @override
  String get homeGuest => 'Viajero';

  @override
  String get swipeToStart => 'Vamos';

  @override
  String get tabHome => 'Inicio';

  @override
  String get tabCommunity => 'Comunidad';

  @override
  String get tabProfile => 'Perfil';

  @override
  String get themeLabel => 'Tema';

  @override
  String get focusTitle => 'ENFOCAR EN';

  @override
  String get premiumTrial => '1 semana gratis';

  @override
  String profileLevelN(int n) {
    return 'Nivel $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return 'para nivel $level · $xp XP';
  }

  @override
  String get profileAtMax => 'Nivel máximo alcanzado';

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
  String get achievements => 'LOGROS';

  @override
  String get friends => 'Amigos';

  @override
  String get invite => 'Invitar';

  @override
  String get friendsSoon => 'Los amigos llegan pronto';

  @override
  String get statsSoon => 'Tus estadísticas aparecerán aquí';

  @override
  String get communitySoonTitle => 'La comunidad llega pronto';

  @override
  String get communitySoonBody =>
      'Aquí vivirán amigos, rutas compartidas y desafíos.';

  @override
  String get sectionAccount => 'Cuenta';

  @override
  String get sectionDeveloper => 'Desarrollador';
}
