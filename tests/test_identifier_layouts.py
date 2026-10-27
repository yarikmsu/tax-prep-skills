"""Official blank IRS words and field rectangles; no taxpayer documents.

identifier_layouts.json was extracted with the locked pdfplumber from the
source URL and page named in each record. Words are [text,x0,top,x1,bottom];
fields are [field name,x0,top,x1,bottom]. Synthetic values sit inside the
form's own fields, without changing any heading or instruction geometry.
"""
import json
from pathlib import Path

import pytest

from agents.lib.parse_utils import _governed_runs

LAYOUTS = json.loads((Path(__file__).parent / 'fixtures/prior_return/identifier_layouts.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('record', LAYOUTS, ids=['1095-A', '8332', 'EIC'])
def test_every_identifier_in_the_remaining_official_layouts(record):
    words = [dict(zip(('text', 'x0', 'top', 'x1', 'bottom'), word)) for word in record['words']]
    values = []
    for i, (_, x0, top, x1, bottom) in enumerate(record['fields']):
        value = ''.join(('000', '00', str(1000 + i)))
        values.append(value)
        words.append({'text': value, 'x0': x0 + 2, 'x1': x0 + 52, 'top': (top + bottom) / 2 - 4, 'bottom': (top + bottom) / 2 + 5})
    assert len(values) == (5 if '1095' in record['source'] else 3)
    governed = _governed_runs(words)
    assert all(value in governed for value in values)
    assert not governed.difference(values), 'no printed form number is an identifier'
