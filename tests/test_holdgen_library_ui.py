from PySide6 import QtWidgets
from rockform.widgets import STYLE
from rockform.holdgen import HoldRequest, generate
from rockform.holdgen.app import GeneratorWindow, EXTRA_STYLE
from test_holdgen_route_ui import wait_for_routes


def test_library_tab_saves_imports_inspects_and_feeds_routes(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLE + EXTRA_STYLE)
    window = GeneratorWindow()
    window.show()
    library, view, routes = window.library, window.library_view, window.routes
    try:
        assert not library.holds and view.table.rowCount() == 0
        window.tabs.setCurrentIndex(1)
        assert not routes.use_library.isEnabled()                    # nothing to use yet

        # Save the hold shown in the Hold tab.
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("My jug", True))
        window.save_to_library()
        assert [h.name for h in library.holds] == ["My jug"] and view.table.rowCount() == 1
        assert not window.save_button.isEnabled()

        # Import model files.
        paths = []
        for kind, d in (("edge", 3.5), ("sloper", 3.), ("crimp", 6.)):
            path = tmp_path / f"{kind}.stl"
            generate(HoldRequest(kind, difficulty=d, seed=2)).mesh.export(path)
            paths.append(str(path))
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileNames", lambda *a, **k: (paths, ""))
        view.type_pick.setCurrentIndex(view.type_pick.findData("edge"))
        view.import_models()
        assert view.table.rowCount() == 4 and "Added 3 hold(s)" in view.message.text()
        assert len(view.selected_ids()) == 3
        view.table.cellWidget(2, 1).setCurrentIndex(3)              # rate the sloper as a sloper
        assert library.holds[2].type == "sloper"
        view.table.item(1, 0).setText("Grey edge")
        assert library.holds[1].name == "Grey edge"

        # Show one in the Hold tab.
        view.table.clearSelection()
        view.table.selectRow(1)
        view.inspect()
        assert window.tabs.currentIndex() == 0 and window.hold.params["library"] == "edge"
        assert not window.save_button.isEnabled()

        # A route from library holds only.
        window.tabs.setCurrentIndex(1)
        assert routes.use_library.isEnabled()
        routes.use_library.setCurrentIndex(routes.use_library.findData("only"))
        routes.grade_group.button(2).setChecked(True)
        routes.add_route()
        wait_for_routes(routes, 1)
        holds = routes.routes[0][2]
        assert all(h.library for h in holds) and routes.routes[0][3]["library_holds"] == len(holds)
        assert any("·" in routes.table.item(r, 2).text() for r in range(routes.table.rowCount()))
    finally:
        window.close()
