import AppKit
import WebKit

/// The full dashboard (the same web UI the server serves) in a native window.
@MainActor
final class FullViewWindowController: NSWindowController, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let webView: WKWebView
    private var baseURL: URL?
    private var loadedBase: URL?
    private var pendingHash: String?
    /// Set right before loadHTMLString so exactly that one about:blank load is allowed.
    private var allowLocalHTMLOnce = false
    private var allowLocalResponseOnce = false
    var onClose: (() -> Void)?

    init() {
        let cfg = WKWebViewConfiguration()
        // Tell the dashboard it is inside the app window (it then leaves room for the
        // traffic lights). Appended, so the default WebKit user agent stays intact.
        cfg.applicationNameForUserAgent = [cfg.applicationNameForUserAgent, Self.userAgentToken]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ")
        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.allowsBackForwardNavigationGestures = true
        webView.setValue(false, forKey: "drawsBackground")   // no white flash before the dark UI paints
        webView.underPageBackgroundColor = Self.surface

        // The page runs under a transparent title bar, so the window reads as one surface.
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 820),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                           backing: .buffered, defer: false)
        win.title = "Agent Tree"
        win.titleVisibility = .hidden
        win.titlebarAppearsTransparent = true
        win.minSize = NSSize(width: 760, height: 520)
        win.isReleasedWhenClosed = false
        win.tabbingMode = .disallowed
        win.backgroundColor = Self.surface
        win.center()
        win.setFrameAutosaveName("AgentTreeFullView")
        win.contentView = webView
        super.init(window: win)
        win.delegate = self
        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    required init?(coder: NSCoder) { fatalError() }

    /// Added to the user agent; the dashboard looks for it to style itself as a native window.
    static let userAgentToken = "AgentTreeApp"

    /// The dashboard's page background in dark and light, shown before it paints and
    /// behind overscroll.
    static let surface = NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(red: 0x0E / 255, green: 0x0F / 255, blue: 0x11 / 255, alpha: 1)
            : NSColor(red: 0xF6 / 255, green: 0xF6 / 255, blue: 0xF7 / 255, alpha: 1)
    }

    /// Show the dashboard at a hash route such as "#/thread/cc%3A123".
    func show(base: URL?, hash: String) {
        let base = Loopback.validate(base)
        baseURL = base
        if let base = base {
            if loadedBase != base {
                loadedBase = base
                webView.load(URLRequest(url: url(base, hash)))
            } else {
                let js = "location.hash = \(jsString(hash));"
                webView.evaluateJavaScript(js) { [weak self] _, err in
                    if err != nil, let self = self { self.webView.load(URLRequest(url: self.url(base, hash))) }
                }
            }
        } else {
            loadedBase = nil
            loadLocalHTML(Self.placeholder)
        }
        showWindow(nil)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func loadLocalHTML(_ html: String) {
        allowLocalHTMLOnce = true
        webView.loadHTMLString(html, baseURL: nil)
    }

    func reload() {
        if let base = loadedBase { webView.load(URLRequest(url: url(base, "#/"))) } else { webView.reload() }
    }

    private func url(_ base: URL, _ hash: String) -> URL {
        URL(string: base.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/" + hash) ?? base
    }

    private func jsString(_ s: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [s])
        let arr = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }

    func windowWillClose(_ notification: Notification) { onClose?() }

    // MARK: navigation: only the backend's exact origin (http://127.0.0.1:<port>)

    private func isBackend(_ url: URL?) -> Bool {
        guard let base = loadedBase else { return false }
        return Loopback.sameOrigin(url, base)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        if allowLocalHTMLOnce, action.targetFrame?.isMainFrame == true, url?.absoluteString == "about:blank" {
            allowLocalHTMLOnce = false
            allowLocalResponseOnce = true  // its response arrives next; let exactly that one through
            decisionHandler(.allow)
            return
        }
        guard isBackend(url) else { decisionHandler(.cancel); return }
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if allowLocalResponseOnce, response.isForMainFrame, response.response.url?.absoluteString == "about:blank" {
            allowLocalResponseOnce = false
            decisionHandler(.allow)
            return
        }
        guard isBackend(response.response.url) else { decisionHandler(.cancel); return }
        if let http = response.response as? HTTPURLResponse,
           let cd = http.value(forHTTPHeaderField: "Content-Disposition"), cd.lowercased().hasPrefix("attachment") {
            decisionHandler(.download); return
        }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    // target=_blank / window.open: never a new window. Backend URLs load here; the rest is dropped.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, isBackend(url) { webView.load(URLRequest(url: url)) }
        return nil
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    // MARK: downloads (exports) go to ~/Downloads

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first else {
            completionHandler(nil); return
        }
        // Keep the server-suggested name inside Downloads.
        var name = (suggestedFilename as NSString).lastPathComponent
        if name.isEmpty || name.hasPrefix(".") { name = "agent-tree-export" }
        var dest = dir.appendingPathComponent(name)
        let base = dest.deletingPathExtension().lastPathComponent, ext = dest.pathExtension
        var i = 1
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = dir.appendingPathComponent("\(base) \(i)" + (ext.isEmpty ? "" : ".\(ext)"))
            i += 1
        }
        completionHandler(dest)
    }

    func downloadDidFinish(_ download: WKDownload) {
        // Show the file the way Safari does: bounce it into view in Finder.
        if let url = download.progress.fileURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { showError(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showError(error) }

    private func showError(_ error: Error) {
        let e = error as NSError
        if e.code == NSURLErrorCancelled { return }
        // Our own policy cancels (blocked links) report "frame load interrupted"; ignore them,
        // and anything that wasn't a backend load.
        if e.domain == "WebKitErrorDomain" && e.code == 102 { return }
        if let failing = e.userInfo[NSURLErrorFailingURLErrorKey] as? URL, !isBackend(failing) { return }
        loadedBase = nil
        loadLocalHTML(Self.page(title: "The dashboard is not reachable",
                                body: "The background server did not answer. Use Restart from the menu bar panel, then open the full view again."))
    }

    static let placeholder = page(title: "The full view needs the server",
                                  body: "This copy is running on sample data, so there is no dashboard to show. Set AGENT_TREE_DEV_URL to a running server to preview it.")

    static func page(title: String, body: String) -> String {
        """
        <!doctype html><html><head><meta charset="utf-8"><style>
        :root{color-scheme:light dark}
        html,body{height:100%;margin:0;background:#0e0f11;color:#e8e9ec;font:13px -apple-system,system-ui}
        main{box-sizing:border-box;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:28px 40px 0;text-align:center}
        h1{font-size:15px;font-weight:600;margin:0} p{color:#9a9ca3;font-size:12px;max-width:400px;line-height:1.45;margin:0}
        @media (prefers-color-scheme:light){html,body{background:#f6f6f7;color:#16171a} p{color:#5f636c}}
        </style></head><body><main><h1>\(title)</h1><p>\(body)</p></main></body></html>
        """
    }
}
