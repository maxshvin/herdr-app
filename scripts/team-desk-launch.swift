// Finder and CLI launches use the same private app profile and core.
import Foundation
import Darwin

let profile = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/herdr-desk-dev")
let root = profile.path
let bundle = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Team Desk Dev.app")
let core = CommandLine.arguments.dropFirst().first == "--dev-core"
let executable = core ? root + "/libexec/hc" : bundle.appendingPathComponent("Contents/MacOS/Renderer").path
let env = ProcessInfo.processInfo.environment
let privateCaller = env["HC_SOCKET"] == root + "/run/dev.sock" && env["HC_SESSION"] == "desk-dev"
for key in env.keys where key.hasPrefix("HC_") && !privateCaller {
    unsetenv(key)
}
let isolated = [
    "HERDR_APP_HOME": root, "HC_SESSION": "desk-dev", "HC_SOCKET": root + "/run/dev.sock",
    "XDG_RUNTIME_DIR": root + "/run", "XDG_STATE_HOME": root + "/state", "XDG_CONFIG_HOME": root + "/config",
    "HERDR_CORE_DIRS": root + "/bin", "HERDR_RELEASES_URL": "file:///dev/null",
    "CLAUDE_CONFIG_DIR": root + "/providers/claude", "CODEX_HOME": root + "/providers/codex",
    "PATH": root + "/bin:" + (getenv("PATH").map { String(cString: $0) } ?? "/usr/bin:/bin")
]
for (key, value) in isolated { setenv(key, value, 1) }
let arguments = ([executable] + CommandLine.arguments.dropFirst(core ? 2 : 1)).map { strdup($0) } + [nil]
arguments.withUnsafeBufferPointer { buffer in
    _ = execv(executable, buffer.baseAddress!)
}
perror("Team Desk Dev renderer")
exit(1)
