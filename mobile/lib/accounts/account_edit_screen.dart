// Edit account data: nickname, birthday (metadata-only, not shown on the profile),
// and — later — a custom avatar. Persists via AuthService.updateProfile.
// Strings are RU for now (primary language); l10n is a follow-up like the rest of the
// redesign's new copy.

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:intl/intl.dart';

import '../ui/components.dart';
import '../ui/design.dart';
import '../ui/wheel_picker.dart';
import 'auth_service.dart';

class AccountEditScreen extends StatefulWidget {
  const AccountEditScreen({super.key});
  @override
  State<AccountEditScreen> createState() => _AccountEditScreenState();
}

class _AccountEditScreenState extends State<AccountEditScreen> {
  late final TextEditingController _nick =
      TextEditingController(text: AuthService.instance.displayName ?? '');
  final _pw1 = TextEditingController();
  final _pw2 = TextEditingController();
  DateTime? _birthday = _parse(AuthService.instance.birthday);
  bool _busy = false;
  bool _pwBusy = false;
  bool _pwHidden = true;

  static DateTime? _parse(String? iso) {
    if (iso == null) return null;
    try {
      return DateTime.parse(iso);
    } catch (_) {
      return null;
    }
  }

  @override
  void dispose() {
    _nick.dispose();
    _pw1.dispose();
    _pw2.dispose();
    super.dispose();
  }

  Future<void> _changePassword() async {
    final p1 = _pw1.text, p2 = _pw2.text;
    if (p1.length < 6) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Пароль — минимум 6 символов')));
      return;
    }
    if (p1 != p2) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Пароли не совпадают')));
      return;
    }
    setState(() => _pwBusy = true);
    try {
      await AuthService.instance.changePassword(p1);
      if (!mounted) return;
      _pw1.clear();
      _pw2.clear();
      setState(() => _pwBusy = false);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Пароль изменён')));
    } catch (e) {
      if (!mounted) return;
      setState(() => _pwBusy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Не удалось: $e')));
    }
  }

  Future<void> _pickBirthday() async {
    final picked = await showAppDatePicker(
      context,
      initial: _birthday,
      title: 'Дата рождения',
    );
    if (picked != null && mounted) setState(() => _birthday = picked);
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    try {
      await AuthService.instance.updateProfile(
        nick: _nick.text.trim().isEmpty ? null : _nick.text.trim(),
        birthdayIso: _birthday == null ? null : DateFormat('yyyy-MM-dd').format(_birthday!),
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Сохранено')));
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Не удалось сохранить: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: GradientBackground(
        child: SafeArea(
          child: Column(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(6, 6, 16, 8),
              child: Row(children: [
                IconButton(
                  icon: Icon(Icons.arrow_back_rounded, color: c.textPrimary),
                  onPressed: () => Navigator.of(context).maybePop(),
                ),
                Expanded(child: Text('Редактировать профиль', style: h2(context), maxLines: 1, overflow: TextOverflow.ellipsis)),
              ]),
            ),
            Expanded(
              child: ListView(
                padding: EdgeInsets.fromLTRB(16, 4, 16, MediaQuery.of(context).padding.bottom + 24),
                children: [
                  Center(
                    child: Column(children: [
                      const TravelerAvatar(size: 100),
                      const SizedBox(height: 10),
                      TextButton(
                        onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Загрузка своей аватарки — скоро')),
                        ),
                        child: const Text('Сменить аватар'),
                      ),
                    ]),
                  ),
                  const SizedBox(height: Gap.md),
                  Text('НИК', style: label(context)),
                  const SizedBox(height: 8),
                  GlassModule(
                    sheen: false,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    child: TextField(
                      controller: _nick,
                      textCapitalization: TextCapitalization.words,
                      decoration: const InputDecoration(
                        border: InputBorder.none,
                        contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 16),
                        hintText: 'Ваш ник',
                      ),
                    ),
                  ),
                  const SizedBox(height: Gap.lg),
                  Text('ДАТА РОЖДЕНИЯ', style: label(context)),
                  const SizedBox(height: 8),
                  GlassModule(
                    sheen: false,
                    child: SettingRow(
                      icon: Icons.cake_rounded,
                      title: _birthday == null ? 'Не указана' : DateFormat.yMMMMd('ru').format(_birthday!),
                      value: 'Изменить',
                      onTap: _pickBirthday,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    child: Text('Не показывается в профиле — нужна только для поздравления с днём рождения.',
                        style: GoogleFonts.manrope(fontSize: 12, fontWeight: FontWeight.w500, height: 1.35, color: c.textFaint)),
                  ),
                  const SizedBox(height: Gap.xxl),
                  Text('СМЕНА ПАРОЛЯ', style: label(context)),
                  const SizedBox(height: 8),
                  GlassModule(
                    sheen: false,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    child: Column(children: [
                      TextField(
                        controller: _pw1,
                        obscureText: _pwHidden,
                        decoration: InputDecoration(
                          border: InputBorder.none,
                          contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 14),
                          hintText: 'Новый пароль',
                          suffixIcon: IconButton(
                            icon: Icon(_pwHidden ? Icons.visibility_outlined : Icons.visibility_off_outlined, color: c.textFaint),
                            onPressed: () => setState(() => _pwHidden = !_pwHidden),
                          ),
                        ),
                      ),
                      Divider(height: 1, color: c.glassBorder),
                      TextField(
                        controller: _pw2,
                        obscureText: _pwHidden,
                        decoration: const InputDecoration(
                          border: InputBorder.none,
                          contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 14),
                          hintText: 'Повторите пароль',
                        ),
                      ),
                    ]),
                  ),
                  const SizedBox(height: 10),
                  AppButton(_pwBusy ? 'Меняем…' : 'Сменить пароль', kind: AppBtnKind.secondary, onTap: _pwBusy ? null : _changePassword),
                  const SizedBox(height: Gap.xxl),
                  AppButton(_busy ? 'Сохранение…' : 'Сохранить', onTap: _busy ? null : _save),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
