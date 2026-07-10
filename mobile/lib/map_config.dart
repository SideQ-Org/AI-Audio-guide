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
class MapConfig {
  static const String _customDark = String.fromEnvironment('MAP_TILE_URL_DARK');
  static const String _customLight = String.fromEnvironment('MAP_TILE_URL_LIGHT');
  static const String _subdomains =
      String.fromEnvironment('MAP_TILE_SUBDOMAINS', defaultValue: 'a,b,c');
  static const String _attribution =
      String.fromEnvironment('MAP_ATTRIBUTION', defaultValue: '© OpenStreetMap, © CARTO');

  // CARTO Dark Matter / Positron — the shipped default (public, prototype-tier).
  static const String _defaultDark =
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png';
  static const String _defaultLight =
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png';

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
}
