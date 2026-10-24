import 'package:flutter/material.dart';

class RemoteHomeLayout extends StatelessWidget {
  const RemoteHomeLayout(
      {super.key, required this.localPane, this.connectionPane});
  final Widget localPane;
  final Widget? connectionPane;

  @override
  Widget build(BuildContext context) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          localPane,
          if (connectionPane != null) ...[
            const VerticalDivider(width: 1),
            Expanded(child: connectionPane!),
          ],
        ],
      );
}

class LocalIdentityGroup extends StatelessWidget {
  const LocalIdentityGroup(
      {super.key, required this.identity, required this.security});
  final Widget identity;
  final Widget security;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
            color: Theme.of(context).scaffoldBackgroundColor,
            borderRadius: BorderRadius.circular(12)),
        child: Column(children: [
          identity,
          const Divider(indent: 16, endIndent: 16, height: 24),
          security
        ]),
      );
}

/// Presentation only: the native pages retain their controllers and callbacks.
class RemoteConnectionPanel extends StatelessWidget {
  const RemoteConnectionPanel(
      {super.key,
      required this.title,
      required this.devicesLabel,
      required this.onDevices,
      required this.input,
      required this.connectLabel,
      required this.onConnect,
      required this.menu});

  final Widget title;
  final String devicesLabel;
  final VoidCallback onDevices;
  final Widget input;
  final String connectLabel;
  final VoidCallback onConnect;
  final Widget menu;

  @override
  Widget build(BuildContext context) => Container(
        width: 520,
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(13),
            border: Border.all(color: Theme.of(context).dividerColor)),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Align(alignment: Alignment.centerLeft, child: title),
          const SizedBox(height: 16),
          input,
          const SizedBox(height: 16),
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            Expanded(
                child: TextButton.icon(
                    onPressed: onDevices,
                    icon: const Icon(Icons.devices, size: 20),
                    label: Text(devicesLabel),
                    style:
                        TextButton.styleFrom(minimumSize: const Size(48, 48)))),
            const SizedBox(width: 8),
            ConstrainedBox(
                constraints: const BoxConstraints(minHeight: 44),
                child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                        minimumSize: const Size(88, 44)),
                    onPressed: onConnect,
                    child: Text(connectLabel))),
            const SizedBox(width: 8),
            SizedBox(width: 44, height: 44, child: menu),
          ]),
        ]),
      );
}

class ConnectionWorkspace extends StatelessWidget {
  const ConnectionWorkspace(
      {super.key, required this.form, required this.peers, this.status});
  final Widget form;
  final Widget peers;
  final Widget? status;

  @override
  Widget build(BuildContext context) => Column(children: [
        Expanded(
            child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Column(children: [
            Padding(padding: const EdgeInsets.only(top: 22), child: form),
            const SizedBox(height: 16),
            const Divider(height: 1),
            Expanded(child: peers),
          ]),
        )),
        if (status != null) ...[const Divider(height: 1), status!],
      ]);
}

class DeviceDetailsLayout extends StatelessWidget {
  const DeviceDetailsLayout(
      {super.key,
      required this.icon,
      required this.details,
      this.readOnlyLabel,
      required this.connectAction,
      required this.moreAction});
  final Widget icon;
  final Widget details;
  final String? readOnlyLabel;
  final Widget connectAction;
  final Widget moreAction;

  @override
  Widget build(BuildContext context) => Theme(
        data: Theme.of(context).copyWith(visualDensity: VisualDensity.standard),
        child: LayoutBuilder(builder: (context, constraints) {
          final compact = constraints.maxWidth < 320;
          final actions = readOnlyLabel != null
              ? Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(readOnlyLabel!,
                      style: Theme.of(context).textTheme.bodySmall))
              : Row(mainAxisSize: MainAxisSize.min, children: [
                  ConstrainedBox(
                      constraints:
                          const BoxConstraints(minWidth: 48, minHeight: 48),
                      child: connectAction),
                  ConstrainedBox(
                      constraints:
                          const BoxConstraints(minWidth: 48, minHeight: 48),
                      child: moreAction),
                ]);
          return Column(mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              icon,
              const SizedBox(width: 10),
              Expanded(child: details),
              if (!compact) actions
            ]),
            if (compact)
              Align(alignment: Alignment.centerRight, child: actions),
          ]);
        }),
      );
}

class RemoteProtectionNotice extends StatelessWidget {
  const RemoteProtectionNotice(
      {super.key,
      required this.incomingOnly,
      required this.devicesLabel,
      required this.readOnlyLabel,
      required this.onDevices});
  final bool incomingOnly;
  final String devicesLabel;
  final String readOnlyLabel;
  final VoidCallback onDevices;

  @override
  Widget build(BuildContext context) => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.lock_outline, size: 28),
                  const SizedBox(height: 12),
                  const Text('远程配置保护已启用',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 8),
                  Text(
                    incomingOnly
                        ? '当前远程操作无法修改本机配置。'
                        : '当前远程操作无法修改本机配置。您可以只读查看和刷新我的设备；连接、复制 ID 和删除设备操作不可用。',
                    textAlign: TextAlign.center,
                  ),
                  if (!incomingOnly) ...[
                    const SizedBox(height: 16),
                    ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF2F65BA),
                          foregroundColor: Colors.white,
                          minimumSize: const Size(48, 48)),
                      onPressed: onDevices,
                      icon: const Icon(Icons.devices, size: 20),
                      label: Text('$devicesLabel ($readOnlyLabel)'),
                    ),
                  ],
                ]),
              ),
            ),
          ),
        ),
      );
}
