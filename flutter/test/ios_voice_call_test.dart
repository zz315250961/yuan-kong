import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_hbb/utils/ios_voice_call.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel(iosVoiceChannelName);
  var requests = 0;
  var closes = 0;
  final errors = <String>[];
  final calls = <MethodCall>[];
  late IosVoiceCallCoordinator controller;
  late Future<dynamic> Function(MethodCall) native;

  setUp(() {
    requests = 0;
    closes = 0;
    errors.clear();
    calls.clear();
    native = (call) async => call.method == 'prepare' ? true : null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) {
      calls.add(call);
      return native(call);
    });
    controller = IosVoiceCallCoordinator(
      owner: 'test-session',
      requestCall: () async => requests++,
      closeCall: () async => closes++,
      onError: errors.add,
    );
  });

  tearDown(() async {
    controller.dispose();
    await Future<void>.delayed(Duration.zero);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('permission and audio preparation precede one remote request', () async {
    final prepared = Completer<bool>();
    native = (call) async => call.method == 'prepare' ? prepared.future : null;
    final requesting = controller.request();
    await Future<void>.delayed(Duration.zero);
    expect(requests, 0);
    expect(await controller.request(), isFalse);
    prepared.complete(true);
    expect(await requesting, isTrue);
    expect(requests, 1);
    expect(await controller.request(), isFalse);
    await controller.stop();
    await controller.stop();
    expect(closes, 1);
    expect(calls.map((call) => call.method), ['prepare', 'end']);
  });

  test('denied permission never requests a remote call and can retry',
      () async {
    native = (call) async {
      if (call.method == 'prepare') {
        throw PlatformException(code: 'microphone_denied');
      }
      return null;
    };
    expect(await controller.request(), isFalse);
    expect(requests, 0);
    expect(errors.single, contains('权限被拒绝'));
    native = (call) async => call.method == 'prepare' ? true : null;
    expect(await controller.request(), isTrue);
    expect(requests, 1);
  });

  test('leaving during permission prompt cannot start a late call', () async {
    final prepared = Completer<bool>();
    native = (call) async => call.method == 'prepare' ? prepared.future : null;
    final requesting = controller.request();
    await Future<void>.delayed(Duration.zero);
    controller.dispose();
    prepared.complete(true);
    expect(await requesting, isFalse);
    expect(requests, 0);
    expect(errors, isEmpty);
  });

  test('background cancels pending permission but inactive does not', () async {
    final prepared = Completer<bool>();
    native = (call) async => call.method == 'prepare' ? prepared.future : null;
    final requesting = controller.request();
    controller.didChangeAppLifecycleState(AppLifecycleState.inactive);
    await Future<void>.delayed(Duration.zero);
    expect(calls.where((call) => call.method == 'end'), isEmpty);
    controller.didChangeAppLifecycleState(AppLifecycleState.paused);
    prepared.complete(true);
    expect(await requesting, isFalse);
    expect(requests, 0);
  });

  test('remote closure restores audio without sending a second close',
      () async {
    expect(await controller.request(), isTrue);
    await controller.stop(sendClose: false);
    expect(closes, 0);
    expect(calls.last.method, 'end');
  });

  test('active call closes on background with no automatic recording resume',
      () async {
    expect(await controller.request(), isTrue);
    controller.didChangeAppLifecycleState(AppLifecycleState.paused);
    await Future<void>.delayed(Duration.zero);
    controller.didChangeAppLifecycleState(AppLifecycleState.resumed);
    expect(requests, 1);
    expect(closes, 1);
  });

  test('old native completion does not release a newer owner', () async {
    final firstPermission = Completer<bool>();
    var prepares = 0;
    native = (call) async {
      if (call.method == 'prepare') {
        return ++prepares == 1 ? firstPermission.future : true;
      }
      return null;
    };
    final oldRequest = controller.request();
    await Future<void>.delayed(Duration.zero);
    await controller.stop();
    expect(await controller.request(), isTrue);
    final currentOwner =
        calls.lastWhere((call) => call.method == 'prepare').arguments['owner'];
    firstPermission.complete(true);
    expect(await oldRequest, isFalse);
    expect(calls.last.arguments['owner'], isNot(currentOwner));
    expect(requests, 1);
  });

  Future<void> interrupt(String owner) async {
    final done = Completer<void>();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
            iosVoiceChannelName,
            const StandardMethodCodec().encodeMethodCall(
                MethodCall('interrupted', {'owner': owner, 'reason': 'route'})),
            (_) => done.complete());
    await done.future;
  }

  test('native route interruption closes only its matching active owner',
      () async {
    expect(await controller.request(), isTrue);
    final owner = calls.first.arguments['owner'] as String;
    await interrupt('another-session:1');
    expect(closes, 0);
    await interrupt(owner);
    expect(closes, 1);
    expect(errors.single, contains('已中断'));
    expect(await controller.request(), isTrue);
    await interrupt(owner);
    expect(closes, 1);
    expect(requests, 2);
  });

  test('transport request failure releases the native audio owner', () async {
    controller.dispose();
    controller = IosVoiceCallCoordinator(
      owner: 'failing-transport',
      requestCall: () async => throw StateError('disconnected'),
      closeCall: () async => closes++,
      onError: errors.add,
    );
    expect(await controller.request(), isFalse);
    expect(calls.last.method, 'end');
    expect(closes, 1);
    expect(errors.single, contains('无法启动'));
  });

  testWidgets('unanswered request times out and restores the audio session',
      (tester) async {
    expect(await controller.request(), isTrue);
    await tester.pump(const Duration(seconds: 46));
    expect(closes, 1);
    expect(errors.single, contains('未响应'));
    expect(calls.last.method, 'end');
  });

  testWidgets('connected call is not ended by the pending-request timeout',
      (tester) async {
    expect(await controller.request(), isTrue);
    controller.connected();
    await tester.pump(const Duration(seconds: 46));
    expect(closes, 0);
    expect(errors, isEmpty);
    await controller.stop();
  });
}
