// Social screens: read-only friend profile, friends list (+ search / recommendations),
// and the invite screen (referral QR + share). Presentational; data + callbacks come
// from the caller. Strings are RU for now (l10n is a follow-up like the rest).

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:share_plus/share_plus.dart';

import 'achievements.dart';
import 'components.dart';
import 'design.dart';
import 'level.dart';

typedef Friend = ({String id, String nick, int walks, bool paid});

Widget _brandHeader(BuildContext context, String title, {List<Widget> actions = const []}) {
  final c = context.colors;
  return Padding(
    padding: const EdgeInsets.fromLTRB(6, 6, 8, 8),
    child: Row(children: [
      IconButton(
        icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
        onPressed: () => Navigator.of(context).maybePop(),
      ),
      Expanded(child: Text(title, style: h2(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
      ...actions,
    ]),
  );
}

Color _blockFill(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark ? context.colors.glass : const Color(0x8CFFFFFF);

// ── read-only friend profile ─────────────────────────────────────────────────
class FriendProfileScreen extends StatelessWidget {
  final Friend friend;
  const FriendProfileScreen({super.key, required this.friend});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final fill = _blockFill(context);
    final lvl = LevelInfo.fromWalks(friend.walks);
    final achs = achievementsFor(ProfileStats(walks: friend.walks, signedIn: true));
    final got = unlockedCount(achs);
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            _brandHeader(context, 'Профиль'),
            Expanded(
              child: ListView(
                padding: EdgeInsets.fromLTRB(16, 4, 16, MediaQuery.of(context).padding.bottom + 24),
                children: [
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.fromLTRB(16, 18, 16, 20),
                    child: Column(children: [
                      _InitialAvatar(nick: friend.nick, size: 96, premium: friend.paid),
                      const SizedBox(height: 12),
                      Text(friend.nick, style: h2(context)),
                      const SizedBox(height: 3),
                      Text('Уровень ${lvl.level}', style: GoogleFonts.manrope(fontSize: 13, fontWeight: FontWeight.w800, color: c.primary)),
                      const SizedBox(height: 16),
                      XpBar(value: lvl.progress),
                    ]),
                  ),
                  const SizedBox(height: Gap.lg),
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.all(16),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        Text('ДОСТИЖЕНИЯ', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: .4, color: c.textPrimary)),
                        const Spacer(),
                        Text('$got / ${achs.length}', style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w800, color: c.primary)),
                      ]),
                      const SizedBox(height: 14),
                      _AchGrid(achs: achs),
                    ]),
                  ),
                  const SizedBox(height: Gap.lg),
                  AppButton('Написать', icon: Icons.chat_bubble_outline_rounded, kind: AppBtnKind.secondary,
                      onTap: () => ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Личные сообщения — скоро')))),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

// A simple even achievement grid (5 per row), read-only.
class _AchGrid extends StatelessWidget {
  final List<AchievementState> achs;
  const _AchGrid({required this.achs});
  @override
  Widget build(BuildContext context) {
    const per = 5;
    final rows = <Widget>[];
    for (var i = 0; i < achs.length; i += per) {
      final end = (i + per) > achs.length ? achs.length : (i + per);
      final slice = achs.sublist(i, end);
      rows.add(Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          for (final a in slice)
            GestureDetector(
              onTap: () => _showAch(context, a),
              child: AchievementBadge(emoji: a.def.emoji, locked: !a.unlocked),
            ),
          for (var k = slice.length; k < per; k++) const SizedBox(width: 48, height: 48),
        ]),
      ));
    }
    return Column(children: rows);
  }

  void _showAch(BuildContext context, AchievementState a) {
    final c = context.colors;
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) => CardSheet(
        child: Padding(
          padding: EdgeInsets.fromLTRB(20, 12, 20, MediaQuery.of(ctx).padding.bottom + 24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16), decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2))),
            AchievementBadge(emoji: a.def.emoji, locked: !a.unlocked),
            const SizedBox(height: 14),
            Text(a.def.title, style: h2(context), textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text(a.def.description, textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
          ]),
        ),
      ),
    );
  }
}

// ── friends list (search + recommendations) ──────────────────────────────────
class FriendsScreen extends StatefulWidget {
  final List<Friend> friends;
  final VoidCallback onInvite;
  const FriendsScreen({super.key, required this.friends, required this.onInvite});
  @override
  State<FriendsScreen> createState() => _FriendsScreenState();
}

class _FriendsScreenState extends State<FriendsScreen> {
  String _q = '';
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final fill = _blockFill(context);
    final shown = widget.friends.where((f) => f.nick.toLowerCase().contains(_q.toLowerCase())).toList();
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            _brandHeader(context, 'Друзья', actions: [
              IconButton(
                icon: Icon(Icons.person_add_alt_1_rounded, color: c.primary),
                tooltip: 'Пригласить',
                onPressed: widget.onInvite,
              ),
            ]),
            Expanded(
              child: ListView(
                padding: EdgeInsets.fromLTRB(16, 4, 16, MediaQuery.of(context).padding.bottom + 24),
                children: [
                  // search
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: TextField(
                      onChanged: (v) => setState(() => _q = v),
                      cursorColor: c.primary,
                      decoration: bareInput(
                        contentPadding: const EdgeInsets.symmetric(vertical: 14),
                        icon: Icon(Icons.search_rounded, color: c.textFaint),
                        hintText: 'Поиск друзей',
                      ),
                    ),
                  ),
                  const SizedBox(height: Gap.lg),
                  if (shown.isEmpty)
                    _empty(context, _q.isEmpty ? 'Пока нет друзей — пригласи первого!' : 'Ничего не найдено')
                  else
                    GlassModule(
                      fill: fill, sheen: false,
                      child: Column(children: [
                        for (var i = 0; i < shown.length; i++) ...[
                          if (i > 0) Divider(height: 1, thickness: 1, color: c.glassBorder, indent: 16, endIndent: 16),
                          _FriendRow(friend: shown[i]),
                        ],
                      ]),
                    ),
                  const SizedBox(height: Gap.xxl),
                  // recommended (by contacts) — needs contacts permission + backend matching.
                  Text('РЕКОМЕНДУЕМ', style: label(context)),
                  const SizedBox(height: 8),
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.all(18),
                    child: Column(children: [
                      Icon(Icons.contacts_rounded, size: 28, color: c.primary),
                      const SizedBox(height: 10),
                      Text('Найти друзей по контактам', style: titleS(context), textAlign: TextAlign.center),
                      const SizedBox(height: 6),
                      Text('Разреши доступ к контактам — покажем, кто из твоих знакомых уже в приложении.',
                          textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
                      const SizedBox(height: 14),
                      AppButton('Разрешить доступ', kind: AppBtnKind.secondary,
                          onTap: () => ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Рекомендации по контактам — скоро')))),
                    ]),
                  ),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }

  Widget _empty(BuildContext context, String text) => Container(
        height: 74, alignment: Alignment.center,
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(Radii.lg), border: Border.all(color: context.colors.glassBorder, width: 1.5)),
        child: Text(text, style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w600, color: context.colors.textFaint)),
      );
}

class _FriendRow extends StatelessWidget {
  final Friend friend;
  const _FriendRow({required this.friend});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final lvl = LevelInfo.fromWalks(friend.walks);
    return InkWell(
      onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => FriendProfileScreen(friend: friend))),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(children: [
          _InitialAvatar(nick: friend.nick, size: 44, premium: friend.paid),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(friend.nick, style: titleS(context)),
              Text('Уровень ${lvl.level}', style: caption(context)),
            ]),
          ),
          Icon(Icons.chevron_right_rounded, color: c.textFaint),
        ]),
      ),
    );
  }
}

// ── invite (referral QR + share) ─────────────────────────────────────────────
class InviteScreen extends StatelessWidget {
  final String inviteUrl;
  final String nick;
  const InviteScreen({super.key, required this.inviteUrl, required this.nick});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final fill = _blockFill(context);
    final message = '$nick зовёт тебя в AI Audio Guide — аудиогид, который сам рассказывает про места вокруг на прогулке. '
        'Скачай и добавимся в друзья: $inviteUrl';
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            _brandHeader(context, 'Пригласить друга'),
            Expanded(
              child: ListView(
                padding: EdgeInsets.fromLTRB(16, 4, 16, MediaQuery.of(context).padding.bottom + 24),
                children: [
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.fromLTRB(20, 22, 20, 22),
                    child: Column(children: [
                      Text('Покажи QR', style: titleS(context)),
                      const SizedBox(height: 4),
                      Text('Друг сканирует, скачивает приложение — и при регистрации сразу попадает к тебе в друзья.',
                          textAlign: TextAlign.center, style: body(context).copyWith(color: c.textSecondary, height: 1.4)),
                      const SizedBox(height: 18),
                      Container(
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(Radii.lg)),
                        child: QrImageView(
                          data: inviteUrl,
                          version: QrVersions.auto,
                          size: 200,
                          eyeStyle: const QrEyeStyle(eyeShape: QrEyeShape.circle, color: Color(0xFF20241C)),
                          dataModuleStyle: const QrDataModuleStyle(dataModuleShape: QrDataModuleShape.circle, color: Color(0xFF20241C)),
                        ),
                      ),
                    ]),
                  ),
                  const SizedBox(height: Gap.lg),
                  // link + copy
                  GlassModule(
                    fill: fill, sheen: false,
                    padding: const EdgeInsets.fromLTRB(16, 6, 6, 6),
                    child: Row(children: [
                      Expanded(child: Text(inviteUrl, maxLines: 1, overflow: TextOverflow.ellipsis, style: body(context).copyWith(color: c.textSecondary))),
                      IconButton(
                        icon: Icon(Icons.copy_rounded, size: 20, color: c.primary),
                        tooltip: 'Скопировать',
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: inviteUrl));
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Ссылка скопирована')));
                        },
                      ),
                    ]),
                  ),
                  const SizedBox(height: Gap.lg),
                  AppButton('Отправить приглашение', icon: Icons.ios_share_rounded,
                      onTap: () => Share.share(message)),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

// ── shared initial avatar ────────────────────────────────────────────────────
class _InitialAvatar extends StatelessWidget {
  final String nick;
  final double size;
  final bool premium;
  const _InitialAvatar({required this.nick, required this.size, this.premium = false});
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final circle = Container(
      width: size, height: size, alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [c.sage, c.primary]),
        border: Border.all(color: c.glassBorder, width: 3),
      ),
      child: Text(nick.characters.firstOrNull?.toUpperCase() ?? '?',
          style: GoogleFonts.manrope(fontSize: size * 0.4, fontWeight: FontWeight.w800, color: Colors.white)),
    );
    if (!premium) return circle;
    return SizedBox(
      width: size, height: size,
      child: Stack(clipBehavior: Clip.none, children: [
        circle,
        Positioned(right: -2, bottom: -2, child: PremiumBadge(size: size * 0.32)),
      ]),
    );
  }
}
