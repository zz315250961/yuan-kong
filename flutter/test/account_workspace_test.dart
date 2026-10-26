import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/widgets/account_workspace.dart';
import 'package:flutter_hbb/common.dart' show MyTheme;
import 'package:flutter_hbb/models/account_login_session.dart';
import 'package:flutter_test/flutter_test.dart';

const sessions = [
  AccountLoginSession(
      id: 'synthetic-session-opaque-01',
      deviceName: '演示电脑',
      platform: 'Windows',
      isCurrent: true),
  AccountLoginSession(
      id: 'synthetic-session-opaque-02',
      deviceName: '',
      platform: '',
      isCurrent: false),
];
void main() {
  for (final dark in [false, true]) {
    testWidgets('current-session label has 4.5 contrast dark=$dark',
        (tester) async {
      final theme = dark ? MyTheme.darkTheme : MyTheme.lightTheme;
      await tester.pumpWidget(MaterialApp(
          theme: theme,
          home: Scaffold(
              body: AccountWorkspace(
                  accountLabel: 'synthetic',
                  sessions: sessions,
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
                  remoteDevices: const SizedBox.shrink()))));
      final label = tester.widget<Text>(find.text('当前会话'));
      final container = tester.widget<Container>(find
          .ancestor(
              of: find.text('当前会话'),
              matching: find.byWidgetPredicate((widget) =>
                  widget is Container &&
                  widget.decoration is BoxDecoration &&
                  (widget.decoration as BoxDecoration).borderRadius ==
                      BorderRadius.circular(6)))
          .first);
      final bg = Color.alphaBlend(
          (container.decoration as BoxDecoration).color!,
          theme.colorScheme.surface);
      final fg = Color.alphaBlend(label.style!.color!, bg);
      final values = [bg.computeLuminance(), fg.computeLuminance()]..sort();
      expect((values.last + .05) / (values.first + .05),
          greaterThanOrEqualTo(4.5));
    });
  }
  Widget workspace(
          {bool loggedIn = true, ValueChanged<AccountLoginSession>? revoke}) =>
      MaterialApp(
          home: Scaffold(
              body: AccountWorkspace(
                  accountLabel: loggedIn ? 'demo@example.test' : '',
                  sessions: sessions,
                  loading: false,
                  error: '',
                  revokingId: null,
                  onLogin: () {},
                  onRegister: () {},
                  onChangeEmail: () {},
                  onResetPassword: () {},
                  onLogout: () {},
                  onRefresh: () {},
                  onSettings: () {},
                  onRevoke: revoke ?? (_) {},
                  remoteDevices: const Text('合成登记设备列表'))));
  testWidgets(
      'true login sessions separate from remote records; current cannot revoke',
      (tester) async {
    AccountLoginSession? target;
    await tester.pumpWidget(workspace(revoke: (value) => target = value));
    expect(find.text('登录设备'), findsOneWidget);
    expect(find.text('当前会话'), findsOneWidget);
    expect(find.text('历史会话（未记录设备名称）'), findsOneWidget);
    expect(find.text('退出登录'), findsOneWidget);
    await tester.tap(find.text('退出登录'));
    expect(target?.id, 'synthetic-session-opaque-02');
    await tester.ensureVisible(find.text('设置'));
    expect(find.text('远控设备'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
  testWidgets('logged out shows login and register, no management actions',
      (tester) async {
    await tester.pumpWidget(workspace(loggedIn: false));
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('注册'), findsOneWidget);
    expect(find.text('退出登录'), findsNothing);
    expect(find.text('更换邮箱'), findsNothing);
  });
  testWidgets('revoke confirmation obscures and clears password after request',
      (tester) async {
    String? submitted;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: LoginSessionRevokeDialog(
                session: sessions.last,
                onSubmit: (value) async {
                  submitted = value;
                  return false;
                }))));
    final field = find.byType(TextField);
    expect(tester.widget<TextField>(field).obscureText, isTrue);
    await tester.enterText(field, 'request-local-secret');
    await tester.tap(find.text('确认退出'));
    await tester.pumpAndSettle();
    expect(submitted, 'request-local-secret');
    expect(tester.widget<TextField>(field).controller?.text, isEmpty);
    expect(find.textContaining('不会断开'), findsOneWidget);
  });
}
