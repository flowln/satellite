import logging

from numpydoc.docscrape import NumpyDocString, ParseError

logger = logging.getLogger(__file__)


def docstring_numpy_to_markdown(docstring: str) -> str:
    """Convert a NumpyDoc-style docstring into a compatible Markdown render."""
    try:
        processed_description = NumpyDocString(docstring)
    except ParseError as e:
        logger.error("Failed to parse docstring as NumpyDoc-styled: %s", str(e))

        return ""

    markdown_description = ""
    if (summary := processed_description.get("Summary")) is not None:
        markdown_description += "\n".join(summary) + "\n\n"
    if (ext_summary := processed_description.get("Extended Summary")) is not None:
        markdown_description += "\n".join(ext_summary) + "\n\n"
    if (parameters := processed_description.get("Parameters")) is not None and len(parameters) != 0:
        markdown_description += "## Parameters\n\n"

        for p_name, p_annotation, p_description in parameters:
            p_description = "\n".join(p_description)
            markdown_description += f"- **{p_name}**: _{p_annotation}_\n\n\t{p_description}\n\n"

    return markdown_description
