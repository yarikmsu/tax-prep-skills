"""Build final reader fixtures from blank official TY2025 forms.

Run --fill DIR in a scratch venv with pypdf, then --extract DIR through
sh agents/lib/run.sh. DIR contains the two PDFs at SOURCES. All supplied
identity values are constructed examples; no taxpayer documents are used.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SOURCES = {'1040': 'https://www.irs.gov/pub/irs-prior/f1040--2025.pdf',
           '540': 'https://www.ftb.ca.gov/forms/2025/2025-540.pdf'}
PRIMARY = ''.join(('000', '00', '6789'))
SPOUSE = ''.join(('000', '00', '1111'))
VALUES = {
    '1040_page1': ('f1040--2025.pdf', 0, {
        'f1_14[0]': 'JOHN A', 'f1_15[0]': 'EXAMPLE', 'f1_16[0]': PRIMARY,
        'f1_17[0]': 'JANE B', 'f1_18[0]': 'EXAMPLE', 'f1_19[0]': SPOUSE,
        'f1_20[0]': '100 SAMPLE ST', 'f1_21[0]': '12',
        'f1_22[0]': 'TESTVILLE', 'f1_23[0]': 'CA', 'f1_24[0]': '90000',
        'f1_31[0]': 'ALPHA', 'f1_32[0]': 'BETA',
        'f1_35[0]': 'EXAMPLE', 'f1_36[0]': 'EXAMPLE',
        'f1_39[0]': ''.join(('000', '00', '2222')), 'f1_40[0]': ''.join(('000', '00', '3333')),
        'f1_43[0]': 'SON', 'f1_44[0]': 'DAUGHTER',
        'topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[1]': '/2',
        'f1_70[0]': '(3,000)', 'f1_73[0]': '50,000', 'f1_74[0]': '0', 'f1_75[0]': '50,000'}),
    '540_side3': ('2025-540.pdf', 2, {'540_form_3027': '700'}),
    '540_side4': ('2025-540.pdf', 3, {'540_form_4003': '200', '540_form_4004': '500'}),
}


def fill(directory):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    for name, (source, page, values) in VALUES.items():
        writer = PdfWriter()
        writer.append(PdfReader(directory / source), pages=[page])
        writer.update_page_form_field_values(None, values, auto_regenerate=False, flatten=True)
        writer.remove_annotations(subtypes='/Widget')
        writer.root_object.pop(NameObject('/AcroForm'), None)
        writer.write(directory / f'aa44-{name}.pdf')


def extract(directory):
    from agents.lib.parse_utils import extract_pdf_text, extract_pdf_words
    pages = {}
    for name, (source, page, _) in VALUES.items():
        path = directory / f'aa44-{name}.pdf'
        pages[name] = {'source': SOURCES['1040' if '1040' in name else '540'], 'source_page': page,
                       'text': extract_pdf_text(str(path), workspace=str(directory))[0],
                       'words': extract_pdf_words(str(path), workspace=str(directory), identity_layout=True)[0]}
    # Keep one word per line so a changed box can be reviewed independently.
    sections = []
    for name, record in pages.items():
        fields = [f'    {json.dumps(key)}: {json.dumps(value)},' for key, value in record.items() if key != 'words']
        fields += ['    "words": [', ',\n'.join('      ' + json.dumps(word) for word in record['words']), '    ]']
        sections.append('  ' + json.dumps(name) + ': {\n' + '\n'.join(fields) + '\n  }')
    Path(__file__).with_name('final_readers_2025.json').write_text('{\n' + ',\n'.join(sections) + '\n}\n', encoding='utf-8')


if __name__ == '__main__':
    (fill if sys.argv[1] == '--fill' else extract)(Path(sys.argv[2]))
