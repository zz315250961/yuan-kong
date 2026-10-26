import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/widgets/account_workspace.dart';
import 'package:flutter_hbb/models/account_login_session.dart';
import 'package:flutter_test/flutter_test.dart';

final manySessions = List.generate(
    8,
    (i) => AccountLoginSession(
        id: 'synthetic-opaque-session-0$i',
        deviceName: '设备 $i',
        platform: 'windows',
        isCurrent: i == 7));

Widget workspace(
        {List<AccountLoginSession>? sessions,
        String account = 'demo',
        ValueChanged<AccountLoginSession>? revoke}) =>
    MaterialApp(
        home: Scaffold(
            body: AccountWorkspace(
                accountLabel: account,
                sessions: sessions ?? manySessions,
                loading: false,
                error: '',
                revokingId: null,
                onLogin: () {},
                onRegister: () {},
                onChangeEmail: () {},
                onResetPassword: () {},
                onLogout: () {},
                onRefresh: () {},
                onRevoke: revoke ?? (_) {},
                remoteDevices: const Text('既有远控记录'))));

void main() {
  testWidgets('eight sessions initially show five with current session first',
      (tester) async {
    await tester.pumpWidget(workspace());
    expect(find.text('共8条，显示5条'), findsOneWidget);
    expect(find.text('当前会话'), findsOneWidget);
    expect(find.text('设备 7'), findsOneWidget);
    expect(find.text('设备 6'), findsNothing);
    expect(find.text('退出登录'), findsNWidgets(4));
  });

  testWidgets('expand and collapse retain exact row actions and actual counts',
      (tester) async {
    AccountLoginSession? target;
    await tester.pumpWidget(workspace(revoke: (v) => target = v));
    await tester.ensureVisible(find.text('展开全部（8条）'));
    await tester.tap(find.text('展开全部（8条）'));
    await tester.pumpAndSettle();
    expect(find.text('共8条，显示8条'), findsOneWidget);
    expect(find.text('设备 6'), findsOneWidget);
    final action = find.descendant(
        of: find
            .ancestor(of: find.text('设备 6'), matching: find.byType(Row))
            .first,
        matching: find.text('退出登录'));
    await tester.ensureVisible(action);
    await tester.tap(action);
    expect(target?.id, manySessions[6].id);
    await tester.ensureVisible(find.text('收起'));
    await tester.tap(find.text('收起'));
    await tester.pumpAndSettle();
    expect(find.text('共8条，显示5条'), findsOneWidget);
    expect(find.text('设备 6'), findsNothing);
  });

  testWidgets(
      'unknown legacy names are explicitly identified instead of guessed',
      (tester) async {
    await tester.pumpWidget(workspace(sessions: const [
      AccountLoginSession(
          id: 'synthetic-legacy-session-id',
          deviceName: '',
          platform: '',
          isCurrent: true)
    ]));
    expect(find.text('历史会话（未记录设备名称）'), findsOneWidget);
    expect(find.text('共1条，显示1条'), findsOneWidget);
    expect(find.textContaining('展开全部'), findsNothing);
  });

  for (final dark in [false, true]) {
    testWidgets('long names and counts fit 700x600 text1.3 dark=$dark',
        (tester) async {
      tester.view.physicalSize = const Size(700, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
          theme: dark ? ThemeData.dark() : ThemeData.light(),
          builder: (context, child) => MediaQuery(
              data: MediaQuery.of(context)
                  .copyWith(textScaler: const TextScaler.linear(1.3)),
              child: child!),
          home: Scaffold(
              body: AccountWorkspace(
                  accountLabel: 'synthetic@example.test',
                  sessions: List.generate(
                      8,
                      (i) => AccountLoginSession(
                          id: 'synthetic-very-long-session-$i',
                          deviceName: '长设备名称' * 16,
                          platform: 'Windows',
                          isCurrent: i == 7)),
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
                  remoteDevices: const Text('合成远控设备')))));
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.text('展开全部（8条）'));
      await tester.tap(find.text('展开全部（8条）'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('共8条，显示8条'), findsOneWidget);
    });
  }
}
