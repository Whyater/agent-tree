import AppKit

let app = NSApplication.shared
let delegate = MainActor.assumeIsolated { AppDelegate() }
app.delegate = delegate

// Treat SIGTERM (logout, `kill`, launchd) like Quit so the server and temp files are cleaned up.
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler {
    // Clean up directly: NSApp.terminate can be postponed indefinitely by an open sheet.
    MainActor.assumeIsolated { delegate.shutdown() }
    exit(0)
}
sigterm.resume()

app.run()
