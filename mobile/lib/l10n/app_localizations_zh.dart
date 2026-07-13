// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Chinese (`zh`).
class AppLocalizationsZh extends AppLocalizations {
  AppLocalizationsZh([String locale = 'zh']) : super(locale);

  @override
  String get bgNotifTitle => 'AI Audio Guide';

  @override
  String get bgNotifText => '正在为您讲述周围的地点';

  @override
  String get bgNotifPaused => '导览已暂停';

  @override
  String get bgPause => '暂停';

  @override
  String get bgResume => '继续';

  @override
  String get connect => '连接';

  @override
  String get disconnect => '断开';

  @override
  String get startWalk => '漫步';

  @override
  String get startGps => 'GPS';

  @override
  String get stop => '停止';

  @override
  String get ask => '提问';

  @override
  String get askHint => '向导游提问……（例如：跳过商店）';

  @override
  String get micAsk => '语音提问';

  @override
  String get micStop => '停止并发送';

  @override
  String get clearFeed => '清空记录';

  @override
  String get voiceOn => '已开启朗读';

  @override
  String get voiceOff => '已关闭朗读';

  @override
  String get language => '语言';

  @override
  String get settings => '设置';

  @override
  String get history => '历史';

  @override
  String get simulatedWalk => '模拟漫步（演示）';

  @override
  String get compassNorth => '朝向正北';

  @override
  String get emptyHint => '点击“漫步”。\n导游会为你讲解周围的地点。';

  @override
  String get following => '正在跟随你';

  @override
  String get freeBrowse => '自由浏览——点击以跟随';

  @override
  String get appearance => '外观';

  @override
  String get themeSystem => '跟随系统';

  @override
  String get themeLight => '浅色';

  @override
  String get themeDark => '深色';

  @override
  String get themeTopic => '导览主题';

  @override
  String get themeAuto => '自动';

  @override
  String get themeHistory => '历史';

  @override
  String get themeArchitecture => '建筑';

  @override
  String get themePeople => '人物';

  @override
  String get themeCulture => '文化';

  @override
  String get themeLegends => '传说';

  @override
  String get route => '路线';

  @override
  String get walkHistory => '漫步记录';

  @override
  String get walkHistoryEmptyTitle => '还没有漫步记录';

  @override
  String get walkHistoryEmptySubtitle => '账号功能上线后，你过去的漫步会显示在这里。';

  @override
  String get nearbyHint => '走近一些，导游就会为你讲解。';

  @override
  String get zoomIn => '放大';

  @override
  String get zoomOut => '缩小';

  @override
  String get chipReconnecting => '正在重连……';

  @override
  String get chipNotConnected => '未连接';

  @override
  String get chipSpeaking => '正在朗读';

  @override
  String get chipScoring => '分析中';

  @override
  String get chipNarrating => '讲解中';

  @override
  String get chipSwitching => '切换中';

  @override
  String get chipListening => '聆听中';

  @override
  String get chipAnswering => '回答中';

  @override
  String get chipExpanding => '扩大范围';

  @override
  String get chipReady => '就绪';

  @override
  String get chipError => '数据源不可用';

  @override
  String get chipOffline => '离线';

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
    return '连接断开，$seconds 秒后重连……';
  }

  @override
  String get metaGeoDisabled => '系统中已关闭定位';

  @override
  String get metaGeoNoPermission => '没有定位权限';

  @override
  String metaGpsUnavailable(String error) {
    return '此平台不支持 GPS：$error';
  }

  @override
  String metaGpsError(String error) {
    return 'GPS：$error';
  }

  @override
  String get metaRealGpsOn => '已开启真实 GPS';

  @override
  String get metaMicNoPermission => '没有麦克风权限';

  @override
  String metaVoiceUnavailable(String lang) {
    return '此设备不支持 $lang 语音';
  }

  @override
  String get signIn => '登录';

  @override
  String get signOut => '退出登录';

  @override
  String signedInAs(String email) {
    return '已登录：$email';
  }

  @override
  String get loginSubtitle => '登录后即可保存并回顾你的散步。';

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
  String get continueWithGoogle => '使用 Google 继续';

  @override
  String get continueWithApple => '使用 Apple 继续';

  @override
  String get emailLabel => '电子邮件';

  @override
  String get passwordLabel => '密码';

  @override
  String get createAccount => '创建账户';

  @override
  String get continueAsGuest => '以访客身份继续';

  @override
  String get orSeparator => '或';

  @override
  String authFailed(String error) {
    return '登录失败：$error';
  }

  @override
  String get signUpCheckEmail => '请查收邮件以确认你的账户。';

  @override
  String get historySignInPrompt => '登录后即可查看已保存的散步。';

  @override
  String get historyLoadError => '无法加载你的散步。';

  @override
  String get retry => '重试';

  @override
  String placesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count 个地点',
      one: '1 个地点',
      zero: '没有地点',
    );
    return '$_temp0';
  }

  @override
  String get deleteWalk => '删除散步';

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
  String get deleteWalkConfirm => '删除这次散步？此操作无法撤销。';

  @override
  String get delete => '删除';

  @override
  String get cancel => '取消';

  @override
  String get deleteAccount => '删除账户';

  @override
  String get deleteAccountConfirm => '永久删除你的账户和所有已保存的散步？此操作无法撤销。';

  @override
  String get goPremium => '升级 Premium';

  @override
  String get premiumTitle => 'AI Guide Premium';

  @override
  String get premiumTagline => '让你的漫步更精彩';

  @override
  String get premiumModel => '更丰富、更高质量的讲解';

  @override
  String get premiumNoAds => '无广告';

  @override
  String get premiumUnlimitedTours => '每天无限次讲解';

  @override
  String get premiumUnlimitedSaves => '无限保存漫步记录';

  @override
  String get premiumMonthly => '按月';

  @override
  String get premiumYearly => '按年';

  @override
  String get premiumRestore => '恢复购买';

  @override
  String get manageSubscription => '管理订阅';

  @override
  String get premiumActive => 'Premium 已开通';

  @override
  String get historyFullTitle => '历史记录已满';

  @override
  String historyFullBody(int count) {
    return '免费账户仅保留最近 $count 次漫步。升级 Premium 可获得无限历史记录。';
  }

  @override
  String get dailyLimitTitle => '今日免费讲解已用完';

  @override
  String dailyLimitBody(int count) {
    return '免费账户每天有 $count 次讲解。升级 Premium 可无限畅听 — 且无广告。';
  }

  @override
  String get confirmPasswordLabel => '确认密码';

  @override
  String get emailRequired => '请输入邮箱';

  @override
  String get emailInvalid => '请输入有效的邮箱地址';

  @override
  String get passwordRequired => '请输入密码';

  @override
  String passwordTooShort(int count) {
    return '密码至少需要 $count 个字符';
  }

  @override
  String get passwordsDontMatch => '两次输入的密码不一致';

  @override
  String get forgotPassword => '忘记密码？';

  @override
  String get resetPasswordTitle => '重置密码';

  @override
  String get resetPasswordHint => '请输入你的账号邮箱';

  @override
  String get resetPasswordSend => '发送链接';

  @override
  String get resetEmailSent => '重置密码链接已发送，请查收邮件。';

  @override
  String get authErrorInvalidCredentials => '邮箱或密码错误。';

  @override
  String get authErrorEmailInUse => '该邮箱已注册。';

  @override
  String get authErrorWeakPassword => '请设置更安全的密码。';

  @override
  String get authErrorRateLimited => '尝试次数过多，请稍后再试。';

  @override
  String get authErrorNetwork => '网络错误，请检查网络后重试。';

  @override
  String get authErrorGeneric => '出了点问题，请重试。';

  @override
  String get cancelSubscription => '取消订阅';

  @override
  String get bgFinish => '结束';

  @override
  String get greetMorning => '早上好，';

  @override
  String get greetAfternoon => '下午好，';

  @override
  String get greetEvening => '晚上好，';

  @override
  String get greetNight => '晚安，';

  @override
  String get homePrompt => '今天去哪儿？';

  @override
  String get homeGuest => '旅行者';

  @override
  String get swipeToStart => '出发';

  @override
  String get tabHome => '首页';

  @override
  String get tabCommunity => '社区';

  @override
  String get tabProfile => '我的';

  @override
  String get themeLabel => '主题';

  @override
  String get focusTitle => '重点关注';

  @override
  String get premiumTrial => '免费一周';

  @override
  String profileLevelN(int n) {
    return '等级 $n';
  }

  @override
  String profileToNext(int level, int xp) {
    return '距 $level 级 · $xp XP';
  }

  @override
  String get profileAtMax => '已达最高等级';

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
  String get achievements => '成就';

  @override
  String get friends => '好友';

  @override
  String get invite => '邀请';

  @override
  String get friendsSoon => '好友功能即将上线';

  @override
  String get statsSoon => '你的统计将显示在这里';

  @override
  String get communitySoonTitle => '社区即将上线';

  @override
  String get communitySoonBody => '好友、共享路线和挑战将在这里呈现。';

  @override
  String get sectionAccount => '账户';

  @override
  String get sectionDeveloper => '开发者';
}
