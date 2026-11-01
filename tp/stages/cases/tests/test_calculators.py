from tp.stages.cases import calculators as calc


def test_boundary_points_and_sides():
    assert calc.boundary_points(255) == [254, 255, 256]
    assert [calc.boundary_expect(v, 255, True, "2xx", "4xx") for v in (254, 255, 256)] == [
        "2xx", "2xx", "4xx"]  # fmt: skip
    assert calc.boundary_expect(255, 255, False, "2xx", "4xx") == "4xx"


def test_page_window_agrees_with_its_independent_property():
    for total in range(7):
        for skip in range(8):
            for limit in range(1, 5):
                got = calc.page_window(total, skip, limit)
                assert calc.page_len_ok(total, skip, limit, got["data_len"])
                assert got["count"] == total
    assert not calc.page_len_ok(10, 0, 3, 2)  # a short page while items remain


def test_monotonic_property_catches_a_flipped_boundary():
    assert calc.boundary_monotonic([1, 2, 3], ["2xx", "2xx", "4xx"], "2xx", "max")
    assert not calc.boundary_monotonic([1, 2, 3], ["2xx", "4xx", "2xx"], "2xx", "max")


def test_lower_bounds_allow_values_from_the_limit_up():
    # skip >= 0: the real run's first draft expected -1 -> 2xx, 1 -> 422 (a max-only calculator)
    got = [calc.boundary_expect(v, 0, True, "2xx", "422", "min") for v in calc.boundary_points(0)]
    assert got == ["422", "2xx", "2xx"]
    assert calc.boundary_monotonic([-1, 0, 1], got, "2xx", "min")
    assert not calc.boundary_monotonic([-1, 0, 1], ["2xx", "2xx", "422"], "2xx", "min")


def test_newest_first():
    assert calc.newest_first(3) == [3, 2, 1]


def test_run_coerces_string_params():
    assert calc.run("page_window", {"total": "5", "skip": "4", "limit": "3"})["data_len"] == 1
