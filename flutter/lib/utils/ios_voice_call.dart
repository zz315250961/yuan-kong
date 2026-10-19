import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

const iosVoiceChannelName = 'linkremote/voice';

/// Coordinates one foreground call, including a permission prompt that may
/// finish after the user leaves. Native owners include a generation so an old
/// completion can never release the audio session belonging to a newer call.
class IosVoiceCallCoordinator with WidgetsBindingObserver {
  IosVoiceCallCoordinator({
    required this.owner,
    required this.requestCall,
    required this.closeCall,
    required this.onError,
  }) {
    if (!_handlerInstalled) {
      _channel.setMethodCallHandler((call) async {
        if (call.method == 'interrupted' && call.arguments is Map) {
          final key = (call.arguments as Map)['owner'];
          final controller = _owners[key];
          if (controller != null && controller._nativeOwner == key) {
            await controller.stop();
            if (!controller._disposed) {
              controller.onError('语音通话已中断，请返回前台后重新发起。');
            }
          }
        }
      });
      _handlerInstalled = true;
    }
    WidgetsBinding.instance.addObserver(this);
  }

  static const _channel = MethodChannel(iosVoiceChannelName);
  static final _owners = <String, IosVoiceCallCoordinator>{};
  static bool _handlerInstalled = false;
  static int _nextNativeOwner = 0;

  final String owner;
  final Future<void> Function() requestCall;
  final Future<void> Function() closeCall;
  final void Function(String) onError;
  int _generation = 0;
  bool _disposed = false;
  bool _requesting = false;
  bool _active = false;
  bool _stopping = false;
  bool _connected = false;
  Timer? _responseTimer;
  String? _nativeOwner;
  Future<void>? _requestCallFuture;

  Future<bool> request() async {
    if (_disposed || _requesting || _active || _stopping) return false;
    _requesting = true;
    final generation = ++_generation;
    // A recreated page/coordinator may keep the same session ID. Its first
    // generation must still differ from a late permission result on the old one.
    final key = '$owner:${++_nextNativeOwner}:$generation';
    _nativeOwner = key;
    _owners[key] = this;
    try {
      final prepared =
          await _channel.invokeMethod<bool>('prepare', {'owner': key});
      if (_disposed || generation != _generation) {
        // The native owner check makes this safe even if a new call is active.
        await _endNative(key);
        return false;
      }
      if (prepared != true) {
        onError('未获得麦克风权限，语音通话未发起。');
        await stop(sendClose: false);
        return false;
      }
      _active = true;
      _connected = false;
      _responseTimer = Timer(const Duration(seconds: 45), () {
        if (!_disposed && generation == _generation && !_connected) {
          onError('对方未响应语音请求，已结束等待。');
          unawaited(stop());
        }
      });
      final requesting = requestCall();
      _requestCallFuture = requesting;
      try {
        await requesting;
      } finally {
        if (identical(_requestCallFuture, requesting)) _requestCallFuture = null;
      }
      return !_disposed && generation == _generation;
    } on PlatformException catch (error) {
      if (!_disposed && generation == _generation) {
        onError(error.code == 'microphone_denied'
            ? '麦克风权限被拒绝，请在系统设置中允许后重试。'
            : '无法启动语音通话，请重试。');
        await stop();
      }
      return false;
    } catch (_) {
      if (!_disposed && generation == _generation) {
        onError('无法启动语音通话，请重试。');
        await stop();
      }
      return false;
    } finally {
      if (generation == _generation) _requesting = false;
      if (_nativeOwner != key) _owners.remove(key);
    }
  }

  void connected() {
    if (_disposed || !_active) return;
    _connected = true;
    _responseTimer?.cancel();
    _responseTimer = null;
  }

  Future<void> _endNative(String key) async {
    try {
      await _channel.invokeMethod<void>('end', {'owner': key});
    } catch (_) {
      // Teardown must not prevent the Rust session from being closed. The
      // native side also ends the audio session on background/interruption.
    }
  }

  Future<void> stop({bool sendClose = true}) async {
    if (_stopping) return;
    _stopping = true;
    ++_generation;
    _responseTimer?.cancel();
    _responseTimer = null;
    _connected = false;
    _requesting = false;
    final active = _active;
    _active = false;
    final key = _nativeOwner;
    _nativeOwner = null;
    if (key != null) _owners.remove(key);
    final requesting = _requestCallFuture;
    final closing = active && sendClose
        ? () async {
            // FFI executes asynchronously: close must follow the original
            // request enqueue, even when the user cancels before it returns.
            if (requesting != null) {
              try {
                await requesting;
              } catch (_) {
                // Still attempt cleanup when the request transport failed.
              }
            }
            try {
              // This future includes the local I/O-loop acknowledgement, not
              // just FFI enqueue. Keep redial fenced until old UI events drain.
              await closeCall();
            } catch (_) {
              // A disconnected remote cannot justify retaining audio access.
            }
          }()
        : null;
    try {
      // Native cleanup must not wait for I/O-loop progress or network writes.
      if (key != null) await _endNative(key);
      if (closing != null) {
        try {
          await closing;
        } catch (_) {
          // Teardown remains safe if a transport implementation throws.
        }
      }
    } finally {
      _stopping = false;
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Inactive includes the microphone permission sheet, not just backgrounding.
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      unawaited(stop());
    }
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    unawaited(stop());
  }
}
