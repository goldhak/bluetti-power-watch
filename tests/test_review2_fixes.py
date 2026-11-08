"""Сценарії з другого рев'ю: вимкнення по колу, годинник, втрата зв'язку зі станцією."""


from bluetti_power_watch import Actions, Machine, handle_events, tick
from test_power_watch import GRID, FakeHost, battery


def make(tmp_path, armed=True):
    host = FakeHost()
    actions = Actions(run=host.run, state_dir=tmp_path, save_brightness=20)
    if armed:
        (tmp_path / "poweroff.armed").touch()
    return host, actions


def quiet(_text):
    return None


class TestPoweroffOncePerOutage:
    def test_pc_turned_on_again_is_not_powered_off(self, tmp_path):
        host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        assert host.powered_off is True

        host.powered_off = False
        after_boot = Actions(run=host.run, state_dir=tmp_path, save_brightness=20)
        handle_events(["critical_again"], battery(5), after_boot, quiet)
        assert host.powered_off is False

    def test_repeat_after_boot_does_not_say_it_failed(self, tmp_path):
        _host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        sent = []
        handle_events(["critical_again"], battery(5), actions, sent.append)
        assert sent == []

    def test_critical_after_boot_tells_to_power_off_by_hand(self, tmp_path):
        host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        host.powered_off = False
        sent = []
        handle_events(["critical"], battery(5), actions, sent.append)
        assert host.powered_off is False
        assert "самі" in sent[0]

    def test_failed_poweroff_does_not_count_as_done(self, tmp_path):
        host, actions = make(tmp_path)
        host.broken.add("systemctl")
        handle_events(["critical"], battery(5), actions, quiet)
        host.broken.clear()
        handle_events(["critical_again"], battery(5), actions, quiet)
        assert host.powered_off is True

    def test_returned_light_allows_poweroff_in_next_outage(self, tmp_path):
        host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        host.powered_off = False
        handle_events(["restored"], GRID, actions, quiet)
        handle_events(["critical"], battery(5), actions, quiet)
        assert host.powered_off is True

    def test_grid_tick_clears_leftover_mark(self, tmp_path):
        """Файл стану міг зникнути: тоді події restored не буде, а знак лишиться."""
        host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        host.powered_off = False
        tick(Machine(), GRID, actions, quiet)
        handle_events(["critical"], battery(5), actions, quiet)
        assert host.powered_off is True

    def test_battery_tick_keeps_the_mark(self, tmp_path):
        host, actions = make(tmp_path)
        handle_events(["critical"], battery(5), actions, quiet)
        host.powered_off = False
        machine = Machine()
        machine.state = "outage"
        tick(machine, battery(5), actions, quiet)
        handle_events(["critical_again"], battery(5), actions, quiet)
        assert host.powered_off is False

    def test_not_armed_leaves_no_mark(self, tmp_path):
        _host, actions = make(tmp_path, armed=False)
        handle_events(["critical"], battery(5), actions, quiet)
        assert not actions.poweroff_done_path.exists()
