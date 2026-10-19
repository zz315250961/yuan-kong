import 'dart:async';
import 'dart:convert';

import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/common/mobile_connection_link.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('connection URI handler rejects non-app schemes before navigation', () {
    expect(urlLinkToCmdArgs(Uri.parse('https://123456789')), isNull);
  });

  test('truncated command arguments are rejected before native access', () {
    for (final flag in [
      '--connect',
      '--file-transfer',
      '--view-camera',
      '--port-forward',
      '--rdp',
      '--terminal',
      '--terminal-admin',
      '--password',
      '--switch_uuid'
    ]) {
      expect(handleUriLink(cmdArgs: [flag]), isFalse, reason: flag);
    }
  });

  test('device QR accepts plain IDs and supported connection link kinds', () {
    expect(
        MobileScanResult.parse(' 123 456 789 ')?.connection?.id, '123456789');
    for (final entry in {
      'linkremote://123456789': MobileConnectionKind.remote,
      'linkremote://connect/123456789': MobileConnectionKind.remote,
      'linkremote://connection/new/123456789': MobileConnectionKind.remote,
      'linkremote://file-transfer/123456789': MobileConnectionKind.fileTransfer,
      'linkremote://view-camera/123456789': MobileConnectionKind.viewCamera,
      'linkremote://terminal/123456789': MobileConnectionKind.terminal,
    }.entries) {
      final result = MobileScanResult.parse(entry.key)!;
      expect(result.connection!.id, '123456789');
      expect(result.connection!.kind, entry.value);
      expect(result.serverConfig, isNull);
    }
  });

  test('link options preserve literal key and password, relay and self checks',
      () {
    final link = MobileConnectionLink.parse(
        'linkremote://123456789/r?key=Ab%2BC%3D&password=secret%2Bword&relay=1')!;
    expect(link.id, '123456789/r?key=Ab%2BC%3D');
    expect(link.password, 'secret+word');
    expect(link.forceRelay, isTrue);
    expect(link.isSelf('123 456 789'), isTrue);
    expect(link.isSelf('987654321'), isFalse);
  });

  test('invalid, unsupported, oversized and ambiguous QR input never connects',
      () {
    for (final value in [
      '',
      ' ',
      '12',
      'https://123456789',
      'javascript:alert(1)',
      'linkremote://',
      'linkremote://connect',
      'linkremote://connect/',
      'linkremote://connection/new/',
      'linkremote://config/',
      'linkremote://password/secret',
      'linkremote://rdp/123456789',
      'linkremote://port-forward/123456789',
      'linkremote://terminal-admin/123456789',
      'linkremote://123456789#fragment',
      'linkremote://user@123456789',
      'linkremote://connect/123%2f456',
      'linkremote://connect/123%00',
      'linkremote://123456789?password=one&password=two',
      'linkremote://123456789?key=one&KEY=two',
      'linkremote://123456789?password=%ZZ',
      'linkremote://123456789?password=%00',
      'linkremote://123456789?unknown=one',
      'linkremote://123456789?password=${'a' * 1025}',
      'a' * 4097,
      '123\n456789',
      r'123\456789',
    ]) {
      expect(MobileScanResult.parse(value), isNull,
          reason: value.substring(0, value.length.clamp(0, 80)));
    }
  });

  test('server config QR is separate, typed, bounded and never a device link',
      () {
    final config = {
      'host': 'example.test:21116',
      'relay': '',
      'api': '',
      'key': 'AB+c='
    };
    final json = jsonEncode(config);
    final encoded =
        base64UrlEncode(utf8.encode(json)).split('').reversed.join();
    for (final value in ['config=$json', 'config=$encoded']) {
      final result = MobileScanResult.parse(value)!;
      expect(result.connection, isNull);
      expect(result.serverConfig, config);
    }
    for (final value in [
      'config=',
      'config=oops',
      'config=[]',
      'config={}',
      'config={"host":123}',
      'config={"host":""}',
      'config={"host":"ok","unexpected":"x"}',
      'config={"host":"\\u0000"}',
    ]) {
      expect(MobileScanResult.parse(value), isNull);
    }
  });

  test('pending validation coalesces repeats and drops disposed-page results',
      () async {
    final gate = MobileConnectionGate();
    final local = Completer<String>();
    var navigations = 0;
    const link = MobileConnectionLink('123456789');
    Future<bool> open() => gate.open(link,
        localId: () => local.future,
        isActive: () => true,
        navigate: (_) async => navigations++,
        onSelf: () => fail('Not self'));
    final first = open();
    expect(await open(), isFalse);
    gate.dispose();
    local.complete('987654321');
    expect(await first, isFalse);
    expect(navigations, 0);
    expect(await open(), isFalse);
  });

  test('self or inactive page does not navigate and errors release the gate',
      () async {
    final gate = MobileConnectionGate();
    var selfErrors = 0;
    var active = true;
    var navigations = 0;
    Future<bool> open(String ownId) =>
        gate.open(const MobileConnectionLink('123456789/r'),
            localId: () async => ownId,
            isActive: () => active,
            navigate: (_) async {
              navigations++;
            },
            onSelf: () => selfErrors++);
    expect(await open('123456789'), isFalse);
    expect(selfErrors, 1);
    active = false;
    expect(await open('987654321'), isFalse);
    active = true;
    expect(await open('987654321'), isTrue);
    expect(navigations, 1);
    await expectLater(
        gate.open(const MobileConnectionLink('123456789'),
            localId: () async => throw StateError('offline'),
            isActive: () => true,
            navigate: (_) async {},
            onSelf: () {}),
        throwsStateError);
    expect(await open('987654321'), isTrue);
  });
}
