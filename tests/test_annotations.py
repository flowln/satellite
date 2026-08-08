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
    argument = annotation.get_argument_with_name("return_value")
    assert argument is not None
    assert argument.kind == Kind.POSITIONAL_OR_KEYWORD
    assert argument.annotation is int


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

    argument = annotation.get_argument_with_name("detectors")
    assert argument is not None
    assert argument.kind == Kind.POSITIONAL_OR_KEYWORD
    assert argument.required

    argument = annotation.get_argument_with_name("args")
    assert argument is not None
    assert argument.kind == Kind.VAR_POSITIONAL
    assert argument.required

    argument = annotation.get_argument_with_name("num")
    assert argument is not None
    assert argument.kind == Kind.KEYWORD_ONLY
    assert not argument.required

    argument = annotation.get_argument_with_name("per_step")
    assert argument is not None
    assert argument.kind == Kind.KEYWORD_ONLY
    assert not argument.required

    argument = annotation.get_argument_with_name("md")
    assert argument is not None
    assert argument.kind == Kind.KEYWORD_ONLY
    assert not argument.required


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


def test_serialize_like_queueserver():
    bp = pytest.importorskip("bluesky.plans")

    annotation = generate_annotation_for_plan(bp.scan, "scan")
    serialized = annotation.model_dump(mode="json")

    assert serialized.get("name") == "scan", serialized
    assert "Scan over one multi-motor trajectory" in serialized.get("description"), serialized

    expected_plan_names = {arg.name for arg in annotation.arguments}
    for parameter in serialized.get("parameters", {}):
        parameter_name = parameter.get("name")
        assert parameter_name in expected_plan_names

        match parameter_name:
            case "detectors":
                assert parameter.get("annotation", {}).get("type") == "collections.abc.Sequence[__READABLE__]"
                assert parameter.get("kind", {}).get("name") == "POSITIONAL_OR_KEYWORD"
                assert parameter.get("kind", {}).get("value") == 1
                assert parameter.get("is_list", False), parameter
                assert not parameter.get("is_optional", True), parameter
            case "args":
                assert parameter.get("annotation", {}).get("type") == "__MOVABLE__ | typing.Any"
                assert parameter.get("kind", {}).get("name") == "VAR_POSITIONAL"
                assert parameter.get("is_list", False), parameter
                assert not parameter.get("is_optional", True), parameter
            case "num":
                assert parameter.get("annotation", {}).get("type") == "int | None"
                assert parameter.get("kind", {}).get("name") == "KEYWORD_ONLY"
                assert not parameter.get("is_list", True), parameter
                assert parameter.get("default", "not present") is None
                assert parameter.get("is_optional", False), parameter
            case "per_step":
                assert parameter.get("kind", {}).get("name") == "KEYWORD_ONLY"
                assert not parameter.get("is_list", True), parameter
                assert parameter.get("default", "not present") is None
                assert parameter.get("is_optional", False), parameter
            case "md":
                assert parameter.get("kind", {}).get("name") == "KEYWORD_ONLY"
                assert not parameter.get("is_list", True), parameter
                assert parameter.get("default", "not present") is None
                assert parameter.get("is_optional", False), parameter

    bpp = pytest.importorskip("bluesky.protocols")

    def _plan(_readable: bpp.Readable):
        yield

    annotation = generate_annotation_for_plan(_plan, "_plan")
    serialized = annotation.model_dump(mode="json")

    assert serialized.get("name") == "_plan", serialized
    assert serialized.get("description") is not None, serialized

    parameters = serialized.get("parameters", [])
    assert len(parameters) == 1
    parameter = parameters[0]

    assert parameter["name"] == "_readable"
    assert parameter["annotation"]["type"] == "__READABLE__"
