"""The objective of these is mainly to validate inputs for plans, and provide a common representation to clients."""

# NOTE: The queueserver compatibility implementations are based of this upstream documentation:
# https://github.com/bluesky/bluesky-queueserver/blob/7427f492737b71cdeafd869133760ce8ee2cf07f/src/bluesky_queueserver/manager/conversions.py#L31

from base64 import standard_b64decode, standard_b64encode
from collections.abc import Callable, Iterable, MutableSequence, Sequence
import inspect
import logging
import pickle
from types import SimpleNamespace
from typing import Any

from bluesky.protocols import Movable, Readable
from numpydoc.docscrape import FunctionDoc, ParseError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError as PydanticValidationError,
    computed_field,
    field_serializer,
    field_validator,
)

logger = logging.getLogger("satellite.annotations")


BLUESKY_PROTOCOL_VARIANTS = {
    "bluesky.protocols.Readable": "__READABLE__",
    "bluesky.protocols.Movable": "__MOVABLE__",
}


class _NoValueSentinel: ...


class _Argument(BaseModel):
    """Definition of a plan argument, used for validation and for usage by clients."""

    model_config = ConfigDict(use_attribute_docstrings=True, serialize_by_alias=True)

    name: str
    """Name of the argument, as defined in the plan signature."""
    description: str | None = Field(default=None, exclude_if=lambda x: x is None)
    """Description of the argument, written on the docstring in NumpyDoc-style."""

    annotation: Any = None
    """Type of the argument, used for validation."""

    kind: inspect._ParameterKind
    """Position constraints for this argument."""

    default_value: Any = Field(
        alias="default",
        default=_NoValueSentinel(),
        exclude_if=lambda x: isinstance(x, _NoValueSentinel),
    )
    """Default value for this argument, if present."""
    required: bool = True
    """Whether this argument must be provided, meaning it doesn't have a default value."""

    minimum_value: int | float | None = Field(alias="min", default=None, exclude_if=lambda x: x is None)
    """Field used for queueserver compatibility."""
    maximum_value: int | float | None = Field(alias="max", default=None, exclude_if=lambda x: x is None)
    """Field used for queueserver compatibility."""
    step_by_value: int | float | None = Field(alias="step", default=None, exclude_if=lambda x: x is None)
    """Field used for queueserver compatibility."""

    @computed_field
    @property
    def is_optional(self) -> bool:
        """Field used for queueserver compatibility."""
        return not self.required

    @computed_field
    @property
    def is_list(self) -> bool:
        """Field used for queueserver compatibility."""
        try:
            # NOTE: Try to validate an empty list to see if the type annotation is compatible with it.
            validator = TypeAdapter(self.annotation, config=ConfigDict(arbitrary_types_allowed=True))
            validator.validate_python([])
        except PydanticValidationError:
            return False

        return True

    def _sanitize_protocol_types(self, value: str) -> str:
        for src_value, dst_value in BLUESKY_PROTOCOL_VARIANTS.items():
            value = value.replace(src_value, dst_value)

        # Remove <class '...'> wrapping if the value came from a direct type -> str conversion.
        value = value.replace("<class '", "")
        value = value.replace("'>", "")

        return value

    @field_serializer("annotation", mode="plain", when_used="json")
    def annotation_serializer(self, value: Any) -> dict[str, bytes | str]:
        return {"value": standard_b64encode(pickle.dumps(value)), "type": self._sanitize_protocol_types(str(value))}

    @field_validator("annotation", mode="before")
    @classmethod
    def annotation_validator(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return pickle.loads(standard_b64decode(value["value"]))
        return value

    @field_serializer("kind", mode="plain", when_used="json")
    def kind_serializer(self, value: inspect._ParameterKind) -> dict[str, str | int]:
        return {"name": value.name, "value": value.value}

    @field_validator("kind", mode="before")
    @classmethod
    def kind_validator(cls, value: dict[str, str | int] | inspect._ParameterKind) -> inspect._ParameterKind:
        if isinstance(value, inspect._ParameterKind):  # noqa
            return value
        return inspect._ParameterKind(value["value"])  # noqa


class PlanAnnotation(BaseModel):
    """Description of a plan's annotation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True, serialize_by_alias=True)

    plan_name: str = Field(alias="name")
    """Name of the plan."""
    plan_description: str = Field(alias="description", default="no description")
    """Description of the plan, usually taken from its docstring."""

    plan_signature: inspect.Signature = Field(alias="signature")

    arguments: MutableSequence[_Argument] = Field(alias="parameters", default=[])
    """Parameters definitions for this plan, keyed by their name."""

    @field_serializer("plan_signature", mode="plain", when_used="json")
    def signature_serializer(self, value: inspect.Signature) -> bytes:  # noqa: D102
        return standard_b64encode(pickle.dumps(value))

    @field_validator("plan_signature", mode="before")
    @classmethod
    def signature_validator(cls, value: Any) -> Any:  # noqa: D102
        if isinstance(value, (bytes, str)):
            return pickle.loads(standard_b64decode(value))
        return value

    def get_argument_with_name(self, name: str) -> _Argument | None:
        """Find an argument definition object with the given name."""
        for argument in self.arguments:
            if argument.name == name:
                return argument


class DeviceAnnotation(BaseModel):
    """Description of a device's annotation."""

    device_name: str

    is_readable: bool = False
    is_movable: bool = False

    def validate_as_argument(self, argument_annotation) -> bool:
        """Test whether this device is valid for the given annotation."""
        if argument_annotation is Readable:
            return self.is_readable
        if argument_annotation is Movable:
            return self.is_movable

        logger.warning(
            "Trying to validate device '%s' (type: %s) with unrecognized annotation '%s'.",
            self.device_name,
            str(type(argument_annotation)),
            str(argument_annotation),
        )
        return False

    def create_mock(self) -> object:
        """Emulate a device implementing the correct protocols."""
        mock = SimpleNamespace()

        mock.name = lambda: self.device_name

        if self.is_readable:
            mock.describe = lambda: ...
            mock.read = lambda: ...

        if self.is_movable:
            mock.set = lambda _x: ...

        return mock


def generate_annotation_for_plan(plan: Callable, plan_name: str) -> PlanAnnotation:
    """
    Generate an annotation object for a plan.

    This object can then be used to validate arguments to the plan's execution,
    ensuring all given arguments match their supposed types.
    """
    signature = inspect.signature(plan)
    plan_annotation = PlanAnnotation(name=plan_name, signature=signature)

    try:
        parsed_docstring = FunctionDoc(plan, doc=inspect.getdoc(plan))
    except ParseError as e:
        logger.error("Failed to parse docstring for plan '%s'.", plan_name)
        logger.exception(e)

        return plan_annotation

    summary = parsed_docstring.get("Summary", [""])
    if len(summary) != 0:
        plan_annotation.plan_description = summary[0]

    for param_name, param in signature.parameters.items():
        parameter = _Argument(
            name=param_name,
            kind=param.kind,
            annotation=param.annotation,
        )

        parameter.required = param.default is inspect.Parameter.empty
        if not parameter.required:
            parameter.default_value = param.default

        plan_annotation.arguments.append(parameter)

    for arg_name, _, arg_desc in parsed_docstring.get("Parameters", []):
        if (argument := plan_annotation.get_argument_with_name(arg_name)) is not None:
            argument.description = "\n".join(arg_desc)

    # TODO: Parse from '_custom_parameter_annotation_', used by bluesky-queueserver
    if hasattr(plan, "_custom_parameter_annotation_"):
        pass

    return plan_annotation


def generate_annotation_for_device(device: object, device_name: str) -> DeviceAnnotation:
    """
    Generate an annotation object for the given device.

    This object can then be used to validate plan arguments, checking whether this
    device conforms to the plan's expected signature.
    """
    device_annotation = DeviceAnnotation(device_name=device_name)

    if isinstance(device, Readable):
        device_annotation.is_readable = True
    if isinstance(device, Movable):
        device_annotation.is_movable = True

    return device_annotation


class ValidationError(TypeError):
    """Error when validating a plan."""

    @classmethod
    def from_exc(cls, other: Exception):
        """Create a ValidationError from a plain Exception."""
        return ValidationError(str(other))


def validate_plan(
    annotation: PlanAnnotation,
    args: Sequence[Any],
    kwargs: dict[str, Any],
    *,
    device_annotations: dict[str, DeviceAnnotation] | None = None,
) -> bool:
    """
    Validates that the provided arguments are correct and sufficient for the given plan.

    Returns
    -------
    bool
        Always True, meaning the provided arguments are indeed valid. Otherwise, an exception is raised.

    Raises
    ------
    ValidationError
        If any argument is incorrect or there's missing arguments.
        Detailed information is provided in the exception itself.

    """
    if device_annotations is None:
        device_annotations = {}

    signature = annotation.plan_signature

    try:
        bound_arguments = signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise ValidationError.from_exc(exc) from exc

    for argument_name, argument_value in bound_arguments.arguments.items():
        argument = annotation.get_argument_with_name(argument_name)
        if argument is None:
            raise RuntimeError
        _argument_annotation = argument.annotation

        if isinstance(argument_value, Iterable) and not isinstance(argument_value, str):
            is_valid = _validate_iterable_argument(
                argument_value,
                _argument_annotation,
                device_annotations=device_annotations,
            )

            if not is_valid:
                raise ValidationError(f"Value '{argument_value}' for argument '{argument_name}' is not valid.")

            continue

        if isinstance(argument_value, str) and argument_value in device_annotations:
            is_valid = device_annotations[argument_value].validate_as_argument(_argument_annotation)

            if not is_valid:
                raise ValidationError(f"Device '{argument_value}' for argument '{argument_name}' is not valid.")

            continue

        validator = TypeAdapter(_argument_annotation, config=ConfigDict(arbitrary_types_allowed=True))

        try:
            _v = validator.validate_python(argument_value)
        except PydanticValidationError as exc:
            raise ValidationError.from_exc(exc) from exc

    return True


def _validate_iterable_argument(
    values: Iterable,
    annotation: Any,
    *,
    device_annotations: dict[str, DeviceAnnotation] | None = None,
) -> bool:
    if device_annotations is None:
        device_annotations = {}

    devices = values

    if isinstance(devices, Sequence):
        devices = list(values)

        for index, device in enumerate(devices):
            if isinstance(device, str) and device in device_annotations:
                mock = device_annotations[device].create_mock()
                devices[index] = mock

    if not isinstance(annotation, str):
        validator = TypeAdapter(annotation, config=ConfigDict(arbitrary_types_allowed=True))

        try:
            _v = validator.validate_python(devices)
        except PydanticValidationError as exc:
            raise ValidationError.from_exc(exc) from exc

        return True

    return False
