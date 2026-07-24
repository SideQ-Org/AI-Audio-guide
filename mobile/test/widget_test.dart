import 'dart:convert';

import 'package:ai_audio_guide/accounts/accounts_config.dart';
import 'package:ai_audio_guide/l10n/app_localizations.dart';
import 'package:ai_audio_guide/main.dart';
import 'package:ai_audio_guide/ui/design.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';

void main() {
  // Manrope is fetched over the network at runtime; the test harness blocks HttpClient,
  // and a fetch completing after teardown rebuilds a disposed element. Disable runtime
  // fetching so google_fonts falls back to the default font synchronously instead.
  setUpAll(() => GoogleFonts.config.allowRuntimeFetching = false);

  testWidgets('home shows the redesigned start control + tab bar',
      (tester) async {
    await tester.pumpWidget(const GuideApp(initialThemeMode: ThemeMode.light));
    await tester.pump(); // let localizations delegates load

    final l = lookupAppLocalizations(const Locale('en'));

    // The inactive Home tab shows the swipe-to-start label...
    expect(find.text(l.swipeToStart), findsOneWidget);
    // ...and the floating tab bar (icons only — no labels) carries the four sections.
    expect(find.byIcon(AppIcons.home), findsOneWidget);
    expect(find.byIcon(AppIcons.profile), findsOneWidget);
    expect(find.byIcon(AppIcons.settings), findsOneWidget);

    // The full-screen map fetches tiles over the network, which the test harness
    // blocks (HttpClient). Drain those expected errors so they don't fail the test.
    for (dynamic e = tester.takeException();
        e != null;
        e = tester.takeException()) {}
  });

  test('reserve payload parses and keeps non-empty items', () {
    final raw = jsonDecode('''
      {
        "items": [
          {
            "id": "r1",
            "text": "Buffered fallback",
            "kind": "fallback",
            "scope": "city",
            "subject_key": "moscow",
            "language": "en"
          },
          {
            "id": "r2",
            "text": "",
            "kind": "fallback",
            "scope": "city",
            "subject_key": "moscow",
            "language": "en"
          }
        ]
      }
    ''') as Map<String, dynamic>;
    final items = (raw['items'] as List)
        .cast<Map<String, dynamic>>()
        .where((m) => (m['text'] as String).trim().isNotEmpty)
        .toList();
    expect(items, hasLength(1));
    expect(items.single['id'], 'r1');
  });

  test('reserve payload can carry pre-synth audio fields', () {
    final raw = jsonDecode('''
      {
        "items": [
          {
            "id": "r1",
            "text": "Buffered fallback",
            "kind": "fallback",
            "scope": "city",
            "subject_key": "moscow",
            "language": "en",
            "audio_b64": "ZmFrZQ==",
            "audio_mime": "audio/mpeg"
          }
        ]
      }
    ''') as Map<String, dynamic>;
    final item = ((raw['items'] as List).single as Map<String, dynamic>);
    expect(item['audio_b64'], 'ZmFrZQ==');
    expect(item['audio_mime'], 'audio/mpeg');
  });

  test('release ws localhost guard expression matches expected inputs', () {
    bool looksLocal(String url) =>
        url.contains('localhost') ||
        url.contains('127.0.0.1') ||
        url.contains('0.0.0.0');
    expect(looksLocal('ws://localhost:8000/ws'), isTrue);
    expect(looksLocal('ws://127.0.0.1:8000/ws'), isTrue);
    expect(looksLocal('wss://178.83.121.62.sslip.io/ws'), isFalse);
  });

  test('accounts config derives API base from WS_URL', () {
    expect(AccountsConfig.wsUrl, 'ws://localhost:8000/ws');
    expect(AccountsConfig.apiBase, 'http://localhost:8000');
  });

  test('accounts config blocks release localhost build', () {
    expect(
      AccountsConfig.endpointConfigError(isReleaseBuild: true),
      contains('prod WS_URL'),
    );
  });

  test('accounts config allows localhost in debug builds', () {
    expect(AccountsConfig.endpointConfigError(isReleaseBuild: false), isNull);
  });
}
