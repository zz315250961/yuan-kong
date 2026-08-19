import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../common.dart';
import '../../models/platform_model.dart';
import 'dialog.dart';

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

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      _myId = (await bind.mainGetMyId()).replaceAll(' ', '');
    } catch (e) {
      debugPrint('my devices: get my id failed: $e');
    }
    if (mounted) {
      await _load();
    }
  }

  Future<void> _load() async {
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
    try {
      final devices = await gFFI.userModel.getMyDevices();
      if (!mounted) return;
      setState(() {
        _devices = devices;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Widget _deviceIcon(String os) {
    final o = os.toLowerCase();
    if (o.contains('android')) return const Icon(Icons.smartphone);
    if (o.contains('windows')) return const Icon(Icons.computer);
    if (o.contains('mac')) return const Icon(Icons.laptop_mac);
    if (o.contains('linux')) return const Icon(Icons.desktop_windows);
    return const Icon(Icons.devices);
  }

  bool _isSelf(String id) {
    final normalized = id.replaceAll(' ', '');
    return _myId.isNotEmpty && normalized == _myId;
  }

  Future<void> _deleteDevice(Map<String, dynamic> device) async {
    final uuid = (device['uuid'] ?? '').toString();
    final id = (device['id'] ?? '').toString();
    final info = (device['info'] is Map<String, dynamic>)
        ? (device['info'] as Map<String, dynamic>)
        : <String, dynamic>{};
    final name = (info['device_name'] ?? '').toString();
    if (uuid.isEmpty) {
      showToast(translate('Cannot connect to self'));
      return;
    }
    CommonConfirmDialog(
      gFFI.dialogManager,
      '${translate('Remove device')}：${name.isEmpty ? id : name}?',
      () async {
        try {
          await gFFI.userModel.deleteMyDevice(uuid);
          if (mounted) {
            setState(() {
              _devices.removeWhere((d) => (d['uuid'] ?? '').toString() == uuid);
            });
          }
          showToast(translate('Removed'));
        } catch (e) {
          showToast(translate('Failed'));
        }
      },
    );
  }

  Future<void> _connectDevice(String id) async {
    if (_isSelf(id)) {
      showToast(translate('Cannot connect to self'));
      return;
    }
    connect(context, id);
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
        child: Text(
          translate('No devices yet'),
          textAlign: TextAlign.center,
        ),
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Align(
          alignment: Alignment.centerRight,
          child: IconButton(
            icon: const Icon(Icons.refresh, size: 20),
            tooltip: translate('Refresh'),
            onPressed: _load,
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
              final id = (d['id'] ?? '').toString();
              final self = _isSelf(id);
              return Card(
                margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 2),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  child: Row(
                    children: [
                      _deviceIcon(os),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              name.isEmpty ? id : name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              self ? '${id}（${translate('This device')}）' : id,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 12,
                                color: Theme.of(context).hintColor,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(Icons.settings_remote, size: 18),
                        tooltip: translate('Connect'),
                        onPressed: self ? null : () => _connectDevice(id),
                      ),
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(Icons.copy, size: 18),
                        tooltip: translate('Copy to clipboard'),
                        onPressed: () async {
                          await Clipboard.setData(ClipboardData(text: id));
                          showToast(translate('Copied'));
                        },
                      ),
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(Icons.delete_outline, size: 18),
                        tooltip: translate('Remove'),
                        onPressed: () => _deleteDevice(d),
                      ),
                    ],
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
