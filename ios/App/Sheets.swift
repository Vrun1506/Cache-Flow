import CacheFlowKit
import SwiftUI

/// "I read x² + 5x + 6 = 0" with a tick or Rewrite.
struct ConfirmSheet: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        VStack(spacing: 28) {
            Text("I read your problem as").font(.title3).foregroundStyle(.secondary)
            Text(client.session.problem ?? "").font(.system(size: 48, weight: .semibold, design: .serif))
                .minimumScaleFactor(0.5).lineLimit(1)
            HStack(spacing: 16) {
                Button { client.confirmProblem(false) } label: { Label("Rewrite", systemImage: "pencil") }
                    .buttonStyle(PillButtonStyle())
                Button { client.confirmProblem(true) } label: { Label("Looks right", systemImage: "checkmark") }
                    .buttonStyle(PillButtonStyle(prominent: true))
            }
        }
        .padding(48)
        .presentationDetents([.height(320)])
        .interactiveDismissDisabled()
    }
}

struct SettingsSheet: View {
    @Environment(TutorClient.self) private var client
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                serverSection
                servicesSection
                Section("Voice") {
                    Picker("Voice", selection: Binding(get: { client.ttsBackend }, set: { client.setTTS($0) })) {
                        Text("Magpie").tag("magpie")
                        Text("Kokoro").tag("kokoro")
                    }
                    .pickerStyle(.segmented)
                }
                Section("Display") {
                    @Bindable var c = client
                    Toggle("Always show ghost ink (no arm attached)", isOn: $c.forceGhostInk)
                }
                Section("Operator") {
                    ForEach([("Next problem", "next_problem"), ("Skip step", "skip_step"),
                             ("Force arm step", "force_arm_step"), ("Reset session", "reset")], id: \.1) { title, action in
                        Button(title) { client.admin(action) }
                    }
                    Button("Clear board") { client.clearBoard() }
                    Button("Calibration mode") { client.toggleCalibration(); dismiss() }
                }
                #if DEBUG
                Section("Debug: force phase") {
                    ForEach([Phase.idle, .working, .stepWrite, .armStep, .spotMistake], id: \.self) { p in
                        Button(p.rawValue) { client.session.phase = p }
                    }
                }
                #endif
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                while !Task.isCancelled {
                    await client.refreshHealth()
                    try? await Task.sleep(for: .seconds(3))
                }
            }
        }
        .presentationDetents([.large])
    }

    private var serverSection: some View {
        Section("Server") {
            Picker("Profile", selection: Binding(get: { client.selectedID }, set: { client.select(profileID: $0) })) {
                ForEach(client.profiles) { Text($0.name).tag($0.id) }
            }
            .pickerStyle(.segmented)
            if !client.profile.isMock {
                TextField("Base URL", text: Binding(get: { client.profile.baseURL },
                                                    set: { client.updateURL($0, for: client.selectedID) }))
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
            }
            LabeledContent("Status") {
                HStack(spacing: 8) {
                    Circle().fill(client.session.connection.color).frame(width: 10, height: 10)
                    Text(client.session.connection.label)
                }
            }
            Button("Reconnect") { client.connect() }
        }
    }

    private var servicesSection: some View {
        Section("Services") {
            let health = client.session.health
            ForEach(HealthReport.serviceOrder, id: \.self) { name in
                let status = health?.services[name]
                LabeledContent(name.uppercased()) {
                    HStack(spacing: 8) {
                        if let ms = status?.latencyMs { Text("\(ms) ms").foregroundStyle(.secondary) }
                        if let d = status?.detail { Text(d).foregroundStyle(.secondary) }
                        Circle().fill(status == nil ? Color.gray : (status!.ok ? Theme.ok : Theme.bad)).frame(width: 10, height: 10)
                    }
                }
            }
            if let ip = health?.tailscaleIP { LabeledContent("Tailscale IP", value: ip) }
            if let ip = health?.lanIP { LabeledContent("LAN IP", value: ip) }
        }
    }
}
