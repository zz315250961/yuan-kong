import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common/mobile_connection_link.dart';
import 'package:flutter_hbb/mobile/pages/connection_page.dart';
import 'package:flutter_hbb/mobile/pages/scan_page.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image_picker/image_picker.dart';
import 'package:image/image.dart' as img;
import 'package:zxing2/qrcode.dart' as qr;

const _captureKey = ValueKey('scanner-visual-capture');
final _captureDirectory = Platform.environment['LINKREMOTE_SCAN_CAPTURE_DIR'];
final _captureFont = Platform.environment['LINKREMOTE_SCAN_CAPTURE_FONT'];

class _Picker extends ImagePicker {
  final result = Completer<XFile?>();
  int calls = 0;
  @override
  Future<XFile?> pickImage(
      {required ImageSource source,
      double? maxWidth,
      double? maxHeight,
      int? imageQuality,
      CameraDevice preferredCameraDevice = CameraDevice.rear,
      bool requestFullMetadata = true}) {
    calls++;
    return result.future;
  }
}

void main() {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    if (_captureFont != null) {
      final font = FontLoader('ScanQa');
      font.addFont(Future.value(
          ByteData.sublistView(File(_captureFont!).readAsBytesSync())));
      await font.load();
      final icons = FontLoader('MaterialIcons');
      icons.addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
      await icons.load();
    }
  });
  const codec = StandardMethodCodec();
  late MethodChannel camera;
  final cameraCalls = <String>[];
  setUp(() {
    cameraCalls.clear();
    binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform_views, (call) async {
      if (call.method == 'create') {
        final id = (call.arguments as Map)['id'];
        camera = MethodChannel('net.touchcapture.qr.flutterqr/qrview_$id');
        binding.defaultBinaryMessenger.setMockMethodCallHandler(camera,
            (call) async {
          cameraCalls.add(call.method);
          return null;
        });
        return 1;
      }
      if (call.method == 'resize') {
        final args = call.arguments as Map;
        return {'width': args['width'], 'height': args['height']};
      }
      return null;
    });
  });
  tearDown(() {
    binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform_views, null);
  });
  Future<void> native(String method, Object value) async {
    await binding.defaultBinaryMessenger.handlePlatformMessage(
        camera.name, codec.encodeMethodCall(MethodCall(method, value)), (_) {});
  }

  Future<void> scan(String code) =>
      native('onRecognizeQR', {'code': code, 'type': 'QR_CODE'});
  Future<void> open(WidgetTester tester, List<MobileScanResult?> results,
      {ImagePicker? picker, ThemeData? theme}) async {
    await tester.pumpWidget(RepaintBoundary(
        key: _captureKey,
        child: MaterialApp(
            debugShowCheckedModeBanner: false,
            theme: theme,
            home: Builder(
                builder: (context) => Scaffold(
                    body: TextButton(
                        onPressed: () async {
                          results.add(await Navigator.of(context)
                              .push<MobileScanResult>(MaterialPageRoute(
                                  builder: (_) =>
                                      ScanPage(imagePicker: picker))));
                        },
                        child: const Text('open')))))));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('扫码连接'), findsOneWidget);
    expect(cameraCalls, contains('startScan'));
  }

  testWidgets(
      'scanner instructions and actions fit phone tablet and large-font narrow layouts',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    for (final variant in [
      (
        name: 'phone-light',
        size: const Size(390, 844),
        scale: 1.0,
        dark: false
      ),
      (name: 'ipad-dark', size: const Size(1024, 1366), scale: 1.0, dark: true),
      (
        name: 'narrow-large-dark',
        size: const Size(320, 568),
        scale: 1.3,
        dark: true
      ),
    ]) {
      tester.view.physicalSize = variant.size;
      tester.platformDispatcher.textScaleFactorTestValue = variant.scale;
      await open(tester, <MobileScanResult?>[],
          theme: ThemeData(
              brightness: variant.dark ? Brightness.dark : Brightness.light,
              fontFamily: _captureFont == null ? null : 'ScanQa'));
      await native('onPermissionSet', false);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('重新扫描'), findsOneWidget);
      if (_captureDirectory != null) {
        await tester.runAsync(() async {
          final boundary = tester
              .renderObject<RenderRepaintBoundary>(find.byKey(_captureKey));
          final image = await boundary.toImage();
          try {
            final png = await image.toByteData(format: ui.ImageByteFormat.png);
            final file = File('$_captureDirectory/${variant.name}.png');
            if (file.existsSync()) {
              throw StateError('Preserve previous capture');
            }
            file.parent.createSync(recursive: true);
            file.writeAsBytesSync(png!.buffer.asUint8List());
          } finally {
            image.dispose();
          }
        });
      }
      await tester.pageBack();
      await tester.pumpAndSettle();
    }
  }, variant: TargetPlatformVariant({TargetPlatform.iOS}));

  testWidgets(
      'real QR controller returns one device result for repeated frames',
      (tester) async {
    final results = <MobileScanResult?>[];
    await open(tester, results);
    await scan('linkremote://123456789');
    await scan('linkremote://123456789');
    await tester.pumpAndSettle();
    expect(results, hasLength(1));
    expect(results.single!.connection!.id, '123456789');
    expect(results.single!.serverConfig, isNull);
    expect(find.text('open'), findsOneWidget);
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets('invalid QR and permission denial keep a recoverable scanner',
      (tester) async {
    final results = <MobileScanResult?>[];
    await open(tester, results);
    await native('onPermissionSet', false);
    await tester.pump();
    expect(find.textContaining('未获相机权限'), findsOneWidget);
    expect(find.byTooltip('从相册选择二维码'), findsOneWidget);
    final cameraOptions = tester
        .widget<PopupMenuButton<String>>(find.byType(PopupMenuButton<String>));
    expect(cameraOptions.enabled, isFalse);
    await native('onPermissionSet', true);
    await scan('https://123456789');
    await tester.pumpAndSettle();
    expect(find.textContaining('二维码无效'), findsOneWidget);
    expect(results, isEmpty);
    await scan('config={"host":"example.test:21116","key":"abc="}');
    await tester.pumpAndSettle();
    expect(results, hasLength(1));
    expect(results.single!.connection, isNull);
    expect(results.single!.serverConfig!['host'], 'example.test:21116');
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets(
      'canceling scanner returns no result and releases camera observer',
      (tester) async {
    final results = <MobileScanResult?>[];
    await open(tester, results);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(results, [null]);
    final before = cameraCalls.length;
    binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    expect(cameraCalls.length, before);
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets('home action opens scanner only once and cancel is harmless',
      (tester) async {
    await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: ScanConnectionAction())));
    await tester.tap(find.byTooltip('扫码连接'));
    await tester.pump();
    await tester.pumpAndSettle();
    expect(find.byType(ScanPage), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(ScanConnectionAction), findsOneWidget);
    expect(tester.widget<IconButton>(find.byType(IconButton)).onPressed,
        isNotNull);
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets(
      'cancelled picker resumes camera and ignores frames while picking',
      (tester) async {
    final results = <MobileScanResult?>[];
    final picker = _Picker();
    await open(tester, results, picker: picker);
    await tester.tap(find.byTooltip('从相册选择二维码'));
    await tester.pump();
    expect(picker.calls, 1);
    expect(cameraCalls, contains('pauseCamera'));
    await scan('123456789');
    await tester.pump();
    expect(results, isEmpty);
    picker.result.complete(null);
    await tester.pumpAndSettle();
    expect(cameraCalls, contains('resumeCamera'));
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await scan('123456789');
    await tester.pumpAndSettle();
    expect(results.single!.connection!.id, '123456789');
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets(
      'late picker failure after leaving scanner does not navigate or update disposed state',
      (tester) async {
    final results = <MobileScanResult?>[];
    final picker = _Picker();
    await open(tester, results, picker: picker);
    await tester.tap(find.byTooltip('从相册选择二维码'));
    await tester.pump();
    await tester.pageBack();
    await tester.pumpAndSettle();
    picker.result.completeError(PlatformException(code: 'photo_access_denied'));
    await tester.pumpAndSettle();
    expect(results, [null]);
    expect(find.text('open'), findsOneWidget);
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets(
      'picker permission denial preserves scanner with a retryable explanation',
      (tester) async {
    final results = <MobileScanResult?>[];
    final picker = _Picker();
    await open(tester, results, picker: picker);
    await tester.tap(find.byTooltip('从相册选择二维码'));
    await tester.pump();
    picker.result.completeError(PlatformException(code: 'photo_access_denied'));
    await tester.pumpAndSettle();
    expect(find.textContaining('检查照片权限'), findsOneWidget);
    expect(results, isEmpty);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets('return from denied-permission settings recreates native scanner',
      (tester) async {
    final results = <MobileScanResult?>[];
    await open(tester, results);
    await native('onPermissionSet', false);
    await tester.pumpAndSettle();
    final oldChannel = camera.name;
    binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(camera.name, isNot(oldChannel));
    await native('onPermissionSet', true);
    await scan('123456789');
    await tester.pumpAndSettle();
    expect(results.single!.connection!.id, '123456789');
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));

  testWidgets(
      'gallery QR is decoded through the production image path and returns a device',
      (tester) async {
    final results = <MobileScanResult?>[];
    final picker = _Picker();
    await open(tester, results, picker: picker);
    final matrix =
        qr.Encoder.encode('linkremote://123456789', qr.ErrorCorrectionLevel.m)
            .matrix!;
    const scale = 5;
    const margin = 4;
    final image = img.Image(
        width: (matrix.width + margin * 2) * scale,
        height: (matrix.height + margin * 2) * scale);
    for (final pixel in image) {
      final x = pixel.x ~/ scale - margin;
      final y = pixel.y ~/ scale - margin;
      final dark = x >= 0 &&
          x < matrix.width &&
          y >= 0 &&
          y < matrix.height &&
          matrix.get(x, y) == 1;
      final value = dark ? 0 : 255;
      pixel.setRgb(value, value, value);
    }
    await tester.tap(find.byTooltip('从相册选择二维码'));
    await tester.pump();
    await tester.runAsync(() async {
      picker.result.complete(
          XFile.fromData(img.encodePng(image), mimeType: 'image/png'));
      final elapsed = Stopwatch()..start();
      while (results.isEmpty && elapsed.elapsed < const Duration(seconds: 10)) {
        await tester.pump(const Duration(milliseconds: 16));
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
    });
    await tester.pumpAndSettle();
    expect(results, hasLength(1));
    expect(results.single!.connection!.id, '123456789');
    expect(tester.takeException(), isNull);
  },
      variant:
          TargetPlatformVariant({TargetPlatform.android, TargetPlatform.iOS}));
}
