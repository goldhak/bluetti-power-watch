"""The AUR package ships bluetti-bt-lib and its Bluetooth helpers in a private folder."""

import sys

from bluetti_power_watch import VENDOR_DIR, use_vendor


def test_packaged_location():
    assert str(VENDOR_DIR) == "/usr/lib/bluetti-power-watch/vendor"


def test_existing_folder_goes_first(tmp_path, monkeypatch):
    # First, so an old system copy of the same library does not win.
    monkeypatch.setattr(sys, "path", ["/usr/lib/python3/site-packages"])
    use_vendor(tmp_path)
    assert sys.path[0] == str(tmp_path)


def test_not_added_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", [])
    use_vendor(tmp_path)
    use_vendor(tmp_path)
    assert sys.path == [str(tmp_path)]


def test_missing_folder_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", [])
    use_vendor(tmp_path / "missing")
    assert sys.path == []
