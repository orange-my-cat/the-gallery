"""Development must not be able to address production.

`data-center` publishes 0.0.0.0:5432 on this host; `data-center-test` publishes
5433. Both hold a database called `gallery`, so here the port and the host are
the only things that tell them apart.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from config.settings.guards import (
    ConfigurationRefused,
    assert_development_database,
    assert_media_delivery,
)

PERMITTED = {"gallery", "gallery_ci"}


def test_the_ordinary_development_configuration_is_accepted():
    assert_development_database("gallery", "5433", PERMITTED, host="localhost")


def test_the_tmpfs_test_instance_is_accepted():
    assert_development_database("gallery_ci", 5434, PERMITTED, host="127.0.0.1")


def test_the_production_port_is_refused():
    with pytest.raises(ConfigurationRefused, match="data-center"):
        assert_development_database("gallery", "5432", PERMITTED)


def test_the_production_host_is_refused_whatever_the_port():
    with pytest.raises(ConfigurationRefused, match="production"):
        assert_development_database("gallery", "5433", PERMITTED, host="Data-Center")


def test_an_unrecognised_database_is_refused():
    with pytest.raises(ConfigurationRefused, match="not a database"):
        assert_development_database("financial_hub_dev", "5433", PERMITTED)


@given(name=st.sampled_from(sorted(PERMITTED)), as_int=st.booleans())
def test_port_5432_is_refused_for_every_permitted_name(name, as_int):
    port = 5432 if as_int else "5432"
    with pytest.raises(ConfigurationRefused):
        assert_development_database(name, port, PERMITTED)


@given(port=st.integers(min_value=1, max_value=65535).filter(lambda p: p != 5432))
def test_any_other_port_is_accepted_for_a_permitted_name(port):
    assert_development_database("gallery", port, PERMITTED)


def test_the_running_configuration_passed_its_own_guard(settings):
    """Belt and braces: the suite itself is not addressing production."""
    assert str(settings.DATABASES["default"]["PORT"]) != "5432"
    assert settings.DATABASES["default"]["HOST"] != "data-center"


@pytest.mark.parametrize("mode", ["django", "accel"])
def test_media_delivery_accepts_the_two_modes(mode):
    assert_media_delivery(mode)


def test_media_delivery_refuses_anything_else():
    with pytest.raises(ConfigurationRefused, match="MEDIA_DELIVERY"):
        assert_media_delivery("nginx")


def test_the_new_hld_keys_are_read(settings):
    for key in ("LIBRARY_ROOT", "CACHE_ROOT", "MEDIA_DELIVERY"):
        assert hasattr(settings, key)
    assert settings.MEDIA_ACCEL_LIBRARY_PREFIX == "/_protected/library/"
    assert settings.MEDIA_ACCEL_CACHE_PREFIX == "/_protected/cache/"
