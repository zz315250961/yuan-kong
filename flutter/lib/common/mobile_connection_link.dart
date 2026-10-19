import 'dart:convert';

enum MobileConnectionKind { remote, fileTransfer, viewCamera, terminal }

/// Untrusted QR/link input is parsed before any native call or navigation.
class MobileConnectionLink {
  const MobileConnectionLink(this.id,
      {this.kind = MobileConnectionKind.remote,
      this.password,
      this.forceRelay = false});

  final String id;
  final MobileConnectionKind kind;
  final String? password;
  final bool forceRelay;

  bool isSelf(String localId) {
    final peer = id.split('?').first.split('/').first.split('@').first;
    return peer == localId.replaceAll(' ', '').trim();
  }

  static MobileConnectionLink? parse(String value,
      {bool allowPlainId = false}) {
    if (value.length > 4096 || _hasControl(value)) return null;
    value = value.trim();
    if (value.isEmpty) return null;
    if (allowPlainId && !value.contains('://')) {
      final id = value.replaceAll(' ', '');
      return _validId(id) ? MobileConnectionLink(id) : null;
    }
    try {
      final uri = Uri.parse(value);
      if (uri.scheme.toLowerCase() != 'linkremote' ||
          uri.hasFragment ||
          uri.userInfo.isNotEmpty ||
          RegExp(r'%(?![0-9a-fA-F]{2})').hasMatch(value)) return null;
      var authority = Uri.decodeComponent(uri.authority);
      var path = Uri.decodeComponent(uri.path);
      MobileConnectionKind kind;
      String id;
      const commands = {
        'connect': MobileConnectionKind.remote,
        'play': MobileConnectionKind.remote,
        'file-transfer': MobileConnectionKind.fileTransfer,
        'view-camera': MobileConnectionKind.viewCamera,
        'terminal': MobileConnectionKind.terminal,
      };
      if (authority == 'connection' && path.startsWith('/new/')) {
        kind = MobileConnectionKind.remote;
        id = path.substring(5);
      } else if (commands.containsKey(authority)) {
        kind = commands[authority]!;
        if (!path.startsWith('/')) return null;
        id = path.substring(1);
      } else {
        if (const [
          'config',
          'password',
          'port-forward',
          'rdp',
          'terminal-admin'
        ].contains(authority)) return null;
        kind = MobileConnectionKind.remote;
        id = authority + (path == '/' ? '' : path);
      }
      if (!_validId(id)) return null;
      final query = <String, String>{};
      for (final entry in uri.queryParametersAll.entries) {
        final name = entry.key.toLowerCase();
        if (!const ['key', 'password', 'relay'].contains(name) ||
            entry.value.length != 1 ||
            query.containsKey(name) ||
            entry.value.single.length > 1024 ||
            _hasControl(entry.value.single)) return null;
        query[name] = entry.value.single;
      }
      final key = query['key'];
      if (key != null) id += '?${Uri(queryParameters: {'key': key}).query}';
      return MobileConnectionLink(id,
          kind: kind,
          password: query['password'],
          forceRelay: query.containsKey('relay'));
    } on FormatException {
      return null;
    }
  }

  static bool _validId(String id) =>
      id.length >= 3 &&
      id.length <= 256 &&
      RegExp(r'^[a-zA-Z0-9][a-zA-Z0-9_.:\-]*(?:@[a-zA-Z0-9_.:\-]+)?(?:/r(?:@[a-zA-Z0-9_.:\-]+)?)?$')
          .hasMatch(id);
}

class MobileScanResult {
  const MobileScanResult._({this.connection, this.serverConfig});
  final MobileConnectionLink? connection;
  final Map<String, String>? serverConfig;

  static MobileScanResult? parse(String value) {
    if (value.length > 4096 || _hasControl(value)) return null;
    value = value.trim();
    if (!value.startsWith('config=')) {
      final connection = MobileConnectionLink.parse(value, allowPlainId: true);
      return connection == null
          ? null
          : MobileScanResult._(connection: connection);
    }
    try {
      final data = value.substring(7);
      dynamic decoded;
      try {
        decoded = jsonDecode(data);
      } on FormatException {
        decoded = jsonDecode(utf8.decode(
            base64Decode(base64.normalize(data.split('').reversed.join()))));
      }
      if (decoded is! Map || decoded.isEmpty) return null;
      final config = <String, String>{};
      for (final entry in decoded.entries) {
        if (!const ['host', 'relay', 'api', 'key'].contains(entry.key) ||
            entry.value is! String ||
            (entry.value as String).length > 2048 ||
            _hasControl(entry.value as String)) return null;
        config[entry.key as String] = (entry.value as String).trim();
      }
      if (config.values.every((value) => value.isEmpty)) return null;
      return MobileScanResult._(serverConfig: Map.unmodifiable(config));
    } on FormatException {
      return null;
    }
  }
}

bool _hasControl(String value) => RegExp(r'[\x00-\x1f\x7f]').hasMatch(value);

/// Coalesces camera callbacks while validation/navigation is pending, and drops
/// late async completions when the owning page has gone away.
class MobileConnectionGate {
  bool _busy = false;
  bool _closed = false;

  void dispose() => _closed = true;

  Future<bool> open(MobileConnectionLink link,
      {required Future<String> Function() localId,
      required bool Function() isActive,
      required Future<void> Function(MobileConnectionLink) navigate,
      required void Function() onSelf}) async {
    if (_closed || _busy || !isActive()) return false;
    _busy = true;
    try {
      final ownId = await localId();
      if (_closed || !isActive()) return false;
      if (link.isSelf(ownId)) {
        onSelf();
        return false;
      }
      await navigate(link);
      return true;
    } finally {
      _busy = false;
    }
  }
}
