import 'dart:async';
import 'dart:convert';
import 'dart:isolate';

import 'package:flutter_hbb/utils/account_device_sync.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

String _freshIsolateRequestUrl() =>
    accountDeviceRequestUri('https://api.example.test', '/api/heartbeat')
        .toString();

void main() {
  test(
      'current heartbeat 401 expires only that account and next sync sends nothing',
      () async {
    var account =
        const AccountDeviceSession('https://api.example.test', 'revoked');
    var posts = 0;
    var expired = 0;
    final sync = AccountDeviceSync(
        session: () async => account,
        identity: () async => const AccountDeviceIdentity('123', 'install', {}),
        post: (_, {headers, body}) async {
          posts++;
          return http.Response('{}', 401);
        },
        onUnauthorized: (expected) async {
          expect(expected.token, 'revoked');
          expired++;
          account = const AccountDeviceSession('', '');
        });
    await expectLater(sync.sync(), throwsA(isA<http.ClientException>()));
    await sync.sync();
    expect(expired, 1);
    expect(posts, 1);
  });
  for (final changeApi in [false, true]) {
    test(
        'late heartbeat 401 after ${changeApi ? 'API' : 'token'} switch cannot expire new account',
        () async {
      var account =
          const AccountDeviceSession('https://api.example.test', 'old');
      final pending = Completer<http.Response>();
      var expired = 0;
      final sync = AccountDeviceSync(
          session: () async => account,
          identity: () async =>
              const AccountDeviceIdentity('123', 'install', {}),
          post: (_, {headers, body}) => pending.future,
          onUnauthorized: (_) async {
            expired++;
          });
      final operation = sync.sync();
      await Future<void>.delayed(Duration.zero);
      account = changeApi
          ? const AccountDeviceSession('https://new.example.test', 'old')
          : const AccountDeviceSession('https://api.example.test', 'new');
      pending.complete(http.Response('{}', 401));
      await expectLater(operation, throwsA(isA<http.ClientException>()));
      expect(expired, 0);
    });
  }
  test(
      'missing native UUID uses one persistent installation UUID, never remote ID 0',
      () async {
    var saved = '';
    var generated = 0;
    Future<String> read(String native) => accountInstallationUuid(native,
        readSaved: () => saved,
        save: (value) async {
          saved = value;
        },
        create: () => 'persistent-${++generated}');
    expect(await read(''), 'persistent-1');
    expect(await read('0'), 'persistent-1');
    expect(await read('NA'), 'persistent-1');
    expect(generated, 1);
    expect(await read('real-native-uuid'), 'persistent-1',
        reason: 'A late native UUID must not create a duplicate installation');
    for (final id in ['', '0', 'NA', 'null', 'unknown']) {
      expect(accountRemoteId(id), '');
    }
    expect(accountRemoteId('123 456 789'), '123456789');
  });
  for (final os in ['windows', 'android', 'ios']) {
    test(
        '$os account sync preserves stable UUID and authenticated ownership with empty ID',
        () async {
      final sent = <Map<String, dynamic>>[];
      final sync = AccountDeviceSync(
        session: () async => const AccountDeviceSession(
            'https://api.example.test/remote', 'account-A'),
        identity: () async => AccountDeviceIdentity(
            '', '$os-install', {'os': os, 'name': '$os device'}),
        post: (url, {headers, body}) async {
          expect(url.path, '/remote/api/heartbeat');
          expect(headers!['Authorization'], 'Bearer account-A');
          sent.add(jsonDecode(body as String));
          return http.Response('{"ok":true}', 200);
        },
      );
      await sync.sync();
      expect(sent.single['uuid'], '$os-install');
      expect(sent.single['id'], '');
      expect(sent.single['deviceInfo']['os'], os);
    });
  }

  test(
      'late remote ID is uploaded under the same installation UUID on next sync',
      () async {
    var id = '';
    final sent = <Map<String, dynamic>>[];
    final sync = AccountDeviceSync(
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () async => AccountDeviceIdentity(id, 'stable-install', {}),
      post: (_, {headers, body}) async {
        sent.add(jsonDecode(body as String));
        return http.Response('{}', 200);
      },
    );
    await sync.sync();
    id = '123456789';
    await sync.sync();
    expect(
        sent.map((body) => body['uuid']), ['stable-install', 'stable-install']);
    expect(sent.map((body) => body['id']), ['', '123456789']);
  });

  test(
      'logout or account switch during identity read never registers the new account under an old token',
      () async {
    for (final nextToken in ['', 'account-B']) {
      var token = 'account-A';
      final device = Completer<AccountDeviceIdentity>();
      var sent = 0;
      final sync = AccountDeviceSync(
        session: () async =>
            AccountDeviceSession('https://api.example.test', token),
        identity: () => device.future,
        post: (_, {headers, body}) async {
          sent++;
          return http.Response('{}', 200);
        },
      );
      final pending = sync.sync();
      await Future<void>.delayed(Duration.zero);
      token = nextToken;
      device.complete(const AccountDeviceIdentity('123', 'install', {}));
      await pending;
      expect(sent, 0);
    }
  });

  test('concurrent refreshes share one request and a failure releases the gate',
      () async {
    final response = Completer<http.Response>();
    var sent = 0;
    final sync = AccountDeviceSync(
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () async => const AccountDeviceIdentity('123', 'install', {}),
      post: (_, {headers, body}) async {
        if (++sent == 1) return response.future;
        return http.Response('{}', 200);
      },
    );
    final one = sync.sync();
    final two = sync.sync();
    await Future<void>.delayed(Duration.zero);
    expect(sent, 1);
    final failures = [
      expectLater(one, throwsA(isA<http.ClientException>())),
      expectLater(two, throwsA(isA<http.ClientException>())),
    ];
    response.complete(http.Response('{}', 503));
    await Future.wait(failures);
    await sync.sync();
    expect(sent, 2);
  });

  test(
      'older API falls back to authenticated currentUser without losing device data',
      () async {
    final paths = <String>[];
    final sync = AccountDeviceSync(
      session: () async =>
          const AccountDeviceSession('https://api.example.test/', 'A'),
      identity: () async =>
          const AccountDeviceIdentity('123', 'install', {'os': 'android'}),
      post: (url, {headers, body}) async {
        paths.add(url.path);
        expect(headers!['Authorization'], 'Bearer A');
        expect(jsonDecode(body as String)['uuid'], 'install');
        return http.Response('{}', paths.length == 1 ? 404 : 200);
      },
    );
    await sync.sync();
    expect(paths, ['/api/heartbeat', '/api/currentUser']);
  });

  test('browser/no-installation and logged-out sessions never create peer rows',
      () async {
    for (final credentials in [('', 'install'), ('A', '')]) {
      var sent = 0;
      final sync = AccountDeviceSync(
        session: () async =>
            AccountDeviceSession('https://api.example.test', credentials.$1),
        identity: () async => AccountDeviceIdentity('', credentials.$2, {}),
        post: (_, {headers, body}) async {
          sent++;
          return http.Response('{}', 200);
        },
      );
      await sync.sync();
      expect(sent, 0);
    }
  });

  test('registration 503 does not hide existing account devices', () async {
    var warnings = 0;
    var gets = 0;
    final sync = AccountDeviceSync(
      session: () async =>
          const AccountDeviceSession('https://api.example.test/remote/', 'A'),
      identity: () async => const AccountDeviceIdentity('', 'install', {}),
      post: (_, {headers, body}) async => http.Response('{}', 503),
    );
    final response = await sync.loadPeers((url, {headers}) async {
      gets++;
      expect(url.path, '/remote/api/peers');
      expect(headers!['Authorization'], 'Bearer A');
      return http.Response('{"data":[{"uuid":"other-install"}]}', 200);
    }, registrationError: (_) => warnings++);
    expect(gets, 1);
    expect(warnings, 1);
    expect(jsonDecode(response!.body)['data'].single['uuid'], 'other-install');
  });

  test(
      'timed-out identity releases gate and its late completion cannot register',
      () async {
    final firstIdentity = Completer<AccountDeviceIdentity>();
    var reads = 0;
    var posts = 0;
    var warnings = 0;
    final sync = AccountDeviceSync(
      timeout: const Duration(milliseconds: 30),
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () => ++reads == 1
          ? firstIdentity.future
          : Future.value(const AccountDeviceIdentity('123', 'install', {})),
      post: (_, {headers, body}) async {
        posts++;
        return http.Response('{}', 200);
      },
    );
    final response = await sync
        .loadPeers((_, {headers}) async => http.Response('{"data":[]}', 200),
            registrationError: (error) {
      expect(error, isA<TimeoutException>());
      warnings++;
    });
    expect(response!.statusCode, 200);
    expect(warnings, 1);
    await sync.sync();
    expect(posts, 1);
    firstIdentity.complete(const AccountDeviceIdentity('123', 'install', {}));
    await Future<void>.delayed(Duration.zero);
    expect(posts, 1);
  });

  test('new account bypasses hung old-account sync and retires its late result',
      () async {
    var token = 'A';
    final firstIdentity = Completer<AccountDeviceIdentity>();
    var reads = 0;
    final tokens = <String>[];
    final sync = AccountDeviceSync(
      session: () async =>
          AccountDeviceSession('https://api.example.test', token),
      identity: () => ++reads == 1
          ? firstIdentity.future
          : Future.value(const AccountDeviceIdentity('123', 'install', {})),
      post: (_, {headers, body}) async {
        tokens.add(headers!['Authorization']!);
        return http.Response('{}', 200);
      },
    );
    final oldSync = sync.sync();
    await Future<void>.delayed(Duration.zero);
    token = 'B';
    await sync.sync();
    expect(tokens, ['Bearer B']);
    firstIdentity.complete(const AccountDeviceIdentity('123', 'install', {}));
    await oldSync;
    expect(tokens, ['Bearer B']);
  });

  test('late 404 after timeout never starts the older-API fallback request',
      () async {
    final lateResponse = Completer<http.Response>();
    var posts = 0;
    final sync = AccountDeviceSync(
      timeout: const Duration(milliseconds: 30),
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () async => const AccountDeviceIdentity('123', 'install', {}),
      post: (_, {headers, body}) {
        posts++;
        return lateResponse.future;
      },
    );
    await expectLater(sync.sync(), throwsA(isA<TimeoutException>()));
    lateResponse.complete(http.Response('{}', 404));
    await Future<void>.delayed(Duration.zero);
    expect(posts, 1);
  });

  for (final changed in ['token', 'api']) {
    test('late peers response is discarded after $changed changes', () async {
      var account = const AccountDeviceSession('https://api.example.test', 'A');
      final lateResponse = Completer<http.Response>();
      final sync = AccountDeviceSync(
        session: () async => account,
        identity: () async => const AccountDeviceIdentity('123', 'install', {}),
        post: (_, {headers, body}) async => http.Response('{}', 200),
      );
      final load = sync.loadPeers((url, {headers}) {
        expect(url.host, 'api.example.test');
        expect(headers!['Authorization'], 'Bearer A');
        return lateResponse.future;
      },
          syncDevice: false,
          registrationError: (_) => fail('Unexpected warning'));
      await Future<void>.delayed(Duration.zero);
      account = changed == 'token'
          ? const AccountDeviceSession('https://api.example.test', 'B')
          : const AccountDeviceSession('https://other.example.test', 'A');
      lateResponse
          .complete(http.Response('{"data":[{"uuid":"private"}]}', 200));
      expect(await load, isNull);
    });
  }

  test('peers GET is bounded even when transport never completes', () async {
    final sync = AccountDeviceSync(
      timeout: const Duration(milliseconds: 30),
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () async => const AccountDeviceIdentity('123', 'install', {}),
      post: (_, {headers, body}) async => http.Response('{}', 200),
    );
    await expectLater(
        sync.loadPeers((_, {headers}) => Completer<http.Response>().future,
            syncDevice: false,
            registrationError: (_) => fail('No registration')),
        throwsA(isA<TimeoutException>()));
  });

  test(
      'slow local registration has a short list-only wait without cancelling sync',
      () async {
    final registration = Completer<http.Response>();
    var warnings = 0;
    final sync = AccountDeviceSync(
      registrationWait: const Duration(milliseconds: 30),
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'A'),
      identity: () async => const AccountDeviceIdentity('123', 'install', {}),
      post: (_, {headers, body}) => registration.future,
    );
    final response = await sync
        .loadPeers((_, {headers}) async => http.Response('{"data":[{}]}', 200),
            registrationError: (error) {
      expect(error, isA<TimeoutException>());
      warnings++;
    });
    expect(response!.statusCode, 200);
    expect(warnings, 1);
    registration.complete(http.Response('{}', 200));
    await sync.sync();
  });

  test(
      'account request URLs isolate native URL-keyed results without leaking identity',
      () {
    final urls = [
      for (final path in [
        '/api/heartbeat',
        '/api/currentUser',
        '/api/peers',
        '/api/peers/device'
      ])
        for (var i = 0; i < 2; i++)
          accountDeviceRequestUri('https://api.example.test/remote/', path),
    ];
    expect(urls.toSet().length, 8);
    final noncePattern = RegExp(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}:[1-9][0-9]*$');
    final scope = urls.first.queryParameters.values.single.split(':').first;
    int? previousSerial;
    for (final url in urls) {
      expect(url.queryParameters.keys, ['account_device_request']);
      final nonce = url.queryParameters.values.single;
      expect(noncePattern.hasMatch(nonce), isTrue);
      expect(nonce.split(':').first, scope);
      final serial = int.parse(nonce.split(':').last);
      if (previousSerial != null) expect(serial, previousSerial + 1);
      previousSerial = serial;
      expect(url.path.startsWith('/remote/api/'), isTrue);
    }
  });

  test('fresh isolates do not reuse their first native HTTP request URL',
      () async {
    final urls = await Future.wait([
      Isolate.run(_freshIsolateRequestUrl),
      Isolate.run(_freshIsolateRequestUrl),
    ]);
    expect(urls[0], isNot(urls[1]),
        reason:
            'Separate Flutter engines share the native URL-keyed result map');
    for (final url in urls) {
      final nonce = Uri.parse(url).queryParameters['account_device_request']!;
      expect(nonce.split(':').last, '1');
    }
  });

  for (final changed in ['api', 'token']) {
    test('remove confirmation from an old $changed session never sends DELETE',
        () async {
      const original = AccountDeviceSession('https://api.example.test', 'A');
      final current = changed == 'api'
          ? const AccountDeviceSession('https://other.example.test', 'A')
          : const AccountDeviceSession('https://api.example.test', 'B');
      final sync = AccountDeviceSync(
        session: () async => current,
        identity: () async => const AccountDeviceIdentity('123', 'install', {}),
        post: (_, {headers, body}) async => http.Response('{}', 200),
      );
      final response = await sync.removePeer('private/device', original, (_,
          {headers}) async {
        fail('Old confirmation must not use the new account or server');
      });
      expect(response, isNull);
    });

    test('late DELETE result after $changed switch cannot update the new list',
        () async {
      var current = const AccountDeviceSession('https://api.example.test', 'A');
      final original = current;
      final removed = Completer<http.Response>();
      final sync = AccountDeviceSync(
        session: () async => current,
        identity: () async => const AccountDeviceIdentity('123', 'install', {}),
        post: (_, {headers, body}) async => http.Response('{}', 200),
      );
      final response =
          sync.removePeer('private/device', original, (url, {headers}) {
        expect(url.path, '/api/peers/private%2Fdevice');
        expect(url.queryParameters['account_device_request'], isNotNull);
        expect(headers!['Authorization'], 'Bearer A');
        return removed.future;
      });
      await Future<void>.delayed(Duration.zero);
      current = changed == 'api'
          ? const AccountDeviceSession('https://other.example.test', 'A')
          : const AccountDeviceSession('https://api.example.test', 'B');
      removed.complete(http.Response('{}', 200));
      expect(await response, isNull);
    });
  }
}
