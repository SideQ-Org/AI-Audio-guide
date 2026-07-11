// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Russian (`ru`).
class AppLocalizationsRu extends AppLocalizations {
  AppLocalizationsRu([String locale = 'ru']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => 'Рассказываю о местах вокруг вас';

  @override
  String get bgNotifPaused => 'Экскурсия на паузе';

  @override
  String get bgPause => 'Пауза';

  @override
  String get bgResume => 'Продолжить';

  @override
  String get connect => 'Подключиться';

  @override
  String get disconnect => 'Отключиться';

  @override
  String get startWalk => 'Прогулка';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => 'Стоп';

  @override
  String get ask => 'Спросить';

  @override
  String get askHint => 'Спросить гида… (напр. пропускай магазины)';

  @override
  String get micAsk => 'Спросить голосом';

  @override
  String get micStop => 'Остановить и отправить';

  @override
  String get clearFeed => 'Очистить ленту';

  @override
  String get voiceOn => 'Озвучка включена';

  @override
  String get voiceOff => 'Озвучка выключена';

  @override
  String get language => 'Язык';

  @override
  String get settings => 'Настройки';

  @override
  String get history => 'История';

  @override
  String get simulatedWalk => 'Симуляция прогулки (демо)';

  @override
  String get compassNorth => 'На север';

  @override
  String get emptyHint =>
      'Нажмите «Прогулка».\nГид расскажет про места вокруг.';

  @override
  String get following => 'Следую за вами';

  @override
  String get freeBrowse => 'Свободный просмотр — нажмите, чтобы следовать';

  @override
  String get appearance => 'Оформление';

  @override
  String get themeSystem => 'Система';

  @override
  String get themeLight => 'Светлая';

  @override
  String get themeDark => 'Тёмная';

  @override
  String get themeTopic => 'Тема рассказа';

  @override
  String get themeAuto => 'Авто';

  @override
  String get themeHistory => 'История';

  @override
  String get themeArchitecture => 'Архитектура';

  @override
  String get themePeople => 'Люди';

  @override
  String get themeCulture => 'Культура';

  @override
  String get themeLegends => 'Легенды';

  @override
  String get route => 'Маршрут';

  @override
  String get walkHistory => 'Истории прогулок';

  @override
  String get walkHistoryEmptyTitle => 'Пока нет прогулок';

  @override
  String get walkHistoryEmptySubtitle =>
      'Ваши прошлые прогулки появятся здесь, когда добавим аккаунты.';

  @override
  String get nearbyHint => 'Подойдите ближе — гид расскажет о нём.';

  @override
  String get zoomIn => 'Приблизить';

  @override
  String get zoomOut => 'Отдалить';

  @override
  String get chipReconnecting => 'переподключение…';

  @override
  String get chipNotConnected => 'не подключено';

  @override
  String get chipSpeaking => 'говорит';

  @override
  String get chipScoring => 'анализ';

  @override
  String get chipNarrating => 'рассказ';

  @override
  String get chipSwitching => 'переключение';

  @override
  String get chipListening => 'слушает';

  @override
  String get chipAnswering => 'отвечает';

  @override
  String get chipExpanding => 'расширяет радиус';

  @override
  String get chipReady => 'готов';

  @override
  String get chipError => 'источник недоступен';

  @override
  String get chipOffline => 'оффлайн';

  @override
  String metaConnectionLost(int seconds) {
    return 'Связь потеряна, переподключение через ${seconds}s…';
  }

  @override
  String get metaGeoDisabled => 'Геолокация выключена в системе';

  @override
  String get metaGeoNoPermission => 'Нет разрешения на геолокацию';

  @override
  String metaGpsUnavailable(String error) {
    return 'GPS недоступен на этой платформе: $error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS: $error';
  }

  @override
  String get metaRealGpsOn => 'Реальный GPS включён';

  @override
  String get metaMicNoPermission => 'Нет доступа к микрофону';

  @override
  String metaVoiceUnavailable(String lang) {
    return 'Голос $lang недоступен на устройстве';
  }

  @override
  String get signIn => 'Войти';

  @override
  String get signOut => 'Выйти';

  @override
  String signedInAs(String email) {
    return 'Вы вошли как $email';
  }

  @override
  String get loginSubtitle =>
      'Войдите, чтобы сохранять прогулки и возвращаться к ним.';

  @override
  String get loginWelcomeTitle => 'С возвращением';

  @override
  String get loginWelcomeSubtitle =>
      'Открой приложение — и просто иди. Гид расскажет о том, что вокруг.';

  @override
  String get loginNewHere => 'Впервые тут?';

  @override
  String get registerSubtitle =>
      'Создай аккаунт, чтобы сохранять прогулки и возвращаться к ним.';

  @override
  String get haveAccount => 'Уже есть аккаунт?';

  @override
  String get nickLabel => 'Ник';

  @override
  String get birthdayLabel => 'Дата рождения';

  @override
  String get birthdayOptional => 'Дата рождения · необязательно';

  @override
  String get avatarChoose => 'Добавить фото · необязательно';

  @override
  String get registerPremiumTitle => 'Оформить Premium сразу';

  @override
  String get registerPremiumSub => 'Весь гид без ограничений.';

  @override
  String get otpTitle => 'Подтвердите почту';

  @override
  String otpSentTo(String email) {
    return 'Мы отправили 6-значный код на $email. Введите его ниже, чтобы завершить.';
  }

  @override
  String get otpCodeLabel => 'Код из письма';

  @override
  String get otpConfirm => 'Подтвердить';

  @override
  String get otpResend => 'Отправить код снова';

  @override
  String get otpResent => 'Код отправлен ещё раз';

  @override
  String get otpInvalid => 'Неверный или просроченный код.';

  @override
  String get orWithEmail => 'или почтой';

  @override
  String get continueWithGoogle => 'Продолжить с Google';

  @override
  String get continueWithApple => 'Продолжить с Apple';

  @override
  String get emailLabel => 'Эл. почта';

  @override
  String get passwordLabel => 'Пароль';

  @override
  String get createAccount => 'Создать аккаунт';

  @override
  String get continueAsGuest => 'Продолжить как гость';

  @override
  String get orSeparator => 'или';

  @override
  String authFailed(String error) {
    return 'Не удалось войти: $error';
  }

  @override
  String get signUpCheckEmail => 'Проверьте почту, чтобы подтвердить аккаунт.';

  @override
  String get historySignInPrompt =>
      'Войдите, чтобы увидеть сохранённые прогулки.';

  @override
  String get historyLoadError => 'Не удалось загрузить прогулки.';

  @override
  String get retry => 'Повторить';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count объектов',
      one: '1 объект',
      zero: 'Нет объектов',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => 'Удалить прогулку';

  @override
  String get deleteWalkConfirm => 'Удалить эту прогулку? Действие необратимо.';

  @override
  String get delete => 'Удалить';

  @override
  String get cancel => 'Отмена';

  @override
  String get deleteAccount => 'Удалить аккаунт';

  @override
  String get deleteAccountConfirm =>
      'Безвозвратно удалить аккаунт и все сохранённые прогулки? Действие необратимо.';

  @override
  String get goPremium => 'Оформить Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => 'Максимум от ваших прогулок';

  @override
  String get premiumModel => 'Более богатый и качественный рассказ';

  @override
  String get premiumNoAds => 'Без рекламы';

  @override
  String get premiumUnlimitedTours => 'Безлимитные прогулки каждый день';

  @override
  String get premiumUnlimitedSaves => 'Безлимитная история прогулок';

  @override
  String get premiumMonthly => 'Месяц';

  @override
  String get premiumYearly => 'Год';

  @override
  String get premiumRestore => 'Восстановить покупки';

  @override
  String get manageSubscription => 'Управление подпиской';

  @override
  String get premiumActive => 'Premium активен';

  @override
  String get historyFullTitle => 'История заполнена';

  @override
  String historyFullBody(int count) {
    return 'На бесплатном аккаунте хранятся последние $count прогулок. Оформите Premium для безлимитной истории.';
  }

  @override
  String get dailyLimitTitle => 'Бесплатные прогулки на сегодня закончились';

  @override
  String dailyLimitBody(int count) {
    return 'На бесплатном аккаунте доступно $count прогулок в день. Оформите Premium — безлимит и без рекламы.';
  }

  @override
  String get confirmPasswordLabel => 'Повторите пароль';

  @override
  String get emailRequired => 'Введите email';

  @override
  String get emailInvalid => 'Введите корректный email';

  @override
  String get passwordRequired => 'Введите пароль';

  @override
  String passwordTooShort(int count) {
    return 'Пароль должен быть не короче $count символов';
  }

  @override
  String get passwordsDontMatch => 'Пароли не совпадают';

  @override
  String get forgotPassword => 'Забыли пароль?';

  @override
  String get resetPasswordTitle => 'Сброс пароля';

  @override
  String get resetPasswordHint => 'Введите email вашего аккаунта';

  @override
  String get resetPasswordSend => 'Отправить ссылку';

  @override
  String get resetEmailSent =>
      'Ссылка для сброса пароля отправлена. Проверьте почту.';

  @override
  String get authErrorInvalidCredentials => 'Неверный email или пароль.';

  @override
  String get authErrorEmailInUse => 'Этот email уже зарегистрирован.';

  @override
  String get authErrorWeakPassword => 'Выберите более надёжный пароль.';

  @override
  String get authErrorRateLimited => 'Слишком много попыток. Повторите позже.';

  @override
  String get authErrorNetwork =>
      'Ошибка сети. Проверьте подключение и повторите.';

  @override
  String get authErrorGeneric => 'Что-то пошло не так. Попробуйте ещё раз.';

  @override
  String get cancelSubscription => 'Отменить подписку';

  @override
  String get bgFinish => 'Завершить';

  @override
  String get greetMorning => 'Доброе утро,';

  @override
  String get greetAfternoon => 'Добрый день,';

  @override
  String get greetEvening => 'Добрый вечер,';

  @override
  String get greetNight => 'Доброй ночи,';

  @override
  String get homePrompt => 'куда сегодня?';

  @override
  String get homeGuest => 'Путешественник';

  @override
  String get swipeToStart => 'Поехали';

  @override
  String get tabHome => 'Главная';

  @override
  String get tabCommunity => 'Комьюнити';

  @override
  String get tabProfile => 'Профиль';

  @override
  String get themeLabel => 'Тема';

  @override
  String get focusTitle => 'НА ЧЁМ ДЕЛАЕМ УПОР';

  @override
  String get premiumTrial => '1 неделя бесплатно';

  @override
  String profileLevelN(int n) {
    return 'Уровень $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return 'до $level уровня · $xp XP';
  }

  @override
  String get profileAtMax => 'Максимальный уровень';

  @override
  String get close => 'Закрыть';

  @override
  String get communityGuest =>
      'Войдите, чтобы видеть друзей, маршруты и челленджи.';

  @override
  String get communityChallenges => 'Состязания';

  @override
  String get communityCreateChallenge => 'Создать состязание';

  @override
  String get communityNoChallenges => 'Пока нет активных состязаний.';

  @override
  String get communityFriendsRoutes => 'Маршруты друзей';

  @override
  String get communityFriends => 'Друзья';

  @override
  String get communityAddFriend => 'Добавить';

  @override
  String get communityWalkingNow => 'на прогулке';

  @override
  String get communityNoFriends => 'Пока никого — добавь друзей по нику.';

  @override
  String get communityRequests => 'Заявки в друзья';

  @override
  String get communityAccept => 'Принять';

  @override
  String get communityDecline => 'Отклонить';

  @override
  String get communityJoin => 'Участвовать';

  @override
  String get communityPickHandleTitle => 'Придумай ник';

  @override
  String get communityPickHandleBody => 'По нику друзья смогут тебя найти.';

  @override
  String get communityHandleField => 'ник';

  @override
  String get communityHandleSave => 'Сохранить';

  @override
  String get communityHandleTaken => 'Ник занят или некорректен';

  @override
  String get communityRequestSent => 'Заявка отправлена';

  @override
  String get communitySearchHandle => 'Поиск по нику';

  @override
  String get communitySendRequest => 'Добавить';

  @override
  String get communityChallengeTitle => 'Название';

  @override
  String get communityMetric => 'Метрика';

  @override
  String get communityMetricDistance => 'Километры';

  @override
  String get communityMetricPlaces => 'Места';

  @override
  String get communityMetricDistricts => 'Районы';

  @override
  String get communityGoalLabel => 'Цель';

  @override
  String get communityDaysLabel => 'Дней';

  @override
  String get communityLeaderboard => 'Таблица лидеров';

  @override
  String get communityNoParticipants => 'Пока нет участников.';

  @override
  String communityRankPlace(int rank) {
    return '$rank место';
  }

  @override
  String communityGoalKm(int km) {
    return '$km км';
  }

  @override
  String communityGoalPlaces(int count) {
    return '$count мест';
  }

  @override
  String communityGoalDistricts(int count) {
    return '$count районов';
  }

  @override
  String feedWalked(String name) {
    return '$name вышел на прогулку';
  }

  @override
  String feedWalkedIn(String name, String city) {
    return '$name гулял по $city';
  }

  @override
  String feedStreak(String name, int days) {
    return '$name — стрик $days дней';
  }

  @override
  String feedBadge(String name, String badge) {
    return '$name получил «$badge»';
  }

  @override
  String feedChallenge(String name) {
    return '$name — новое состязание';
  }

  @override
  String xpValue(int n) {
    return '$n XP';
  }

  @override
  String get achievements => 'ДОСТИЖЕНИЯ';

  @override
  String get friends => 'Друзья';

  @override
  String get invite => 'Пригласить';

  @override
  String get friendsSoon => 'Друзья скоро появятся';

  @override
  String get statsSoon => 'Здесь появится ваша статистика';

  @override
  String get communitySoonTitle => 'Комьюнити скоро';

  @override
  String get communitySoonBody =>
      'Здесь появятся друзья, общие маршруты и челленджи.';

  @override
  String get sectionAccount => 'Аккаунт';

  @override
  String get sectionDeveloper => 'Разработчик';
}
