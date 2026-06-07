from recursive_unzip_tool.gui import COL_EXECUTE, COL_NAME, _display_status, archive_color


def test_display_status_supports_interrupted() -> None:
    assert _display_status("interrupted") == "已中断"


def test_archive_color_maps_known_formats() -> None:
    assert archive_color(".zip").name() == "#0f8b8d"
    assert archive_color(".7z").name() == "#7c3aed"
    assert archive_color(".tar.gz").name() == "#2563eb"
    assert archive_color("file").name() == "#98a2b3"


def test_tree_name_column_keeps_native_indentation() -> None:
    assert COL_NAME == 0
    assert COL_EXECUTE == 1
