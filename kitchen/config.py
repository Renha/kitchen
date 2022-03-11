"""
Loads kitchen configuration from the given source and provides
convenient interface for using the configuration.
"""
from __future__ import annotations

from enum import Enum
from json import load as json_load
from os import path
from typing import Optional, TextIO, List, Union, Dict

from yaml import load as yaml_load
from yaml import Loader as yaml_Loader

from jsonschema import validate


class ConfigFormatUnrecognized(Exception):
    pass


class ConfigReadError(Exception):
    pass


class ConfigParsingError(Exception):
    pass


class ConfigFormat(Enum):
    YAML = {"yaml", "yml"}
    JSON = {"json"}


class KitchenConfig:
    """
    Kitchen configuration class, loadable from the provided file in either
    .yaml or .json format.

    Arguments:
        file_path: path to the config file placement
        file_format: file format, if None then juessed based on the file name

    Raises:
        ConfigFormatUnrecognized: unknown file format (based on args, file
            itself has not been checked)
        ConfigReadError: file reading error, parsing has not been tried
        ConfigParsingError: wrong inner file format or schema check failed.
    """

    TypeAlias = List[Dict[str, Union[str, int, bool, List[str]]]]
    schema = {
        "type": "object",
        "required": ["kitchen"],
        "properties": {
            "kitchen": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind"],
                    "properties": {
                        "kind": {"type": "string"},
                        "count": {"type": "integer"},
                        "operations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "border_state": {"type": "string"},
                        "reset_state": {"type": "string"},
                        "after_oven": {"type": "boolean"},
                    },
                },
            },
        },
    }

    def __init__(
        self,
        file_path: str = "kitchen/kitchen.yaml",
        file_format: Optional[ConfigFormat] = None,
    ):
        if file_format is None:
            try:
                file_postfix = path.splitext(file_path)[1].lower()[1:]
                for config_format in ConfigFormat:
                    if file_postfix in config_format.value:
                        file_format = config_format
                        break
            except:
                file_format = None
        if not file_format in set(ConfigFormat):
            raise ConfigFormatUnrecognized(file_format, set(ConfigFormat))

        if file_format == ConfigFormat.YAML:

            def loader_yaml_json_like(fp: TextIO):
                return yaml_load(fp, Loader=yaml_Loader)

            loader = loader_yaml_json_like
        elif file_format == ConfigFormat.JSON:
            loader = json_load
        try:
            with open(file_path, "r") as input_file:
                try:
                    data = loader(input_file)
                except:
                    raise ConfigParsingError
        except (IOError, FileNotFoundError, OSError) as e:
            raise ConfigReadError(e, file_path)

        try:
            validate(instance=data, schema=self.schema)
        except Exception as e:
            raise ConfigParsingError(e, data)

        self.data = data
