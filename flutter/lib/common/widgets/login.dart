import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common/hbbs/hbbs.dart';
import 'package:flutter_hbb/models/platform_model.dart';
import 'package:flutter_hbb/models/user_model.dart';
import 'package:get/get.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../common.dart';
import './dialog.dart';
import './oidc_auth_status.dart';

const kOpSvgList = [
  'github',
  'gitlab',
  'google',
  'apple',
  'okta',
  'facebook',
  'azure',
  'auth0',
  'microsoft'
];
const _requestingAccountAuth = 'Requesting account auth';
const _waitingAccountAuth = 'Waiting account auth';

class _OidcProviderBranding {
  final String label;
  final String iconKey;

  const _OidcProviderBranding({
    required this.label,
    required this.iconKey,
  });
}

_OidcProviderBranding _oidcProviderBranding(String op) {
  switch (op.toLowerCase()) {
    case 'azure':
      return _OidcProviderBranding(
        label: 'Microsoft',
        iconKey: 'microsoft',
      );
    default:
      return _OidcProviderBranding(
        label: {
              'github': 'GitHub',
              'gitlab': 'GitLab',
            }[op.toLowerCase()] ??
            toCapitalized(op),
        iconKey: op.toLowerCase(),
      );
  }
}

class _IconOP extends StatelessWidget {
  final String op;
  final String? icon;
  final EdgeInsets margin;
  const _IconOP(
      {Key? key,
      required this.op,
      required this.icon,
      this.margin = const EdgeInsets.symmetric(horizontal: 4.0)})
      : super(key: key);

  @override
  Widget build(BuildContext context) {
    final svgFile =
        kOpSvgList.contains(op.toLowerCase()) ? op.toLowerCase() : 'default';
    return Container(
      margin: margin,
      child: icon == null
          ? SvgPicture.asset(
              'assets/auth-$svgFile.svg',
              width: 20,
            )
          : SvgPicture.string(
              icon!,
              width: 20,
            ),
    );
  }
}

class ButtonOP extends StatelessWidget {
  final String op;
  final RxString curOP;
  final String? icon;
  final Color primaryColor;
  final double height;
  final Function() onTap;
  final bool Function() canStartAuth;

  const ButtonOP({
    Key? key,
    required this.op,
    required this.curOP,
    required this.icon,
    required this.primaryColor,
    required this.height,
    required this.onTap,
    required this.canStartAuth,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    final branding = _oidcProviderBranding(op);
    final buttonLabel = translate("Continue with {${branding.label}}");
    return Row(children: [
      Container(
        height: height,
        width: 200,
        child: Obx(() => ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: primaryColor,
            ).copyWith(elevation: ButtonStyleButton.allOrNull(0.0)),
            onPressed:
                curOP.value == 'rustdesk' || !canStartAuth() ? null : onTap,
            child: Row(
              children: [
                SizedBox(
                  width: 30,
                  child: _IconOP(
                    op: branding.iconKey,
                    icon: icon,
                    margin: EdgeInsets.only(right: 5),
                  ),
                ),
                Expanded(
                  child: FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Center(child: Text(buttonLabel)),
                  ),
                ),
              ],
            ))),
      ),
    ]);
  }
}

class ConfigOP {
  final String op;
  final String? icon;
  ConfigOP({required this.op, required this.icon});
}

class _OidcAuthController {
  final RxString curOP = ''.obs;
  Future<void> _pendingOperation = Future<void>.value();
  int _authAttempt = 0;
  bool _closed = false;
  final _cancelInProgress = false.obs;

  bool _isCurrent(int authAttempt, String op) {
    return !_closed && authAttempt == _authAttempt && curOP.value == op;
  }

  Future<bool> start(String op) {
    if (!canStart()) {
      return Future<bool>.value(false);
    }
    final authAttempt = ++_authAttempt;
    curOP.value = op;
    // Web auth must start during the original user gesture so popups are allowed.
    if (isWeb) {
      return _startWeb(authAttempt, op);
    }
    final completer = Completer<bool>();
    _pendingOperation = _pendingOperation.then((_) async {
      if (!_isCurrent(authAttempt, op)) {
        completer.complete(false);
        return;
      }
      try {
        await bind.mainAccountAuthCancel();
        if (!_isCurrent(authAttempt, op)) {
          completer.complete(false);
          return;
        }
        await bind.mainAccountAuth(op: op, rememberMe: true);
        completer.complete(_isCurrent(authAttempt, op));
      } catch (error, stackTrace) {
        completer.completeError(error, stackTrace);
      }
    });
    return completer.future;
  }

  Future<bool> _startWeb(int authAttempt, String op) async {
    await bind.mainAccountAuth(op: op, rememberMe: true);
    return _isCurrent(authAttempt, op);
  }

  bool canStart() {
    return !_closed && !_cancelInProgress.value;
  }

  Future<bool> cancelCurrent(String op) {
    if (!canStart() || curOP.value != op) {
      return Future<bool>.value(false);
    }
    final authAttempt = ++_authAttempt;
    final completer = Completer<bool>();
    _cancelInProgress.value = true;
    _pendingOperation = _pendingOperation.then((_) async {
      try {
        await bind.mainAccountAuthCancel();
        completer.complete(_isCurrent(authAttempt, op));
      } catch (error, stackTrace) {
        completer.completeError(error, stackTrace);
      } finally {
        _cancelInProgress.value = false;
      }
    });
    return completer.future;
  }

  Future<void> _cancelBackend() async {
    try {
      await bind.mainAccountAuthCancel();
    } catch (error, stackTrace) {
      debugPrint('Failed to cancel account authentication $error');
      debugPrintStack(stackTrace: stackTrace);
    }
  }

  Future<void> close() async {
    if (_closed) {
      return;
    }
    final hasActiveOidcAuth =
        curOP.value.isNotEmpty && curOP.value != 'rustdesk';
    _closed = true;
    _authAttempt++;
    curOP.value = '';
    if (hasActiveOidcAuth) {
      await _cancelBackend();
    }
    await _pendingOperation;
    if (hasActiveOidcAuth) {
      await _cancelBackend();
    }
  }
}

class WidgetOP extends StatefulWidget {
  final ConfigOP config;
  final RxString curOP;
  final Function(Map<String, dynamic>) cbLogin;
  final Future<bool> Function(String) startAuth;
  final Future<bool> Function(String) cancelAuth;
  final bool Function() canStartAuth;
  const WidgetOP({
    Key? key,
    required this.config,
    required this.curOP,
    required this.cbLogin,
    required this.startAuth,
    required this.cancelAuth,
    required this.canStartAuth,
  }) : super(key: key);

  @override
  State<StatefulWidget> createState() {
    return _WidgetOPState();
  }
}

class _WidgetOPState extends State<WidgetOP> {
  Timer? _updateTimer;
  bool _isAuthStatusQueryInFlight = false;
  int _authAttempt = 0;
  String _stateMsg = '';
  String _failedMsg = '';
  String _url = '';

  @override
  void dispose() {
    super.dispose();
    _updateTimer?.cancel();
  }

  _beginQueryState(int authAttempt) {
    _updateTimer?.cancel();
    unawaited(_runAuthStatusQuery(() => _updateState(authAttempt)));
    _updateTimer = Timer.periodic(Duration(seconds: 1), (timer) {
      unawaited(_runAuthStatusQuery(() => _updateState(authAttempt)));
    });
  }

  Future<void> _runAuthStatusQuery(Future<void> Function() query) async {
    if (_isAuthStatusQueryInFlight) {
      return;
    }
    _isAuthStatusQueryInFlight = true;
    try {
      await query();
    } finally {
      _isAuthStatusQueryInFlight = false;
    }
  }

  Future<void> _launchAuthUrl(String url) async {
    try {
      final launched = await launchUrl(
        Uri.parse(url),
        mode: LaunchMode.externalApplication,
      );
      if (!launched) {
        debugPrint('Failed to open OIDC authentication URL');
      }
    } catch (error, stackTrace) {
      debugPrint(
          'Failed to open OIDC authentication URL (${error.runtimeType})');
      debugPrintStack(stackTrace: stackTrace);
    }
  }

  Future<void> _copyAuthUrl(String url) async {
    try {
      await Clipboard.setData(ClipboardData(text: url));
      showToast(
        translate('Copied'),
      );
    } catch (error, stackTrace) {
      debugPrint(
          'Failed to copy OIDC authentication URL (${error.runtimeType})');
      debugPrintStack(stackTrace: stackTrace);
      showToast(translate('Failed'));
    }
  }

  void _runCurrentAuthUrlAction(
    int authAttempt,
    String authUrl,
    Future<void> Function(String) action,
  ) {
    if (!mounted ||
        authAttempt != _authAttempt ||
        widget.curOP.value != widget.config.op ||
        authUrl.isEmpty ||
        _url != authUrl) {
      return;
    }
    unawaited(action(authUrl));
  }

  void _invalidateAuthAttempt() {
    _authAttempt++;
    _url = '';
  }

  bool _isCurrentAuthAttempt(int authAttempt) {
    return mounted &&
        authAttempt == _authAttempt &&
        widget.curOP.value == widget.config.op;
  }

  Future<void> _handleAuthFailure(
    int authAttempt,
    Object error,
    String operation,
  ) async {
    debugPrint('Failed to $operation $error');
    if (!_isCurrentAuthAttempt(authAttempt)) {
      return;
    }
    _updateTimer?.cancel();
    setState(() => _failedMsg = 'Failed');
    try {
      final canceled = await widget.cancelAuth(widget.config.op);
      if (!canceled || !_isCurrentAuthAttempt(authAttempt)) {
        return;
      }
    } catch (cancelError, stackTrace) {
      debugPrint('Failed to cancel account authentication $cancelError');
      debugPrintStack(stackTrace: stackTrace);
      return;
    }
    setState(() {
      _invalidateAuthAttempt();
      widget.curOP.value = '';
    });
  }

  Future<void> _updateState(int authAttempt) {
    if (!mounted ||
        authAttempt != _authAttempt ||
        widget.curOP.value != widget.config.op) {
      _updateTimer?.cancel();
      return Future<void>.value();
    }
    return bind.mainAccountAuthResult().then<void>((result) {
      if (!mounted ||
          authAttempt != _authAttempt ||
          widget.curOP.value != widget.config.op ||
          result.isEmpty) {
        return;
      }
      final resultMap = jsonDecode(result);
      if (resultMap == null) {
        return;
      }
      final String backendStateMsg = resultMap['state_msg'];
      String failedMsg = resultMap['failed_msg'];
      final String? url = resultMap['url'];
      final stateMsg = backendStateMsg == _requestingAccountAuth &&
              (url == null || url.isEmpty)
          ? _waitingAccountAuth
          : backendStateMsg;
      final bool urlLaunched = (resultMap['url_launched'] as bool?) ?? false;
      final authBody = resultMap['auth_body'];
      if (authBody != null) {
        _updateTimer?.cancel();
        _invalidateAuthAttempt();
        widget.curOP.value = '';
        widget.cbLogin(authBody as Map<String, dynamic>);
        return;
      }
      final stateChanged = _stateMsg != stateMsg || _failedMsg != failedMsg;
      final newUrl = _url.isEmpty && url != null && url.isNotEmpty ? url : null;
      if (!stateChanged && newUrl == null) {
        return;
      }
      setState(() {
        _stateMsg = stateMsg;
        _failedMsg = failedMsg;
        if (newUrl != null) {
          _url = newUrl;
        }
        if (failedMsg.isNotEmpty) {
          _invalidateAuthAttempt();
          widget.curOP.value = '';
          _updateTimer?.cancel();
        }
      });
      if (newUrl != null && failedMsg.isEmpty && !urlLaunched) {
        unawaited(_launchAuthUrl(newUrl));
      }
    }).catchError(
      (e) => _handleAuthFailure(
        authAttempt,
        e,
        'query account authentication',
      ),
    );
  }

  int _resetState() {
    _updateTimer?.cancel();
    setState(() {
      _invalidateAuthAttempt();
      _stateMsg = _waitingAccountAuth;
      _failedMsg = '';
    });
    return _authAttempt;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        ButtonOP(
          op: widget.config.op,
          curOP: widget.curOP,
          icon: widget.config.icon,
          primaryColor: str2color(widget.config.op, 0x7f),
          height: 36,
          canStartAuth: widget.canStartAuth,
          onTap: () async {
            if (!widget.canStartAuth()) {
              return;
            }
            final authAttempt = _resetState();
            try {
              final started = await widget.startAuth(widget.config.op);
              if (!started) {
                return;
              }
            } catch (e) {
              await _handleAuthFailure(
                authAttempt,
                e,
                'start account authentication',
              );
              return;
            }
            if (!mounted ||
                authAttempt != _authAttempt ||
                widget.curOP.value != widget.config.op) {
              return;
            }
            _beginQueryState(authAttempt);
          },
        ),
        Obx(() {
          if (widget.curOP.isNotEmpty &&
              widget.curOP.value != widget.config.op) {
            _failedMsg = '';
          }
          final authAttempt = _authAttempt;
          final authUrl = _url;
          return Offstage(
            offstage:
                _failedMsg.isEmpty && widget.curOP.value != widget.config.op,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                if (_stateMsg.isNotEmpty && _failedMsg.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: OidcAuthStatus(
                      message: translate(_stateMsg),
                      browserFallbackPrompt: translate(
                        "Browser didn't open? Use the url below to sign in.",
                      ),
                      authUrl: authUrl,
                      copyLabel: translate('Copy to clipboard'),
                      onCopy: authUrl.isEmpty
                          ? null
                          : () => _runCurrentAuthUrlAction(
                                authAttempt,
                                authUrl,
                                _copyAuthUrl,
                              ),
                    ),
                  ),
                if (_failedMsg.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: Builder(builder: (context) {
                      final errorColor = Theme.of(context).colorScheme.error;
                      final bgColor = Theme.of(context)
                          .colorScheme
                          .errorContainer
                          .withOpacity(0.3);
                      return Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8.0, vertical: 6.0),
                        decoration: BoxDecoration(
                          color: bgColor,
                          borderRadius: BorderRadius.circular(4.0),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.error_outline,
                                color: errorColor, size: 16),
                            const SizedBox(width: 6),
                            Flexible(
                              child: SelectableText(
                                translate(_failedMsg),
                                style:
                                    DefaultTextStyle.of(context).style.copyWith(
                                          fontSize: 13,
                                          color: errorColor,
                                        ),
                              ),
                            ),
                          ],
                        ),
                      );
                    }),
                  ),
              ],
            ),
          );
        }),
      ],
    );
  }
}

class LoginWidgetOP extends StatelessWidget {
  final List<ConfigOP> ops;
  final RxString curOP;
  final Function(Map<String, dynamic>) cbLogin;
  final Future<bool> Function(String) startAuth;
  final Future<bool> Function(String) cancelAuth;
  final bool Function() canStartAuth;

  LoginWidgetOP({
    Key? key,
    required this.ops,
    required this.curOP,
    required this.cbLogin,
    required this.startAuth,
    required this.cancelAuth,
    required this.canStartAuth,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    var children = ops
        .map((op) => [
              WidgetOP(
                config: op,
                curOP: curOP,
                cbLogin: cbLogin,
                startAuth: startAuth,
                cancelAuth: cancelAuth,
                canStartAuth: canStartAuth,
              ),
              const Divider(
                indent: 5,
                endIndent: 5,
              )
            ])
        .expand((i) => i)
        .toList();
    if (children.isNotEmpty) {
      children.removeLast();
    }
    return SingleChildScrollView(
        child: Container(
            width: 200,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: children,
            )));
  }
}

class LoginWidgetUserPass extends StatelessWidget {
  final TextEditingController username;
  final TextEditingController pass;
  final String? usernameTitle;
  final Widget? usernamePrefixIcon;
  final String? usernameMsg;
  final String? passMsg;
  final bool isInProgress;
  final RxString curOP;
  final Function() onLogin;
  final FocusNode? userFocusNode;
  const LoginWidgetUserPass({
    Key? key,
    this.userFocusNode,
    required this.username,
    required this.pass,
    this.usernameTitle,
    this.usernamePrefixIcon,
    required this.usernameMsg,
    required this.passMsg,
    required this.isInProgress,
    required this.curOP,
    required this.onLogin,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Padding(
        padding: EdgeInsets.all(0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DialogTextField(
                title: usernameTitle ?? translate(DialogTextField.kUsernameTitle),
                controller: username,
                focusNode: userFocusNode,
                prefixIcon: usernamePrefixIcon ?? DialogTextField.kUsernameIcon,
                keyboardType: TextInputType.emailAddress,
                errorText: usernameMsg),
            const SizedBox(height: 4.0),
            PasswordWidget(
              controller: pass,
              autoFocus: false,
              reRequestFocus: false,
              errorText: passMsg,
            ),
            // NOT use Offstage to wrap LinearProgressIndicator
            if (isInProgress) const LinearProgressIndicator(),
            const SizedBox(height: 12.0),
            SizedBox(
              height: 44,
              width: double.infinity,
              child: Obx(() => ElevatedButton(
                    child: Text(
                      translate('Login'),
                      style: const TextStyle(fontSize: 16),
                    ),
                    onPressed: curOP.value.isEmpty && !isInProgress
                        ? () {
                            onLogin();
                          }
                        : null,
                  )),
            ),
          ],
        ));
  }
}

class _ThirdPartyLoginButtons extends StatelessWidget {
  const _ThirdPartyLoginButtons({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        const SizedBox(height: 8),
        Row(
          children: [
            const Expanded(child: Divider()),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                '其他登录方式',
                style: TextStyle(
                  fontSize: 12,
                  color: Theme.of(context).textTheme.bodySmall?.color,
                ),
              ),
            ),
            const Expanded(child: Divider()),
          ],
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: 40,
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: () => showToast('微信登录即将上线，敬请期待'),
            icon: const Icon(Icons.chat_bubble_outline, size: 18),
            label: const Text('微信登录（预留）'),
          ),
        ),
      ],
    );
  }
}

bool _isValidEmail(String email) =>
    RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(email.trim());

/// 邮箱+验证码+密码 的通用表单（注册 / 忘记密码 / 更换绑定邮箱共用）。
/// 验证码发送按钮自带 60 秒倒计时，倒计时状态由本组件内部管理，安全销毁。
class _AccountForm extends StatefulWidget {
  final String emailPurpose; // register | reset | change_email
  final bool needCurrentPassword;
  final bool needNewPassword;
  final bool needConfirmPassword;
  final String emailTitle;
  final String? emailHelperText;
  final String newPasswordLabel;
  final String submitLabel;
  final Future<String?> Function({
    required String email,
    required String code,
    required String password,
  }) onSubmit;
  final VoidCallback onSuccess;

  const _AccountForm({
    Key? key,
    required this.emailPurpose,
    this.needCurrentPassword = false,
    this.needNewPassword = true,
    this.needConfirmPassword = true,
    this.emailTitle = 'Email',
    this.emailHelperText,
    required this.newPasswordLabel,
    required this.submitLabel,
    required this.onSubmit,
    required this.onSuccess,
  }) : super(key: key);

  @override
  State<_AccountForm> createState() => _AccountFormState();
}

class _AccountFormState extends State<_AccountForm> {
  final _email = TextEditingController();
  final _code = TextEditingController();
  final _currentPassword = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _emailFocusNode = FocusNode();

  String? _emailError;
  String? _codeError;
  String? _currentPasswordError;
  String? _passwordError;
  String? _confirmError;
  bool _isInProgress = false;
  bool _sendingCode = false;
  int _countdown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    _email.dispose();
    _code.dispose();
    _currentPassword.dispose();
    _password.dispose();
    _confirm.dispose();
    _emailFocusNode.dispose();
    super.dispose();
  }

  Future<void> _sendCode() async {
    final email = _email.text.trim();
    if (!_isValidEmail(email)) {
      setState(() => _emailError = '请输入正确的邮箱地址');
      return;
    }
    setState(() {
      _sendingCode = true;
      _emailError = null;
      _codeError = null;
    });
    try {
      await gFFI.userModel.sendCode(email, widget.emailPurpose);
      if (!mounted) return;
      setState(() {
        _sendingCode = false;
        _countdown = 60;
      });
      _timer = Timer.periodic(const Duration(seconds: 1), (t) {
        if (!mounted) {
          t.cancel();
          return;
        }
        setState(() {
          _countdown--;
          if (_countdown <= 0) {
            t.cancel();
            _countdown = 0;
          }
        });
      });
    } on RequestException catch (err) {
      if (!mounted) return;
      setState(() {
        _sendingCode = false;
        _codeError = translate(err.cause);
      });
    } catch (err) {
      debugPrint('send code failed: $err');
      if (!mounted) return;
      setState(() {
        _sendingCode = false;
        _codeError = '网络错误，请检查网络后重试';
      });
    }
  }

  Future<void> _submit() async {
    if (_isInProgress) {
      return;
    }
    final email = _email.text.trim();
    final code = _code.text.trim();
    final currentPassword = _currentPassword.text;
    final password = _password.text;
    final confirm = _confirm.text;
    if (!_isValidEmail(email)) {
      setState(() => _emailError = '请输入正确的邮箱地址');
      return;
    }
    if (widget.needCurrentPassword && currentPassword.isEmpty) {
      setState(() => _currentPasswordError = '请输入当前密码');
      return;
    }
    if (code.isEmpty) {
      setState(() => _codeError = '请输入验证码');
      return;
    }
    if (widget.needNewPassword && password.length < 4) {
      setState(() => _passwordError = '密码至少 4 位');
      return;
    }
    if (widget.needConfirmPassword && password != confirm) {
      setState(() => _confirmError = '两次输入的密码不一致');
      return;
    }
    setState(() {
      _isInProgress = true;
      _emailError = null;
      _codeError = null;
      _currentPasswordError = null;
      _passwordError = null;
      _confirmError = null;
    });
    try {
      final error = await widget.onSubmit(
        email: email,
        code: code,
        password: widget.needCurrentPassword ? currentPassword : password,
      );
      if (!mounted) return;
      if (error == null) {
        widget.onSuccess();
        return;
      }
      setState(() {
        _isInProgress = false;
        _codeError = error;
      });
    } on RequestException catch (err) {
      if (!mounted) return;
      setState(() {
        _isInProgress = false;
        _codeError = translate(err.cause);
      });
    } catch (err) {
      debugPrint('account form submit failed: $err');
      if (!mounted) return;
      setState(() {
        _isInProgress = false;
        _codeError = '网络错误，请检查网络后重试';
      });
    }
  }

  Widget _buildSendCodeButton() {
    final enabled = !_sendingCode && _countdown == 0;
    return SizedBox(
      height: 48,
      child: ElevatedButton(
        style: ElevatedButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 12),
        ),
        onPressed: enabled ? _sendCode : null,
        child: Text(
          _sendingCode
              ? translate('Waiting')
              : _countdown > 0
                  ? '${_countdown}s'
                  : translate('Send code'),
          style: const TextStyle(fontSize: 13),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DialogTextField(
          title: translate(widget.emailTitle),
          controller: _email,
          focusNode: _emailFocusNode,
          prefixIcon: const Icon(Icons.email_outlined),
          keyboardType: TextInputType.emailAddress,
          helperText: widget.emailHelperText,
          errorText: _emailError,
        ),
        if (widget.needCurrentPassword)
          DialogTextField(
            title: translate('Current password'),
            controller: _currentPassword,
            obscureText: true,
            prefixIcon: DialogTextField.kPasswordIcon,
            errorText: _currentPasswordError,
          ),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: DialogTextField(
                title: translate('Verification code'),
                controller: _code,
                keyboardType: TextInputType.number,
                prefixIcon: const Icon(Icons.verified_outlined),
                errorText: _codeError,
              ),
            ),
            const SizedBox(width: 8),
            _buildSendCodeButton(),
          ],
        ),
        if (widget.needNewPassword)
          DialogTextField(
            title: translate(widget.newPasswordLabel),
            controller: _password,
            obscureText: true,
            prefixIcon: DialogTextField.kPasswordIcon,
            errorText: _passwordError,
          ),
        if (widget.needConfirmPassword)
          DialogTextField(
            title: translate('Confirm password'),
            controller: _confirm,
            obscureText: true,
            prefixIcon: DialogTextField.kPasswordIcon,
            errorText: _confirmError,
          ),
        // NOT use Offstage to wrap LinearProgressIndicator
        if (_isInProgress) const LinearProgressIndicator(),
        const SizedBox(height: 8),
        SizedBox(
          height: 44,
          child: ElevatedButton(
            child: Text(
              translate(widget.submitLabel),
              style: const TextStyle(fontSize: 16),
            ),
            onPressed: _isInProgress ? null : _submit,
          ),
        ),
      ],
    );
  }
}

const kAuthReqTypeOidc = 'oidc/';

Future<bool?>? _activeLoginDialog;

// call this directly
Future<bool?> loginDialog() {
  final activeDialog = _activeLoginDialog;
  if (activeDialog != null) {
    return activeDialog;
  }
  final dialog = _openLoginDialogOnce();
  _activeLoginDialog = dialog;
  return dialog;
}

Future<bool?> _openLoginDialogOnce() async {
  try {
    return await _openLoginDialog();
  } finally {
    _activeLoginDialog = null;
  }
}

Future<bool?> _openLoginDialog() async {
  var username =
      TextEditingController(text: UserModel.getLocalUserInfo()?['name'] ?? '');
  var password = TextEditingController();
  final userFocusNode = FocusNode();

  String? usernameMsg;
  String? passwordMsg;
  var isInProgress = false;
  final oidcAuth = _OidcAuthController();
  final curOP = oidcAuth.curOP;
  // Track hover state for the close icon
  bool isCloseHovered = false;

  final res = await gFFI.dialogManager.show<bool>((setState, close, context) {
    username.addListener(() {
      if (usernameMsg != null) {
        setState(() => usernameMsg = null);
      }
    });

    password.addListener(() {
      if (passwordMsg != null) {
        setState(() => passwordMsg = null);
      }
    });

    onDialogCancel() {
      isInProgress = false;
      close(false);
    }

    handleLoginResponse(LoginResponse resp, bool storeIfAccessToken,
        void Function([dynamic])? close) async {
      switch (resp.type) {
        case HttpType.kAuthResTypeToken:
          if (resp.access_token != null) {
            if (storeIfAccessToken) {
              await bind.mainSetLocalOption(
                  key: 'access_token', value: resp.access_token!);
              await bind.mainSetLocalOption(
                  key: 'user_info', value: jsonEncode(resp.user ?? {}));
            }
            if (close != null) {
              close(true);
            }
            return;
          }
          break;
        case HttpType.kAuthResTypeEmailCheck:
          bool? isEmailVerification;
          if (resp.tfa_type == null ||
              resp.tfa_type == HttpType.kAuthResTypeEmailCheck) {
            isEmailVerification = true;
          } else if (resp.tfa_type == HttpType.kAuthResTypeTfaCheck) {
            isEmailVerification = false;
          } else {
            passwordMsg = "Failed, bad tfa type from server";
          }
          if (isEmailVerification != null) {
            if (isMobile) {
              if (close != null) close(null);
              verificationCodeDialog(
                  resp.user, resp.secret, isEmailVerification);
            } else {
              setState(() => isInProgress = false);
              // Workaround for web, close the dialog first, then show the verification code dialog.
              // Otherwise, the text field will keep selecting the text and we can't input the code.
              // Not sure why this happens.
              if (isWeb && close != null) close(null);
              final res = await verificationCodeDialog(
                  resp.user, resp.secret, isEmailVerification);
              if (res == true) {
                if (!isWeb && close != null) close(false);
                return;
              }
            }
          }
          break;
        default:
          passwordMsg = "Failed, bad response from server";
          break;
      }
    }

    onLogin() async {
      if (curOP.value.isNotEmpty || isInProgress) {
        return;
      }
      // validate
      if (username.text.isEmpty) {
        setState(() => usernameMsg = translate('Username missed'));
        return;
      }
      if (password.text.isEmpty) {
        setState(() => passwordMsg = translate('Password missed'));
        return;
      }
      curOP.value = 'rustdesk';
      setState(() => isInProgress = true);
      try {
        final resp = await gFFI.userModel.login(LoginRequest(
            username: username.text,
            password: password.text,
            id: await bind.mainGetMyId(),
            uuid: await bind.mainGetUuid(),
            autoLogin: true,
            type: HttpType.kAuthReqTypeAccount));
        await handleLoginResponse(resp, true, close);
      } on RequestException catch (err) {
        passwordMsg = translate(err.cause);
      } catch (err) {
        passwordMsg = "Unknown Error: $err";
      }
      curOP.value = '';
      setState(() => isInProgress = false);
    }

    thirdAuthWidget() => const _ThirdPartyLoginButtons();

    final title = Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          translate('Login'),
        ).marginOnly(top: MyTheme.dialogPadding),
        MouseRegion(
          onEnter: (_) => setState(() => isCloseHovered = true),
          onExit: (_) => setState(() => isCloseHovered = false),
          child: InkWell(
            child: Icon(
              Icons.close,
              size: 25,
              // No need to handle the branch of null.
              // Because we can ensure the color is not null when debug.
              color: isCloseHovered
                  ? Colors.white
                  : Theme.of(context)
                      .textTheme
                      .titleLarge
                      ?.color
                      ?.withOpacity(0.55),
            ),
            onTap: onDialogCancel,
            hoverColor: Colors.red,
            borderRadius: BorderRadius.circular(5),
          ),
        ).marginOnly(top: 10, right: 15),
      ],
    );
    final titlePadding = EdgeInsets.fromLTRB(MyTheme.dialogPadding, 0, 0, 0);

    return CustomAlertDialog(
      title: title,
      titlePadding: titlePadding,
      contentBoxConstraints: BoxConstraints(minWidth: 400),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          LoginWidgetUserPass(
            username: username,
            pass: password,
            usernameTitle: translate('Email'),
            usernamePrefixIcon: Icon(Icons.email_outlined),
            usernameMsg: usernameMsg,
            passMsg: passwordMsg,
            isInProgress: isInProgress,
            curOP: curOP,
            onLogin: onLogin,
            userFocusNode: userFocusNode,
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              TextButton(
                style: TextButton.styleFrom(
                  foregroundColor: Theme.of(context).colorScheme.primary,
                ),
                onPressed: isInProgress || curOP.value.isNotEmpty
                    ? null
                    : () async {
                        final registered = await registerDialog();
                        if (registered == true) {
                          close(true);
                        }
                      },
                child: Text(translate('Register')),
              ),
              TextButton(
                style: TextButton.styleFrom(
                  foregroundColor: Theme.of(context).colorScheme.primary,
                ),
                onPressed: isInProgress || curOP.value.isNotEmpty
                    ? null
                    : () {
                        forgotPasswordDialog();
                      },
                child: Text(translate('Forget Password')),
              ),
            ],
          ),
          thirdAuthWidget(),
        ],
      ),
      onCancel: onDialogCancel,
      onSubmit: onLogin,
    );
  }, clickMaskDismiss: true, backDismiss: true).whenComplete(oidcAuth.close);

  if (res != null) {
    await UserModel.updateOtherModels();
  }

  return res;
}

Widget _accountDialogTitle(
    BuildContext context, String title, VoidCallback onClose) {
  return Row(
    mainAxisAlignment: MainAxisAlignment.spaceBetween,
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(title).marginOnly(top: MyTheme.dialogPadding),
      InkWell(
        onTap: onClose,
        hoverColor: Colors.red,
        borderRadius: BorderRadius.circular(5),
        child: Padding(
          padding: const EdgeInsets.all(6),
          child: Icon(
            Icons.close,
            size: 25,
            color: Theme.of(context)
                .textTheme
                .titleLarge
                ?.color
                ?.withOpacity(0.55),
          ),
        ),
      ).marginOnly(top: 10, right: 15),
    ],
  );
}

Future<bool?> registerDialog() async {
  final res = await gFFI.dialogManager.show<bool>((setState, close, context) {
    return CustomAlertDialog(
      title: _accountDialogTitle(context, translate('Register'), () => close()),
      contentBoxConstraints: BoxConstraints(maxWidth: 340),
      content: _AccountForm(
        emailPurpose: 'register',
        newPasswordLabel: 'Password',
        needConfirmPassword: true,
        submitLabel: 'Register',
        onSubmit: ({required email, required code, required password}) async {
          final resp = await gFFI.userModel.register(LoginRequest(
              username: email,
              password: password,
              code: code,
              id: await bind.mainGetMyId(),
              uuid: await bind.mainGetUuid(),
              autoLogin: true,
              type: HttpType.kAuthReqTypeAccount));
          if (resp.access_token != null) {
            await bind.mainSetLocalOption(
                key: 'access_token', value: resp.access_token!);
            await bind.mainSetLocalOption(
                key: 'user_info', value: jsonEncode(resp.user ?? {}));
            return null;
          }
          return '注册失败，请重试';
        },
        onSuccess: () {
          showToast('注册成功，已自动登录');
          close(true);
        },
      ),
      onCancel: close,
    );
  }, clickMaskDismiss: true, backDismiss: true);
  return res;
}

Future<bool?> forgotPasswordDialog() async {
  final res = await gFFI.dialogManager.show<bool>((setState, close, context) {
    return CustomAlertDialog(
      title: _accountDialogTitle(
          context, translate('Forget Password'), () => close()),
      contentBoxConstraints: BoxConstraints(maxWidth: 340),
      content: _AccountForm(
        emailPurpose: 'reset',
        newPasswordLabel: 'New Password',
        needConfirmPassword: true,
        submitLabel: 'Reset',
        onSubmit: ({required email, required code, required password}) async {
          await gFFI.userModel.resetPassword(email, code, password);
          return null;
        },
        onSuccess: () {
          showToast('密码已重置，请使用新密码登录');
          close(true);
        },
      ),
      onCancel: close,
    );
  }, clickMaskDismiss: true, backDismiss: true);
  return res;
}

Future<bool?> changeEmailDialog() async {
  final res = await gFFI.dialogManager.show<bool>((setState, close, context) {
    return CustomAlertDialog(
      title:
          _accountDialogTitle(context, translate('Change email'), () => close()),
      contentBoxConstraints: BoxConstraints(maxWidth: 340),
      content: _AccountForm(
        emailPurpose: 'change_email',
        needCurrentPassword: true,
        needNewPassword: false,
        needConfirmPassword: false,
        emailTitle: 'New Email',
        emailHelperText: '验证码将发送到新邮箱',
        newPasswordLabel: 'Password',
        submitLabel: 'Confirm',
        onSubmit: ({required email, required code, required password}) async {
          await gFFI.userModel.changeEmail(
              newEmail: email, code: code, password: password);
          return null;
        },
        onSuccess: () {
          showToast('邮箱已更换成功');
          close(true);
        },
      ),
      onCancel: close,
    );
  }, clickMaskDismiss: true, backDismiss: true);
  return res;
}

Future<bool?> verificationCodeDialog(
    UserPayload? user, String? secret, bool isEmailVerification) async {
  var autoLogin = true;
  var isInProgress = false;
  String? errorText;

  final code = TextEditingController();

  final res = await gFFI.dialogManager.show<bool>((setState, close, context) {
    void onVerify() async {
      setState(() => isInProgress = true);

      try {
        final resp = await gFFI.userModel.login(LoginRequest(
            verificationCode: code.text,
            tfaCode: isEmailVerification ? null : code.text,
            secret: secret,
            username: user?.name,
            id: await bind.mainGetMyId(),
            uuid: await bind.mainGetUuid(),
            autoLogin: autoLogin,
            type: HttpType.kAuthReqTypeEmailCode));

        switch (resp.type) {
          case HttpType.kAuthResTypeToken:
            if (resp.access_token != null) {
              await bind.mainSetLocalOption(
                  key: 'access_token', value: resp.access_token!);
              close(true);
              return;
            }
            break;
          default:
            errorText = "Failed, bad response from server";
            break;
        }
      } on RequestException catch (err) {
        errorText = translate(err.cause);
      } catch (err) {
        errorText = "Unknown Error: $err";
      }

      setState(() => isInProgress = false);
    }

    final codeField = isEmailVerification
        ? DialogEmailCodeField(
            controller: code,
            errorText: errorText,
            readyCallback: onVerify,
            onChanged: () => errorText = null,
          )
        : Dialog2FaField(
            controller: code,
            errorText: errorText,
            readyCallback: onVerify,
            onChanged: () => errorText = null,
          );

    getOnSubmit() => codeField.isReady ? onVerify : null;

    return CustomAlertDialog(
        title: Text(translate("Verification code")),
        contentBoxConstraints: BoxConstraints(maxWidth: 300),
        content: Column(
          children: [
            Offstage(
                offstage: !isEmailVerification || user?.email == null,
                child: TextField(
                  decoration: InputDecoration(
                      labelText: "Email", prefixIcon: Icon(Icons.email)),
                  readOnly: true,
                  controller: TextEditingController(text: user?.email),
                ).workaroundFreezeLinuxMint()),
            isEmailVerification ? const SizedBox(height: 8) : const Offstage(),
            codeField,
            /*
            CheckboxListTile(
              contentPadding: const EdgeInsets.all(0),
              dense: true,
              controlAffinity: ListTileControlAffinity.leading,
              title: Row(children: [
                Expanded(child: Text(translate("Trust this device")))
              ]),
              value: trustThisDevice,
              onChanged: (v) {
                if (v == null) return;
                setState(() => trustThisDevice = !trustThisDevice);
              },
            ),
            */
            // NOT use Offstage to wrap LinearProgressIndicator
            if (isInProgress) const LinearProgressIndicator(),
          ],
        ),
        onCancel: close,
        onSubmit: getOnSubmit(),
        actions: [
          dialogButton("Cancel", onPressed: close, isOutline: true),
          dialogButton("Verify", onPressed: getOnSubmit()),
        ]);
  });
  // For verification code, desktop update other models in login dialog, mobile need to close login dialog first,
  // otherwise the soft keyboard will jump out on each key press, so mobile update in verification code dialog.
  if (isMobile && res == true) {
    await UserModel.updateOtherModels();
  }

  return res;
}

void logOutConfirmDialog() {
  gFFI.dialogManager.show((setState, close, context) {
    submit() {
      close();
      gFFI.userModel.logOut();
    }

    return CustomAlertDialog(
      content: Text(translate("logout_tip")),
      actions: [
        dialogButton(translate("Cancel"), onPressed: close, isOutline: true),
        dialogButton(translate("OK"), onPressed: submit),
      ],
      onSubmit: submit,
      onCancel: close,
    );
  });
}
