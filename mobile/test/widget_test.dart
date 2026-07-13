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

  testWidgets('home shows the redesigned start control + tab bar', (tester) async {
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
    for (dynamic e = tester.takeException(); e != null; e = tester.takeException()) {}
  });
}
