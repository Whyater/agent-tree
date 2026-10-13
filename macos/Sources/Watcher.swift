import Foundation
import CoreServices

/// Watches the agent tools' log folders with FSEvents and calls `onChange` (debounced)
/// when something relevant is written. Only paths are inspected; file contents are never read.
final class LogWatcher {
    private var stream: FSEventStreamRef?
    private var debounce: DispatchWorkItem?
    private let delay: TimeInterval
    var onChange: () -> Void = {}

    init(debounce: TimeInterval = 2) {
        delay = debounce
    }

    deinit { stop() }

    /// Folders to watch. The T3 Code database lives next to other files, so its folder is
    /// watched and events are filtered down to the database's write-ahead log.
    static var targets: [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return [home + "/.claude/projects", home + "/.codex/sessions", home + "/.codex/archived_sessions", home + "/.t3/userdata"]
    }

    static func isRelevant(_ path: String) -> Bool {
        if path.contains("/tool-results/") { return false }
        if path.contains("/.t3/userdata/") || path.hasSuffix("/.t3/userdata") {
            return path.hasSuffix("state.sqlite-wal") || path.hasSuffix("state.sqlite")
        }
        return true
    }

    func start() {
        stop()
        let paths = Self.targets.filter { FileManager.default.fileExists(atPath: $0) }
        guard !paths.isEmpty else { return }
        var ctx = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(),
                                       retain: nil, release: nil, copyDescription: nil)
        let flags = UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer | kFSEventStreamCreateFlagUseCFTypes)
        let callback: FSEventStreamCallback = { _, info, count, eventPaths, _, _ in
            guard let info = info else { return }
            let watcher = Unmanaged<LogWatcher>.fromOpaque(info).takeUnretainedValue()
            let arr = Unmanaged<CFArray>.fromOpaque(eventPaths).takeUnretainedValue() as? [String] ?? []
            if arr.prefix(count).contains(where: LogWatcher.isRelevant) { watcher.fire() }
        }
        guard let s = FSEventStreamCreate(kCFAllocatorDefault, callback, &ctx, paths as CFArray,
                                          FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 1.0, flags) else { return }
        FSEventStreamSetDispatchQueue(s, DispatchQueue.main)
        FSEventStreamStart(s)
        stream = s
    }

    func stop() {
        debounce?.cancel()
        guard let s = stream else { return }
        FSEventStreamStop(s)
        FSEventStreamInvalidate(s)
        FSEventStreamRelease(s)
        stream = nil
    }

    private func fire() {
        debounce?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.onChange() }
        debounce = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }
}
