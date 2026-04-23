from app.services.patchwise_service import parse_review_cards


def test_patchwise_review_card_parser():
    raw = "ERROR: missing SPDX\nWARNING: line exceeds 100 chars\nINFO: style looks okay"
    cards = parse_review_cards(raw)
    assert len(cards) == 3
    severities = [card['severity'] for card in cards]
    assert 'HIGH' in severities
    assert 'MEDIUM' in severities
