//
//  ViewController.swift
//  Shared (App)
//
//  Created by Jon Onulak on 10/28/26.
//

import WebKit
import SafariServices

#if os(iOS)
import UIKit
typealias PlatformViewController = UIViewController
#elseif os(macOS)
import Cocoa
typealias PlatformViewController = NSViewController
#endif

let extensionBundleIdentifier = "com.whitebridgelabs.ffl-cookie-spike.Extension"
let espnFantasyURL = URL(string: "https://www.espn.com/fantasy/")!

class ViewController: PlatformViewController, WKNavigationDelegate, WKScriptMessageHandler {

    @IBOutlet var webView: WKWebView!

    override func viewDidLoad() {
        super.viewDidLoad()

        self.webView.navigationDelegate = self
        self.webView.configuration.userContentController.add(self, name: "controller")
        self.webView.loadFileURL(Bundle.main.url(forResource: "Main", withExtension: "html")!, allowingReadAccessTo: Bundle.main.resourceURL!)

#if os(iOS)
        // Back from Settings: show whether the extension is on now.
        NotificationCenter.default.addObserver(self, selector: #selector(refresh), name: UIApplication.didBecomeActiveNotification, object: nil)
#endif
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        refresh()
#if DEBUG && os(iOS)
        // Recording the setup clips (ffl-soc.6.3.9): the simulator's UI automation can't tap inside the web view, so a
        // launch argument presses the page's buttons. Debug builds only.
        let args = ProcessInfo.processInfo.arguments
        for action in ["open-settings", "open-espn"] where args.contains("-\(action)") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.act(action) }
        }
#endif
    }

    /// Tell the page the extension's state, and whether Settings can be opened at the extension itself.
    @objc func refresh() {
#if os(iOS)
        if #available(iOS 26.2, *) {
            SFSafariExtensionManager.getStateOfExtension(withIdentifier: extensionBundleIdentifier) { state, error in
                DispatchQueue.main.async {
                    let enabled = (state != nil && error == nil) ? String(state!.isEnabled) : "undefined"
                    self.webView.evaluateJavaScript("show('ios', \(enabled), true)")
                }
            }
        } else {
            webView.evaluateJavaScript("show('ios', undefined, false)")
        }
#elseif os(macOS)
        SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: extensionBundleIdentifier) { state, error in
            DispatchQueue.main.async {
                let enabled = (state != nil && error == nil) ? String(state!.isEnabled) : "undefined"
                self.webView.evaluateJavaScript("show('mac', \(enabled), true)")
            }
        }
#endif
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let action = message.body as? String else { return }
        act(action)
    }

    /// A button on the page: open-settings / open-espn (iOS), open-preferences (macOS).
    func act(_ action: String) {
#if os(iOS)
        switch action {
        case "open-settings":
            if #available(iOS 26.2, *) {
                // Settings › Apps › Safari › Extensions › this extension (one identifier: its detail view)
                // iOS limits how often an app may do this (SFErrorMaximumAttemptsExceeded); refused, the page names the
                // path by hand instead. The app's own Settings page is no fallback: it has no link to the extension.
                SFSafariSettings.openExtensionsSettings(forIdentifiers: [extensionBundleIdentifier]) { error in
                    guard error != nil else { return }
                    NSLog("openExtensionsSettings refused: %@", String(describing: error!))
                    self.webView.evaluateJavaScript("cannotOpen()")
                }
            }
        case "open-espn":
            UIApplication.shared.open(espnFantasyURL)
        default:
            break
        }
#elseif os(macOS)
        guard action == "open-preferences" else { return }
        SFSafariApplication.showPreferencesForExtension(withIdentifier: extensionBundleIdentifier) { error in
            guard error == nil else { return }
            DispatchQueue.main.async {
                NSApp.terminate(self)
            }
        }
#endif
    }

}
