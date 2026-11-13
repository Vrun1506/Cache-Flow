import CacheFlowKit
import SwiftUI

/// The arm's planned strokes: a faint dotted preview while it draws, or an
/// animated blue pen when the arm didn't ink (the ghost-ink fallback).
struct GhostLayer: View {
    let ghosts: [Ghost]

    var body: some View {
        TimelineView(.animation(paused: ghosts.isEmpty)) { timeline in
            Canvas { ctx, _ in
                for g in ghosts {
                    switch g.kind {
                case .preview:
                    ctx.stroke(path(g.strokes), with: .color(Theme.tutorInk.opacity(0.35)),
                               style: StrokeStyle(lineWidth: 3, lineCap: .round, dash: [2, 7]))
                    case .playback:
                        let progress = timeline.date.timeIntervalSince(g.started) / g.duration
                        ctx.stroke(path(StrokeGeometry.partial(g.strokes, progress: progress)),
                                   with: .color(Theme.tutorInk), style: StrokeStyle(lineWidth: 4, lineCap: .round, lineJoin: .round))
                    }
                }
            }
        }
        .allowsHitTesting(false)
    }

    private func path(_ strokes: [[CGPoint]]) -> Path {
        var p = Path()
        for s in strokes { p.addLines(s) }
        return p
    }
}

/// Read-back text under each of the student's lines, and the red highlight.
struct LineOverlays: View {
    @Environment(TutorClient.self) private var client
    let layout: BoardLayout

    var body: some View {
        let s = client.session
        ZStack(alignment: .topLeading) {
            ForEach(s.readBack, id: \.index) { line in
                if let rect = s.lineRects[line.index] {
                    HStack(spacing: 4) {
                        Image(systemName: line.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                        Text("I read: \(line.expr)")
                    }
                    .font(.caption.weight(.medium))
                    .foregroundStyle(line.ok ? Theme.ok : Theme.bad)
                    .offset(x: rect.minX, y: rect.maxY + 14)
                    .accessibilityLabel("Line \(line.index + 1): I read \(line.expr), \(line.ok ? "correct" : "needs another look")")
                }
            }
            if let h = s.highlight {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Theme.bad.opacity(0.15))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.bad.opacity(0.7), lineWidth: 2))
                    .frame(width: h.rect.width + 16, height: h.rect.height + 16)
                    .offset(x: h.rect.minX - 8, y: h.rect.minY - 8)
                    .transition(.opacity)
                    .id(h.id)
            }
        }
        .animation(.easeOut(duration: 0.4), value: s.highlight)
        .allowsHitTesting(false)
    }
}

/// Hidden calibration mode: a 3x3 grid of targets in the tutor strip. Touch each
/// with the arm's stylus; every touch is logged in board coordinates.
struct CalibrationOverlay: View {
    @Environment(TutorClient.self) private var client
    let layout: BoardLayout

    var body: some View {
        let targets = CalibrationLog.targets(in: layout)
        let log = client.session.calibration
        ZStack(alignment: .topLeading) {
            ForEach(Array(targets.enumerated()), id: \.offset) { i, p in
                ZStack {
                    Circle().strokeBorder(Theme.bad, lineWidth: 2).frame(width: 36, height: 36)
                    Rectangle().fill(Theme.bad).frame(width: 1, height: 36)
                    Rectangle().fill(Theme.bad).frame(width: 36, height: 1)
                    Text("\(i + 1)").font(.caption2.bold()).foregroundStyle(Theme.bad).offset(x: 16, y: -16)
                }
                .position(p)
            }
            ForEach(Array((log?.touches ?? []).enumerated()), id: \.offset) { _, t in
                Circle().fill(Theme.tutorInk.opacity(0.6)).frame(width: 8, height: 8).position(x: t.x, y: t.y)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Calibration · \(layout.fingerprint) · \(log?.touches.count ?? 0) touches").font(.footnote.monospaced())
                HStack {
                    if let log, let json = try? JSONEncoder().encode(log) {
                        ShareLink(item: String(decoding: json, as: UTF8.self)) { Label("Export JSON", systemImage: "square.and.arrow.up") }
                    }
                    Button("Done") { client.toggleCalibration() }
                }
                .buttonStyle(PillButtonStyle())
            }
            .padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            .offset(x: layout.workArea.minX + 16, y: layout.workArea.minY + 40)
        }
        .allowsHitTesting(true)
        .background(Color.clear.contentShape(Rectangle()).allowsHitTesting(false))
    }
}
