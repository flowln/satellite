import ast
import copy
import itertools
import pathlib
from string import Template
import subprocess
import sys

import click

_GET_ASYNC_CODE = Template("""
response = await self.get_implementation(\"$endpoint\"$combined_parameters)
response.raise_for_status()
ret = $return_type_conversion(response.json())
return ret
""")

_POST_ASYNC_CODE = Template("""
response = await self.post_implementation(\"$endpoint\"$combined_parameters)
response.raise_for_status()
ret = $return_type_conversion(response.json())
return ret
""")

_GET_SYNC_CODE = Template("""
response = self.get_implementation(\"$endpoint\"$combined_parameters)
response.raise_for_status()
ret = $return_type_conversion(response.json())
return ret
""")

_POST_SYNC_CODE = Template("""
response = self.post_implementation(\"$endpoint\"$combined_parameters)
response.raise_for_status()
ret = $return_type_conversion(response.json())
return ret
""")


def _get_current_git_revision() -> str:
    command = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True)
    if command.returncode != 0:
        print(f"Failed to get current git revision: {command.stderr.decode()}", file=sys.stderr)

        return "unknown"

    return command.stdout.decode().strip()


def _load_template(package_root: pathlib.Path) -> ast.Module:
    template_path = package_root / "client" / "_base_template.py"

    with open(template_path) as _file:
        source_code = _file.read()

    parsed_template = ast.parse(source_code, template_path)

    if not isinstance(parsed_template.body[0], ast.Expr) or not isinstance(parsed_template.body[0].value, ast.Constant):
        print("Failed to parse template's docstring.", file=sys.stderr)

        return parsed_template

    docstring = Template(str(parsed_template.body[0].value.value))

    from datetime import UTC, datetime

    current_time = datetime.now(UTC).isoformat(timespec="minutes")
    git_revision = _get_current_git_revision()

    new_docstring = docstring.substitute(generation_date=current_time, generation_git_revision=git_revision)

    parsed_template.body[0].value.value = new_docstring

    return parsed_template


def _parse_args_and_kwargs_from_node(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str]:
    return_type_conversion = "()"
    if isinstance(node.returns, ast.Name):
        return_type_name = node.returns.id
        return_type_conversion = f"{return_type_name}.model_validate"
    elif node.returns is not None:
        return_type_conversion = ""

    args = node.args.args[:]
    if node.args.vararg is not None:
        args.append(node.args.vararg)
    kwargs = node.args.kwonlyargs[:]
    if node.args.kwarg is not None:
        kwargs.append(node.args.kwarg)

    combined_parameters = ", ".join(
        f"{_arg.arg}={_arg.arg}" for _arg in itertools.chain(args, kwargs) if _arg.arg != "self"
    )

    if len(combined_parameters) != 0:
        combined_parameters = ", " + combined_parameters

    return return_type_conversion, combined_parameters


def _generate_async_implementation(node: ast.AsyncFunctionDef, endpoint: str, endpoint_type: str):
    """
    Generate a default implementation for methods calling an endpoint.

    This function generates the code body of the method specified by 'node'.
    """
    return_type_conversion, combined_parameters = _parse_args_and_kwargs_from_node(node)

    match endpoint_type:
        case "GET":
            return ast.parse(
                _GET_ASYNC_CODE.substitute(
                    endpoint=endpoint,
                    combined_parameters=combined_parameters,
                    return_type_conversion=return_type_conversion,
                )
            )
        case "POST":
            return ast.parse(
                _POST_ASYNC_CODE.substitute(
                    endpoint=endpoint,
                    combined_parameters=combined_parameters,
                    return_type_conversion=return_type_conversion,
                )
            )
        case _:
            raise RuntimeError(
                f"Failed to generate implementation for '{endpoint}': Unrecognized type: {endpoint_type}"
            )


def _generate_sync_implementation(node: ast.FunctionDef, endpoint: str, endpoint_type: str):
    """
    Generate a default implementation for methods calling an endpoint.

    This function generates the code body of the method specified by 'node'.
    """
    return_type_conversion, combined_parameters = _parse_args_and_kwargs_from_node(node)

    match endpoint_type:
        case "GET":
            return ast.parse(
                _GET_SYNC_CODE.substitute(
                    endpoint=endpoint,
                    combined_parameters=combined_parameters,
                    return_type_conversion=return_type_conversion,
                )
            )
        case "POST":
            return ast.parse(
                _POST_SYNC_CODE.substitute(
                    endpoint=endpoint,
                    combined_parameters=combined_parameters,
                    return_type_conversion=return_type_conversion,
                )
            )
        case _:
            raise RuntimeError(
                f"Failed to generate implementation for '{endpoint}': Unrecognized type: {endpoint_type}"
            )


def _clear_fastapi_dependencies_from_args(node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
    """Remove FastAPI dependency arguments from a node's signature."""

    def _set_clean_arguments(name: str, defaults_name: str | None = None):
        arguments = getattr(node.args, name)

        if arguments is None:
            return

        is_single_element = isinstance(arguments, ast.arg)
        if is_single_element:
            arguments = [arguments]

        new_arguments = []
        for arg_idx, argument in enumerate(arguments):
            # NOTE: All these are used to remove 'Annotated[x, ...]' arguments, since they're likely
            # FastAPI dependencies, and shouldn't be included in the client.
            if argument.annotation is None or not isinstance(argument.annotation, ast.Subscript):
                pass
            elif not isinstance(argument.annotation.value, ast.Name) or argument.annotation.value.id != "Annotated":
                pass
            elif not isinstance(argument.annotation.slice, ast.Tuple):
                pass
            else:
                if defaults_name is not None:
                    default_list: list = getattr(node.args, defaults_name)
                    arg_idx_in_defaults = arg_idx - (len(arguments) - len(default_list))
                    if 0 <= arg_idx_in_defaults < len(default_list):
                        default_list.pop(arg_idx_in_defaults)
                    setattr(node.args, defaults_name, default_list)
                continue

            new_arguments.append(argument)

        setattr(node.args, name, new_arguments if not is_single_element else new_arguments[0])

    _set_clean_arguments("args", "defaults")
    _set_clean_arguments("vararg")
    _set_clean_arguments("kwonlyargs", "kw_defaults")
    _set_clean_arguments("kwarg")

    return node


def _parse_and_generate_from_class_node(class_node: ast.ClassDef, package_root: pathlib.Path) -> ast.Module:
    """Parse endpoint methods from 'class_node', and generate equivalent client-side methods from them."""
    finished_node = _load_template(package_root)

    def _add_endpoint(node: ast.AsyncFunctionDef, endpoint: str, endpoint_type: str):
        """Add the endpoint implemented by 'node' to the list of methods to generate in the output."""
        nonlocal finished_node

        # Change the method name to the endpoint's name
        node.name = endpoint.replace("/", "_").removeprefix("_")

        node = _clear_fastapi_dependencies_from_args(node)

        for _output_node in finished_node.body:
            if not isinstance(_output_node, ast.ClassDef):
                continue

            if "async" in _output_node.name.lower():
                # Keep only the docstring, and add the new logic
                node.body = [node.body[0], _generate_async_implementation(node, endpoint, endpoint_type)]

                _output_node.body.append(copy.deepcopy(node))
            else:
                sync_node = ast.FunctionDef(
                    node.name,
                    node.args,
                    node.body,
                    node.decorator_list,
                    node.returns,
                    node.type_comment,
                    node.type_params,
                    col_offset=node.col_offset,
                    end_col_offset=node.end_col_offset,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno,
                )

                # Keep only the docstring, and add the new logic
                sync_node.body = [sync_node.body[0], _generate_sync_implementation(sync_node, endpoint, endpoint_type)]

                _output_node.body.append(sync_node)

    for _node in class_node.body:
        match _node:
            case ast.AsyncFunctionDef(_, _, _, _decorators, _, _, _):
                for idx, _decorator in enumerate(_node.decorator_list):
                    if not isinstance(_decorator, ast.Call):
                        continue
                    if not isinstance(_decorator.func, ast.Name):
                        continue
                    if "endpoint" not in _decorator.func.id:
                        continue
                    if not isinstance(_decorator.args[0], ast.Constant):
                        continue

                    endpoint = str(_decorator.args[0].value)
                    endpoint_type = _decorator.func.id.split("_")[0].upper()

                    _node_copy = copy.deepcopy(_node)
                    _node_copy.decorator_list.pop(idx)
                    _add_endpoint(_node_copy, endpoint, endpoint_type)

                    break
            case _:
                continue

    return finished_node


def _run_ruff_on_file(file: str | pathlib.Path):
    ruff_format_output = subprocess.run(["ruff", "format", file], capture_output=True)

    if ruff_format_output.returncode != 0:
        print("Failed to run 'ruff format' on the generated file:", file=sys.stderr)
        print(ruff_format_output.stdout.decode(), file=sys.stderr)
        print(ruff_format_output.stderr.decode(), file=sys.stderr)
    else:
        print("Successfully ran 'ruff format' on the generated file.")

    ruff_check_output = subprocess.run(["ruff", "check", "--fix", file], capture_output=True)

    if ruff_check_output.returncode != 0:
        print("Failed to run 'ruff check --fix' on the generated file:", file=sys.stderr)
        print(ruff_check_output.stdout.decode(), file=sys.stderr)
        print(ruff_check_output.stderr.decode(), file=sys.stderr)
    else:
        print("Successfully ran 'ruff check --fix' on the generated file.")


@click.command("satellite-client-generate")
@click.option("-o", "--output-file", default=None, help="Path on which to output the generated file.")
def generate_client(output_file: str | None):
    """Generate a base implementation of Python clients for the satellite server API."""
    package_root = pathlib.Path(__file__).parent.parent

    queue_manager_path = package_root / "server" / "queue_manager.py"

    with open(queue_manager_path) as _file:
        source_code = _file.read()

    root_node = ast.parse(source_code, queue_manager_path)

    queue_manager_class_node = None
    for _node in root_node.body:
        match _node:
            case ast.ClassDef("QueueManager", _):
                queue_manager_class_node = _node
                break
            case _:
                continue

    if queue_manager_class_node is None:
        raise RuntimeError("Failed to find 'QueueManager' class inside AST.")

    finished_node = _parse_and_generate_from_class_node(queue_manager_class_node, package_root)
    generated_source_code = ast.unparse(finished_node)

    if output_file is None:
        output_file = str(package_root / "client" / "_generated_base_client.py")

    with open(output_file, "w") as _file:
        _file.write(generated_source_code)

    _run_ruff_on_file(output_file)

    print(f"Generated file successfully at '{output_file}'.")
