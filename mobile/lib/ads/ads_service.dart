// Video ads for the FREE tier: one interstitial before the tour starts (pre-roll) and
// one every few narrations (mid-tour). Paid users never see an ad. Failures degrade to
// "no ad shown" so a missing/late ad never blocks the tour.
//
// Ad unit ids come from dart-defines; the defaults are Google's official TEST ids so a
// dev/test build shows test ads without an AdMob account. Set real ids for release:
//   --dart-define=AD_UNIT_INTERSTITIAL=ca-app-pub-XXXX/YYYY
// and put your AdMob APP id in AndroidManifest (com.google.android.gms.ads.APPLICATION_ID).

import 'dart:async';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:google_mobile_ads/google_mobile_ads.dart';

import '../accounts/auth_service.dart';

// Google's official sample interstitial unit id (test ads only).
const _kTestInterstitial = 'ca-app-pub-3940256099942544/1033173712';
const _kAdUnitInterstitial = String.fromEnvironment(
  'AD_UNIT_INTERSTITIAL',
  defaultValue: _kTestInterstitial,
);

// Show a mid-tour ad every N narrations for free users.
const int kMidTourAdEvery = 6;

class AdsService {
  AdsService._();
  static final AdsService instance = AdsService._();

  bool _ready = false;
  InterstitialAd? _ad;
  bool _loading = false;

  /// Initialize the Mobile Ads SDK once. No-op on web (no AdMob).
  Future<void> init() async {
    if (kIsWeb || _ready) return;
    try {
      await MobileAds.instance.initialize();
      _ready = true;
      _preload();
    } catch (_) {
      _ready = false; // ads unavailable — the app just never shows one
    }
  }

  void _preload() {
    if (kIsWeb || _loading || _ad != null) return;
    _loading = true;
    InterstitialAd.load(
      adUnitId: _kAdUnitInterstitial,
      request: const AdRequest(),
      adLoadCallback: InterstitialAdLoadCallback(
        onAdLoaded: (ad) {
          _ad = ad;
          _loading = false;
        },
        onAdFailedToLoad: (_) {
          _ad = null;
          _loading = false;
        },
      ),
    );
  }

  /// True when the caller (a free-tier session) should see ads.
  bool get _adsEnabled => !kIsWeb && _ready && !AuthService.instance.isPaid;

  /// A pre-roll ad before the tour begins. Returns once the ad is dismissed (or
  /// immediately if the user is paid / no ad is available). Never throws.
  Future<void> showPreroll() => _showIfFree();

  /// A mid-tour ad. Same contract as [showPreroll].
  Future<void> showMid() => _showIfFree();

  Future<void> _showIfFree() async {
    if (!_adsEnabled) return;
    final ad = _ad;
    if (ad == null) {
      _preload(); // not ready this time — warm one for next time, don't block
      return;
    }
    _ad = null; // consume it
    final done = Completer<void>();
    ad.fullScreenContentCallback = FullScreenContentCallback(
      onAdDismissedFullScreenContent: (ad) {
        ad.dispose();
        _preload();
        if (!done.isCompleted) done.complete();
      },
      onAdFailedToShowFullScreenContent: (ad, _) {
        ad.dispose();
        _preload();
        if (!done.isCompleted) done.complete();
      },
    );
    try {
      await ad.show();
    } catch (_) {
      if (!done.isCompleted) done.complete();
    }
    // Safety valve: never hang the tour if the SDK drops the dismiss callback.
    await done.future.timeout(const Duration(seconds: 45), onTimeout: () {});
  }
}
