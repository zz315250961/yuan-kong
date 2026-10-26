import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common.dart' show MyTheme;
import 'package:flutter_hbb/common/widgets/account_workspace.dart';
import 'package:flutter_hbb/common/widgets/desktop_movable_dialog.dart';
import 'package:flutter_hbb/common/widgets/remote_home_layout.dart';
import 'package:flutter_hbb/models/account_login_session.dart';
import 'package:flutter_test/flutter_test.dart';

const captureKey = ValueKey('account-workspace-capture');
final captureDir = Platform.environment['LINKREMOTE_ACCOUNT_CAPTURE_DIR'];
final captureFont = Platform.environment['LINKREMOTE_ACCOUNT_CAPTURE_FONT'];
List<AccountLoginSession> sampleSessions() => [
      for (var i = 0; i < 3; i++)
        AccountLoginSession(
            id: 'synthetic-session-opaque-0$i',
            deviceName: ['演示电脑', '演示手机', ''][i],
            platform: ['Windows', 'Android', ''][i],
            isCurrent: i == 0,
            createdAt: DateTime.utc(2026, 10, 24, 1),
            lastSeenAt: i == 2 ? null : DateTime.utc(2026, 10, 25, 6, 30)),
    ];
Widget sampleDevices() => Column(mainAxisSize: MainAxisSize.min, children: [
      for (var i = 0; i < 2; i++)
        Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: DeviceDetailsLayout(
                icon: Icon(i == 0 ? Icons.computer : Icons.smartphone),
                details: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(i == 0 ? '演示电脑（本机）' : '演示手机',
                          style: const TextStyle(fontWeight: FontWeight.w600)),
                      Text(i == 0 ? '123 456 789' : '987 654 321'),
                      const Text('最近同步：10月25日 14:28',
                          style: TextStyle(fontSize: 12)),
                    ]),
                connectAction: TextButton(
                    onPressed: i == 0 ? null : () {}, child: const Text('连接')),
                moreAction: IconButton(
                    onPressed: () {}, icon: const Icon(Icons.more_vert)))),
    ]);
Future<void> capture(WidgetTester tester, String name) async {
  if (captureDir == null) return;
  await tester.runAsync(() async {
    final boundary =
        tester.renderObject<RenderRepaintBoundary>(find.byKey(captureKey));
    final bitmap = await boundary.toImage();
    try {
      final bytes = await bitmap.toByteData(format: ui.ImageByteFormat.png);
      final file = File('$captureDir/$name.png');
      if (file.existsSync()) throw StateError('Preserve previous capture');
      file.parent.createSync(recursive: true);
      file.writeAsBytesSync(bytes!.buffer.asUint8List());
    } finally {
      bitmap.dispose();
    }
  });
}

void main() {
  setUpAll(() async {
    if (captureFont != null) {
      final loader = FontLoader('AccountQa');
      loader.addFont(Future.value(
          ByteData.sublistView(File(captureFont!).readAsBytesSync())));
      await loader.load();
      final icons = FontLoader('MaterialIcons');
      icons.addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
      await icons.load();
    }
  });
  for (final dark in [false, true]) {
    for (final size in [
      const Size(700, 600),
      const Size(1000, 700),
      const Size(1280, 800)
    ]) {
      testWidgets(
          'workspace fits $size dark=$dark and settings remains reachable',
          (tester) async {
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = size;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final theme = dark ? MyTheme.darkTheme : MyTheme.lightTheme;
        await tester.pumpWidget(MaterialApp(
            theme: captureFont == null
                ? theme
                : theme.copyWith(
                    textTheme: theme.textTheme.apply(fontFamily: 'AccountQa')),
            builder: (context, child) => MediaQuery(
                data: MediaQuery.of(context).copyWith(
                    textScaler: TextScaler.linear(size.width == 700 ? 1.3 : 1)),
                child: child!),
            home: RepaintBoundary(
                key: captureKey,
                child: Scaffold(
                    body: AccountWorkspace(
                        accountLabel: 'demo***@example.test · 演示数据',
                        sessions: sampleSessions(),
                        loading: false,
                        error: '',
                        revokingId: null,
                        onLogin: () {},
                        onRegister: () {},
                        onChangeEmail: () {},
                        onResetPassword: () {},
                        onLogout: () {},
                        onRefresh: () {},
                        onRevoke: (_) {},
                        onSettings: () {},
                        remoteDevices: sampleDevices())))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await capture(
            tester, 'my-${size.width.toInt()}-${dark ? 'dark' : 'light'}');
        await tester.ensureVisible(find.text('设置'));
        expect(tester.getRect(find.text('设置')).bottom, lessThan(size.height));
        expect(tester.takeException(), isNull);
      });
    }
    testWidgets('movable device dialog fits 700x600 text1.3 dark=$dark',
        (tester) async {
      debugDisableShadows = false;
      tester.view.devicePixelRatio = 1;
      tester.view.physicalSize = const Size(700, 600);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final theme = dark ? MyTheme.darkTheme : MyTheme.lightTheme;
      await tester.pumpWidget(MaterialApp(
          theme: captureFont == null
              ? theme
              : theme.copyWith(
                  textTheme: theme.textTheme.apply(fontFamily: 'AccountQa')),
          builder: (context, child) => MediaQuery(
              data: MediaQuery.of(context)
                  .copyWith(textScaler: const TextScaler.linear(1.3)),
              child: child!),
          home: RepaintBoundary(
              key: captureKey,
              child: Scaffold(
                  body: DesktopMovableDialog(
                      title: const Text('我的设备 · 演示数据'),
                      content: SingleChildScrollView(child: sampleDevices()),
                      onCancel: () {},
                      actions: [
                    OutlinedButton(onPressed: () {}, child: const Text('关闭'))
                  ])))));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await capture(tester, 'dialog-700-${dark ? 'dark' : 'light'}');
      debugDisableShadows = true;
    });
  }
}
