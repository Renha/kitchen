from kitchen import config
import pytest


class TestAssertions:
    def test_unknown_format(self):
        with pytest.raises(config.ConfigFormatUnrecognized):
            config.KitchenConfig("kitchen.xml")

    def test_read_error(self):
        with pytest.raises(config.ConfigReadError):
            config.KitchenConfig("dog/kitten.json")

    def test_wrong_format(self):
        with pytest.raises(config.ConfigParsingError):
            config.KitchenConfig("tests/config_good.yaml", config.ConfigFormat.JSON)

    def test_schema_error(self):
        with pytest.raises(config.ConfigParsingError):
            config.KitchenConfig("tests/config_bad.yaml")


class TestNormalLoading:
    def test_yaml(self):
        conf = config.KitchenConfig("tests/config_good.yaml")
        assert not conf.data is None
        assert "kitchen" in conf.data

    def test_order(self):
        conf = config.KitchenConfig("tests/config_good.yaml")
        machine_kinds = tuple((m["kind"] for m in conf.data["kitchen"]))
        machine_kinds_expected = ("robot", "oven", "robot", "camera-system")
        assert machine_kinds == machine_kinds_expected

    def test_yaml_json(self):
        conf_yaml = config.KitchenConfig("tests/config_good.yaml")
        conf_json = config.KitchenConfig("tests/config_good.json")
        assert str(conf_yaml.data) == str(conf_json.data)
