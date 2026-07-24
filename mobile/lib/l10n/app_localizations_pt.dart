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

  @override
  String get greetMorning => 'Bom dia,';

  @override
  String get greetAfternoon => 'Boa tarde,';

  @override
  String get greetEvening => 'Boa noite,';

  @override
  String get greetNight => 'Boa noite,';

  @override
  String get homePrompt => 'para onde hoje?';

  @override
  String get homeGuest => 'Viajante';

  @override
  String get swipeToStart => 'Vamos';

  @override
  String get tabHome => 'Início';

  @override
  String get tabCommunity => 'Comunidade';

  @override
  String get tabProfile => 'Perfil';

  @override
  String get themeLabel => 'Tema';

  @override
  String get focusTitle => 'FOCAR EM';

  @override
  String get premiumTrial => '1 semana grátis';

  @override
  String profileLevelN(int n) {
    return 'Nível $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return 'até o nível $level · $xp XP';
  }

  @override
  String get profileAtMax => 'Nível máximo atingido';

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
  String get achievements => 'CONQUISTAS';

  @override
  String get friends => 'Amigos';

  @override
  String get invite => 'Convidar';

  @override
  String get friendsSoon => 'Amigos chegam em breve';

  @override
  String get statsSoon => 'Suas estatísticas aparecerão aqui';

  @override
  String get communityStreakLeave => 'Sair da sequência';

  @override
  String get communityUnfriend => 'Remover dos amigos';

  @override
  String communityUnfriendConfirm(String name) {
    return 'Remover $name dos amigos?';
  }

  @override
  String communityRequestOutgoing(String handle) {
    return 'Pedido enviado: @$handle';
  }

  @override
  String get communityAlreadyFriends => 'Já são amigos';

  @override
  String feedWalkShared(String name) {
    return '$name compartilhou um passeio';
  }

  @override
  String get communityCoWalkShare => 'Compartilhar código';

  @override
  String communityCoWalkShareMsg(String code) {
    return 'Caminhe comigo no AI Guide! Código: $code';
  }

  @override
  String get sectionAccount => 'Conta';

  @override
  String get sectionDeveloper => 'Desenvolvedor';
}
