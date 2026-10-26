import 'dart:async';
import 'package:flutter/material.dart';
import '../../common.dart';
import '../../common/widgets/account_workspace.dart';
import '../../common/widgets/login.dart';
import '../../common/widgets/my_devices.dart';
import '../../models/account_login_session.dart';
import '../../models/platform_model.dart';
import '../../utils/account_session_controller.dart';
import 'desktop_tab_page.dart';

class DesktopMyPage extends StatefulWidget {
  const DesktopMyPage({super.key});
  @override
  State<DesktopMyPage> createState() => _DesktopMyPageState();
}

class _DesktopMyPageState extends State<DesktopMyPage> {
  late final AccountSessionController _sessions;
  StreamSubscription? _account;
  @override
  void initState() {
    super.initState();
    _sessions = AccountSessionController(gFFI.userModel.accountSessions);
    _sessions.addListener(_changed);
    _account = gFFI.userModel.userName.listen((_) {
      _sessions.clear();
      if (gFFI.userModel.isLogin) unawaited(_sessions.load());
    });
    if (gFFI.userModel.isLogin) unawaited(_sessions.load());
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _account?.cancel();
    _sessions.removeListener(_changed);
    _sessions.dispose();
    super.dispose();
  }

  Future<bool> _confirm(String title, String explanation) async =>
      await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                  title: Text(title),
                  content: Text(explanation),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.of(context).pop(false),
                        child: const Text('取消')),
                    FilledButton(
                        onPressed: () => Navigator.of(context).pop(true),
                        child: const Text('继续'))
                  ])) ==
      true;
  Future<void> _revoke(AccountLoginSession session) async {
    if (session.isCurrent || _sessions.revokingId != null) return;
    final revision = _sessions.revision;
    await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => LoginSessionRevokeDialog(
            session: session,
            onSubmit: (password) async =>
                mounted && revision == _sessions.revision
                    ? await _sessions.revoke(session.id, password)
                    : false,
            errorMessage: () => _sessions.error.isEmpty
                ? '账号或列表已变化，请关闭后重新选择目标。'
                : _sessions.error));
  }

  Future<void> _login(bool register) async {
    final ok = await (register ? registerDialog() : loginDialog());
    if (ok == true) gFFI.userModel.refreshCurrentUser();
  }

  @override
  Widget build(BuildContext context) => AccountWorkspace(
        accountLabel: bind.isDisableAccount()
            ? ''
            : gFFI.userModel.accountLabelWithHandle,
        sessions: _sessions.sessions,
        loading: _sessions.loading,
        error: _sessions.error,
        revokingId: _sessions.revokingId,
        onLogin: () {
          if (!bind.isDisableAccount()) unawaited(_login(false));
        },
        onRegister: () {
          if (!bind.isDisableAccount()) unawaited(_login(true));
        },
        onChangeEmail: () => changeEmailDialog(),
        onResetPassword: () async {
          if (await _confirm(
                  '重置密码会使全部账号会话失效', '所有设备将需要重新登录账号。此操作不是无影响修改；确定继续吗？') &&
              mounted) {
            await forgotPasswordDialog();
          }
        },
        onLogout: () async {
          final expected = await gFFI.userModel.accountDeviceSession();
          if (await _confirm('退出本账号？', '本机的账号同步将停止，不会主动断开已建立的远控连接。') &&
              mounted &&
              expected.matches(await gFFI.userModel.accountDeviceSession())) {
            await gFFI.userModel.logOut();
          }
        },
        onRefresh: _sessions.load,
        onRevoke: _revoke,
        remoteDevices: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                      icon: const Icon(Icons.open_in_new),
                      label: const Text('打开可移动列表'),
                      onPressed: () => showMyDevicesDialog(
                          readOnly: bind.isIncomingOnly()))),
              MyDevicesDialogContent(
                  key: ValueKey(gFFI.userModel.userName.value),
                  embedded: true,
                  readOnly: bind.isIncomingOnly())
            ]),
        onSettings:
            bind.isDisableSettings() ? null : DesktopTabPage.onAddSetting,
      );
}
