import 'dart:async';
import 'dart:ui' show PointerDeviceKind;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common.dart' show buildRemoteBlock;
import 'package:flutter_hbb/common/widgets/remote_home_layout.dart';
import 'package:flutter_hbb/utils/account_device_sync.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:http/http.dart' as http;

void main() {
  test(
      'readonly list refresh fetches peers without identity reads or registration',
      () async {
    var identityReads = 0;
    var registrations = 0;
    var listReads = 0;
    final sync = AccountDeviceSync(
      session: () async =>
          const AccountDeviceSession('https://api.example.test', 'account-A'),
      identity: () async {
        identityReads++;
        return const AccountDeviceIdentity('123', 'install-A', {});
      },
      post: (_, {headers, body}) async {
        registrations++;
        return http.Response('{}', 200);
      },
    );
    for (var refresh = 0; refresh < 2; refresh++) {
      final response = await sync.loadPeers((url, {headers}) async {
        expect(url.path, '/api/peers');
        expect(headers!['Authorization'], 'Bearer account-A');
        listReads++;
        return http.Response('{"data":[{"uuid":"other-device"}]}', 200);
      }, syncDevice: false, registrationError: (_) => fail('No registration'));
      expect(response!.body, '{"data":[{"uuid":"other-device"}]}');
    }
    expect(listReads, 2);
    expect(identityReads, 0);
    expect(registrations, 0);
  });

  testWidgets(
      'explicit safe overlay works while protected clicks and focus stay blocked',
      (tester) async {
    final block = false.obs;
    final nativeCheck = Completer<bool>();
    final protectedFocus = FocusNode();
    addTearDown(protectedFocus.dispose);
    var protectedClicks = 0;
    var safeClicks = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SizedBox(
      width: 600,
      height: 400,
      child: buildRemoteBlock(
        block: block,
        mask: true,
        // Hold the existing native eligibility boundary, then drive Rx state.
        // This fixture does not reproduce native remote-input detection.
        use: () => nativeCheck.future,
        child: Align(
            alignment: Alignment.topLeft,
            child: TextButton(
              focusNode: protectedFocus,
              onPressed: () => protectedClicks++,
              child: const Text('Protected settings'),
            )),
        blockedOverlay: RemoteProtectionNotice(
          incomingOnly: false,
          devicesLabel: 'My devices',
          readOnlyLabel: 'Read-only',
          onDevices: () => safeClicks++,
        ),
      ),
    ))));
    final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: const Offset(590, 390));
    addTearDown(mouse.removePointer);
    block.value = true;
    await tester.pump();
    await tester.tap(find.text('Protected settings'), warnIfMissed: false);
    expect(protectedClicks, 0);
    protectedFocus.requestFocus();
    await tester.pump();
    expect(protectedFocus.hasFocus, isFalse);
    expect(find.text('My devices (Read-only)'), findsOneWidget);
    await mouse.moveTo(tester.getCenter(find.text('My devices (Read-only)')));
    await tester.pump();
    expect(block.value, isTrue,
        reason: 'Moving into the safe overlay must not exit its MouseRegion');
    await tester.tap(find.text('My devices (Read-only)'));
    expect(safeClicks, 1);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pump();
    expect(protectedFocus.hasFocus, isFalse);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(protectedClicks, 0);
    expect(safeClicks, 2,
        reason: 'Only the safe overlay participates in keyboard traversal');
    block.value = false;
    await tester.pump();
    expect(find.text('My devices (Read-only)'), findsNothing);
    await tester.tap(find.text('Protected settings'));
    expect(protectedClicks, 1);
  });

  testWidgets('default remote block grants no exception', (tester) async {
    final block = true.obs;
    var clicks = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SizedBox(
      width: 600,
      height: 400,
      child: buildRemoteBlock(
          block: block,
          mask: true,
          use: () => Completer<bool>().future,
          child: TextButton(
              onPressed: () => clicks++, child: const Text('Settings'))),
    ))));
    await tester.tap(find.text('Settings'), warnIfMissed: false);
    expect(clicks, 0);
    expect(find.byType(RemoteProtectionNotice), findsNothing);
  });

  for (final dark in [false, true]) {
    for (final incomingOnly in [false, true]) {
      testWidgets(
          'protection notice fits 700x600 at 1.3 text dark=$dark incoming=$incomingOnly',
          (tester) async {
        tester.view.physicalSize = const Size(700, 600);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        var clicks = 0;
        await tester.pumpWidget(MaterialApp(
          theme: dark ? ThemeData.dark() : ThemeData.light(),
          home: MediaQuery(
              data: const MediaQueryData(textScaler: TextScaler.linear(1.3)),
              child: Scaffold(
                  body: RemoteProtectionNotice(
                incomingOnly: incomingOnly,
                devicesLabel: 'My devices',
                readOnlyLabel: 'Read-only',
                onDevices: () => clicks++,
              ))),
        ));
        expect(tester.takeException(), isNull);
        expect(find.textContaining('远程配置保护已启用'), findsOneWidget);
        expect(find.text('My devices (Read-only)'),
            incomingOnly ? findsNothing : findsOneWidget);
        if (!incomingOnly) {
          await tester.tap(find.text('My devices (Read-only)'));
          expect(clicks, 1);
        } else {
          expect(find.byType(TextButton), findsNothing);
          expect(clicks, 0);
        }
      });
    }
  }

  for (final readOnly in [true, false]) {
    for (final width in [280.0, 360.0]) {
      testWidgets('device actions readonly=$readOnly width=$width',
          (tester) async {
        var connects = 0;
        var copies = 0;
        var removes = 0;
        await tester.pumpWidget(MaterialApp(
            home: Scaffold(
                body: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(
              width: width,
              child: MediaQuery(
                data: const MediaQueryData(textScaler: TextScaler.linear(1.3)),
                child: DeviceDetailsLayout(
                  icon: const Icon(Icons.computer),
                  details: const Text(
                      'Synthetic workstation with a long device name'),
                  readOnlyLabel: readOnly ? 'Read-only' : null,
                  connectAction: TextButton(
                      onPressed: () => connects++,
                      child: const Text('Connect')),
                  moreAction: PopupMenuButton<String>(
                    onSelected: (value) {
                      if (value == 'copy') {
                        copies++;
                      } else {
                        removes++;
                      }
                    },
                    itemBuilder: (_) => const [
                      PopupMenuItem(value: 'copy', child: Text('Copy ID')),
                      PopupMenuItem(
                          value: 'remove', child: Text('Remove device')),
                    ],
                  ),
                ),
              )),
        ))));
        expect(tester.takeException(), isNull);
        if (readOnly) {
          expect(find.text('Connect'), findsNothing);
          expect(find.byType(PopupMenuButton<String>), findsNothing);
          expect(find.text('Read-only'), findsOneWidget);
          await tester.tap(find.text('Read-only'));
          await tester.sendKeyEvent(LogicalKeyboardKey.tab);
          await tester.sendKeyEvent(LogicalKeyboardKey.enter);
          expect([connects, copies, removes], [0, 0, 0]);
        } else {
          await tester.tap(find.text('Connect'));
          await tester.tap(find.byType(PopupMenuButton<String>));
          await tester.pumpAndSettle();
          await tester.tap(find.text('Copy ID'));
          await tester.pumpAndSettle();
          await tester.tap(find.byType(PopupMenuButton<String>));
          await tester.pumpAndSettle();
          await tester.tap(find.text('Remove device'));
          await tester.pumpAndSettle();
          expect([connects, copies, removes], [1, 1, 1]);
        }
      });
    }
  }
}
