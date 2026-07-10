// Subscription purchases via in_app_purchase (Google Play Billing / StoreKit).
//
// The store is the payment rail; the BACKEND is the source of truth for entitlement:
// on a successful purchase we hand the purchase token to the backend
// (WalkApi.verifyGooglePurchase) which verifies the receipt with the store and flips
// the account to paid, then we refresh AuthService's profile. Exposed as a
// ChangeNotifier singleton mirroring AuthService. Inert when accounts are disabled.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:in_app_purchase/in_app_purchase.dart';

import '../accounts/accounts_config.dart';
import '../accounts/api_client.dart';
import '../accounts/auth_service.dart';

/// Store product ids — must match the subscription products created in the Play
/// Console / App Store Connect. (Also referenced by the backend for verification.)
const kProductMonthly = 'premium_monthly';
const kProductYearly = 'premium_yearly';

/// Stub billing: simulate a successful purchase locally instead of charging via the
/// store, so the premium flow is testable without Play Billing / StoreKit set up.
/// Build with `--dart-define=STUB_BILLING=false` (or flip the default) to use the real
/// store path once products + backend verification are live.
const kStubBilling = bool.fromEnvironment('STUB_BILLING', defaultValue: true);

class BillingService extends ChangeNotifier {
  BillingService._();
  static final BillingService instance = BillingService._();

  final InAppPurchase _iap = InAppPurchase.instance;
  StreamSubscription<List<PurchaseDetails>>? _sub;

  bool _available = false;
  bool _busy = false;
  String? _error; // last purchase/verification error, for the UI
  final Map<String, ProductDetails> _products = {}; // id -> details (price string, etc.)

  bool get available => _available;
  bool get busy => _busy;
  String? get error => _error;
  ProductDetails? get monthly => _products[kProductMonthly];
  ProductDetails? get yearly => _products[kProductYearly];

  /// Set up the purchase stream + load the products. Safe to call more than once.
  Future<void> init() async {
    if (!AccountsConfig.enabled) return; // billing needs the backend to verify
    if (kStubBilling) {
      // No store wiring in stub mode; the buy path fakes a purchase locally.
      _available = true;
      notifyListeners();
      return;
    }
    _sub ??= _iap.purchaseStream.listen(
      _onPurchases,
      onError: (e) {
        _error = '$e';
        notifyListeners();
      },
    );
    try {
      _available = await _iap.isAvailable();
    } catch (_) {
      _available = false;
    }
    if (_available) await _loadProducts();
    notifyListeners();
  }

  Future<void> _loadProducts() async {
    try {
      final resp = await _iap.queryProductDetails({kProductMonthly, kProductYearly});
      _products
        ..clear()
        ..addEntries(resp.productDetails.map((p) => MapEntry(p.id, p)));
    } catch (_) {
      // leave _products empty — the UI falls back to plain labels
    }
  }

  /// Kick off a subscription purchase for the given product. The result arrives
  /// asynchronously on the purchase stream (_onPurchases).
  Future<void> buy(String productId) async {
    if (_busy) return;
    if (kStubBilling) return _stubPurchase();
    final pd = _products[productId];
    if (pd == null) return;
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      await _iap.buyNonConsumable(purchaseParam: PurchaseParam(productDetails: pd));
    } catch (e) {
      _busy = false;
      _error = '$e';
      notifyListeners();
    }
  }

  Future<void> buyMonthly() => buy(kProductMonthly);
  Future<void> buyYearly() => buy(kProductYearly);

  // Fake a purchase: show the "processing" spinner briefly, then grant premium via the
  // local entitlement override. No store, no backend. Stub mode only.
  Future<void> _stubPurchase() async {
    _busy = true;
    _error = null;
    notifyListeners();
    await Future<void>.delayed(const Duration(milliseconds: 1200));
    await AuthService.instance.setStubEntitlement(true);
    _busy = false;
    notifyListeners();
  }

  /// Revoke the stubbed premium (test affordance). Stub mode only.
  Future<void> cancelStub() async {
    await AuthService.instance.setStubEntitlement(false);
    notifyListeners();
  }

  /// Ask the store to re-deliver active subscriptions (e.g. after reinstall).
  Future<void> restore() async {
    if (kStubBilling || !_available) return; // stub premium persists locally already
    try {
      await _iap.restorePurchases();
    } catch (e) {
      _error = '$e';
      notifyListeners();
    }
  }

  Future<void> _onPurchases(List<PurchaseDetails> purchases) async {
    for (final p in purchases) {
      switch (p.status) {
        case PurchaseStatus.pending:
          _busy = true;
          notifyListeners();
        case PurchaseStatus.error:
          _busy = false;
          _error = p.error?.message ?? 'purchase failed';
          notifyListeners();
          if (p.pendingCompletePurchase) await _iap.completePurchase(p);
        case PurchaseStatus.canceled:
          _busy = false;
          notifyListeners();
          if (p.pendingCompletePurchase) await _iap.completePurchase(p);
        case PurchaseStatus.purchased:
        case PurchaseStatus.restored:
          await _verifyWithBackend(p);
          if (p.pendingCompletePurchase) await _iap.completePurchase(p);
      }
    }
  }

  // Hand the store receipt to the backend, which verifies it and grants the tier;
  // then refresh the local profile so the whole app updates (ads off, model, limits).
  Future<void> _verifyWithBackend(PurchaseDetails p) async {
    try {
      await WalkApi.verifyGooglePurchase(
        p.verificationData.serverVerificationData,
        p.productID,
      );
      await AuthService.instance.refreshEntitlement();
    } catch (e) {
      _error = 'verification failed: $e';
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}
