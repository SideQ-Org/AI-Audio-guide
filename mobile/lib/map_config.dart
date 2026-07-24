// Map tile provider config, injected at build time via --dart-define. Defaults to the
// public CARTO basemaps (fine for the prototype). For a production/high-load deploy,
// point these at a paid provider (MapTiler, Mapbox, self-hosted) so tiles don't rely on
// CARTO's free/fair-use CDN. No code change needed — just build with the keys, e.g.:
//
//   flutter build web \
//     --dart-define=MAP_TILE_URL_DARK=https://api.maptiler.com/maps/streets-v2-dark/{z}/{x}/{y}.png?key=KEY \
//     --dart-define=MAP_TILE_URL_LIGHT=https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key=KEY \
//     --dart-define=MAP_TILE_SUBDOMAINS= \
//     --dart-define=MAP_ATTRIBUTION=© OpenStreetMap © MapTiler
//
// The provider key is baked into the URL template above (put the full URL incl. ?key= /
// ?access_token=). Providers like MapTiler/Mapbox serve from a single host, so pass an
// EMPTY MAP_TILE_SUBDOMAINS (the {s} placeholder is then unused).
import 'dart:async';
import 'dart:math';

import 'package:flutter_map/flutter_map.dart';

class MapConfig {
  static const String _customDark = String.fromEnvironment('MAP_TILE_URL_DARK');
  static const String _customLight = String.fromEnvironment('MAP_TILE_URL_LIGHT');
  static const String _subdomains =
      String.fromEnvironment('MAP_TILE_SUBDOMAINS', defaultValue: 'a,b,c');
  static const String _attribution =
      String.fromEnvironment('MAP_ATTRIBUTION', defaultValue: '© OpenStreetMap, © CARTO');

  // CARTO raster defaults. Light uses Voyager for better daytime contrast. Dark uses
  // Dark Matter (`dark_all`), which is the best free CARTO dark style with labels.
  static const String _defaultDark =
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png';
  static const String _defaultLight =
      'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png';

  static const int tileCacheMaxBytes = 300 * 1000 * 1000;
  static const Duration tileFreshAge = Duration(hours: 24);
  static const int tilePanBuffer = 1;
  static const int tileKeepBuffer = 3;

  static String? _cacheRoot;
  static BuiltInMapCachingProvider? _cacheProvider;
  static Timer? _prefetchDebounce;
  static final Set<String> _prefetchInFlight = <String>{};

  /// True once a custom provider is configured (so we can surface it if needed).
  static bool get isCustom => _customDark.isNotEmpty || _customLight.isNotEmpty;

  /// Tile URL template for the current theme; falls back to CARTO per-theme.
  static String tileUrl({required bool dark}) {
    if (dark) return _customDark.isNotEmpty ? _customDark : _defaultDark;
    return _customLight.isNotEmpty ? _customLight : _defaultLight;
  }

  /// Subdomains for the `{s}` placeholder; empty for single-host providers.
  static List<String> get subdomains =>
      _subdomains.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();

  static String get attribution => _attribution;

  static Future<void> configureTileCache(String cacheRoot) async {
    final nextRoot = '$cacheRoot/flutter_map_tiles';
    if (_cacheRoot == nextRoot && _cacheProvider != null) return;
    if (_cacheProvider != null) {
      await _cacheProvider!.destroy();
    }
    _cacheRoot = nextRoot;
    _cacheProvider = BuiltInMapCachingProvider.getOrCreateInstance(
      cacheDirectory: nextRoot,
      maxCacheSize: tileCacheMaxBytes,
      overrideFreshAge: tileFreshAge,
    );
  }

  static TileProvider tileProvider({String userAgentPackageName = 'com.example.ai_audio_guide'}) {
    return NetworkTileProvider(
      headers: {'User-Agent': 'flutter_map ($userAgentPackageName)'},
      abortObsoleteRequests: true,
      cachingProvider: _cacheProvider,
    );
  }

  static TileLayer buildTileLayer({
    required bool dark,
    required String userAgentPackageName,
    TileBuilder? tileBuilder,
    int? panBuffer,
    int? keepBuffer,
  }) {
    return TileLayer(
      urlTemplate: tileUrl(dark: dark),
      subdomains: subdomains,
      userAgentPackageName: userAgentPackageName,
      tileProvider: tileProvider(userAgentPackageName: userAgentPackageName),
      panBuffer: panBuffer ?? tilePanBuffer,
      keepBuffer: keepBuffer ?? tileKeepBuffer,
      tileDisplay: const TileDisplay.fadeIn(duration: Duration(milliseconds: 80), startOpacity: 0.0, reloadStartOpacity: 0.15),
      tileBuilder: tileBuilder,
    );
  }

  static void schedulePrefetch({
    required bool dark,
    required double lat,
    required double lon,
    required int zoom,
    int radius = 1,
  }) {
    final provider = _cacheProvider;
    if (provider == null || !provider.isSupported) return;
    _prefetchDebounce?.cancel();
    _prefetchDebounce = Timer(const Duration(milliseconds: 350), () async {
      final z = zoom.clamp(0, 19);
      final centerX = ((lon + 180.0) / 360.0 * (1 << z)).floor();
      final latRad = lat * 0.017453292519943295;
      final centerY = ((1 - (log(tan(latRad) + 1 / cos(latRad)) / pi)) / 2 * (1 << z)).floor();
      final tpl = tileUrl(dark: dark);
      for (var dx = -radius; dx <= radius; dx++) {
        for (var dy = -radius; dy <= radius; dy++) {
          final x = centerX + dx;
          final y = centerY + dy;
          if (x < 0 || y < 0) continue;
          final key = '$z/$x/$y/${dark ? 'dark' : 'light'}';
          if (!_prefetchInFlight.add(key)) continue;
          unawaited(() async {
            try {
              final url = tpl
                  .replaceAll('{z}', '$z')
                  .replaceAll('{x}', '$x')
                  .replaceAll('{y}', '$y')
                  .replaceAll('{s}', subdomains.isEmpty ? '' : subdomains[(x + y).abs() % subdomains.length]);
              await provider.getTile(url);
            } catch (_) {
              // best-effort cache warm only
            } finally {
              _prefetchInFlight.remove(key);
            }
          }());
        }
      }
    });
  }
}
