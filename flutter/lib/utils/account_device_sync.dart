import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

final String _accountDeviceRequestScope = const Uuid().v4();
int _accountDeviceRequestSerial = 0;

Uri accountDeviceRequestUri(String base, String path) =>
    Uri.parse('${base.replaceFirst(RegExp(r'/+$'), '')}$path')
        .replace(queryParameters: {
      // Native HTTP stores results by URL. Keep concurrent/retired account
      // requests and Flutter engines separate without exposing account/device
      // data. Each isolate owns an independent random scope and serial.
      'account_device_request':
          '$_accountDeviceRequestScope:${++_accountDeviceRequestSerial}',
    });

String accountRemoteId(String value) {
  final id = value.replaceAll(' ', '').trim();
  return ['', '0', 'NA', 'null', 'unknown'].contains(id) ? '' : id;
}

Future<String> accountInstallationUuid(String nativeUuid,
    {required String Function() readSaved,
    required Future<void> Function(String) save,
    required String Function() create}) async {
  bool usable(String value) =>
      !['', '0', 'NA', 'null', 'unknown'].contains(value.trim());
  final existing = readSaved();
  if (usable(existing)) return existing;
  if (usable(nativeUuid)) return nativeUuid.trim();
  final generated = create();
  await save(generated);
  return generated;
}

class AccountDeviceSession {
  const AccountDeviceSession(this.apiServer, this.token);
  final String apiServer;
  final String token;

  bool matches(AccountDeviceSession other) =>
      apiServer == other.apiServer && token == other.token;
}

class AccountDeviceIdentity {
  const AccountDeviceIdentity(this.id, this.uuid, this.info);
  final String id;
  final String uuid;
  final Map<String, dynamic> info;

  Map<String, dynamic> toJson() => {'id': id, 'uuid': uuid, 'deviceInfo': info};
}

class _DeviceSyncAttempt {
  _DeviceSyncAttempt(this.account);
  final AccountDeviceSession account;
  bool active = true;
}

/// Authenticated GUI-account registration, separate from the unauthenticated
/// native server heartbeat. It runs on all controller platforms, including iOS.
class AccountDeviceSync {
  AccountDeviceSync(
      {required this.session,
      required this.identity,
      required this.post,
      this.onUnauthorized,
      this.timeout = const Duration(seconds: 35),
      this.registrationWait = const Duration(seconds: 4)});
  final Duration timeout;
  final Duration registrationWait;
  final Future<AccountDeviceSession> Function() session;
  final Future<AccountDeviceIdentity> Function() identity;
  final Future<http.Response> Function(Uri,
      {Map<String, String>? headers, Object? body}) post;
  final Future<void> Function(AccountDeviceSession)? onUnauthorized;
  Future<void>? _pending;
  _DeviceSyncAttempt? _attempt;

  Future<void> sync() async {
    final account = await session().timeout(timeout);
    final pending = _pending;
    if (pending != null && _attempt?.account.matches(account) == true) {
      return pending;
    }
    // A hung old-account request must not block the newly logged-in account.
    // Retire it so a late identity/result cannot start another HTTP request.
    _attempt?.active = false;
    final attempt = _DeviceSyncAttempt(account);
    _attempt = attempt;
    final operation = _sync(attempt).timeout(timeout);
    _pending = operation;
    try {
      await operation;
    } finally {
      attempt.active = false;
      if (identical(_pending, operation)) {
        _pending = null;
        _attempt = null;
      }
    }
  }

  Future<bool> _isCurrent(_DeviceSyncAttempt attempt) async =>
      attempt.active &&
      attempt.account.matches(await session()) &&
      attempt.active;

  Future<void> _sync(_DeviceSyncAttempt attempt) async {
    final account = attempt.account;
    if (account.token.isEmpty || account.apiServer.isEmpty) return;
    final device = await identity();
    if (device.uuid.isEmpty || !await _isCurrent(attempt)) return;
    final headers = {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ${account.token}'
    };
    final base = account.apiServer.replaceFirst(RegExp(r'/+$'), '');
    final body = jsonEncode(device.toJson());
    var response = await post(accountDeviceRequestUri(base, '/api/heartbeat'),
        headers: headers, body: body);
    // Keep older self-hosted API versions usable while the new endpoint rolls
    // out; currentUser already authenticates and upserts the same device data.
    if (response.statusCode == 404 && await _isCurrent(attempt)) {
      response = await post(accountDeviceRequestUri(base, '/api/currentUser'),
          headers: headers, body: body);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      if (response.statusCode == 401 && await _isCurrent(attempt)) {
        await onUnauthorized?.call(account);
      }
      throw http.ClientException(
          'Device registration HTTP ${response.statusCode}');
    }
  }

  Future<http.Response?> loadPeers(
      Future<http.Response> Function(Uri, {Map<String, String>? headers}) get,
      {required void Function(Object) registrationError,
      bool syncDevice = true}) async {
    final account = await session().timeout(timeout);
    if (account.token.isEmpty || account.apiServer.isEmpty) return null;
    Object? syncError;
    if (syncDevice) {
      try {
        await sync().timeout(registrationWait);
      } catch (error) {
        syncError = error;
      }
    }
    if (!account.matches(await session().timeout(timeout))) return null;
    if (syncError != null) registrationError(syncError);
    final response = await get(
        accountDeviceRequestUri(account.apiServer, '/api/peers'),
        headers: {'Authorization': 'Bearer ${account.token}'}).timeout(timeout);
    if (!account.matches(await session().timeout(timeout))) return null;
    if (response.statusCode == 401) await onUnauthorized?.call(account);
    return response;
  }

  Future<http.Response?> removePeer(
      String uuid,
      AccountDeviceSession expectedSession,
      Future<http.Response> Function(Uri, {Map<String, String>? headers})
          delete) async {
    final account = await session().timeout(timeout);
    if (account.token.isEmpty || !account.matches(expectedSession)) return null;
    final response = await delete(
        accountDeviceRequestUri(
            account.apiServer, '/api/peers/${Uri.encodeComponent(uuid)}'),
        headers: {'Authorization': 'Bearer ${account.token}'}).timeout(timeout);
    if (!account.matches(await session().timeout(timeout))) return null;
    if (response.statusCode == 401) await onUnauthorized?.call(account);
    return response;
  }
}
