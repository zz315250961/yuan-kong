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

  test('recreated coordinator cannot reuse a pending native owner key',
      () async {
    final permission = Completer<bool>();
    var preparations = 0;
    native = (call) async => call.method == 'prepare'
        ? (++preparations == 1 ? permission.future : true)
        : null;
    final oldRequest = controller.request();
    await Future<void>.delayed(Duration.zero);
    controller.dispose();
    await Future<void>.delayed(Duration.zero);
    controller = IosVoiceCallCoordinator(
      owner: 'test-session',
      requestCall: () async => requests++,
      closeCall: () async => closes++,
      onError: errors.add,
    );
    expect(await controller.request(), isTrue);
    final newOwner =
        calls.lastWhere((call) => call.method == 'prepare').arguments['owner'];
    permission.complete(true);
    expect(await oldRequest, isFalse);
    expect(calls.last.arguments['owner'], isNot(newOwner));
    expect(requests, 1);
  });

  test('close acknowledgement fences redial without delaying native cleanup',
      () async {
    controller.dispose();
    final acknowledged = Completer<void>();
    controller = IosVoiceCallCoordinator(
      owner: 'close-barrier',
      requestCall: () async => requests++,
      closeCall: () {
        closes++;
        return acknowledged.future;
      },
      onError: errors.add,
    );
    expect(await controller.request(), isTrue);
    final stopping = controller.stop();
    await Future<void>.delayed(Duration.zero);
    expect(closes, 1);
    expect(calls.last.method, 'end',
        reason: 'Audio policy cleanup must not wait for queued Rust close');
    expect(await controller.request(), isFalse);
    // The old closed event precedes its acknowledgement on the same stream.
    unawaited(controller.stop(sendClose: false));
    expect(await controller.request(), isFalse);
    acknowledged.complete();
    await stopping;
    expect(await controller.request(), isTrue);
  });

  test('cancel waits for request enqueue before sending its close', () async {
    controller.dispose();
    final enqueued = Completer<void>();
    final acknowledged = Completer<void>();
    var first = true;
    controller = IosVoiceCallCoordinator(
      owner: 'enqueue-order',
      requestCall: () {
        requests++;
        if (first) {
          first = false;
          return enqueued.future;
        }
        return Future<void>.value();
      },
      closeCall: () {
        closes++;
        return acknowledged.future;
      },
      onError: errors.add,
    );
    final requesting = controller.request();
    await Future<void>.delayed(Duration.zero);
    final stopping = controller.stop();
    await Future<void>.delayed(Duration.zero);
    expect(closes, 0);
    expect(calls.last.method, 'end');
    expect(await controller.request(), isFalse);
    enqueued.complete();
    expect(await requesting, isFalse);
    await Future<void>.delayed(Duration.zero);
    expect(closes, 1);
    expect(await controller.request(), isFalse);
    acknowledged.complete();
    await stopping;
    expect(await controller.request(), isTrue);
  });

  test('terminal connection round releases a close whose I/O loop has exited',
      () async {
    controller.dispose();
    final session = IosVoiceCallSession(
      owner: 'terminal-session',
      requestCall: () async => requests++,
      closeCall: () async => closes++,
      onError: errors.add,
    );
    addTearDown(session.dispose);
    expect(session.event(1, 'started'), isTrue);
    expect(await session.request(), isTrue);
    final closing = session.close();
    await Future<void>.delayed(Duration.zero);
    expect(closes, 1);
    var completed = false;
    unawaited(closing.then((_) => completed = true));
    expect(session.event(1, 'ended'), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(completed, isTrue,
        reason: 'An exited loop can never deliver its queued close ack');
    expect(calls.last.method, 'end');
    expect(await session.request(), isFalse);
    expect(session.event(2, 'started'), isTrue);
    expect(await session.request(), isTrue);
  });

  IosVoiceCallSession session({
    Future<void> Function()? request,
    Future<void> Function()? close,
  }) {
    final result = IosVoiceCallSession(
      owner: 'round-session',
      requestCall: request ?? () async => requests++,
      closeCall: close ?? () async => closes++,
      onError: errors.add,
    );
    addTearDown(result.dispose);
    return result;
  }

  test('old round close, ack and terminal cannot release a newer call',
      () async {
    final current = session();
    expect(current.event(8, 'started'), isTrue);
    expect(await current.request(), isTrue);
    expect(current.event(9, 'started'), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(await current.request(), isTrue);
    final owner = calls.last.arguments['owner'];
    final closing = current.close();
    await Future<void>.delayed(Duration.zero);
    for (final state in [
      'ended',
      'closed',
      'close_ack',
      'connected',
      'started'
    ]) {
      expect(current.event(8, state), isFalse);
    }
    expect(await current.request(), isFalse,
        reason: 'A stale ack cannot release the new round close barrier');
    expect(
        calls.where((call) => call.method == 'prepare').last.arguments['owner'],
        owner);
    expect(current.event(9, 'close_ack'), isTrue);
    await closing;
    expect(await current.request(), isTrue);
  });

  test('terminal round drains a delayed request before allowing reconnect',
      () async {
    final enqueued = Completer<void>();
    var first = true;
    final current = session(request: () {
      requests++;
      if (first) {
        first = false;
        return enqueued.future;
      }
      return Future<void>.value();
    });
    current.event(1, 'started');
    final requesting = current.request();
    await Future<void>.delayed(Duration.zero);
    current.event(1, 'ended');
    current.event(2, 'started');
    expect(await current.request(), isFalse);
    expect(calls.last.method, 'end');
    enqueued.complete();
    expect(await requesting, isFalse);
    await Future<void>.delayed(Duration.zero);
    expect(closes, 0,
        reason:
            'No close may be enqueued into the new round for an old request');
    expect(await current.request(), isTrue);
  });

  test('terminal round drains a delayed close FFI before reconnect', () async {
    final enqueued = Completer<void>();
    final current = session(close: () {
      closes++;
      return enqueued.future;
    });
    current.event(1, 'started');
    expect(await current.request(), isTrue);
    final closing = current.close();
    await Future<void>.delayed(Duration.zero);
    current.event(1, 'ended');
    current.event(2, 'started');
    expect(await current.request(), isFalse);
    expect(calls.last.method, 'end');
    enqueued.complete();
    await closing;
    await Future<void>.delayed(Duration.zero);
    expect(await current.request(), isTrue);
  });

  test('terminal during permission cannot start a late request after reconnect',
      () async {
    final permission = Completer<bool>();
    var first = true;
    native = (call) async {
      if (call.method != 'prepare') return null;
      if (first) {
        first = false;
        return permission.future;
      }
      return true;
    };
    final current = session();
    current.event(1, 'started');
    final requesting = current.request();
    await Future<void>.delayed(Duration.zero);
    current.event(1, 'ended');
    current.event(2, 'started');
    await Future<void>.delayed(Duration.zero);
    expect(await current.request(), isTrue);
    final owner = calls.last.arguments['owner'];
    permission.complete(true);
    expect(await requesting, isFalse);
    expect(calls.last.arguments['owner'], isNot(owner));
    expect(requests, 1);
  });

  test(
      'round counter wraps once but duplicate or invalid starts cannot revive it',
      () async {
    final current = session();
    expect(await current.request(), isFalse);
    expect(current.event(0xffffffff, 'started'), isTrue);
    expect(current.event(0xffffffff, 'ended'), isTrue);
    expect(current.event(0xffffffff, 'started'), isFalse);
    expect(current.event(0, 'started'), isTrue);
    expect(current.event(0xffffffff, 'ended'), isFalse);
    expect(current.event(-1, 'started'), isFalse);
    expect(current.event(0x100000000, 'started'), isFalse);
    expect(current.event(0, 'invalid'), isFalse);
    expect(await current.request(), isTrue);
  });

  test('a scoped failed request releases audio and cannot become connected',
      () async {
    final current = session();
    current.event(1, 'started');
    expect(await current.request(), isTrue);
    expect(current.event(0, 'failed', 'old error'), isFalse);
    expect(errors, isEmpty);
    expect(current.event(1, 'failed', '请先启用远程声音再发起语音。'), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(errors.single, contains('启用远程声音'));
    expect(calls.last.method, 'end');
    expect(closes, 0);
    expect(current.event(1, 'connected'), isFalse);
  });

  test('closing the UI locally releases audio even after stream events stop',
      () async {
    final current = session();
    current.event(4, 'started');
    expect(await current.request(), isTrue);
    final closing = current.close();
    await Future<void>.delayed(Duration.zero);
    current.end();
    await closing;
    await Future<void>.delayed(Duration.zero);
    expect(calls.last.method, 'end');
    expect(await current.request(), isFalse);
  });

  test('reused mobile session resets rounds without skipping retired FFI',
      () async {
    final enqueued = Completer<void>();
    final current = session(close: () => enqueued.future);
    current.event(4, 'started');
    expect(await current.request(), isTrue);
    final closing = current.close();
    await Future<void>.delayed(Duration.zero);
    current.end();
    current.reset();
    expect(current.event(1, 'started'), isTrue);
    expect(await current.request(), isFalse);
    enqueued.complete();
    await closing;
    await Future<void>.delayed(Duration.zero);
    expect(await current.request(), isTrue);
  });

  test(
      'failed native policy restoration asks for restart without remote request',
      () async {
    native = (call) async {
      if (call.method == 'prepare') {
        throw PlatformException(code: 'audio_cleanup_failed');
      }
      return null;
    };
    expect(await controller.request(), isFalse);
    expect(requests, 0);
    expect(errors.single, contains('重启应用'));
    expect(calls.last.method, 'end');
  });

  test('simultaneous stops join the same acknowledgement barrier', () async {
    controller.dispose();
    final acknowledged = Completer<void>();
    controller = IosVoiceCallCoordinator(
      owner: 'joining-stops',
      requestCall: () async => requests++,
      closeCall: () {
        closes++;
        return acknowledged.future;
      },
      onError: errors.add,
    );
    expect(await controller.request(), isTrue);
    final first = controller.stop();
    final second = controller.stop(sendClose: false);
    expect(identical(first, second), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(closes, 1);
    expect(calls.last.method, 'end');
    acknowledged.complete();
    await Future.wait([first, second]);
  });

  test('failed close transport releases native state and allows explicit retry',
      () async {
    final current = session(close: () async {
      closes++;
      throw StateError('session no longer exists');
    });
    current.event(1, 'started');
    expect(await current.request(), isTrue);
    await current.close();
    expect(calls.last.method, 'end');
    expect(closes, 1);
    expect(await current.request(), isTrue);
  });
}
