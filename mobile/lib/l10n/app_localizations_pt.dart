// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Portuguese (`pt`).
class AppLocalizationsPt extends AppLocalizations {
  AppLocalizationsPt([String locale = 'pt']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Contando sobre os lugares ao seu redor';

  @override
  String get bgNotifPaused => 'Passeio em pausa';

  @override
  String get bgPause => 'Pausar';

  @override
  String get bgResume => 'Retomar';

  @override
  String get connect => 'Conectar';

  @override
  String get disconnect => 'Desconectar';

  @override
  String get startWalk => 'Passeio';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Parar';

  @override
  String get ask => 'Perguntar';

  @override
  String get askHint => 'Pergunte ao guia… (ex.: pule as lojas)';

  @override
  String get micAsk => 'Perguntar por voz';

  @override
  String get micStop => 'Parar e enviar';

  @override
  String get clearFeed => 'Limpar o histórico';

  @override
  String get voiceOn => 'Narração ativada';

  @override
  String get voiceOff => 'Narração desativada';

  @override
  String get language => 'Idioma';

  @override
  String get settings => 'Configurações';

  @override
  String get history => 'Histórico';

  @override
  String get simulatedWalk => 'Passeio simulado (demo)';

  @override
  String get compassNorth => 'Orientar ao norte';

  @override
  String get emptyHint =>
      'Toque em «Passeio».\nO guia vai falar sobre os lugares ao seu redor.';

  @override
  String get following => 'Seguindo você';

  @override
  String get freeBrowse => 'Navegação livre — toque para seguir';

  @override
  String get appearance => 'Aparência';

  @override
  String get themeSystem => 'Sistema';

  @override
  String get themeLight => 'Claro';

  @override
  String get themeDark => 'Escuro';

  @override
  String get themeTopic => 'Tema do passeio';

  @override
  String get themeAuto => 'Auto';

  @override
  String get themeHistory => 'História';

  @override
  String get themeArchitecture => 'Arquitetura';

  @override
  String get themePeople => 'Pessoas';

  @override
  String get themeCulture => 'Cultura';

  @override
  String get themeLegends => 'Lendas';

  @override
  String get route => 'Rota';

  @override
  String get walkHistory => 'Histórico de passeios';

  @override
  String get walkHistoryEmptyTitle => 'Ainda sem passeios';

  @override
  String get walkHistoryEmptySubtitle =>
      'Seus passeios anteriores aparecerão aqui quando as contas chegarem.';

  @override
  String get nearbyHint => 'Chegue mais perto e o guia vai falar sobre ele.';

  @override
  String get zoomIn => 'Aproximar';

  @override
  String get zoomOut => 'Afastar';

  @override
  String get chipReconnecting => 'reconectando…';

  @override
  String get chipNotConnected => 'sem conexão';

  @override
  String get chipSpeaking => 'falando';

  @override
  String get chipScoring => 'analisando';

  @override
  String get chipNarrating => 'narrando';

  @override
  String get chipSwitching => 'alternando';

  @override
  String get chipListening => 'ouvindo';

  @override
  String get chipAnswering => 'respondendo';

  @override
  String get chipExpanding => 'ampliando o raio';

  @override
  String get chipReady => 'pronto';

  @override
  String get chipError => 'fonte indisponível';

  @override
  String get chipOffline => 'offline';

  @override
  String metaConnectionLost(int seconds) {
    return 'conexão perdida, reconectando em ${seconds}s…';
  }

  @override
  String get metaGeoDisabled => 'A localização está desativada no sistema';

  @override
  String get metaGeoNoPermission => 'Sem permissão de localização';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS indisponível nesta plataforma: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'GPS real ativado';

  @override
  String get metaMicNoPermission => 'Sem acesso ao microfone';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'a voz para $lang não está disponível neste dispositivo';
  }

  @override
  String get signIn => 'Entrar';

  @override
  String get signOut => 'Sair';

  @override
  String signedInAs(String email) {
    return 'Conectado como $email';
  }

  @override
  String get loginSubtitle => 'Entre para salvar seus passeios e revê-los.';

  @override
  String get continueWithGoogle => 'Continuar com o Google';

  @override
  String get continueWithApple => 'Continuar com a Apple';

  @override
  String get emailLabel => 'E-mail';

  @override
  String get passwordLabel => 'Senha';

  @override
  String get createAccount => 'Criar conta';

  @override
  String get continueAsGuest => 'Continuar como convidado';

  @override
  String get orSeparator => 'ou';

  @override
  String authFailed(String error) {
    return 'Falha ao entrar: $error';
  }

  @override
  String get signUpCheckEmail =>
      'Verifique seu e-mail para confirmar sua conta.';

  @override
  String get historySignInPrompt => 'Entre para ver seus passeios salvos.';

  @override
  String get historyLoadError => 'Não foi possível carregar seus passeios.';

  @override
  String get retry => 'Tentar novamente';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count lugares',
      one: '1 lugar',
      zero: 'Nenhum lugar',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Excluir passeio';

  @override
  String get deleteWalkConfirm =>
      'Excluir este passeio? Não pode ser desfeito.';

  @override
  String get delete => 'Excluir';

  @override
  String get cancel => 'Cancelar';

  @override
  String get deleteAccount => 'Excluir conta';

  @override
  String get deleteAccountConfirm =>
      'Excluir permanentemente sua conta e todos os passeios salvos? Não pode ser desfeito.';

  @override
  String get goPremium => 'Assinar Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'O melhor dos seus passeios';

  @override
  String get premiumModel => 'Narração mais rica e de maior qualidade';

  @override
  String get premiumNoAds => 'Sem anúncios';

  @override
  String get premiumUnlimitedTours => 'Tours ilimitados todos os dias';

  @override
  String get premiumUnlimitedSaves => 'Passeios salvos ilimitados';

  @override
  String get premiumMonthly => 'Mensal';

  @override
  String get premiumYearly => 'Anual';

  @override
  String get premiumRestore => 'Restaurar compras';

  @override
  String get manageSubscription => 'Gerenciar assinatura';

  @override
  String get premiumActive => 'Premium ativo';

  @override
  String get historyFullTitle => 'O histórico está cheio';

  @override
  String historyFullBody(int count) {
    return 'Contas gratuitas guardam seus últimos $count passeios. Assine o Premium para histórico ilimitado.';
  }

  @override
  String get dailyLimitTitle => 'Sem tours gratuitos hoje';

  @override
  String dailyLimitBody(int count) {
    return 'Contas gratuitas têm $count tours por dia. Assine o Premium para tours ilimitados — e sem anúncios.';
  }

  @override
  String get confirmPasswordLabel => 'Confirmar senha';

  @override
  String get emailRequired => 'Digite seu e-mail';

  @override
  String get emailInvalid => 'Digite um e-mail válido';

  @override
  String get passwordRequired => 'Digite sua senha';

  @override
  String passwordTooShort(int count) {
    return 'A senha deve ter pelo menos $count caracteres';
  }

  @override
  String get passwordsDontMatch => 'As senhas não coincidem';

  @override
  String get forgotPassword => 'Esqueceu a senha?';

  @override
  String get resetPasswordTitle => 'Redefinir senha';

  @override
  String get resetPasswordHint => 'Digite o e-mail da sua conta';

  @override
  String get resetPasswordSend => 'Enviar link';

  @override
  String get resetEmailSent =>
      'Link de redefinição enviado. Verifique seu e-mail.';

  @override
  String get authErrorInvalidCredentials => 'E-mail ou senha incorretos.';

  @override
  String get authErrorEmailInUse => 'Esse e-mail já está registrado.';

  @override
  String get authErrorWeakPassword => 'Escolha uma senha mais forte.';

  @override
  String get authErrorRateLimited =>
      'Muitas tentativas. Tente novamente mais tarde.';

  @override
  String get authErrorNetwork =>
      'Erro de rede. Verifique sua conexão e tente novamente.';

  @override
  String get authErrorGeneric => 'Algo deu errado. Tente novamente.';

  @override
  String get cancelSubscription => 'Cancelar assinatura';

  @override
  String get bgFinish => 'Encerrar';
}
