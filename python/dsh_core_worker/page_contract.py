"""Frozen FPL page wire contracts, served and enforced by the independent Core."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .contracts import CoreError


def page_contract():
    return json.loads(Path(__file__).with_name("fpl_page_contract.json").read_text())


def install_contract(app):
    original = app.openapi
    contract = page_contract()

    def openapi():
        schema = original()
        schema.setdefault("components", {}).setdefault("schemas", {}).update(
            contract["components"]["schemas"]
        )
        for path, operations in contract["paths"].items():
            schema["paths"].setdefault(path, {}).update(operations)
        schema["info"]["description"] = (
            "Core-owned local APIs and adopted FPL page contracts. Browser owner session required."
        )
        return schema

    app.openapi = openapi


def validate_page_input(path, method, body):
    contract = page_contract()
    operation = contract["paths"].get(path, {}).get(method.lower(), {})
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if schema:
        validator = Draft202012Validator({**schema, "components": contract["components"]})
        if next(validator.iter_errors(body), None):
            raise CoreError("请求字段或参数不符合页面接口要求", "validation")
