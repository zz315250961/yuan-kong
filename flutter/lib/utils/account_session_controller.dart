import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/account_login_session.dart';
import 'account_device_sync.dart';

class AccountSessionException implements Exception {
  const AccountSessionException(this.status, this.message);
  final int status;
  final String message;
  @override
  String toString() => message;
}

/// The same HTTP boundary is used by UserModel and the production workspace.
/// Before sending and accepting any result, both account and API must match.
class AccountSessionClient {
  AccountSessionClient(
      {required this.session,
      required this.get,
      required this.post,
      this.onUnauthorized});
  final Future<AccountDeviceSession> Function() session;
  final Future<http.Response> Function(Uri, {Map<String, String>? headers}) get;
  final Future<http.Response> Function(Uri,
      {Map<String, String>? headers, Object? body}) post;
  final Future<void> Function(AccountDeviceSession)? onUnauthorized;
  static const timeout = Duration(seconds: 20);

  Future<bool> isCurrent(AccountDeviceSession expected,
          [bool Function()? active]) async =>
      (active?.call() ?? true) &&
      expected.token.isNotEmpty &&
      expected.apiServer.isNotEmpty &&
      expected.matches(await session().timeout(timeout)) &&
      (active?.call() ?? true);

  Future<Map<String, dynamic>> _body(http.Response response,
      AccountDeviceSession expected, bool Function()? active) async {
    if (response.statusCode == 401 && await isCurrent(expected, active)) {
      await onUnauthorized?.call(expected);
      throw const AccountSessionException(401, '登录已失效，请重新登录');
    }
    if (response.statusCode == 404) {
      throw const AccountSessionException(404, '服务器暂不支持登录设备管理，请联系管理员更新');
    }
    Map<String, dynamic> body;
    try {
      body =
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    } catch (_) {
      throw const AccountSessionException(0, '服务器返回的登录会话数据无效');
    }
    if (response.statusCode != 200 || body['error'] != null) {
      final error = body['error'];
      throw AccountSessionException(response.statusCode,
          error is String && error.isNotEmpty ? error : '操作失败，请稍后重试');
    }
    return body;
  }

  Future<List<AccountLoginSession>?> load(AccountDeviceSession expected,
      {bool Function()? active}) async {
    if (!await isCurrent(expected, active)) return null;
    final response = await get(
            accountDeviceRequestUri(expected.apiServer, '/api/sessions'),
            headers: {'Authorization': 'Bearer ${expected.token}'})
        .timeout(timeout);
    if (!await isCurrent(expected, active)) return null;
    final body = await _body(response, expected, active);
    final rows = body['sessions'];
    if (rows is! List) throw const FormatException('登录会话列表无效');
    return rows
        .map((row) =>
            AccountLoginSession.fromJson(Map<String, dynamic>.from(row as Map)))
        .toList(growable: false);
  }

  Future<bool> revoke(AccountDeviceSession expected, String id, String password,
      {bool Function()? active}) async {
    if (!RegExp(r'^[A-Za-z0-9_-]{22,64}$').hasMatch(id) ||
        password.isEmpty ||
        !await isCurrent(expected, active)) return false;
    final response = await post(
        accountDeviceRequestUri(expected.apiServer,
            '/api/sessions/${Uri.encodeComponent(id)}/revoke'),
        headers: {
          'Authorization': 'Bearer ${expected.token}',
          'Content-Type': 'application/json'
        },
        body: jsonEncode({'password': password})).timeout(timeout);
    if (!await isCurrent(expected, active)) return false;
    final body = await _body(response, expected, active);
    if (body['revoked'] != true) throw const FormatException('退出结果无效');
    return true;
  }
}

class AccountSessionController extends ChangeNotifier {
  AccountSessionController(this.api);
  final AccountSessionClient api;
  List<AccountLoginSession> _sessions = const [];
  List<AccountLoginSession> get sessions => _sessions;
  bool loading = false;
  String error = '';
  String? revokingId;
  bool _disposed = false;
  int _generation = 0;
  AccountDeviceSession? _loadedAccount;

  void clear() {
    if (_disposed) return;
    _generation++;
    _loadedAccount = null;
    _sessions = const [];
    loading = false;
    revokingId = null;
    error = '';
    notifyListeners();
  }

  Future<void> load() async {
    if (_disposed || revokingId != null) return;
    final generation = ++_generation;
    bool active() => !_disposed && generation == _generation;
    _sessions = const [];
    _loadedAccount = null;
    loading = true;
    error = '';
    notifyListeners();
    try {
      final expected = await api.session();
      if (!active()) return;
      final data = await api.load(expected, active: active);
      if (data == null || !active()) return;
      _loadedAccount = expected;
      _sessions = List.unmodifiable(data);
    } catch (exception) {
      if (active()) error = _message(exception);
    } finally {
      if (active()) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<bool> revoke(String id, String password) async {
    if (_disposed ||
        loading ||
        revokingId != null ||
        _loadedAccount == null ||
        !_sessions.any((s) => s.id == id && !s.isCurrent)) return false;
    final generation = _generation;
    bool active() => !_disposed && generation == _generation;
    revokingId = id;
    error = '';
    notifyListeners();
    try {
      final ok =
          await api.revoke(_loadedAccount!, id, password, active: active);
      if (!ok || !active()) return false;
      _sessions = List.unmodifiable(_sessions.where((s) => s.id != id));
      return true;
    } catch (exception) {
      if (active()) error = _message(exception);
      return false;
    } finally {
      if (active()) {
        revokingId = null;
        notifyListeners();
      }
    }
  }

  String _message(Object exception) => exception is AccountSessionException
      ? exception.message
      : '无法获取或更新登录设备，请检查网络后重试';
  @override
  void dispose() {
    _disposed = true;
    _generation++;
    super.dispose();
  }
}
