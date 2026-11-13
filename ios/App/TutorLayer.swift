import CacheFlowKit
import SwiftUI

/// The tutor's own layer, drawn above the student's canvas and never part of
/// it: committed ink, ghost previews, approved scene primitives, highlights,
/// and the read-back/evidence labels under the student's lines. It ignores
/// touches, so the student's Pencil always reaches their canvas.
struct TutorLayer: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let lesson = client.model.lesson
        ZStack(alignment: .topLeading) {
            Canvas { ctx, _ in
                for item in lesson.scenes { draw(item.scene, committed: item.committed, in: &ctx) }
                for item in lesson.ink where !item.ink.strokes.isEmpty {
                    ctx.stroke(path(item.ink.strokes), with: .color(color(item)),
                               style: StrokeStyle(lineWidth: item.ink.style == .write ? 4 : 3, lineCap: .round, lineJoin: .round,
                                                  dash: item.committed ? [] : [3, 7]))
                }
                for h in lesson.highlights {
                    let r = h.bbox.insetBy(dx: -8, dy: -8)
                    ctx.fill(Path(roundedRect: r, cornerRadius: 8), with: .color(Theme.bad.opacity(0.10)))
                }
            }
            ForEach(lesson.ink.filter { $0.ink.formula != nil }, id: \.ink.inkID) { item in
                Text(item.ink.formula ?? "")
                    .font(.system(size: 30, weight: .medium, design: .serif))
                    .minimumScaleFactor(0.4).lineLimit(1)
                    .foregroundStyle(Theme.tutorInk.opacity(item.committed ? 1 : 0.35))
                    .frame(width: item.ink.region.width, height: item.ink.region.height, alignment: .leading)
                    .offset(x: item.ink.region.minX, y: item.ink.region.minY)
                    .accessibilityIdentifier(item.committed ? "tutor.ink.formula" : "tutor.ink.preview")
            }
            ForEach(lesson.scenes, id: \.scene.sceneID) { item in
                SceneLabels(scene: item.scene, committed: item.committed)
            }
            LineLabels()
        }
        .allowsHitTesting(false)
        .animation(.easeOut(duration: 0.25), value: lesson.ink.map(\.committed))
    }

    private func color(_ item: TutorInkItem) -> Color {
        let base = item.ink.style == .write ? Theme.tutorInk : Theme.bad
        return base.opacity(item.committed ? 0.9 : 0.35)
    }

    private func path(_ strokes: [[CGPoint]]) -> Path {
        var p = Path()
        for s in strokes { p.addLines(s) }
        return p
    }

    /// Only approved primitive kinds are drawn; the engine already rejected
    /// any scene containing others.
    private func draw(_ scene: TutorScene, committed: Bool, in ctx: inout GraphicsContext) {
        let ink = GraphicsContext.Shading.color(Theme.tutorInk.opacity(committed ? 0.9 : 0.35))
        let dash: [CGFloat] = committed ? [] : [3, 7]
        ctx.stroke(Path(roundedRect: scene.region, cornerRadius: 10), with: .color(Theme.guide), lineWidth: 1)
        for p in scene.primitives {
            switch p.kind {
            case .axes:
                guard p.points.count == 4 else { continue }
                var path = Path()
                path.move(to: p.points[0]); path.addLine(to: p.points[1])
                path.move(to: p.points[2]); path.addLine(to: p.points[3])
                ctx.stroke(path, with: .color(.secondary), style: StrokeStyle(lineWidth: 1.5, dash: dash))
            case .plot, .segment, .numberLine:
                var path = Path(); path.addLines(p.points)
                ctx.stroke(path, with: ink, style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round, dash: dash))
            case .point:
                for pt in p.points { ctx.fill(Path(ellipseIn: CGRect(x: pt.x - 6, y: pt.y - 6, width: 12, height: 12)), with: ink) }
            case .box:
                guard p.points.count == 2 else { continue }
                let r = CGRect(x: p.points[0].x, y: p.points[0].y, width: p.points[1].x - p.points[0].x, height: p.points[1].y - p.points[0].y)
                ctx.stroke(Path(r), with: ink, style: StrokeStyle(lineWidth: 3, dash: dash))
            case .arrow:
                guard p.points.count == 2 else { continue }
                let a: CGPoint = p.points[0]
                let b: CGPoint = p.points[1]
                let angle: Double = atan2(Double(b.y - a.y), Double(b.x - a.x))
                var path = Path(); path.move(to: a); path.addLine(to: b)
                for d in [Double.pi * 0.85, -Double.pi * 0.85] {
                    let tip = CGPoint(x: b.x + CGFloat(14 * cos(angle + d)), y: b.y + CGFloat(14 * sin(angle + d)))
                    path.move(to: b)
                    path.addLine(to: tip)
                }
                ctx.stroke(path, with: ink, style: StrokeStyle(lineWidth: 3, lineCap: .round, dash: dash))
            default:
                continue
            }
        }
    }
}

private struct SceneLabels: View {
    let scene: TutorScene
    let committed: Bool

    var body: some View {
        ZStack(alignment: .topLeading) {
            Text(scene.title)
                .font(.caption.weight(.semibold)).foregroundStyle(Theme.tutorInk)
                .offset(x: scene.region.minX + 10, y: scene.region.minY + 6)
            ForEach(Array(scene.primitives.enumerated()), id: \.offset) { _, p in
                if let text = p.text, let at = p.points.last {
                    Text(text).font(.caption).foregroundStyle(Theme.tutorInk)
                        .offset(x: at.x + 6, y: at.y + 4)
                }
            }
        }
        .opacity(committed ? 1 : 0.4)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(committed ? "tutor.scene" : "tutor.scene.preview")
        .accessibilityLabel("Tutor diagram: \(scene.title)")
    }
}

/// "I read …" under each student line, plus the latest evidence for it.
private struct LineLabels: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let lesson = client.model.lesson
        let evidenceByLine = Dictionary(lesson.currentEvidence.compactMap { e in e.lineIndex.map { ($0, e) } }, uniquingKeysWith: { _, b in b })
        let regions = lesson.lineReadings.keys.sorted()
        ZStack(alignment: .topLeading) {
            ForEach(regions, id: \.self) { index in
                if let rect = client.lineRect(index) {
                    let e = evidenceByLine[index]
                    HStack(spacing: 6) {
                        if let e { Image(systemName: e.icon) }
                        Text("I read: \(lesson.lineReadings[index] ?? "")")
                        if let e { Text("· \(e.summary)").fontWeight(.semibold) }
                    }
                    .font(.caption.weight(.medium))
                    .foregroundStyle(e?.tint ?? .secondary)
                    .offset(x: rect.minX, y: rect.maxY + 6)
                }
            }
        }
    }
}

extension Evidence {
    var icon: String {
        switch verdict {
        case .valid: "checkmark.circle.fill"
        case .invalid: "exclamationmark.circle.fill"
        case .partial: "circle.lefthalf.filled"
        case .unknown: "questionmark.circle"
        }
    }
    var tint: Color {
        switch verdict {
        case .valid: Theme.ok
        case .invalid: Theme.bad
        case .partial: Theme.warn
        case .unknown: .secondary
        }
    }
}
