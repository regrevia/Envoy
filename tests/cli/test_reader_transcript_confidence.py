"""A5: transcript coverage is judged against the span the ASR pass reached."""

from envoy_cli import reader


def _speech(seconds: float) -> list[dict]:
    return [{"start": 0.0, "end": seconds}]


def test_a_partial_pass_is_scored_against_the_transcribed_span():
    # 40s of a one-hour video transcribed, 12s of real speech inside it: the
    # pass covered its range, so the transcript is quotable.
    confidence, seconds, ratio = reader._transcript_confidence(
        _speech(12.0),
        3600.0,
        processed_from=0.0,
        processed_until=40.0,
    )
    assert confidence == "normal"
    assert seconds == 12.0
    assert ratio == 0.3


def test_the_same_speech_fails_when_the_whole_file_was_processed():
    confidence, _, ratio = reader._transcript_confidence(_speech(12.0), 3600.0)
    assert confidence == "low"
    assert ratio < 0.02


def test_the_processed_span_starts_at_the_requested_offset():
    # A read of 600-640s with 12s of speech covers 30% of its own window.
    confidence, _, ratio = reader._transcript_confidence(
        [{"start": 600.0, "end": 612.0}],
        3600.0,
        processed_from=600.0,
        processed_until=640.0,
    )
    assert confidence == "normal"
    assert ratio == 0.3


def test_an_unusable_span_falls_back_to_the_full_duration():
    confidence, _, ratio = reader._transcript_confidence(
        _speech(2.0),
        3600.0,
        processed_from=None,
        processed_until=None,
    )
    assert confidence == "low"
    assert 0.0 < ratio < 0.01
