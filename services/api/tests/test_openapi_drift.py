"""The OpenAPI document that is committed, the one the application generates now, and the TypeScript types made from it
must agree. Pure Python: no database, no native extension (so it runs where Smart App Control blocks Schemathesis).

When one of these fails, regenerate the generated files (see README, "API types"):

    cd services\\api
    uv run --no-sync python -c "import json, pathlib; from geopoc.api.main import app; pathlib.Path('openapi.json').write_text(json.dumps(app.openapi(), indent=2) + '\\n', encoding='utf-8')"
    cd ..\\..\\apps\\web; pnpm run gen:api
"""

import json
import re

from geopoc.api.main import app
from geopoc.config import REPO_ROOT

OPENAPI_FILE = REPO_ROOT / "services" / "api" / "openapi.json"
TYPES_FILE = REPO_ROOT / "apps" / "web" / "src" / "api" / "schema.d.ts"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
REGENERATE = (
    "Regenerate the API files: in services\\api run the one-line export from the README ('API types'), then in apps\\web "
    "run `pnpm run gen:api`."
)


def live_schema() -> dict:
    return json.loads(json.dumps(app.openapi()))  # what a client would receive over the wire


def committed_schema() -> dict:
    return json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))


def operations(spec: dict) -> set[tuple[str, str]]:
    return {(method.upper(), path) for path, item in spec["paths"].items() for method in item if method in HTTP_METHODS}


def describe_difference(committed: dict, live: dict) -> str:
    lines = []
    old, new = operations(committed), operations(live)
    for method, path in sorted(new - old):
        lines.append(f"  operation added in code, missing from the file: {method} {path}")
    for method, path in sorted(old - new):
        lines.append(f"  operation in the file, gone from code: {method} {path}")
    for name in sorted(set(live["components"]["schemas"]) - set(committed["components"]["schemas"])):
        lines.append(f"  schema added in code: {name}")
    for name in sorted(set(committed["components"]["schemas"]) - set(live["components"]["schemas"])):
        lines.append(f"  schema gone from code: {name}")
    for name in sorted(set(live["components"]["schemas"]) & set(committed["components"]["schemas"])):
        if live["components"]["schemas"][name] != committed["components"]["schemas"][name]:
            lines.append(f"  schema changed: {name}")
    for path in sorted(set(live["paths"]) & set(committed["paths"])):
        if live["paths"][path] != committed["paths"][path]:
            lines.append(f"  path changed: {path}")
    return "\n".join(lines) or "  (the documents differ in a field outside paths and schemas)"


def test_committed_openapi_document_matches_the_application():
    committed, live = committed_schema(), live_schema()
    assert committed == live, f"openapi.json is out of date.\n{describe_difference(committed, live)}\n{REGENERATE}"


def test_every_path_and_schema_appears_in_the_generated_typescript_types():
    """The TypeScript client types are generated from openapi.json; a forgotten `pnpm run gen:api` shows up here."""
    types = TYPES_FILE.read_text(encoding="utf-8")
    spec = committed_schema()
    missing_paths = [path for path in spec["paths"] if f'"{path}": {{' not in types]
    assert not missing_paths, f"schema.d.ts lacks {len(missing_paths)} path(s), e.g. {missing_paths[:3]}. {REGENERATE}"
    declared = set(re.findall(r"^\s{8}(\w+): \{", types, re.MULTILINE)) | set(
        re.findall(r"^\s{8}(\w+):", types, re.MULTILINE)
    )
    missing_schemas = [name for name in spec["components"]["schemas"] if name not in declared]
    assert not missing_schemas, f"schema.d.ts lacks schema(s) {missing_schemas[:5]}. {REGENERATE}"


def test_the_drift_check_notices_a_changed_schema():
    """The check itself must be able to fail: a copy of the document with one response changed no longer matches."""
    live = live_schema()
    tampered = json.loads(json.dumps(live))
    first_path = next(iter(tampered["paths"]))
    first_method = next(m for m in tampered["paths"][first_path] if m in HTTP_METHODS)
    tampered["paths"][first_path][first_method]["responses"].pop("200", None)
    assert tampered != live
    assert first_path in describe_difference(tampered, live)
