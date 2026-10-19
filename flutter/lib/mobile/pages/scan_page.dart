import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image/image.dart' as img;
import 'package:image_picker/image_picker.dart';
import 'package:qr_code_scanner/qr_code_scanner.dart';
import 'package:zxing2/qrcode.dart' hide BarcodeFormat;

import '../../common/mobile_connection_link.dart';

String _decodeImageCode(Uint8List bytes) {
  final decoder = img.findDecoderForData(bytes);
  final info = decoder?.startDecode(bytes);
  if (info == null || info.width * info.height > 16000000) {
    throw const FormatException('Unsupported image');
  }
  final image = decoder!.decodeFrame(0);
  if (image == null) throw const FormatException('Unsupported image');
  final source = RGBLuminanceSource(image.width, image.height,
      image.getBytes(order: img.ChannelOrder.abgr).buffer.asInt32List());
  return QRCodeReader().decode(BinaryBitmap(HybridBinarizer(source))).text;
}

class ScanPage extends StatefulWidget {
  const ScanPage({super.key, this.imagePicker});

  final ImagePicker? imagePicker;

  @override
  State<ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<ScanPage> with WidgetsBindingObserver {
  QRViewController? controller;
  GlobalKey qrKey = GlobalKey(debugLabel: 'QR');
  StreamSubscription? scanSubscription;
  bool _picking = false;
  bool _finished = false;
  bool _cameraAllowed = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  Future<void> _cameraAction(
      Future<void> Function(QRViewController) action) async {
    final camera = controller;
    if (camera == null || _finished) return;
    try {
      await action(camera);
    } catch (_) {
      if (mounted && !_finished) {
        setState(() => _error = '无法使用相机，请重试或从相册选择二维码。');
      }
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !_picking && !_finished) {
      if (!_cameraAllowed) {
        _restartCamera();
      } else {
        unawaited(_cameraAction((camera) => camera.resumeCamera()));
      }
    } else if (state != AppLifecycleState.resumed) {
      unawaited(_cameraAction((camera) => camera.pauseCamera()));
    }
  }

  void _restartCamera() {
    if (!mounted || _finished || _picking) return;
    scanSubscription?.cancel();
    scanSubscription = null;
    controller?.dispose();
    controller = null;
    setState(() {
      qrKey = GlobalKey(debugLabel: 'QR');
      _error = null;
    });
  }

  @override
  void reassemble() {
    super.reassemble();
    unawaited(_cameraAction((camera) async {
      if (Platform.isAndroid) await camera.pauseCamera();
      await camera.resumeCamera();
    }));
  }

  void _acceptCode(String? code) {
    if (!mounted || _finished || code == null) return;
    final result = MobileScanResult.parse(code);
    if (result == null) {
      if (_error == null) setState(() => _error = '二维码无效，请扫描设备连接码或服务器配置码。');
      return;
    }
    _finished = true;
    // Pop once; the caller validates self ID and opens the selected connection
    // or the editable server-settings confirmation, never both.
    Navigator.of(context).pop(result);
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: const Text('扫码连接'),
        actions: [
          IconButton(
            tooltip: '从相册选择二维码',
            onPressed: _picking || _finished ? null : _pickImage,
            icon: const Icon(Icons.image_search),
          ),
          PopupMenuButton<String>(
            tooltip: '相机选项',
            enabled: _cameraAllowed && controller != null && !_picking,
            onSelected: (action) => _cameraAction((camera) async {
              if (action == 'flash') {
                await camera.toggleFlash();
              } else {
                await camera.flipCamera();
              }
            }),
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'flash', child: Text('切换闪光灯')),
              PopupMenuItem(value: 'camera', child: Text('切换相机')),
            ],
          ),
        ],
      ),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            child: LayoutBuilder(builder: (context, constraints) {
              final scanArea =
                  (constraints.biggest.shortestSide * 0.7).clamp(100.0, 300.0);
              return QRView(
                key: qrKey,
                formatsAllowed: const [BarcodeFormat.qrcode],
                onQRViewCreated: _onQRViewCreated,
                overlay: QrScannerOverlayShape(
                  borderColor: colors.primary,
                  borderRadius: 10,
                  borderLength: 30,
                  borderWidth: 6,
                  cutOutSize: scanArea,
                ),
                onPermissionSet: (camera, allowed) {
                  if (!mounted || _finished || camera != controller) return;
                  setState(() {
                    _cameraAllowed = allowed;
                    _error = allowed ? null : '未获相机权限。可在系统设置中允许后返回，或从相册选择二维码。';
                  });
                },
              );
            }),
          ),
          Flexible(
            flex: 0,
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text(_error ?? '扫描设备连接码；服务器配置码将先打开设置供你确认。',
                    style: Theme.of(context).textTheme.bodyMedium),
                if (_error != null && !_picking)
                  TextButton.icon(
                    onPressed: _restartCamera,
                    icon: const Icon(Icons.refresh),
                    label: const Text('重新扫描'),
                  ),
                if (_picking) ...[
                  const SizedBox(height: 8),
                  const LinearProgressIndicator(),
                ],
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  void _onQRViewCreated(QRViewController camera) {
    if (!mounted || _finished) {
      camera.dispose();
      return;
    }
    scanSubscription?.cancel();
    controller = camera;
    setState(() {});
    scanSubscription = camera.scannedDataStream.listen((scanData) {
      if (!_picking) _acceptCode(scanData.code);
    }, onError: (_) {
      if (mounted && !_finished) setState(() => _error = '无法读取二维码，请重试或从相册选择。');
    });
  }

  Future<void> _pickImage() async {
    if (_picking || _finished) return;
    setState(() {
      _picking = true;
      _error = null;
    });
    await _cameraAction((camera) => camera.pauseCamera());
    try {
      final file = await (widget.imagePicker ?? ImagePicker()).pickImage(
          source: ImageSource.gallery,
          maxWidth: 2400,
          maxHeight: 2400,
          requestFullMetadata: false);
      if (!mounted || _finished || file == null) return;
      if (await file.length() > 20 * 1024 * 1024) {
        throw const FormatException('Image too large');
      }
      final bytes = await file.readAsBytes();
      if (!mounted || _finished) return;
      final code = await compute(_decodeImageCode, bytes);
      if (mounted && !_finished) _acceptCode(code);
    } on PlatformException {
      if (mounted && !_finished) setState(() => _error = '无法打开相册，请检查照片权限后重试。');
    } catch (_) {
      if (mounted && !_finished) setState(() => _error = '图片中未找到可用二维码，请换一张图片。');
    } finally {
      if (mounted && !_finished) {
        setState(() => _picking = false);
        await _cameraAction((camera) => camera.resumeCamera());
      }
    }
  }

  @override
  void dispose() {
    _finished = true;
    WidgetsBinding.instance.removeObserver(this);
    scanSubscription?.cancel();
    controller?.dispose();
    super.dispose();
  }
}
