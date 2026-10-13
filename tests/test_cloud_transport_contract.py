"""Offline canonical storage contracts, never presented as Fabric verification."""
import pytest
from simulator.models.events import MatchEvent,EventType
from simulator.realtime.models import EventEnvelope
from simulator.production.cloud import fabric_envelopes

def stored(envelopes):
    return {'Tables':[{'Columns':[{'ColumnName':'envelope'}],
                       'Rows':[[e.model_dump(mode='json')] for e in envelopes]}]}

def envelope():
    return EventEnvelope.wrap(MatchEvent(event_id='event',match_id='match',timestamp=0,
        minute=0,event_type=EventType.MATCH_START),0,correlation_id='run')

def test_fabric_full_envelope_and_duplicates_preserved():
    e=envelope();expected={(e.match_id,e.event_id,e.sequence_number):e}
    assert fabric_envelopes(stored([e,e]),expected)==[e,e]

@pytest.mark.parametrize('field,value',[('correlation_id','wrong'),('sequence_number',99),
                                     ('source','corrupted')])
def test_fabric_valid_but_corrupted_envelope_rejected(field,value):
    e=envelope();expected={(e.match_id,e.event_id,e.sequence_number):e}
    bad=e.model_copy(update={field:value})
    with pytest.raises(ValueError):fabric_envelopes(stored([bad]),expected)

def test_fabric_invalid_payload_rejected():
    e=envelope();data=stored([e]);data['Tables'][0]['Rows'][0][0]['payload']['event_id']='different'
    with pytest.raises(ValueError):fabric_envelopes(data,{(e.match_id,e.event_id,e.sequence_number):e})
