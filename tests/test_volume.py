import pytest

from kalinka_plugin_roon import volume


@pytest.mark.parametrize(
    "kind,low,high,current,percent",
    [
        ("number", 0, 100, 30, 30),
        ("db", -80, 0, -40, 50),
        ("number", 10, 210, 60, 25),
    ],
)
def test_normalization_and_reverse_mapping(kind, low, high, current, percent):
    v = dict(type=kind, min=low, max=high, value=current, step=1)
    assert volume.state(v).current_volume == percent
    assert volume.value(v, percent) == current


def test_limits_and_mute():
    v = dict(
        type="db",
        min=-80,
        max=10,
        value=-40,
        hard_limit_min=-60,
        hard_limit_max=0,
        soft_limit=-20,
        step=0.5,
    )
    assert volume.state(v).current_volume == 50
    assert volume.value(v, 100) == -20
    assert volume.value(v, 0) == -60
    assert volume.value(v, 51) == -39.5
    assert volume.state({**v, "is_muted": True}).current_volume == 0


@pytest.mark.parametrize(
    "v",
    [
        None,
        {},
        {"type": "fixed"},
        {"type": "incremental"},
        dict(type="number", min=0, max=0, value=0),
        dict(type="number", min=0, max=100, value=float("nan")),
    ],
)
def test_unavailable_volume_cannot_be_set(v):
    assert not volume.state(v).supported
    with pytest.raises(NotImplementedError):
        volume.value(v, 50)
