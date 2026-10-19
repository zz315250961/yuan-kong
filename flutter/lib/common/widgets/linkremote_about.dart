import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

const linkRemoteSourceUrl = 'https://github.com/zz315250961/yuan-kong';
bool _licenseRegistered = false;

/// Register lazily: opening the app does not wait for reading license assets.
void registerLinkRemoteLicense() {
  if (_licenseRegistered) return;
  _licenseRegistered = true;
  LicenseRegistry.addLicense(() async* {
    final notice = await rootBundle.loadString('assets/NOTICE.md');
    final license = await rootBundle.loadString('assets/LICENCE');
    yield LicenseEntryWithLineBreaks(['LinkRemote'], '$notice\n\n$license');
  });
}

class LinkRemoteAboutPage extends StatelessWidget {
  const LinkRemoteAboutPage({
    super.key,
    required this.version,
    this.openSource = launchUrl,
  });

  final String version;
  final Future<bool> Function(Uri) openSource;

  Future<void> _openSource(BuildContext context) async {
    bool opened;
    try {
      opened = await openSource(Uri.parse(linkRemoteSourceUrl));
    } catch (_) {
      opened = false;
    }
    if (!opened && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('无法打开浏览器，请复制上方源码地址。'),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('关于 LinkRemote')),
      body: SafeArea(
        child: Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 600),
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                Text('LinkRemote',
                    style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 8),
                Text('版本：$version'),
                const SizedBox(height: 24),
                const Text('本软件基于 RustDesk 社区项目，遵循 GNU AGPL v3.0。'
                    '原作者版权及第三方许可仍然适用；LinkRemote 品牌不代表上游认可。'),
                const SizedBox(height: 16),
                const SelectableText(linkRemoteSourceUrl),
                const SizedBox(height: 12),
                ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 48),
                  child: OutlinedButton.icon(
                    onPressed: () => _openSource(context),
                    icon: const Icon(Icons.open_in_new),
                    label: const Text('公开源码与版本'),
                  ),
                ),
                const SizedBox(height: 8),
                ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 48),
                  child: TextButton.icon(
                    onPressed: () => showLicensePage(
                      context: context,
                      applicationName: 'LinkRemote',
                      applicationVersion: version,
                    ),
                    icon: const Icon(Icons.description_outlined),
                    label: const Text('开源许可（可离线查看）'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
