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
}
