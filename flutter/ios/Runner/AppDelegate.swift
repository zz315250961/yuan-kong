import UIKit
import Flutter

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    GeneratedPluginRegistrant.register(with: self)
    dummyMethodToEnforceBundling();
    #if targetEnvironment(simulator)
    // CI waits for an actual Flutter frame, rather than accepting a launch PID
    // while asynchronous Rust initialization might still be stuck.
    if let controller = window?.rootViewController as? FlutterViewController {
      controller.setFlutterViewDidRenderCallback {
        NSLog("LINKREMOTE_SMOKE_FIRST_FRAME")
      }
    }
    #endif
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
    
  public func dummyMethodToEnforceBundling() {
      dummy_method_to_enforce_bundling();
    session_get_rgba(nil, 0);
  }
}
