"""Strict parser for the two-column UEFA tactical lineup PDF format."""
import re
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader


def parse_lineup_pdf(content, *, kickoff_utc, expected_header):
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) != 1:
        raise ValueError('Expected a one-page UEFA lineup sheet')
    return parse_lineup_text(reader.pages[0].extract_text(extraction_mode='layout'),
                             kickoff_utc=kickoff_utc, expected_header=expected_header)


def parse_lineup_text(text, *, kickoff_utc, expected_header):
    lines = text.splitlines()
    when = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
    # PDF headers are in English; month names must not depend on OS locale.
    months = 'January February March April May June July August September October November December'.split()
    day_pattern = rf'\b0?{when.day} {months[when.month-1]} {when.year}\b'
    if (not lines or lines[0].strip() != 'Tactical Line-ups' or not re.search(day_pattern, '\n'.join(lines[:3]))
            or not any(' '.join(line.split()) == ' '.join(expected_header.split()) for line in lines[:6])):
        raise ValueError('UEFA sheet title, date or team header mismatch')
    home, away = [], []
    for line in lines:
        match = re.fullmatch(r'\s*(\d{1,3})\s+(.+?)\s{3,}(\d{1,3})\s+(.+?)\s*', line)
        if not match:
            if home:
                break  # The starter block ends before the pitch diagram/bench.
            continue
        home.append({'number': int(match[1]), 'name': match[2].strip()})
        away.append({'number': int(match[3]), 'name': match[4].strip()})
    for roster in (home, away):
        if len(roster) != 11 or len({p['name'] for p in roster}) != 11 or len({p['number'] for p in roster}) != 11:
            raise ValueError('Expected 11 unique starters per side')
    return {'home': home, 'away': away}
