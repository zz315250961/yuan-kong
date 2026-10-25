import 'dart:async';
import 'dart:convert';

import 'package:bot_toast/bot_toast.dart';
import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/hbbs/hbbs.dart';
import 'package:flutter_hbb/models/ab_model.dart';
import 'package:get/get.dart';
import 'package:uuid/uuid.dart';

import '../common.dart';
import '../utils/http_service.dart' as http;
import '../utils/account_device_sync.dart';
import '../utils/account_session_controller.dart';
import 'account_login_session.dart';
import 'model.dart';
import 'platform_model.dart';

bool refreshingUser = false;

class UserModel {
  final RxString userName = ''.obs;
  final RxString displayName = ''.obs;
  final RxString avatar = ''.obs;
  final RxBool isAdmin = false.obs;
  final RxString networkError = ''.obs;
  final RxString deviceSyncError = ''.obs;
  // True when networkError carries a server-reported error rather than a
  // connectivity failure; netWorkErrorWidget hides the network tip then.
  final RxBool networkErrorFromServer = false.obs;
  bool get isLogin => userName.isNotEmpty;
  String get displayNameOrUserName =>
      displayName.value.trim().isEmpty ? userName.value : displayName.value;
  String get accountLabelWithHandle {
    final username = userName.value.trim();
    if (username.isEmpty) {
      return '';
    }
    final preferred = displayName.value.trim();
    if (preferred.isEmpty || preferred == username) {
      return username;
    }
    return '$preferred (@$username)';
  }

  WeakReference<FFI> parent;
  Timer? _deviceHeartbeat;
  late final _deviceSync = AccountDeviceSync(
      session: accountDeviceSession,
      identity: _readAccountDevice,
      post: (url, {headers, body}) =>
          http.post(url, headers: headers, body: body));
  late final accountSessions = AccountSessionClient(
      session: accountDeviceSession,
      get: (url, {headers}) => http.get(url, headers: headers),
      post: (url, {headers, body}) =>
          http.post(url, headers: headers, body: body),
      onUnauthorized: (expected) async {
        if (expected.matches(await accountDeviceSession())) {
          await reset(resetOther: true);
        }
      });

  Future<List<AccountLoginSession>> getLoginSessions(
          {required AccountDeviceSession expectedSession}) async =>
      await accountSessions.load(expectedSession) ?? const [];

  Future<bool> revokeLoginSession(
          {required String sessionId,
          required String password,
          required AccountDeviceSession expectedSession}) =>
      accountSessions.revoke(expectedSession, sessionId, password);

  Future<AccountDeviceSession> accountDeviceSession() async {
    final token = bind.mainGetLocalOption(key: 'access_token');
    final api =
        await bind.mainGetApiServer().timeout(const Duration(seconds: 5));
    return token == bind.mainGetLocalOption(key: 'access_token')
        ? AccountDeviceSession(api, token)
        : const AccountDeviceSession('', '');
  }

  Future<AccountDeviceIdentity> _readAccountDevice() async {
    Map<String, dynamic> info = {};
    try {
      final value = jsonDecode(bind.mainGetLoginDeviceInfo());
      if (value is Map<String, dynamic>) info = value;
    } catch (_) {
      debugPrint('Unable to read account device metadata');
    }
    final uuid = await accountInstallationUuid(await bind.mainGetUuid(),
        readSaved: () => bind.mainGetLocalOption(key: 'account-device-uuid'),
        save: (value) =>
            bind.mainSetLocalOption(key: 'account-device-uuid', value: value),
        create: () => const Uuid().v4());
    return AccountDeviceIdentity(
        accountRemoteId(await bind.mainGetMyId()), uuid, info);
  }

  void _startDeviceHeartbeat() {
    if (isWeb || _deviceHeartbeat != null) return;
    _deviceHeartbeat = Timer.periodic(const Duration(seconds: 60), (_) {
      if (!isLogin) {
        _deviceHeartbeat?.cancel();
        _deviceHeartbeat = null;
        return;
      }
      _deviceSync.sync().catchError((Object error) {
        debugPrint('Account device registration failed: ${error.runtimeType}');
      });
    });
  }

  UserModel(this.parent) {
    userName.listen((p0) {
      // When user name becomes empty, show login button
      // When user name becomes non-empty:
      //  For _updateLocalUserInfo, network error will be set later
      //  For login success, should clear network error
      networkError.value = '';
    });
  }

  void refreshCurrentUser() async {
    if (bind.isDisableAccount()) return;
    networkError.value = '';
    networkErrorFromServer.value = false;
    final token = bind.mainGetLocalOption(key: 'access_token');
    if (token == '') {
      await updateOtherModels();
      return;
    }
    _updateLocalUserInfo();
    final url = await bind.mainGetApiServer();
    final body = (await _readAccountDevice()).toJson();
    if (token != bind.mainGetLocalOption(key: 'access_token')) return;
    if (refreshingUser) return;
    try {
      refreshingUser = true;
      final http.Response response;
      try {
        response = await http.post(
            accountDeviceRequestUri(url, '/api/currentUser'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $token'
            },
            body: json.encode(body));
      } catch (e) {
        networkError.value = e.toString();
        rethrow;
      }
      refreshingUser = false;
      final status = response.statusCode;
      if (token != bind.mainGetLocalOption(key: 'access_token')) return;
      if (status == 401 || status == 400) {
        reset(resetOther: status == 401);
        return;
      }
      final data = json.decode(decode_http_response(response));
      final error = data['error'];
      if (error != null) {
        // The only failure known to come from the server itself, so the
        // check-your-network tip does not apply. Flag before the message is
        // set in the catch below so rebuilds read a consistent pair.
        networkErrorFromServer.value = true;
        throw error;
      }

      final user = UserPayload.fromJson(data);
      _parseAndUpdateUser(user);
    } catch (e) {
      debugPrint('Failed to refreshCurrentUser: $e');
      // Surface failures in the address book / group tabs, which offer a
      // retry. Anything not flagged above -- transport errors, non-JSON or
      // unexpected-schema bodies (e.g. a filter's block page) -- keeps the
      // check-your-network tip.
      if (networkError.value.isEmpty) {
        networkError.value = e.toString();
      }
    } finally {
      refreshingUser = false;
      await updateOtherModels();
    }
  }

  static Map<String, dynamic>? getLocalUserInfo() {
    final userInfo = bind.mainGetLocalOption(key: 'user_info');
    if (userInfo == '') {
      return null;
    }
    try {
      return json.decode(userInfo);
    } catch (e) {
      debugPrint('Failed to get local user info "$userInfo": $e');
    }
    return null;
  }

  _updateLocalUserInfo() {
    final userInfo = getLocalUserInfo();
    if (userInfo != null) {
      userName.value = (userInfo['name'] ?? '').toString();
      displayName.value = (userInfo['display_name'] ?? '').toString();
      avatar.value = (userInfo['avatar'] ?? '').toString();
    }
  }

  Future<void> reset({bool resetOther = false}) async {
    _deviceHeartbeat?.cancel();
    _deviceHeartbeat = null;
    deviceSyncError.value = '';
    await bind.mainSetLocalOption(key: 'access_token', value: '');
    await bind.mainSetLocalOption(key: 'user_info', value: '');
    if (resetOther) {
      await gFFI.abModel.reset();
      await gFFI.groupModel.reset();
    }
    userName.value = '';
    displayName.value = '';
    avatar.value = '';
  }

  _parseAndUpdateUser(UserPayload user) {
    userName.value = user.name;
    displayName.value = user.displayName;
    avatar.value = user.avatar;
    isAdmin.value = user.isAdmin;
    bind.mainSetLocalOption(key: 'user_info', value: jsonEncode(user));
    _startDeviceHeartbeat();
    if (isWeb) {
      // ugly here, tmp solution
      bind.mainSetLocalOption(key: 'verifier', value: user.verifier ?? '');
    }
  }

  // update ab and group status
  static Future<void> updateOtherModels() async {
    await Future.wait([
      gFFI.abModel.pullAb(force: ForcePullAb.listAndCurrent, quiet: false),
      gFFI.groupModel.pull()
    ]);
  }

  Future<void> logOut({String? apiServer}) async {
    final tag = gFFI.dialogManager.showLoading(translate('Waiting'));
    try {
      final url = apiServer ?? await bind.mainGetApiServer();
      final authHeaders = getHttpHeaders();
      authHeaders['Content-Type'] = "application/json";
      await http
          .post(Uri.parse('$url/api/logout'),
              body: jsonEncode({
                'id': await bind.mainGetMyId(),
                'uuid': await bind.mainGetUuid(),
              }),
              headers: authHeaders)
          .timeout(Duration(seconds: 2));
    } catch (e) {
      debugPrint("request /api/logout failed: err=$e");
    } finally {
      await reset(resetOther: true);
      gFFI.dialogManager.dismissByTag(tag);
    }
  }

  /// throw [RequestException]
  Future<LoginResponse> login(LoginRequest loginRequest) async {
    return _postAuth('/api/login', loginRequest);
  }

  /// throw [RequestException]
  Future<LoginResponse> register(LoginRequest loginRequest) async {
    return _postAuth('/api/register', loginRequest);
  }

  /// 发送邮箱验证码（purpose: register / reset / change_email）。
  /// 成功后服务端会发邮件；SMTP 未配置时验证码打印在服务器日志。
  /// throw [RequestException]
  Future<void> sendCode(String email, String purpose) async {
    final url = await bind.mainGetApiServer();
    final resp = await http.post(Uri.parse('$url/api/send-code'),
        body: jsonEncode({'email': email, 'purpose': purpose}));
    _throwIfError(resp);
  }

  /// 忘记密码：验证码 + 新密码。
  /// throw [RequestException]
  Future<void> resetPassword(String email, String code, String password) async {
    final url = await bind.mainGetApiServer();
    final resp = await http.post(Uri.parse('$url/api/reset-password'),
        body: jsonEncode({'email': email, 'code': code, 'password': password}));
    _throwIfError(resp);
  }

  /// 更换绑定邮箱：需登录态 + 当前密码 + 发往新邮箱的验证码。
  /// throw [RequestException]
  Future<UserPayload> changeEmail(
      {required String newEmail,
      required String code,
      required String password}) async {
    final expected = await accountDeviceSession();
    if (!await accountSessions.isCurrent(expected)) {
      throw RequestException(401, '登录已失效，请重新登录');
    }
    final resp = await http
        .post(accountDeviceRequestUri(expected.apiServer, '/api/change-email'),
            headers: {
              'Authorization': 'Bearer ${expected.token}',
              'Content-Type': 'application/json'
            },
            body: jsonEncode({
              'new_email': newEmail,
              'code': code,
              'password': password,
            }))
        .timeout(AccountSessionClient.timeout);
    if (!await accountSessions.isCurrent(expected)) {
      throw RequestException(409, '账号或服务器已切换，本次结果不再应用');
    }
    if (resp.statusCode == 401) {
      await accountSessions.onUnauthorized?.call(expected);
      throw RequestException(401, '登录已失效，请重新登录');
    }
    final Map<String, dynamic> body;
    try {
      body = jsonDecode(decode_http_response(resp));
    } catch (e) {
      debugPrint("changeEmail: jsonDecode resp body failed: ${e.toString()}");
      if (resp.statusCode != 200) {
        BotToast.showText(
            contentColor: Colors.red, text: 'HTTP ${resp.statusCode}');
      }
      rethrow;
    }
    if (resp.statusCode != 200) {
      throw RequestException(resp.statusCode, body['error'] ?? '');
    }
    if (body['error'] != null) {
      throw RequestException(0, body['error']);
    }
    final user = UserPayload.fromJson(body['user'] ?? {});
    _parseAndUpdateUser(user);
    return user;
  }

  /// 当前账号下所有设备（我的设备）。
  /// throw [RequestException]
  Future<List<Map<String, dynamic>>> getMyDevices(
      {bool syncDevice = true}) async {
    deviceSyncError.value = '';
    final resp = await _deviceSync.loadPeers(
        (url, {headers}) => http.get(url, headers: headers),
        syncDevice: syncDevice && !isWeb,
        registrationError: (_) =>
            deviceSyncError.value = '本机设备暂未同步，已保留其他设备列表；可稍后刷新重试。');
    if (resp == null) return [];
    final Map<String, dynamic> body;
    try {
      body = jsonDecode(decode_http_response(resp));
    } catch (e) {
      debugPrint("getMyDevices: jsonDecode failed: ${e.toString()}");
      rethrow;
    }
    if (resp.statusCode != 200) {
      throw RequestException(resp.statusCode, body['error'] ?? '');
    }
    if (body['error'] != null) {
      throw RequestException(0, body['error']);
    }
    final list = body['data'];
    if (list is! List) {
      return [];
    }
    return list.whereType<Map<String, dynamic>>().toList();
  }

  /// 删除当前账号下的设备记录。
  Future<bool> deleteMyDevice(String uuid,
      {required AccountDeviceSession expectedSession}) async {
    final resp = await _deviceSync.removePeer(uuid, expectedSession,
        (url, {headers}) => http.delete(url, headers: headers));
    if (resp == null) return false;
    _throwIfError(resp);
    return true;
  }

  void _throwIfError(http.Response resp) {
    final Map<String, dynamic> body;
    try {
      body = jsonDecode(decode_http_response(resp));
    } catch (e) {
      debugPrint("auth request failed to decode: ${e.toString()}");
      if (resp.statusCode != 200) {
        BotToast.showText(
            contentColor: Colors.red, text: 'HTTP ${resp.statusCode}');
      }
      rethrow;
    }
    if (resp.statusCode != 200) {
      throw RequestException(resp.statusCode, body['error'] ?? '');
    }
    if (body['error'] != null) {
      throw RequestException(0, body['error']);
    }
  }

  Future<LoginResponse> _postAuth(
      String path, LoginRequest loginRequest) async {
    final device = await _readAccountDevice();
    loginRequest.id = accountRemoteId(loginRequest.id ?? device.id);
    loginRequest.uuid = device.uuid;
    final url = await bind.mainGetApiServer();
    final resp = await http.post(Uri.parse('$url$path'),
        body: jsonEncode(loginRequest.toJson()));

    final Map<String, dynamic> body;
    try {
      body = jsonDecode(decode_http_response(resp));
    } catch (e) {
      debugPrint(
          "post auth $path: jsonDecode resp body failed: ${e.toString()}");
      if (resp.statusCode != 200) {
        BotToast.showText(
            contentColor: Colors.red, text: 'HTTP ${resp.statusCode}');
      }
      rethrow;
    }
    if (resp.statusCode != 200) {
      throw RequestException(resp.statusCode, body['error'] ?? '');
    }
    if (body['error'] != null) {
      throw RequestException(0, body['error']);
    }

    return getLoginResponseFromAuthBody(body);
  }

  LoginResponse getLoginResponseFromAuthBody(Map<String, dynamic> body) {
    final LoginResponse loginResponse;
    try {
      loginResponse = LoginResponse.fromJson(body);
    } catch (e) {
      debugPrint("login: jsonDecode LoginResponse failed: ${e.toString()}");
      rethrow;
    }

    final isLogInDone = loginResponse.type == HttpType.kAuthResTypeToken &&
        loginResponse.access_token != null;
    if (isLogInDone && loginResponse.user != null) {
      _parseAndUpdateUser(loginResponse.user!);
    }

    return loginResponse;
  }

  /// Throws on network failures, non-success responses, and invalid response
  /// data. Returns an empty list when no API server is configured or a
  /// successful response contains no third-party login options.
  static Future<List<dynamic>> queryOidcLoginOptions() async {
    final url = await bind.mainGetApiServer();
    if (url.trim().isEmpty) return [];
    final resp = await http.get(Uri.parse('$url/api/login-options'));
    const successStatusCodeStart = 200;
    const successStatusCodeEnd = 300;
    if (resp.statusCode < successStatusCodeStart ||
        resp.statusCode >= successStatusCodeEnd) {
      throw RequestException(
          resp.statusCode, resp.reasonPhrase ?? 'Request failed');
    }
    final List<String> ops = [];
    for (final item in jsonDecode(resp.body)) {
      ops.add(item as String);
    }
    for (final item in ops) {
      if (item.startsWith('common-oidc/')) {
        return jsonDecode(item.substring('common-oidc/'.length));
      }
    }
    return ops
        .where((item) => item.startsWith('oidc/'))
        .map((item) => {'name': item.substring('oidc/'.length)})
        .toList();
  }
}
