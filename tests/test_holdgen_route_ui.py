import time
from PySide6 import QtWidgets
from rockform.widgets import STYLE
from rockform.holdgen.app import GeneratorWindow, EXTRA_STYLE
from rockform.holdgen.holding import orientation


def wait_for_routes(view, count):
    deadline = time.monotonic() + 90
    while len(view.routes) < count and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(.01)  # Release the GIL so the planner thread can run.
    assert len(view.routes) == count, view.message.text()


def test_route_tab_plans_shows_and_inspects_routes():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLE + EXTRA_STYLE)
    window = GeneratorWindow()
    window.show()
    view = window.routes
    try:
        window.tabs.setCurrentIndex(1)
        view.grade_group.button(2).setChecked(True)       # V2
        view.add_route()
        wait_for_routes(view, 1)
        view.add_route()                                   # a second route keeps clear of the first
        wait_for_routes(view, 2)
        holds = [h for *_, hs, _s in view.routes for h in hs]
        assert view.table.rowCount() == len(holds)
        assert view.big.text() == view.routes[1][3]["estimated_grade"]
        assert view.export_button.isEnabled()

        view.table.selectRow(1)
        assert view.selected is not None and view.inspect_button.isEnabled()
        view.inspect()
        assert window.tabs.currentIndex() == 0
        assert window.hold is view.selected.hold                  # the drilled, placed hold
        assert window.rotation.value() == round(view.selected.spin)

        # Manual adjustment: move one bolt, turn, type a position, overlap warning, undo.
        window.tabs.setCurrentIndex(1)
        hand = next(h for h in view.routes[0][2] if h.role == "HAND")
        view.table.selectRow(next(r for r, (_i, h) in enumerate(view.rows) if h is hand))
        u, v, spin, before = hand.u, hand.v, hand.spin, list(view.routes[0][3]["move_costs"])
        view.adjust(du=1)
        assert hand.u == u + view.bolt_box.value() and hand.moved and view.selected is hand
        assert view.routes[0][3]["move_costs"] != before and view.routes[0][3]["adjusted"] == 1
        view.adjust(turn=15.)
        assert hand.spin == (spin + 195.) % 360. - 180.
        view.adjust(face=True)
        other = next(h for h in view.routes[0][2] if h is not hand)
        view.adjust(to=(other.u + 3., other.v - 4.))           # snaps onto the other hold's bolt
        assert (hand.u, hand.v) == (other.u, other.v) and id(hand) in view.problems
        for _ in range(4):
            view.undo()
        assert (hand.u, hand.v, hand.spin, hand.moved) == (u, v, spin, False)
        assert view.routes[0][3]["move_costs"] == before and not view.history

        # Drag in the 3D view: press on a hold, move, turn with the wheel, release; empty space still orbits.
        iren, ren = view.plotter.iren.interactor, view.plotter.renderer
        R = orientation(view.angle.value())

        def screen(u, v, z):
            ren.SetWorldPoint(*(R @ [u, v, z]), 1.)
            ren.WorldToDisplay()
            return [int(round(c)) for c in ren.GetDisplayPoint()[:2]]

        def send(event, x, y):
            iren.SetEventInformation(x, y, 0, 0, "\0", 0, None)
            iren.InvokeEvent(event)
            QtWidgets.QApplication.processEvents()

        view.draw(keep_camera=False)
        camera = view.plotter.camera_position
        x, y = screen(hand.u, hand.v, hand.size_mm[2] / 2)
        send("LeftButtonPressEvent", x, y)
        assert view.drag is not None and view.selected is hand
        tx, ty = screen(hand.u + 2 * view.bolt_box.value(), hand.v - view.bolt_box.value(), hand.size_mm[2] / 2)
        for k in range(1, 6):
            send("MouseMoveEvent", x + (tx - x) * k // 5, y + (ty - y) * k // 5)
        send("MouseWheelForwardEvent", tx, ty)
        send("LeftButtonReleaseEvent", tx, ty)
        g = view.bolt_box.value()
        assert (hand.u, hand.v, hand.spin) == (u + 2 * g, v - g, (spin + 195.) % 360. - 180.)
        assert len(view.history) == 1 and view.plotter.camera_position == camera
        send("LeftButtonPressEvent", 5, 5)
        for k in range(5):
            send("MouseMoveEvent", 5 + 25 * k, 5 + 10 * k)
        send("LeftButtonReleaseEvent", 105, 45)
        assert view.plotter.camera_position != camera and len(view.history) == 1
        view.undo()
        assert (hand.u, hand.v, hand.spin) == (u, v, spin)

        view.remove_last()
        assert len(view.routes) == 1 and view.table.rowCount() == len(view.routes[0][2])
        view.clear_wall()
        assert not view.routes and view.table.rowCount() == 0 and not view.export_button.isEnabled()
    finally:
        window.close()
