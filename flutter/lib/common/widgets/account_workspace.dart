import 'package:flutter/material.dart';
import '../../models/account_login_session.dart';

String accountSessionTime(DateTime? value) =>
    value == null ? '时间未知' : value.toLocal().toString().split('.').first;

/// The production My workspace. Callbacks perform the real account operations;
/// the view never owns credentials or infers device presence from timestamps.
class AccountWorkspace extends StatelessWidget {
  const AccountWorkspace(
      {super.key,
      required this.accountLabel,
      required this.sessions,
      required this.loading,
      required this.error,
      required this.revokingId,
      required this.onLogin,
      required this.onRegister,
      required this.onChangeEmail,
      required this.onResetPassword,
      required this.onLogout,
      required this.onRefresh,
      required this.onRevoke,
      required this.remoteDevices,
      this.onSettings});
  final String accountLabel;
  final List<AccountLoginSession> sessions;
  final bool loading;
  final String error;
  final String? revokingId;
  final VoidCallback onLogin,
      onRegister,
      onChangeEmail,
      onResetPassword,
      onLogout,
      onRefresh;
  final ValueChanged<AccountLoginSession> onRevoke;
  final VoidCallback? onSettings;
  final Widget remoteDevices;

  Widget _section(BuildContext context, String title, List<Widget> children,
          {Widget? action}) =>
      Padding(
          padding: const EdgeInsets.only(top: 20),
          child: Material(
              color: Theme.of(context).colorScheme.surface,
              borderRadius: BorderRadius.circular(12),
              child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(children: [
                          Expanded(
                              child: Text(title,
                                  style: Theme.of(context)
                                      .textTheme
                                      .titleLarge
                                      ?.copyWith(fontWeight: FontWeight.w600))),
                          if (action != null) action
                        ]),
                        const SizedBox(height: 12),
                        ...children
                      ]))));

  @override
  Widget build(BuildContext context) {
    final signedIn = accountLabel.isNotEmpty;
    final theme = Theme.of(context);
    return ColoredBox(
        color: Color.alphaBlend(theme.colorScheme.onSurface.withOpacity(.035),
            theme.colorScheme.surface),
        child: SingleChildScrollView(
            child: Center(
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1140),
                    child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Text('我的',
                                  style: Theme.of(context)
                                      .textTheme
                                      .headlineSmall
                                      ?.copyWith(fontWeight: FontWeight.w600)),
                              const SizedBox(height: 16),
                              Material(
                                  color: Theme.of(context).colorScheme.surface,
                                  borderRadius: BorderRadius.circular(12),
                                  child: Padding(
                                      padding: const EdgeInsets.all(16),
                                      child: LayoutBuilder(
                                          builder: (context, constraints) {
                                        final summary = Row(children: [
                                          const Icon(
                                              Icons.account_circle_outlined,
                                              size: 28),
                                          const SizedBox(width: 12),
                                          Expanded(
                                              child: SelectableText(
                                                  signedIn
                                                      ? accountLabel
                                                      : '登录后管理你的账号与设备',
                                                  style: Theme.of(context)
                                                      .textTheme
                                                      .titleMedium))
                                        ]);
                                        final actions = Wrap(
                                            spacing: 12,
                                            runSpacing: 8,
                                            children: signedIn
                                                ? [
                                                    OutlinedButton(
                                                        onPressed:
                                                            onChangeEmail,
                                                        child:
                                                            const Text('更换邮箱')),
                                                    OutlinedButton(
                                                        onPressed:
                                                            onResetPassword,
                                                        child:
                                                            const Text('重置密码')),
                                                    TextButton(
                                                        onPressed: onLogout,
                                                        child: const Text(
                                                            '退出本账号')),
                                                  ]
                                                : [
                                                    ElevatedButton(
                                                        onPressed: onLogin,
                                                        child:
                                                            const Text('登录')),
                                                    OutlinedButton(
                                                        onPressed: onRegister,
                                                        child: const Text('注册'))
                                                  ]);
                                        if (constraints.maxWidth >= 760) {
                                          return Column(
                                              crossAxisAlignment:
                                                  CrossAxisAlignment.end,
                                              children: [
                                                Row(children: [
                                                  Expanded(child: summary),
                                                  const SizedBox(width: 20),
                                                  actions
                                                ]),
                                                if (signedIn)
                                                  const Text('重置密码会使全部账号会话失效。',
                                                      style: TextStyle(
                                                          fontSize: 12))
                                              ]);
                                        }
                                        return Column(
                                            crossAxisAlignment:
                                                CrossAxisAlignment.start,
                                            children: [
                                              summary,
                                              const SizedBox(height: 16),
                                              actions,
                                              if (signedIn) ...[
                                                const SizedBox(height: 8),
                                                const Text('重置密码会使全部账号会话失效。',
                                                    style:
                                                        TextStyle(fontSize: 12))
                                              ],
                                            ]);
                                      }))),
                              if (signedIn) ...[
                                _section(
                                    context,
                                    '登录设备',
                                    [
                                      const Text(
                                          '管理账号登录，不会断开已建立的远控连接。退出指定会话需验证当前密码。'),
                                      const SizedBox(height: 12),
                                      if (loading)
                                        const LinearProgressIndicator(),
                                      if (error.isNotEmpty)
                                        Semantics(
                                            liveRegion: true,
                                            child: Padding(
                                                padding:
                                                    const EdgeInsets.symmetric(
                                                        vertical: 12),
                                                child: Text(error,
                                                    style: TextStyle(
                                                        color: Theme.of(context)
                                                            .colorScheme
                                                            .error)))),
                                      if (!loading &&
                                          sessions.isEmpty &&
                                          error.isEmpty)
                                        const Padding(
                                            padding: EdgeInsets.all(16),
                                            child: Text('暂无可显示的登录会话，请刷新重试。')),
                                      for (final session in sessions)
                                        _sessionRow(context, session),
                                    ],
                                    action: IconButton(
                                        tooltip: '刷新登录设备',
                                        onPressed: loading || revokingId != null
                                            ? null
                                            : onRefresh,
                                        icon: const Icon(Icons.refresh))),
                                _section(context, '远控设备', [
                                  const Text(
                                      '登记设备用于连接；移除记录不等于退出账号，活跃客户端可能重新登记。'),
                                  const SizedBox(height: 12),
                                  remoteDevices,
                                ]),
                              ],
                              if (onSettings != null)
                                Padding(
                                    padding: const EdgeInsets.only(top: 24),
                                    child: Material(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .surface,
                                        borderRadius: BorderRadius.circular(12),
                                        child: ListTile(
                                            leading: const Icon(
                                                Icons.settings_outlined),
                                            title: const Text('设置'),
                                            subtitle: const Text('显示、连接与安全选项'),
                                            trailing:
                                                const Icon(Icons.chevron_right),
                                            onTap: onSettings))),
                            ]))))));
  }

  Widget _sessionRow(BuildContext context, AccountLoginSession session) =>
      Column(children: [
        const Divider(),
        Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: LayoutBuilder(builder: (context, constraints) {
              final wide = constraints.maxWidth >= 760;
              final times = Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('最近使用：${accountSessionTime(session.lastSeenAt)}',
                        style: Theme.of(context).textTheme.bodySmall),
                    Text('创建时间：${accountSessionTime(session.createdAt)}',
                        style: Theme.of(context).textTheme.bodySmall)
                  ]);
              return Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                        session.platform.toLowerCase().contains('android') ||
                                session.platform.toLowerCase().contains('ios')
                            ? Icons.smartphone_outlined
                            : Icons.computer_outlined,
                        size: 26),
                    const SizedBox(width: 12),
                    Expanded(
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                          Text(
                              session.deviceName.isEmpty
                                  ? '历史会话'
                                  : session.deviceName,
                              style:
                                  const TextStyle(fontWeight: FontWeight.w600)),
                          Text(session.platform.isEmpty
                              ? '设备信息未知'
                              : session.platform),
                          if (!wide) times
                        ])),
                    if (wide) SizedBox(width: 285, child: times),
                    const SizedBox(width: 12),
                    if (session.isCurrent)
                      Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 6),
                          decoration: BoxDecoration(
                              color: Theme.of(context).brightness ==
                                      Brightness.dark
                                  ? const Color(0xFF12304F)
                                  : const Color(0xFFE8F1FF),
                              borderRadius: BorderRadius.circular(6)),
                          child: Text('当前会话',
                              style: TextStyle(
                                  color: Theme.of(context).brightness ==
                                          Brightness.dark
                                      ? const Color(0xFFB6DBFF)
                                      : const Color(0xFF1556A0))))
                    else
                      OutlinedButton(
                          onPressed: revokingId == null && !loading
                              ? () => onRevoke(session)
                              : null,
                          child:
                              Text(revokingId == session.id ? '退出中…' : '退出登录')),
                  ]);
            }))
      ]);
}

class LoginSessionRevokeDialog extends StatefulWidget {
  const LoginSessionRevokeDialog(
      {super.key,
      required this.session,
      required this.onSubmit,
      this.errorMessage});
  final AccountLoginSession session;
  final Future<bool> Function(String) onSubmit;
  final String Function()? errorMessage;
  @override
  State<LoginSessionRevokeDialog> createState() =>
      _LoginSessionRevokeDialogState();
}

class _LoginSessionRevokeDialogState extends State<LoginSessionRevokeDialog> {
  final _password = TextEditingController();
  bool _busy = false;
  String _error = '';
  @override
  void dispose() {
    _password.clear();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy || widget.session.isCurrent || _password.text.isEmpty) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    var password = _password.text;
    _password.clear();
    try {
      final result = await widget.onSubmit(password);
      if (!mounted) return;
      if (result) {
        Navigator.of(context).pop(true);
      } else {
        setState(() =>
            _error = widget.errorMessage?.call() ?? '未能退出该会话，请确认账号和密码后重试。');
      }
    } catch (_) {
      if (mounted) setState(() => _error = '操作失败，请稍后重试。');
    } finally {
      password = '';
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !_busy,
      child: AlertDialog(
          title: const Text('退出这台设备的账号登录？'),
          content: SizedBox(
              width: 420,
              child: SingleChildScrollView(
                  child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                    Text(widget.session.deviceName.isEmpty
                        ? '历史会话（设备信息未知）'
                        : widget.session.deviceName),
                    Text(
                        '平台：${widget.session.platform.isEmpty ? '未知' : widget.session.platform}'),
                    Text(
                        '创建时间：${accountSessionTime(widget.session.createdAt)}'),
                    const SizedBox(height: 12),
                    const Text('仅退出所选账号会话，不会断开已建立的远控连接。'),
                    const SizedBox(height: 16),
                    TextField(
                        controller: _password,
                        obscureText: true,
                        enableSuggestions: false,
                        autocorrect: false,
                        enabled: !_busy && !widget.session.isCurrent,
                        decoration: const InputDecoration(labelText: '当前密码'),
                        onSubmitted: (_) => _submit()),
                    if (_error.isNotEmpty)
                      Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Text(_error,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error))),
                  ]))),
          actions: [
            TextButton(
                onPressed: _busy ? null : () => Navigator.of(context).pop(),
                child: const Text('取消')),
            FilledButton(
                onPressed: _busy || widget.session.isCurrent ? null : _submit,
                child: Text(_busy ? '验证中…' : '确认退出'))
          ]));
}
