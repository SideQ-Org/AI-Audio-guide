import 'package:ai_audio_guide/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('app shows the top-bar controls', (tester) async {
    await tester.pumpWidget(const GuideApp(initialThemeMode: ThemeMode.system));
    await tester.pump(); // let localizations delegates load

    // The language and settings controls are always present (locale-independent).
    expect(find.byIcon(Icons.translate), findsOneWidget);
    expect(find.byIcon(Icons.tune_rounded), findsOneWidget);

    // The full-screen map fetches tiles over the network, which the test harness
    // blocks (HttpClient). Drain those expected errors so they don't fail the test.
    for (dynamic e = tester.takeException(); e != null; e = tester.takeException()) {}
  });
}
