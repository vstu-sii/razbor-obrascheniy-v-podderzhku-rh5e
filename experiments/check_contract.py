"""Validate the whole OpenAPI document, all component schemas and 200 examples."""
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate_spec
from lab2_checks import expanded_schema

root = Path(__file__).resolve().parents[1]
spec = yaml.safe_load((root / 'api/openapi.yaml').read_text(encoding='utf-8'))
validate_spec(spec)
for name in spec['components']['schemas']:
    expanded_schema(name)
validator = Draft202012Validator(expanded_schema('AnalyzeResponse'))
examples = spec['paths']['/v1/analyze']['post']['responses']['200']['content']['application/json']['examples']
for example in examples.values():
    validator.validate(example['value'])
print('PASS: full OpenAPI; component schemas; 4 response examples')
