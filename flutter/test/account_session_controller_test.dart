import 'dart:async';
import 'dart:convert';
import 'package:flutter_hbb/utils/account_device_sync.dart';
import 'package:flutter_hbb/utils/account_session_controller.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

const accountA = AccountDeviceSession('https://api.example.test/remote', 'A');
const accountB = AccountDeviceSession('https://api.example.test/remote', 'B');
const targetId = 'synthetic-session-opaque-02';
String listing() => jsonEncode({
      'sessions': [
        {
          'id': 'synthetic-session-opaque-01',
          'device_name': 'Same name',
          'platform': 'windows',
          'is_current': true
        },
        {
          'id': targetId,
          'device_name': 'Same name',
          'platform': 'windows',
          'is_current': false
        },
      ]
    });

void main() {
  test('production HTTP adapter sends scoped bearer and request-local password',
      () async {
    final api = AccountSessionClient(
        session: () async => accountA,
        get: (url, {headers}) async {
          expect(url.path, '/remote/api/sessions');
          expect(headers!['Authorization'], 'Bearer A');
          return http.Response(listing(), 200);
        },
        post: (url, {headers, body}) async {
          expect(url.path, '/remote/api/sessions/$targetId/revoke');
          expect(
              jsonDecode(body as String), {'password': 'temporary-password'});
          return http.Response('{"revoked":true}', 200);
        });
    final controller = AccountSessionController(api);
    await controller.load();
    expect(controller.sessions.length, 2);
    expect(await controller.revoke('synthetic-session-opaque-01', 'password'),
        isFalse);
    expect(await controller.revoke(targetId, 'temporary-password'), isTrue);
    expect(controller.sessions.single.isCurrent, isTrue);
    controller.dispose();
  });
  test(
      'late load and 401 after account or API changes do not touch new account',
      () async {
    var account = accountA;
    var unauthorized = 0;
    final pending = Completer<http.Response>();
    final api = AccountSessionClient(
        session: () async => account,
        get: (_, {headers}) => pending.future,
        post: (_, {headers, body}) async => http.Response('{}', 200),
        onUnauthorized: (_) async {
          unauthorized++;
        });
    final controller = AccountSessionController(api);
    final operation = controller.load();
    await Future<void>.delayed(Duration.zero);
    account = accountB;
    pending.complete(http.Response('{}', 401));
    await operation;
    expect(controller.sessions, isEmpty);
    expect(unauthorized, 0);
    expect(controller.error, isEmpty);
    controller.dispose();
  });
  test(
      'duplicate revoke blocked, wrong password preserved and dispose ignores late response',
      () async {
    final pending = Completer<http.Response>();
    var calls = 0;
    final api = AccountSessionClient(
        session: () async => accountA,
        get: (_, {headers}) async => http.Response(listing(), 200),
        post: (_, {headers, body}) {
          calls++;
          return pending.future;
        });
    final controller = AccountSessionController(api);
    await controller.load();
    final first = controller.revoke(targetId, 'wrong');
    await Future<void>.delayed(Duration.zero);
    expect(await controller.revoke(targetId, 'wrong'), isFalse);
    pending
        .complete(http.Response.bytes(utf8.encode('{"error":"当前密码不正确"}'), 403));
    expect(await first, isFalse);
    expect(calls, 1);
    expect(controller.sessions.length, 2);
    expect(controller.error, contains('当前密码不正确'));
    controller.dispose();
    expect(await controller.revoke(targetId, 'not-sent'), isFalse);
    expect(calls, 1);
  });
  test('retired page and changed API cannot issue a mutation', () async {
    var account = accountA;
    var posts = 0;
    final api = AccountSessionClient(
        session: () async => account,
        get: (_, {headers}) async => http.Response(listing(), 200),
        post: (_, {headers, body}) async {
          posts++;
          return http.Response('{"revoked":true}', 200);
        });
    final controller = AccountSessionController(api);
    await controller.load();
    account = const AccountDeviceSession('https://other.example.test', 'A');
    expect(await controller.revoke(targetId, 'password'), isFalse);
    expect(posts, 0);
    controller.dispose();
  });
  test('disposed load cannot process late 401 or notify listeners', () async {
    final pending = Completer<http.Response>();
    var unauthorized = 0;
    final controller = AccountSessionController(AccountSessionClient(
        session: () async => accountA,
        get: (_, {headers}) => pending.future,
        post: (_, {headers, body}) async => http.Response('{}', 200),
        onUnauthorized: (_) async {
          unauthorized++;
        }));
    final loading = controller.load();
    await Future<void>.delayed(Duration.zero);
    controller.dispose();
    pending.complete(http.Response('{}', 401));
    await loading;
    expect(unauthorized, 0);
  });
  for (final status in [401, 409, 429, 503]) {
    test('HTTP $status is explicit and does not remove a session', () async {
      var unauthorized = 0;
      final controller = AccountSessionController(AccountSessionClient(
          session: () async => accountA,
          get: (_, {headers}) async => http.Response(listing(), 200),
          post: (_, {headers, body}) async =>
              http.Response('{"error":"expected failure"}', status),
          onUnauthorized: (_) async {
            unauthorized++;
          }));
      await controller.load();
      expect(await controller.revoke(targetId, 'password'), isFalse);
      expect(controller.sessions.length, 2);
      expect(controller.error, isNotEmpty);
      expect(unauthorized, status == 401 ? 1 : 0);
      controller.dispose();
    });
  }
}
