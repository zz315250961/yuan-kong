import 'dart:async';

import 'package:auto_size_text_field/auto_size_text_field.dart';
import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/formatter/id_formatter.dart';
import 'package:flutter_hbb/common/widgets/connection_page_title.dart';
import 'package:flutter_hbb/models/state_model.dart';
import 'package:get/get.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:flutter_hbb/models/peer_model.dart';

import '../../common.dart';
import '../../common/mobile_connection_link.dart';
import '../../common/widgets/peer_tab_page.dart';
import '../../common/widgets/autocomplete.dart';
import '../../common/widgets/my_devices.dart';
import '../../consts.dart';
import '../../models/model.dart';
import '../../models/platform_model.dart';
import 'home_page.dart';
import 'scan_page.dart';
import '../widgets/dialog.dart';

/// Connection page for connecting to a remote peer.
class ConnectionPage extends StatefulWidget implements PageShape {
  ConnectionPage({Key? key, required this.appBarActions}) : super(key: key);

  @override
  final icon = const Icon(Icons.connected_tv);

  @override
  final title = translate("Connection");

  @override
  final List<Widget> appBarActions;

  @override
  State<ConnectionPage> createState() => _ConnectionPageState();
}

/// State for the connection page.
class _ConnectionPageState extends State<ConnectionPage> {
  /// Controller for the id input bar.
  final _idController = IDTextEditingController();
  final RxBool _idEmpty = true.obs;

  final FocusNode _idFocusNode = FocusNode();
  final TextEditingController _idEditingController = TextEditingController();

  final AllPeersLoader _allPeersLoader = AllPeersLoader();

  StreamSubscription? _uniLinksSubscription;
  bool _openingConnection = false;

  // https://github.com/flutter/flutter/issues/157244
  Iterable<Peer> _autocompleteOpts = [];

  _ConnectionPageState() {
    if (!isWeb) _uniLinksSubscription = listenUniLinks();
    _idController.addListener(() {
      _idEmpty.value = _idController.text.isEmpty;
    });
    Get.put<IDTextEditingController>(_idController);
  }

  @override
  void initState() {
    super.initState();
    _allPeersLoader.init(setState);
    _idFocusNode.addListener(onFocusChanged);
    if (_idController.text.isEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        final lastRemoteId = await bind.mainGetLastRemoteId();
        if (!mounted || _idController.id.isNotEmpty) return;
        if (lastRemoteId != _idController.id) {
          setState(() {
            _idController.id = lastRemoteId;
          });
        }
      });
    }
    Get.put<TextEditingController>(_idEditingController);
  }

  @override
  Widget build(BuildContext context) {
    Provider.of<FfiModel>(context);
    return CustomScrollView(
      slivers: [
        SliverList(
            delegate: SliverChildListDelegate([
          if (!bind.isCustomClient() && !isIOS)
            Obx(() => _buildUpdateUI(stateGlobal.updateUrl.value)),
          _buildRemoteIDTextField(),
        ])),
        SliverFillRemaining(
          hasScrollBody: true,
          child: PeerTabPage(),
        )
      ],
    ).marginOnly(top: 2, left: 10, right: 10);
  }

  /// Callback for the connect button.
  /// Connects to the selected peer.
  Future<void> onConnect() async {
    final id = _idController.id.replaceAll(' ', '');
    if (id.isEmpty || _openingConnection) return;
    _openingConnection = true;
    try {
      final myId = (await bind.mainGetMyId()).replaceAll(' ', '');
      if (!mounted) return;
      if (id == myId) {
        showToast(translate('Cannot connect to self'));
        return;
      }
      await connect(context, id);
    } finally {
      _openingConnection = false;
    }
  }

  void onFocusChanged() {
    _idEmpty.value = _idEditingController.text.isEmpty;
    if (_idFocusNode.hasFocus) {
      if (_allPeersLoader.needLoad) {
        _allPeersLoader.getAllPeers();
      }

      final textLength = _idEditingController.value.text.length;
      // Select all to facilitate removing text, just following the behavior of address input of chrome.
      _idEditingController.selection =
          TextSelection(baseOffset: 0, extentOffset: textLength);
    }
  }

  /// UI for software update.
  /// If _updateUrl] is not empty, shows a button to update the software.
  Widget _buildUpdateUI(String updateUrl) {
    return updateUrl.isEmpty
        ? const SizedBox(height: 0)
        : InkWell(
            onTap: () async {
              // LinkRemote 定制：使用自有下载页作为兜底。
              final url = updateUrl.isEmpty
                  ? 'https://zperme.top/#download'
                  : updateUrl;
              // https://pub.dev/packages/url_launcher#configuration
              // https://developer.android.com/training/package-visibility/use-cases#open-urls-custom-tabs
              //
              // `await launchUrl(Uri.parse(url))` can also run if skip
              // 1. The following check
              // 2. `<action android:name="android.support.customtabs.action.CustomTabsService" />` in AndroidManifest.xml
              //
              // But it is better to add the check.
              await launchUrl(Uri.parse(url));
            },
            child: Container(
                alignment: AlignmentDirectional.center,
                width: double.infinity,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.secondary,
                  borderRadius: BorderRadius.circular(12),
                ),
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(translate('Download new version'),
                    style: TextStyle(
                        color: Theme.of(context).colorScheme.onSecondary,
                        fontWeight: FontWeight.w600))));
  }

  /// UI for the remote ID TextField.
  /// Search for a peer and connect to it if the id exists.
  Widget _buildRemoteIDTextField() {
    final w = ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 84),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 2),
        child: Ink(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.all(Radius.circular(13)),
          ),
          child: Row(
            children: <Widget>[
              Expanded(
                child: Container(
                  padding: const EdgeInsets.only(left: 16, right: 16),
                  child: RawAutocomplete<Peer>(
                    optionsBuilder: (TextEditingValue textEditingValue) {
                      if (textEditingValue.text == '') {
                        _autocompleteOpts = const Iterable<Peer>.empty();
                      } else if (_allPeersLoader.peers.isEmpty &&
                          !_allPeersLoader.isPeersLoaded) {
                        Peer emptyPeer = Peer(
                          id: '',
                          username: '',
                          hostname: '',
                          alias: '',
                          platform: '',
                          tags: [],
                          hash: '',
                          password: '',
                          forceAlwaysRelay: false,
                          rdpPort: '',
                          rdpUsername: '',
                          loginName: '',
                          device_group_name: '',
                          note: '',
                        );
                        _autocompleteOpts = [emptyPeer];
                      } else {
                        String textWithoutSpaces =
                            textEditingValue.text.replaceAll(" ", "");
                        if (int.tryParse(textWithoutSpaces) != null) {
                          textEditingValue = TextEditingValue(
                            text: textWithoutSpaces,
                            selection: textEditingValue.selection,
                          );
                        }
                        String textToFind = textEditingValue.text.toLowerCase();

                        _autocompleteOpts = _allPeersLoader.peers
                            .where((peer) =>
                                peer.id.toLowerCase().contains(textToFind) ||
                                peer.username
                                    .toLowerCase()
                                    .contains(textToFind) ||
                                peer.hostname
                                    .toLowerCase()
                                    .contains(textToFind) ||
                                peer.alias.toLowerCase().contains(textToFind))
                            .toList();
                        _allPeersLoader.queryOnlines(_autocompleteOpts);
                      }
                      return _autocompleteOpts;
                    },
                    focusNode: _idFocusNode,
                    textEditingController: _idEditingController,
                    fieldViewBuilder: (BuildContext context,
                        TextEditingController fieldTextEditingController,
                        FocusNode fieldFocusNode,
                        VoidCallback onFieldSubmitted) {
                      updateTextAndPreserveSelection(
                          fieldTextEditingController, _idController.text);
                      return AutoSizeTextField(
                        controller: fieldTextEditingController,
                        focusNode: fieldFocusNode,
                        minFontSize: 18,
                        autocorrect: false,
                        enableSuggestions: false,
                        keyboardType: TextInputType.visiblePassword,
                        textInputAction: TextInputAction.go,
                        // keyboardType: TextInputType.number,
                        onChanged: (String text) {
                          _idController.id = text;
                        },
                        style: const TextStyle(
                          fontFamily: 'WorkSans',
                          fontWeight: FontWeight.bold,
                          fontSize: 30,
                          color: MyTheme.idColor,
                        ),
                        decoration: InputDecoration(
                          labelText: translate('Remote ID'),
                          contentPadding:
                              const EdgeInsets.symmetric(vertical: 12),
                          // hintText: 'Enter your remote ID',
                          border: InputBorder.none,
                          helperStyle: const TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                            color: MyTheme.darkGray,
                          ),
                          labelStyle: const TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 16,
                            letterSpacing: 0.2,
                            color: MyTheme.darkGray,
                          ),
                        ),
                        inputFormatters: [IDTextInputFormatter()],
                        onSubmitted: (_) {
                          onConnect();
                        },
                      );
                    },
                    onSelected: (option) {
                      setState(() {
                        _idController.id = option.id;
                        FocusScope.of(context).unfocus();
                      });
                    },
                    optionsViewBuilder: (BuildContext context,
                        AutocompleteOnSelected<Peer> onSelected,
                        Iterable<Peer> options) {
                      options = _autocompleteOpts;
                      double maxHeight = options.length * 50;
                      if (options.length == 1) {
                        maxHeight = 52;
                      } else if (options.length == 3) {
                        maxHeight = 146;
                      } else if (options.length == 4) {
                        maxHeight = 193;
                      }
                      maxHeight = maxHeight.clamp(0, 200);
                      return Align(
                          alignment: Alignment.topLeft,
                          child: Container(
                              decoration: BoxDecoration(
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withOpacity(0.3),
                                    blurRadius: 5,
                                    spreadRadius: 1,
                                  ),
                                ],
                              ),
                              child: ClipRRect(
                                  borderRadius: BorderRadius.circular(5),
                                  child: Material(
                                      elevation: 4,
                                      child: ConstrainedBox(
                                          constraints: BoxConstraints(
                                            maxHeight: maxHeight,
                                            maxWidth: 320,
                                          ),
                                          child: _allPeersLoader
                                                      .peers.isEmpty &&
                                                  !_allPeersLoader.isPeersLoaded
                                              ? Container(
                                                  height: 80,
                                                  child: Center(
                                                      child:
                                                          CircularProgressIndicator(
                                                    strokeWidth: 2,
                                                  )))
                                              : ListView(
                                                  padding:
                                                      EdgeInsets.only(top: 5),
                                                  children: options
                                                      .map((peer) =>
                                                          AutocompletePeerTile(
                                                              onSelect: () =>
                                                                  onSelected(
                                                                      peer),
                                                              peer: peer))
                                                      .toList(),
                                                ))))));
                    },
                  ),
                ),
              ),
              Obx(() => Offstage(
                    offstage: _idEmpty.value,
                    child: IconButton(
                        tooltip: translate('Clear'),
                        onPressed: () {
                          setState(() {
                            _idController.clear();
                          });
                        },
                        icon: Icon(Icons.clear, color: MyTheme.darkGray)),
                  )),
              SizedBox(
                width: 60,
                height: 60,
                child: IconButton(
                  tooltip: translate('Connect'),
                  icon: const Icon(Icons.arrow_forward,
                      color: MyTheme.darkGray, size: 45),
                  onPressed: onConnect,
                ),
              ),
            ],
          ),
        ),
      ),
    );
    final child = Column(children: [
      if (isWebDesktop)
        getConnectionPageTitle(context, true)
            .marginOnly(bottom: 10, top: 15, left: 12),
      w
    ]);
    return Align(
        alignment: Alignment.topCenter,
        child: Container(constraints: kMobilePageConstraints, child: child));
  }

  @override
  void dispose() {
    _uniLinksSubscription?.cancel();
    _idController.dispose();
    _idFocusNode.removeListener(onFocusChanged);
    _allPeersLoader.clear();
    _idFocusNode.dispose();
    _idEditingController.dispose();
    if (Get.isRegistered<IDTextEditingController>()) {
      Get.delete<IDTextEditingController>();
    }
    if (Get.isRegistered<TextEditingController>()) {
      Get.delete<TextEditingController>();
    }
    super.dispose();
  }
}

/// 顶栏「我的设备」图标：查看同一账号下的所有设备。
Widget myDevicesAction() {
  return IconButton(
    icon: const Icon(Icons.devices),
    tooltip: translate('My devices'),
    onPressed: () {
      showMyDevicesDialog();
    },
  );
}

/// Scan results are returned before navigating, so camera callbacks cannot
/// close an existing session or silently overwrite server settings.
class ScanConnectionAction extends StatefulWidget {
  const ScanConnectionAction({super.key});

  @override
  State<ScanConnectionAction> createState() => _ScanConnectionActionState();
}

class _ScanConnectionActionState extends State<ScanConnectionAction> {
  final _gate = MobileConnectionGate();
  bool _opening = false;

  Future<void> _scan() async {
    if (_opening) return;
    setState(() => _opening = true);
    try {
      final result = await Navigator.of(context).push<MobileScanResult>(
          MaterialPageRoute(builder: (_) => const ScanPage()));
      if (!mounted || result == null) return;
      final config = result.serverConfig;
      if (config != null) {
        showServerSettingsWithValue(
            ServerConfig(
                idServer: config['host'],
                relayServer: config['relay'],
                apiServer: config['api'],
                key: config['key']),
            gFFI.dialogManager,
            null);
        return;
      }
      final link = result.connection;
      if (link != null) {
        await _gate.open(link,
            localId: bind.mainGetMyId,
            isActive: () => mounted && stateGlobal.isInMainPage,
            onSelf: () => showToast(translate('Cannot connect to self')),
            navigate: (request) async {
              await connect(context, request.id,
                  isFileTransfer:
                      request.kind == MobileConnectionKind.fileTransfer,
                  isViewCamera: request.kind == MobileConnectionKind.viewCamera,
                  isTerminal: request.kind == MobileConnectionKind.terminal,
                  forceRelay: request.forceRelay,
                  password: request.password);
            });
      }
    } catch (e) {
      debugPrint('Unable to open scanned connection: ${e.runtimeType}');
      if (mounted) showToast(translate('Failed'));
    } finally {
      if (mounted) setState(() => _opening = false);
    }
  }

  @override
  Widget build(BuildContext context) => IconButton(
      tooltip: '扫码连接',
      onPressed: _opening ? null : _scan,
      icon: const Icon(Icons.qr_code_scanner));

  @override
  void dispose() {
    _gate.dispose();
    super.dispose();
  }
}
