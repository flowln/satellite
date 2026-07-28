"""The objective of these is mainly to validate inputs for plans, and provide a common representation to clients."""

from base64 import standard_b64decode, standard_b64encode
from collections.abc import Callable, Iterable, Sequence
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
    TypeAdapter,
    ValidationError as PydanticValidationError,
    field_serializer,
    field_validator,
)
from pydantic.v1.utils import sequence_like

logger = logging.getLogger("satellite.annotations")


class _Argument(BaseModel):
    name: str
    """Name of the argument, as defined in the plan signature."""
    description: str | None = None
    """Description of the argument, written on the docstring in NumpyDoc-style."""

    kind: inspect._ParameterKind
    """Position constraints for this argument."""
    required: bool = True
    """Whether this argument must be provided, meaning it doesn't have a default value."""

    annotation: Any = None
    """Type of the argument, used for validation."""

    @field_serializer("annotation", mode="plain", when_used="json")
    def annotation_serializer(self, value: Any) -> bytes:
        return standard_b64encode(pickle.dumps(value))

    @field_validator("annotation", mode="before")
    @classmethod
    def annotation_validator(cls, value: Any) -> Any:
        if isinstance(value, (bytes, str)):
            return pickle.loads(standard_b64decode(value))
        return value


class PlanAnnotation(BaseModel):
    """Description of a plan's annotation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan_name: str
    plan_signature: inspect.Signature

    arguments: dict[str, _Argument] = {}

    @field_serializer("plan_signature", mode="plain", when_used="json")
    def signature_serializer(self, value: inspect.Signature) -> bytes:  # noqa: D102
        return standard_b64encode(pickle.dumps(value))

    @field_validator("plan_signature", mode="before")
    @classmethod
    def signature_validator(cls, value: Any) -> Any:  # noqa: D102
        if isinstance(value, (bytes, str)):
            return pickle.loads(standard_b64decode(value))
        return value


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
    plan_annotation = PlanAnnotation(plan_name=plan_name, plan_signature=signature)

    try:
        parsed_docstring = FunctionDoc(plan, doc=inspect.getdoc(plan))
    except ParseError as e:
        logger.error("Failed to parse docstring for plan '%s'.", plan_name)
        logger.exception(e)

        return plan_annotation

    for param_name, param in signature.parameters.items():
        parameter = _Argument(
            name=param_name,
            kind=param.kind,
            annotation=param.annotation,
        )

        parameter.required = param.default is inspect.Parameter.empty

        plan_annotation.arguments[param_name] = parameter

    for arg_name, _, arg_desc in parsed_docstring.get("Parameters", []):
        if (argument := plan_annotation.arguments.get(arg_name)) is not None:
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
        _argument_annotation = annotation.arguments[argument_name].annotation

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

    if sequence_like(devices):
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
