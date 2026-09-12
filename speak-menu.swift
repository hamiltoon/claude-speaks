// speak-menu: menu bar switch for claude-speaks.
//
// Shows a waveform icon while speech is on and a crossed-out one while it
// is off. The state is the same flag file the hooks read (~/.claude/speak.on),
// so toggling from the terminal with touch/rm is picked up here too.
//
// Build:  swiftc -O -swift-version 5 speak-menu.swift -o build/speak-menu
// Runs as the launch agent se.hamiltoon.speak-menu.

import AppKit

let home = FileManager.default.homeDirectoryForCurrentUser.path
let flagPath = home + "/.claude/speak.on"
let python = home + "/.venvs/kokoro/bin/python"
let speakScript = home + "/.claude/hooks/speak.py"

final class SpeakMenu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    let toggleItem = NSMenuItem(title: "Speak answers", action: #selector(toggle), keyEquivalent: "")
    var lastState: Bool?

    var isOn: Bool { FileManager.default.fileExists(atPath: flagPath) }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        toggleItem.target = self
        menu.addItem(toggleItem)

        let stop = NSMenuItem(title: "Stop speaking now", action: #selector(stopNow), keyEquivalent: "")
        stop.target = self
        menu.addItem(stop)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: ""))
        menu.delegate = self  // refresh the checkmark every time the menu opens
        item.menu = menu

        refresh()
        // Cheap poll so terminal changes to the flag file show up in the icon.
        // .common mode keeps it ticking while a menu is open, too.
        let timer = Timer(timeInterval: 2, target: self, selector: #selector(refresh), userInfo: nil, repeats: true)
        RunLoop.main.add(timer, forMode: .common)
    }

    static func symbol(_ names: [String], _ description: String) -> NSImage? {
        for name in names {
            if let image = NSImage(systemSymbolName: name, accessibilityDescription: description) {
                image.isTemplate = true  // follows light/dark menu bar automatically
                return image
            }
        }
        return nil
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    @objc func refresh() {
        let on = isOn
        if on == lastState { return }
        lastState = on
        toggleItem.state = on ? .on : .off
        item.button?.image = on
            ? SpeakMenu.symbol(["waveform", "speaker.wave.2"], "Speech on")
            : SpeakMenu.symbol(["waveform.slash", "speaker.slash"], "Speech off")
        item.button?.toolTip = on ? "Claude speaks: on" : "Claude speaks: off"
    }

    @objc func toggle() {
        if isOn {
            try? FileManager.default.removeItem(atPath: flagPath)
            stopNow()  // turning off should also silence what is playing now
        } else {
            FileManager.default.createFile(atPath: flagPath, contents: nil)
        }
        refresh()
    }

    @objc func stopNow() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [speakScript, "--stop"]
        try? process.run()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // no Dock icon, no app menu
let delegate = SpeakMenu()
app.delegate = delegate
app.run()
