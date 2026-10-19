import AVFoundation
import Flutter
import UIKit

/// Audio-session policy only. PCM capture and Opus transport stay in Rust;
/// this class never records before explicit permission and a call request.
final class LinkRemoteVoiceSession {
  private let channel: FlutterMethodChannel
  private let audio = AVAudioSession.sharedInstance()
  private var owner: String?
  private var generation: UInt64 = 0
  private var pendingResult: FlutterResult?
  private var previous: (AVAudioSession.Category, AVAudioSession.Mode, AVAudioSession.CategoryOptions)?
  private var observers: [NSObjectProtocol] = []

  init(messenger: FlutterBinaryMessenger) {
    channel = FlutterMethodChannel(name: "linkremote/voice", binaryMessenger: messenger)
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self = self else {
        result(FlutterError(code: "unavailable", message: "Voice session unavailable", details: nil))
        return
      }
      switch call.method {
      case "prepare":
        guard let arguments = call.arguments as? [String: Any],
              let key = arguments["owner"] as? String, !key.isEmpty, key.count <= 256 else {
          result(FlutterError(code: "invalid_owner", message: "Invalid voice owner", details: nil))
          return
        }
        self.prepare(owner: key, result: result)
      case "end":
        let key = (call.arguments as? [String: Any])?["owner"] as? String
        if let key = key, key == self.owner {
          self.end(reason: nil, deactivate: UIApplication.shared.applicationState == .background)
        }
        result(nil)
      case "isSupported":
        result(true)
      default:
        result(FlutterMethodNotImplemented)
      }
    }
    let center = NotificationCenter.default
    observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification,
      object: audio, queue: .main) { [weak self] notification in
      if let value = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
         AVAudioSession.InterruptionType(rawValue: value) == .began {
        self?.end(reason: "audio_interrupted", deactivate: true)
      }
    })
    observers.append(center.addObserver(forName: AVAudioSession.routeChangeNotification,
      object: audio, queue: .main) { [weak self] notification in
      if let value = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
         AVAudioSession.RouteChangeReason(rawValue: value) == .oldDeviceUnavailable {
        // Never silently switch a private headset call to room microphone/speaker.
        self?.end(reason: "audio_route_removed", deactivate: true)
      }
    })
    observers.append(center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification,
      object: audio, queue: .main) { [weak self] _ in
      self?.end(reason: "audio_services_reset", deactivate: true)
    })
    observers.append(center.addObserver(forName: UIApplication.didEnterBackgroundNotification,
      object: nil, queue: .main) { [weak self] _ in
      self?.end(reason: "background", deactivate: true)
    })
  }

  private func prepare(owner key: String, result: @escaping FlutterResult) {
    guard owner == nil else {
      result(FlutterError(code: "busy", message: "A voice call is already preparing or active", details: nil))
      return
    }
    guard UIApplication.shared.applicationState != .background else {
      result(FlutterError(code: "background", message: "Start voice calls in the foreground", details: nil))
      return
    }
    generation &+= 1
    let token = generation
    owner = key
    pendingResult = result
    let permissionResult: (Bool) -> Void = { [weak self] granted in
      DispatchQueue.main.async {
        guard let self = self, self.generation == token, self.owner == key else { return }
        guard granted else {
          self.failPrepare(code: "microphone_denied", message: "Microphone permission denied")
          return
        }
        guard UIApplication.shared.applicationState != .background else {
          self.end(reason: "background", deactivate: true)
          return
        }
        self.previous = (self.audio.category, self.audio.mode, self.audio.categoryOptions)
        do {
          try self.audio.setCategory(.playAndRecord, mode: .voiceChat,
                                    options: [.defaultToSpeaker, .allowBluetooth])
          try self.audio.setActive(true)
          let callback = self.pendingResult
          self.pendingResult = nil
          callback?(true)
        } catch {
          self.failPrepare(code: "audio_session_failed", message: "Could not activate voice audio")
        }
      }
    }
    switch audio.recordPermission {
    case .granted: permissionResult(true)
    case .denied: permissionResult(false)
    case .undetermined: audio.requestRecordPermission(permissionResult)
    @unknown default: permissionResult(false)
    }
  }

  private func failPrepare(code: String, message: String) {
    let callback = pendingResult
    pendingResult = nil
    end(reason: nil, deactivate: false)
    callback?(FlutterError(code: code, message: message, details: nil))
  }

  private func end(reason: String?, deactivate: Bool) {
    guard let key = owner else { return }
    generation &+= 1
    owner = nil
    let callback = pendingResult
    pendingResult = nil
    callback?(FlutterError(code: "cancelled", message: "Voice call preparation cancelled", details: nil))
    if let saved = previous {
      do {
        if deactivate {
          try audio.setActive(false, options: .notifyOthersOnDeactivation)
        }
        try audio.setCategory(saved.0, mode: saved.1, options: saved.2)
        if !deactivate { try audio.setActive(true) }
      } catch {
        // A capture stream may still be stopping in Rust; notify/close rather
        // than silently reacquiring microphone access after an interruption.
        NSLog("LinkRemote voice audio-session cleanup failed: %@", error.localizedDescription)
      }
    }
    previous = nil
    if let reason = reason {
      channel.invokeMethod("interrupted", arguments: ["owner": key, "reason": reason])
    }
  }

  deinit {
    for observer in observers { NotificationCenter.default.removeObserver(observer) }
    channel.setMethodCallHandler(nil)
  }
}
