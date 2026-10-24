import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../common.dart';
import '../../models/platform_model.dart';
import '../../utils/account_device_sync.dart';
import 'dialog.dart';
import 'remote_home_layout.dart';

Future<void> showMyDevicesDialog() async {
  await gFFI.dialogManager.show((setState, close, context) {
    return CustomAlertDialog(
      title: Text(translate('My devices')),
      contentBoxConstraints: BoxConstraints(maxWidth: 360, maxHeight: 420),
      content: const MyDevicesDialogContent(),
      onCancel: close,
      actions: [
        dialogButton(translate('Close'), onPressed: close, isOutline: true),
      ],
    );
  }, clickMaskDismiss: true, backDismiss: true);
}

class MyDevicesDialogContent extends StatefulWidget {
  const MyDevicesDialogContent({Key? key}) : super(key: key);

  @override
  State<MyDevicesDialogContent> createState() => _MyDevicesDialogContentState();
}

class _MyDevicesDialogContentState extends State<MyDevicesDialogContent> {
  var _devices = <Map<String, dynamic>>[];
  Object? _error;
  var _loading = true;
  var _myId = '';
  var _myUuid = '';
  int _loadGeneration = 0;
  StreamSubscription? _accountSubscription;
  AccountDeviceSession? _loadedSession;
  var _requestInProgress = false;
  var _openingConnection = false;
  final _removing = <String>{};

  @override
  void initState() {
    super.initState();
    _accountSubscription = gFFI.userModel.userName.listen((_) {
      if (!mounted) return;
      ++_loadGeneration;
      _requestInProgress = false;
      _removing.clear();
      setState(() {
        _devices = [];
        _loadedSession = null;
        _error = null;
      });
      unawaited(_load());
    });
    _init();
  }

  Future<void> _init() async {
    try {
      _myId = (await bind.mainGetMyId()).replaceAll(' ', '');
      _myUuid = await bind.mainGetUuid();
    } catch (e) {
      debugPrint('my devices: get my id failed: $e');
    }
    if (mounted) {
      await _load();
    }
  }

  Future<void> _load() async {
    if (!mounted || _requestInProgress || _removing.isNotEmpty) return;
    if (!gFFI.userModel.isLogin) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = null;
        });
      }
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    _requestInProgress = true;
    final generation = ++_loadGeneration;
    try {
      final account = await gFFI.userModel.accountDeviceSession();
      final devices = await gFFI.userModel.getMyDevices();
      if (!mounted || generation != _loadGeneration) return;
      final current = await _sameSession(account);
      if (!mounted || generation != _loadGeneration) return;
      setState(() {
        _devices = current ? devices : [];
        _loadedSession = current ? account : null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted || generation != _loadGeneration) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    } finally {
      if (generation == _loadGeneration) _requestInProgress = false;
    }
  }

  Widget _deviceIcon(String os) {
    final o = os.toLowerCase();
    if (o.contains('android')) return const Icon(Icons.smartphone);
    if (o.contains('ios') || o.contains('ipad')) {
      return const Icon(Icons.phone_iphone);
    }
    if (o.contains('windows')) return const Icon(Icons.computer);
    if (o.contains('mac')) return const Icon(Icons.laptop_mac);
    if (o.contains('linux')) return const Icon(Icons.desktop_windows);
    return const Icon(Icons.devices);
  }

  bool _isSelf(String id, [String uuid = '']) {
    final normalized = id.replaceAll(' ', '');
    return (uuid.isNotEmpty && uuid == _myUuid) ||
        (_myId.isNotEmpty && normalized == _myId);
  }

  Future<bool> _sameSession(AccountDeviceSession account) async {
    try {
      return account.matches(await gFFI.userModel.accountDeviceSession());
    } catch (_) {
      return false;
    }
  }

  Future<void> _deleteDevice(Map<String, dynamic> device) async {
    final generation = _loadGeneration;
    final account = _loadedSession;
    if (account == null ||
        !await _sameSession(account) ||
        !mounted ||
        generation != _loadGeneration ||
        account.token.isEmpty) {
      return;
    }
    final uuid = (device['uuid'] ?? '').toString();
    final id = (device['id'] ?? '').toString();
    final info = (device['info'] is Map<String, dynamic>)
        ? (device['info'] as Map<String, dynamic>)
        : <String, dynamic>{};
    final name = (info['device_name'] ?? '').toString();
    if (uuid.isEmpty || _removing.contains(uuid)) return;
    CommonConfirmDialog(
      gFFI.dialogManager,
      '${translate('Remove device')}：${name.isEmpty ? id : name}?',
      () async {
        if (!mounted ||
            generation != _loadGeneration ||
            _removing.contains(uuid)) return;
        if (!await _sameSession(account) ||
            !mounted ||
            generation != _loadGeneration) return;
        setState(() => _removing.add(uuid));
        try {
          final removed = await gFFI.userModel
              .deleteMyDevice(uuid, expectedSession: account);
          if (removed && mounted && generation == _loadGeneration) {
            setState(() {
              _devices.removeWhere((d) => (d['uuid'] ?? '').toString() == uuid);
            });
          }
          if (removed && mounted && generation == _loadGeneration) {
            showToast(translate('Removed'));
          }
        } catch (e) {
          debugPrint('my devices: remove failed: $e');
          if (mounted && generation == _loadGeneration) {
            showToast(translate('Failed'));
          }
        } finally {
          if (mounted) {
            setState(() => _removing.remove(uuid));
          }
        }
      },
    );
  }

  Future<void> _connectDevice(String id) async {
    if (!mounted || id.trim().isEmpty || _openingConnection) return;
    final account = _loadedSession;
    if (account == null || !await _sameSession(account) || !mounted) return;
    if (_isSelf(id)) {
      showToast(translate('Cannot connect to self'));
      return;
    }
    _openingConnection = true;
    try {
      await connect(context, id);
    } finally {
      _openingConnection = false;
    }
  }

  @override
  void dispose() {
    _accountSubscription?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (!gFFI.userModel.isLogin) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Text(
          translate('Please login first'),
          textAlign: TextAlign.center,
        ),
      );
    }
    if (_error != null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(translate('Network error')),
            TextButton(
              onPressed: _load,
              child: Text(translate('Retry')),
            ),
          ],
        ),
      );
    }
    if (_devices.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.devices,
                size: 36, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 12),
            Text(translate('No devices yet'), textAlign: TextAlign.center),
            if (gFFI.userModel.deviceSyncError.value.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(gFFI.userModel.deviceSyncError.value,
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall),
              ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              label: Text(translate('Refresh')),
            ),
          ],
        ),
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (gFFI.userModel.deviceSyncError.value.isNotEmpty)
          Padding(
              padding: const EdgeInsets.all(8),
              child: Text(gFFI.userModel.deviceSyncError.value,
                  style: Theme.of(context).textTheme.bodySmall)),
        Align(
          alignment: Alignment.centerRight,
          child: IconButton(
            constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
            visualDensity: VisualDensity.standard,
            icon: const Icon(Icons.refresh, size: 20),
            tooltip: translate('Refresh'),
            onPressed: _removing.isEmpty ? _load : null,
          ),
        ),
        Flexible(
          child: ListView.builder(
            shrinkWrap: true,
            itemCount: _devices.length,
            itemBuilder: (context, i) {
              final d = _devices[i];
              final info = (d['info'] is Map<String, dynamic>)
                  ? (d['info'] as Map<String, dynamic>)
                  : <String, dynamic>{};
              final name = (info['device_name'] ?? '').toString();
              final os = (info['os'] ?? '').toString();
              final id = accountRemoteId((d['id'] ?? '').toString());
              final uuid = (d['uuid'] ?? '').toString();
              final lastLogin =
                  DateTime.tryParse((d['last_login'] ?? '').toString());
              final self = _isSelf(id, uuid);
              final controllerOnly = d['controller_only'] == true ||
                  os.toLowerCase() == 'ios' ||
                  os.toLowerCase() == 'ipados';
              final canConnect =
                  id.isNotEmpty && !controllerOnly && d['can_connect'] != false;
              final removing = _removing.contains(uuid);
              final VoidCallback? onConnect = self || !canConnect || removing
                  ? null
                  : () => _connectDevice(id);
              return Card(
                margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 2),
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  child: DeviceDetailsLayout(
                    icon: _deviceIcon(os),
                    details: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          name.isEmpty ? (id.isEmpty ? '未命名设备' : id) : name,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          controllerOnly
                              ? 'iOS 控制端（不提供被控 ID）'
                              : id.isEmpty
                                  ? '设备 ID 尚未就绪'
                                  : self
                                      ? '$id（${translate('This device')}）'
                                      : id,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                        if (os.isNotEmpty)
                          Text(os,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall),
                        if (lastLogin != null)
                          Text(
                              '最近登录：${lastLogin.toLocal().toString().split('.').first}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall),
                      ],
                    ),
                    connectAction: TextButton.icon(
                      style:
                          TextButton.styleFrom(minimumSize: const Size(48, 48)),
                      onPressed: onConnect,
                      icon: const Icon(Icons.settings_remote, size: 18),
                      label: Text(translate('Connect')),
                    ),
                    moreAction: removing
                        ? const SizedBox(
                            width: 48,
                            height: 48,
                            child: Center(
                                child: SizedBox(
                                    width: 20,
                                    height: 20,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2))),
                          )
                        : PopupMenuButton<String>(
                            tooltip: translate('More'),
                            constraints: const BoxConstraints(minWidth: 160),
                            icon: const Icon(Icons.more_vert),
                            onSelected: (action) async {
                              if (action == 'copy') {
                                final account = _loadedSession;
                                if (account == null ||
                                    !await _sameSession(account) ||
                                    !mounted) return;
                                await Clipboard.setData(
                                    ClipboardData(text: id));
                                showToast(translate('Copied'));
                              } else if (action == 'remove') {
                                _deleteDevice(d);
                              }
                            },
                            itemBuilder: (context) => [
                              PopupMenuItem(
                                  value: 'copy',
                                  enabled: canConnect,
                                  child: Text(translate('Copy to clipboard'))),
                              PopupMenuItem(
                                  value: 'remove',
                                  enabled: uuid.isNotEmpty,
                                  child: Text(translate('Remove device'))),
                            ],
                          ),
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}
