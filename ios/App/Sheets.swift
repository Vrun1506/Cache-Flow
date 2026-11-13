import CacheFlowKit
import SwiftUI

/// Confirm or correct what the tutor read: the question, a line of working,
/// or a push-to-talk transcript. Ambiguous readings are never graded until
/// the student confirms them.
struct ReadBackSheet: View {
    @Environment(TutorClient.self) private var client
    let readBack: ReadBack
    @State private var editing = ""

    var body: some View {
        VStack(spacing: 20) {
            Text(title).font(.title3).foregroundStyle(.secondary)
            Text(readBack.text)
                .font(.system(size: 40, weight: .semibold, design: .serif))
                .minimumScaleFactor(0.5).lineLimit(1)
                .accessibilityIdentifier("readback.text")
            if let note = readBack.note { Text(note).font(.callout).foregroundStyle(Theme.warn) }
            if readBack.alternatives.count > 1 {
                Text("Which did you mean?").font(.headline)
                HStack(spacing: 12) {
                    ForEach(Array(readBack.alternatives.enumerated()), id: \.offset) { i, alt in
                        Button(alt) { client.confirmReadBack(accepted: true, text: alt) }
                            .buttonStyle(PillButtonStyle(prominent: i == 0))
                            .accessibilityIdentifier("readback.alt.\(i)")
                    }
                }
            } else {
                Button { client.confirmReadBack(accepted: true) } label: { Label("Looks right", systemImage: "checkmark") }
                    .buttonStyle(PillButtonStyle(prominent: true))
                    .accessibilityIdentifier("readback.accept")
            }
            HStack(spacing: 10) {
                TextField("Or type what you meant", text: $editing)
                    .textFieldStyle(.roundedBorder).frame(width: 320)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("readback.edit")
                Button("Use this") { client.confirmReadBack(accepted: true, text: editing) }
                    .buttonStyle(PillButtonStyle())
                    .disabled(editing.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityIdentifier("readback.useEdit")
                Button { client.confirmReadBack(accepted: false) } label: { Label(rejectTitle, systemImage: "arrow.uturn.backward") }
                    .buttonStyle(PillButtonStyle())
                    .accessibilityIdentifier("readback.reject")
            }
        }
        .padding(40)
        .presentationDetents([.height(380)])
        .interactiveDismissDisabled()
        .accessibilityIdentifier("readback.sheet")
    }

    private var title: String {
        switch readBack.source {
        case .question: "I read your problem as"
        case .transcript: "I heard"
        default: "I read this line as"
        }
    }

    private var rejectTitle: String { readBack.source == .transcript ? "Say it again" : "Rewrite" }
}

/// Assisted versus independent work, from the server's evidence records.
struct SummarySheet: View {
    @Environment(TutorClient.self) private var client
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let s = client.model.summary {
                    Section("Problems") {
                        ForEach(Array(s.items.enumerated()), id: \.offset) { _, item in
                            HStack {
                                Text(item.problem).font(.body.weight(.semibold))
                                Spacer()
                                Text(item.independent ? "Independent" : (item.assisted ? "Helped" : item.outcome.rawValue.capitalized))
                                    .foregroundStyle(item.independent ? Theme.ok : Theme.warn)
                            }
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("summary.item")
                        }
                    }
                    Section("Skills") {
                        ForEach(s.skills, id: \.skill) { k in
                            LabeledContent(k.label, value: "Level \(k.level) · \(k.independentEvidence) independent\(k.assisted ? " · helped" : "")")
                        }
                    }
                    Section("Next step") { Text(s.nextStep) }
                }
            }
            .navigationTitle("Session summary")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .accessibilityIdentifier("summary.sheet")
    }
}

struct SettingsSheet: View {
    @Environment(TutorClient.self) private var client
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false

    var body: some View {
        NavigationStack {
            Form {
                serverSection
                componentsSection
                Section("Agent trace (actual calls reported by the server)") {
                    let traces = client.model.lesson.traces
                    if traces.isEmpty { Text("No calls yet").foregroundStyle(.secondary) }
                    ForEach(Array(traces.enumerated().reversed()), id: \.offset) { _, t in
                        LabeledContent("\(t.component) · \(t.action)", value: "\(t.outcome) · \(t.latencyMs) ms")
                    }
                }
                Section("Operator") {
                    Button("Delete this session's data", role: .destructive) { confirmDelete = true }
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .confirmationDialog("Delete this session's stored evidence on the tutor server?", isPresented: $confirmDelete) {
                Button("Delete", role: .destructive) { client.deleteSession(); dismiss() }
            }
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
                TextField("Base URL (https://…)", text: Binding(get: { client.profile.baseURL },
                                                              set: { client.updateURL($0, for: client.selectedID) }))
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
            } else {
                Text("Mock: scripted lesson running in the app. Not a Spark, model or verifier result.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            LabeledContent("Status") {
                HStack(spacing: 8) {
                    Circle().fill(client.model.connection.color).frame(width: 10, height: 10)
                    Text(client.model.connection.label)
                }
            }
            Button("Reconnect") { client.connect() }
        }
    }

    private var componentsSection: some View {
        Section("Local components") {
            let health = client.model.health
            ForEach(HealthReport.componentOrder, id: \.self) { name in
                let status = health?.components[name]
                LabeledContent(name) {
                    HStack(spacing: 8) {
                        if let r = status?.revision { Text(r).foregroundStyle(.secondary) }
                        if let ms = status?.latencyMs { Text("\(ms) ms").foregroundStyle(.secondary) }
                        Circle().fill(status == nil ? Color.gray : (status!.ok ? Theme.ok : Theme.bad)).frame(width: 10, height: 10)
                    }
                }
            }
        }
    }
}

/// UI-test / operator fault injection for the in-process mock, plus a
/// machine-readable status line. Only shown with `-uitest`.
struct DebugPanel: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text(client.model.debugStatus)
                .font(.system(size: 9, design: .monospaced)).foregroundStyle(.secondary)
                .accessibilityIdentifier("debug.status")
                .accessibilityLabel(client.model.debugStatus)
            HStack(spacing: 6) {
                Button("Lose next audio") { client.inject(.loseNextAudio) }.accessibilityIdentifier("debug.loseAudio")
                Button("Stale next ink") { client.inject(.staleNextInk) }.accessibilityIdentifier("debug.staleInk")
                Button("Drop connection") { client.dropConnection() }.accessibilityIdentifier("debug.dropConnection")
            }
            .font(.caption2)
            .buttonStyle(.bordered)
        }
        .padding(6)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
