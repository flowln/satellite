from inspect import _ParameterKind as Kind

import pytest

from satellite.annotations import (
    ValidationError,
    generate_annotation_for_device,
    generate_annotation_for_plan,
    validate_plan,
)


def test_annotate_plan_with_no_args():
    def _plan():
        yield

    annotation = generate_annotation_for_plan(_plan, "_plan")

    assert annotation.plan_name == "_plan"
    assert len(annotation.arguments) == 0


def test_validate_plan_with_no_args():
    def _plan():
        yield

    annotation = generate_annotation_for_plan(_plan, "_plan")

    assert validate_plan(annotation, (), {})


def test_validate_plan_with_no_args_fails_with_args():
    def _plan():
        yield

    annotation = generate_annotation_for_plan(_plan, "_plan")

    with pytest.raises(ValidationError):
        validate_plan(annotation, (123,), {})
    with pytest.raises(ValidationError):
        validate_plan(annotation, (), {"something": "abc"})


def test_annotate_plan_with_pos_arg():
    def _plan(return_value: int):
        yield return_value

    annotation = generate_annotation_for_plan(_plan, "_plan")

    assert annotation.plan_name == "_plan"
    assert "return_value" in annotation.arguments
    assert annotation.arguments["return_value"].kind == Kind.POSITIONAL_OR_KEYWORD
    assert annotation.arguments["return_value"].annotation is int


def test_validate_plan_with_pos_arg():
    def _plan(return_value: int):
        yield return_value

    annotation = generate_annotation_for_plan(_plan, "_plan")

    assert validate_plan(annotation, (123,), {})
    assert validate_plan(annotation, (), {"return_value": 123})


def test_validate_plan_with_pos_arg_fails_with_incorrect_arg():
    def _plan(return_value: int):
        yield return_value

    annotation = generate_annotation_for_plan(_plan, "_plan")

    with pytest.raises(ValidationError):
        validate_plan(annotation, (), {})
    with pytest.raises(ValidationError):
        validate_plan(annotation, ("not an int",), {})
    with pytest.raises(ValidationError):
        validate_plan(annotation, (123, 456), {})


def test_annotate_bluesky_scan():
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")
    assert annotation.plan_name == "scan"

    key = "detectors"
    assert key in annotation.arguments
    assert annotation.arguments[key].kind == Kind.POSITIONAL_OR_KEYWORD
    assert annotation.arguments[key].required

    key = "args"
    assert key in annotation.arguments
    assert annotation.arguments[key].kind == Kind.VAR_POSITIONAL
    assert annotation.arguments[key].required

    key = "num"
    assert key in annotation.arguments
    assert annotation.arguments[key].kind == Kind.KEYWORD_ONLY
    assert not annotation.arguments[key].required

    key = "per_step"
    assert key in annotation.arguments
    assert annotation.arguments[key].kind == Kind.KEYWORD_ONLY
    assert not annotation.arguments[key].required

    key = "md"
    assert key in annotation.arguments
    assert annotation.arguments[key].kind == Kind.KEYWORD_ONLY
    assert not annotation.arguments[key].required


def test_validate_bluesky_scan(sim_readable, sim_movable):
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")

    assert validate_plan(annotation, ([sim_readable], sim_movable, -1, 1, 10), {})
    assert validate_plan(annotation, ([sim_readable], sim_movable, -1, 1), {"num": 10})
    assert validate_plan(annotation, ([sim_readable], sim_movable, -1, 1), {"md": {"a": "aa", "b": "bb"}})


def test_validate_bluesky_scan_with_wrong_args(sim_readable, sim_movable):
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")

    with pytest.raises(ValidationError):
        assert validate_plan(annotation, (["not a readable"], sim_movable, -1, 1, 10), {})
    with pytest.raises(ValidationError):
        assert validate_plan(annotation, (sim_readable, sim_movable, -1, 1, 10), {})
    with pytest.raises(ValidationError):
        assert validate_plan(annotation, (sim_movable, -1, 1, 10), {})
    with pytest.raises(ValidationError):
        assert validate_plan(annotation, ([sim_readable], sim_movable, -1, 1), {"num": "not valid"})
    with pytest.raises(ValidationError):
        assert validate_plan(annotation, ([sim_readable], sim_movable, -1, 1), {"md": "not valid"})


def test_validate_from_string(sim_readable, sim_movable):
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")

    device_annotations = {
        "detector": generate_annotation_for_device(sim_readable, "detector"),
        "motor": generate_annotation_for_device(sim_movable, "motor"),
    }

    assert validate_plan(
        annotation,
        (["detector"], sim_movable, -1, 1, 10),
        {},
        device_annotations=device_annotations,
    )


def test_validate_from_string_wrong(sim_readable, sim_movable):
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")

    device_annotations = {
        "detector": generate_annotation_for_device(sim_readable, "detector"),
        "motor": generate_annotation_for_device(sim_movable, "motor"),
    }

    with pytest.raises(ValidationError):
        assert validate_plan(
            annotation,
            (["detector", "not a detector"], sim_movable, -1, 1, 10),
            {},
            device_annotations=device_annotations,
        )
