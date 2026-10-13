"""Transport JSON Schema with the unchanged Phase 1 typed payload alternatives."""
from .models import EVENT_MODELS, EventEnvelope
from simulator.models.events import MatchEvent, EventType

def envelope_json_schema():
    schema = EventEnvelope.model_json_schema()
    definitions = schema.setdefault('$defs', {})
    alternatives = []
    for kind, model in EVENT_MODELS.items():
        typed = model.model_json_schema()
        definitions.update(typed.pop('$defs', {}))
        typed['properties']['event_type'] = {'type': 'string', 'const': kind.value}
        typed.setdefault('required', []).append('event_type')
        definitions[model.__name__] = typed
        alternatives.append({'$ref': '#/$defs/' + model.__name__})
    base = MatchEvent.model_json_schema()
    definitions.update(base.pop('$defs', {}))
    base['properties']['event_type'] = {'type': 'string', 'enum': [kind.value for kind in EventType if kind not in EVENT_MODELS]}
    definitions['MatchEvent'] = base
    alternatives.append({'$ref': '#/$defs/MatchEvent'})
    schema['properties']['payload'] = {'oneOf': alternatives}
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    return schema
