# Socrates

Socrates is a robot tutor that shares an iPad whiteboard with a student. It catches the first step that changes the maths, points to it, and asks a question the student can answer by revising their own work. The session ends with a new problem that tests the same skill and a short summary for a parent or caregiver.

## The first session

1. The first build opens a preset `x² + 5x + 6 = 0` and accepts typed working. With Pencil or push-to-talk enabled, Socrates shows what it read so the student can correct a misread sign or exponent.
2. The student submits `(x − 2)(x − 3) = 0`. SymPy finds that the proposed factors multiply to `x² − 5x + 6`. **Mistake Glow** underlines the relevant signs and explains that the constants multiply to `6` and add to `−5`. When the arm is available and the operator has cleared the board, it can circle the same region.
3. Socrates asks, “What sign does the middle term in your original equation have?” The student changes the factors to `(x + 2)(x + 3)` and works out the roots. The tutor waits for the student to take the next step.
4. When the tutor adds an annotation, **Ghost Ink** previews the stroke in a fixed tutor strip on the iPad. The SO-101 follows the approved path with a pen on a calibrated practice surface. The full experience uses an Apple Pencil on the iPad after contact and safety testing.
5. The student checks their understanding on a fresh quadratic. The skill map records the difference between a correction made with help and an independent answer. The parent summary shows both and suggests one next practice step.

## The product bet

Seeing the exact step that went wrong, then fixing it in place, gives the learner a chance to explain the reasoning. The arm makes that shared attention visible. A tutor session remains complete with digital annotations when the arm is parked or unavailable.

The first release covers one lesson on factoring monic quadratics. Develop the tutoring loop and the physical drawing ladder in parallel. The arm progresses through corner calibration, line, circle, square, digits, and the equation before attempting screen contact. See the [product requirements](prd.md), [architecture](architecture.md), [decisions](decisions.md), and [status](status.md).
