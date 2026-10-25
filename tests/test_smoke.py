"""Smoke tests for core configuration and geometry helpers.

These verify the project imports cleanly and its foundational math is correct,
giving CI a real signal without requiring an LLM API key or a full simulation.
"""

import config


def test_core_config_constants_are_positive():
    assert config.MAP_WIDTH > 0
    assert config.MAP_HEIGHT > 0
    assert config.UAV_MAX_SPEED > 0
    assert config.UAV_MAX_BATTERY > 0
    assert config.UAV_HEALTH_MAX > 0


def test_uav_roles_are_defined():
    for role in ("leader", "scout", "striker", "relay"):
        assert role in config.UAV_ROLES


def test_distance_2d():
    p1 = {"x": 0, "y": 0}
    p2 = {"x": 3, "y": 4}
    assert config.distance(p1, p2) == 5.0


def test_distance_sq_3d():
    p1 = {"x": 0, "y": 0, "z": 0}
    p2 = {"x": 1, "y": 2, "z": 2}
    assert config.distance_sq(p1, p2) == 9


def test_distance_sq_matches_distance():
    p1 = {"x": 1.0, "y": 1.0, "z": 1.0}
    p2 = {"x": 4.0, "y": 5.0, "z": 1.0}
    assert config.distance_sq(p1, p2) == config.distance(p1, p2) ** 2
