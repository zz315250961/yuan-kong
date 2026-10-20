import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

const iosVoiceChannelName = 'linkremote/voice';

/// Owns voice state for one remote-session connection round. A terminal round
/// cannot acknowledge a close or release the microphone of a later reconnect.
class IosVoiceCallSession {
  IosVoiceCallSession({
    required this.owner,
    required this.requestCall,
    required this.closeCall,
    required this.onError,
  });

  final String owner;
  final Future<void> Function() requestCall;
  final Future<void> Function() closeCall;
  final void Function(String) onError;
  IosVoiceCallCoordinator? _call;
  Completer<void>? _closeAck;
  Future<void>? _retiring;
  int? _round;
  bool _ended = true;
  bool _disposed = false;

  bool event(int round, String state, [String reason = '']) {
    if (_disposed || round < 0 || round > 0xffffffff) return false;
    if (state == 'started') {
      final previous = _round;
      // ConnectionRoundState is a u32 counter. Compare serial numbers rather
      // than accepting an old loop that happens to start or finish late.
      if (previous != null) {
        final distance = (round - previous) & 0xffffffff;
        if (distance == 0 || distance >= 0x80000000) return false;
      }
      _ended = true;
      _retire();
      _round = round;
      _ended = false;
      return true;
    }
    if (round != _round || _ended) return false;
    if (state == 'ended') {
      _ended = true;
      _retire();
    } else if (state == 'close_ack') {
      _acknowledgeClose();
    } else if (state == 'connected') {
      if (_call?.connected() != true) return false;
    } else if (state == 'closed' || state == 'failed') {
      unawaited(_call?.stop(sendClose: false) ?? Future<void>.value());
      if (state == 'failed' && reason.isNotEmpty) {
        onError(reason.startsWith('iOS audio cleanup failed;')
            ? '音频清理失败，请重启应用后再发起语音。'
            : reason);
      }
    } else if (state != 'waiting') {
      return false;
    }
    return true;
  }

  Future<bool> request() async {
    if (_disposed || _ended || _retiring != null) return false;
    final round = _round!;
    _call ??= IosVoiceCallCoordinator(
      owner: owner,
      requestCall: requestCall,
      closeCall: () => _closeRound(round),
      onError: onError,
    );
    return _call!.request();
  }

  Future<void> close() => _call?.stop() ?? Future<void>.value();

  Future<void> _closeRound(int round) async {
    if (_disposed || _ended || round != _round) return;
    final pending = Completer<void>();
    _closeAck = pending;
    try {
      await closeCall();
      if (!_disposed && !_ended && round == _round) await pending.future;
    } finally {
      if (identical(_closeAck, pending)) _closeAck = null;
    }
  }

  void _acknowledgeClose() {
    final pending = _closeAck;
    _closeAck = null;
    if (pending != null && !pending.isCompleted) pending.complete();
  }

  void _retire() {
    // A completed/aborted connection round cannot deliver another local ack.
    // Release only its barrier; the coordinator still drains old FFI futures
    // before a later round may issue any new native request.
    _acknowledgeClose();
    final call = _call;
    _call = null;
    if (call == null) return;
    final previous = _retiring;
    final draining = call.disposeAsync(sendClose: false);
    final retired = Future.wait<void>([
      if (previous != null) previous,
      draining,
    ]).then<void>((_) {});
    _retiring = retired;
    unawaited(retired.whenComplete(() {
      if (identical(_retiring, retired)) _retiring = null;
    }));
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _ended = true;
    _retire();
  }

  void end() {
    if (_disposed) return;
    _ended = true;
    _retire();
  }

  void reset() {
    end();
    // A newly added Rust Session starts its connection counter at one again.
    // Its UI stream is generation-fenced by FFI.start; old FFI still drains.
    _round = null;
  }
}

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
  Future<void>? _stopFuture;

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
        if (identical(_requestCallFuture, requesting)) {
          _requestCallFuture = null;
        }
      }
      return !_disposed && generation == _generation;
    } on PlatformException catch (error) {
      if (!_disposed && generation == _generation) {
        onError(error.code == 'microphone_denied'
            ? '麦克风权限被拒绝，请在系统设置中允许后重试。'
            : error.code == 'audio_cleanup_failed'
                ? '音频清理失败，请重启应用后再发起语音。'
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

  bool connected() {
    if (_disposed || !_active) return false;
    _connected = true;
    _responseTimer?.cancel();
    _responseTimer = null;
    return true;
  }

  Future<void> _endNative(String key) async {
    try {
      await _channel.invokeMethod<void>('end', {'owner': key});
    } catch (_) {
      // Teardown must not prevent the Rust session from being closed. The
      // native side also ends the audio session on background/interruption.
    }
  }

  Future<void> stop({bool sendClose = true}) {
    final stopping = _stopFuture;
    if (stopping != null) return stopping;
    late final Future<void> operation;
    operation = _stop(sendClose: sendClose).whenComplete(() {
      if (identical(_stopFuture, operation)) _stopFuture = null;
    });
    _stopFuture = operation;
    return operation;
  }

  Future<void> _stop({required bool sendClose}) async {
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
      } else if (requesting != null) {
        try {
          // A terminal loop no longer needs a protocol close, but its queued
          // FFI request must finish before a new round may arm a microphone.
          await requesting;
        } catch (_) {
          // No old request remains queued once this future has returned.
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
    unawaited(disposeAsync());
  }

  Future<void> disposeAsync({bool sendClose = true}) {
    if (_disposed) return stop(sendClose: sendClose);
    _disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    return stop(sendClose: sendClose);
  }
}
