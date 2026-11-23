// The existing Team Desk Dock item opens the native workspace in Herdr.
import AppKit

let bundle = "dev.herdr.app"
let cli = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/herdr-app")
func selectTeamDesk() -> Bool {
    let process = Process()
    process.executableURL = cli
    process.arguments = ["do", "layout.switch", "{\"name\":\"team-desk\"}"]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do { try process.run() } catch { return false }
    process.waitUntilExit()
    return process.terminationStatus == 0
}

func showError(_ message: String) -> Never {
    let alert = NSAlert()
    alert.messageText = "Could not open Team Desk"
    alert.informativeText = message
    alert.runModal()
    exit(1)
}

guard FileManager.default.isExecutableFile(atPath: cli.path) else {
    showError("Install the Herdr command-line tool in ~/.local/bin.")
}
if NSRunningApplication.runningApplications(withBundleIdentifier: bundle).isEmpty {
    guard let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else {
        showError("Install Herdr.app and open it once.")
    }
    NSWorkspace.shared.openApplication(at: app, configuration: .init()) { _, error in
        if let error { showError(error.localizedDescription) }
    }
}
for _ in 0..<30 {
    if selectTeamDesk() {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first?.activate(options: [])
        exit(0)
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.25))
}
showError("Check that Herdr is running and scripts/install-team-desk has installed the layout.")
