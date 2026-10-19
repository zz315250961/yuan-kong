import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/widgets/gestures.dart';
import 'package:flutter_hbb/common/widgets/remote_input.dart';
import 'package:flutter_hbb/models/input_model.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_test/flutter_test.dart';

class _Input extends Fake implements InputModel {
  final clicks = <MouseButtons>[];
  final downs = <MouseButtons>[];
  final ups = <MouseButtons>[];

  @override
  bool shouldIgnoreTouchTap(Offset position) => false;

  @override
  Future<void> tap(MouseButtons button) async => clicks.add(button);

  @override
  Future<void> tapDown(MouseButtons button) async => downs.add(button);

  @override
  Future<void> tapUp(MouseButtons button) async => ups.add(button);

  @override
  Future<void> sendMouse(String type, MouseButtons button) async {}
}

class _Cursor extends Fake implements CursorModel {
  final moves = <Offset>[];
  bool blocked = false;

  @override
  bool get lastIsBlocked => blocked;

  @override
  bool shouldBlock(double x, double y) => blocked;

  @override
  bool isInRemoteRect(Offset point) =>
      const Rect.fromLTWH(0, 0, 400, 400).contains(point);

  @override
  Future<bool> move(double x, double y) async {
    if (blocked || !isInRemoteRect(Offset(x, y))) return false;
    moves.add(Offset(x, y));
    return true;
  }
}

class _Session extends Fake implements FfiModel {
  @override
  bool get touchMode => true;

  @override
  bool get isPeerMobile => false;

  @override
  bool get isPeerAndroid => false;
}

class _Ffi extends Fake implements FFI {
  @override
  final _Input inputModel = _Input();
  @override
  final _Cursor cursorModel = _Cursor();
  @override
  final _Session ffiModel = _Session();
}

Future<void> _twoFingerTap(WidgetTester tester, {int pointer = 1}) async {
  final first = await tester.startGesture(const Offset(120, 130),
      pointer: pointer, kind: PointerDeviceKind.touch);
  await tester.pump(const Duration(milliseconds: 20));
  final second = await tester.startGesture(const Offset(160, 130),
      pointer: pointer + 1, kind: PointerDeviceKind.touch);
  await tester.pump(const Duration(milliseconds: 20));
  await first.up();
  await second.up();
}

Future<void> _pumpRecognizer(
    WidgetTester tester, ValueChanged<TapDownDetails> onRightClick,
    {VoidCallback? onScale}) async {
  await tester.pumpWidget(Directionality(
    textDirection: TextDirection.ltr,
    child: Align(
      alignment: Alignment.topLeft,
      child: SizedBox(
        width: 400,
        height: 400,
        child: RawGestureDetector(
          behavior: HitTestBehavior.opaque,
          gestures: {
            TapGestureRecognizer:
                GestureRecognizerFactoryWithHandlers<TapGestureRecognizer>(
                    TapGestureRecognizer.new, (instance) {
              instance.onTap = () {};
            }),
            DoubleTapGestureRecognizer: GestureRecognizerFactoryWithHandlers<
                    DoubleTapGestureRecognizer>(DoubleTapGestureRecognizer.new,
                (instance) {
              instance.onDoubleTap = () {};
            }),
            DoubleFinerTapGestureRecognizer:
                GestureRecognizerFactoryWithHandlers<
                        DoubleFinerTapGestureRecognizer>(
                    DoubleFinerTapGestureRecognizer.new, (instance) {
              instance.onDoubleFinerTap = onRightClick;
            }),
            CustomTouchGestureRecognizer: GestureRecognizerFactoryWithHandlers<
                    CustomTouchGestureRecognizer>(
                CustomTouchGestureRecognizer.new, (instance) {
              instance.onTwoFingerScaleUpdate = (_) => onScale?.call();
            }),
          },
          child: const SizedBox.expand(),
        ),
      ),
    ),
  ));
}

void main() {
  testWidgets('two-finger tap keeps the first finger target', (tester) async {
    final taps = <TapDownDetails>[];
    await _pumpRecognizer(tester, taps.add);
    await _twoFingerTap(tester);
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(taps, hasLength(1));
    expect(taps.single.localPosition, const Offset(120, 130));
    expect(taps.single.globalPosition, const Offset(120, 130));
  });

  testWidgets('two successive two-finger taps are both recognized',
      (tester) async {
    final taps = <TapDownDetails>[];
    await _pumpRecognizer(tester, taps.add);
    await _twoFingerTap(tester);
    await tester.pump(const Duration(milliseconds: 60));
    await _twoFingerTap(tester, pointer: 3);
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(taps, hasLength(2));
  });

  testWidgets('touch-mode right click first moves to the touched target',
      (tester) async {
    final ffi = _Ffi();
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Align(
        alignment: Alignment.topLeft,
        child: SizedBox(
          width: 400,
          height: 400,
          child: RawTouchGestureDetectorRegion(
              ffi: ffi,
              child: const ColoredBox(
                  color: Colors.white, child: SizedBox.expand())),
        ),
      ),
    ));
    await _twoFingerTap(tester);
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(ffi.inputModel.clicks, [MouseButtons.right]);
    expect(ffi.inputModel.downs, isEmpty);
    expect(ffi.cursorModel.moves, [const Offset(120, 130)]);
    await tester.pump(const Duration(milliseconds: 60));
    await _twoFingerTap(tester, pointer: 3);
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(ffi.inputModel.clicks, [MouseButtons.right, MouseButtons.right]);
  });

  for (final action in ['tap', 'double tap', 'long press']) {
    testWidgets('touch-mode $action keeps its existing mouse action',
        (tester) async {
      final ffi = _Ffi();
      await tester.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: RawTouchGestureDetectorRegion(
            ffi: ffi, child: const ColoredBox(color: Colors.white)),
      ));
      final finger = await tester.startGesture(const Offset(120, 130));
      if (action == 'long press') {
        await tester.pump(kLongPressTimeout + const Duration(milliseconds: 20));
      }
      await finger.up();
      if (action == 'double tap') {
        await tester.pump(const Duration(milliseconds: 80));
        await tester.tapAt(const Offset(120, 130));
      }
      await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
      if (action == 'tap') {
        expect(ffi.inputModel.downs, [MouseButtons.left]);
        expect(ffi.inputModel.ups, [MouseButtons.left]);
        expect(ffi.inputModel.clicks, isEmpty);
      } else {
        expect(
            ffi.inputModel.clicks,
            action == 'double tap'
                ? [MouseButtons.left, MouseButtons.left]
                : [MouseButtons.right]);
      }
    });
  }

  testWidgets('two-finger movement still reaches the scale recognizer',
      (tester) async {
    final taps = <TapDownDetails>[];
    var scales = 0;
    await _pumpRecognizer(tester, taps.add, onScale: () => scales++);
    final first = await tester.startGesture(const Offset(120, 130), pointer: 1);
    final second =
        await tester.startGesture(const Offset(160, 130), pointer: 2);
    await second.moveBy(const Offset(60, 0));
    await tester.pump(const Duration(milliseconds: 20));
    await second.moveBy(const Offset(40, 0));
    await first.up();
    await second.up();
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(taps, isEmpty);
    expect(scales, greaterThan(0));
  });

  testWidgets('touch-mode right-click callback positions the remote cursor',
      (tester) async {
    final ffi = _Ffi();
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: RawTouchGestureDetectorRegion(
          ffi: ffi, child: const ColoredBox(color: Colors.white)),
    ));
    final dynamic state =
        tester.state(find.byType(RawTouchGestureDetectorRegion));
    const target = Offset(120, 130);
    final details = TapDownDetails(
        kind: PointerDeviceKind.touch,
        globalPosition: target,
        localPosition: target);
    await state.onDoubleFinerTapDown(details);
    await state.onDoubleFinerTap(details);
    expect(ffi.cursorModel.moves, [target]);
    expect(ffi.inputModel.clicks, [MouseButtons.right]);
    ffi.cursorModel.blocked = true;
    await state.onDoubleFinerTapDown(details);
    await state.onDoubleFinerTap(details);
    expect(ffi.inputModel.clicks, [MouseButtons.right]);
  });

  testWidgets('a single-finger double tap is never a two-finger right click',
      (tester) async {
    final taps = <TapDownDetails>[];
    await _pumpRecognizer(tester, taps.add);
    await tester.tapAt(const Offset(120, 130));
    await tester.pump(const Duration(milliseconds: 80));
    await tester.tapAt(const Offset(120, 130));
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(taps, isEmpty);
  });

  for (final interruption in ['move', 'cancel', 'third finger', 'hold']) {
    testWidgets('$interruption cancels right click and the next tap recovers',
        (tester) async {
      final taps = <TapDownDetails>[];
      await _pumpRecognizer(tester, taps.add);
      final first =
          await tester.startGesture(const Offset(120, 130), pointer: 1);
      final second =
          await tester.startGesture(const Offset(160, 130), pointer: 2);
      if (interruption == 'move') {
        await second.moveBy(const Offset(40, 0));
      } else if (interruption == 'cancel') {
        await first.cancel();
      } else if (interruption == 'third finger') {
        final third =
            await tester.startGesture(const Offset(200, 130), pointer: 3);
        await third.up();
      } else {
        await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
      }
      if (interruption != 'cancel') await first.up();
      await second.up();
      await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
      expect(taps, isEmpty);
      await _twoFingerTap(tester, pointer: 4);
      await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
      expect(taps, hasLength(1));
    });
  }

  testWidgets('a pending two-finger gesture is safely disposed',
      (tester) async {
    final taps = <TapDownDetails>[];
    await _pumpRecognizer(tester, taps.add);
    final first = await tester.startGesture(const Offset(120, 130), pointer: 1);
    final second =
        await tester.startGesture(const Offset(160, 130), pointer: 2);
    await tester.pumpWidget(const SizedBox.shrink());
    await first.up();
    await second.up();
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 20));
    expect(taps, isEmpty);
    expect(tester.takeException(), isNull);
  });
}
