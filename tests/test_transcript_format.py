"""Unit tests for transcript paragraphing (VTT parsing + re-formatting)."""
from kb.scrapers.youtube import (
    _assemble_paragraphs,
    _format_transcript_text,
    _parse_vtt_cues,
    _split_long_paragraph,
    _vtt_to_paragraphs,
)

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.500 --> 00:00:02.000
Nothing said on Ford guidance is a
recommendation to buy or sell any

00:00:02.100 --> 00:00:04.000
investments or products.
<c>All right, what's going on everybody?</c>

00:00:04.100 --> 00:00:06.000
>> Welcome back to another episode

00:00:06.100 --> 00:00:08.000
of Forward Guidance.

00:00:30.000 --> 00:00:32.000
After a long pause, a new topic.
"""


class TestParseVttCues:
    def test_cues_with_times_and_joined_text(self):
        cues = _parse_vtt_cues(VTT)
        assert len(cues) == 5
        assert cues[0][0] == 0.5 and cues[0][1] == 2.0
        assert cues[0][2] == "Nothing said on Ford guidance is a recommendation to buy or sell any"
        # inline tags stripped
        assert "All right, what's going on everybody?" in cues[1][2]
        # >> kept in cue text for the paragrapher to act on
        assert cues[2][2].startswith(">>")

    def test_rollup_duplicate_cue_dropped(self):
        vtt = ("00:00:01.000 --> 00:00:02.000\nthe market is going\n\n"
               "00:00:02.100 --> 00:00:03.000\nthe market is going\n\n"
               "00:00:03.100 --> 00:00:04.000\nthe market is going up\n")
        cues = _parse_vtt_cues(vtt)
        assert [c[2] for c in cues] == ["the market is going", "the market is going up"]

    def test_no_cues(self):
        assert _parse_vtt_cues("WEBVTT\n") == []


class TestVttToParagraphs:
    def test_speaker_marker_and_pause_break_paragraphs(self):
        out = _vtt_to_paragraphs(VTT)
        pars = out.split("\n\n")
        # 3 paragraphs: [disclaimer+greeting — no break signal between those
        # cues], [>> speaker change], [26-second pause → new topic].
        assert len(pars) == 3
        # caption lines joined into flowing text
        assert pars[0] == ("Nothing said on Ford guidance is a recommendation "
                           "to buy or sell any investments or products. "
                           "All right, what's going on everybody?")
        assert pars[1] == "Welcome back to another episode of Forward Guidance."
        assert pars[2] == "After a long pause, a new topic."
        assert ">>" not in out

    def test_empty(self):
        assert _vtt_to_paragraphs("") == ""


class TestFormatTranscriptText:
    def test_joins_caption_lines_and_breaks_at_markers(self):
        stored = ("\nNothing said on Ford guidance is a\nrecommendation to buy\n\n"
                  ">> dude? It's a pleasure.\nMan, great to see you.\n")
        out = _format_transcript_text(stored)
        assert out == ("Nothing said on Ford guidance is a recommendation to buy\n\n"
                       "dude? It's a pleasure. Man, great to see you.")

    def test_idempotent(self):
        once = _format_transcript_text("line one here\nline two\n\n>> new speaker talks\nmore words.\n")
        twice = _format_transcript_text(once)
        assert once == twice

    def test_markerless_wall_is_single_paragraph(self):
        out = _format_transcript_text("a\nb\nc\n")
        assert out == "a b c"


class TestSplitLongParagraph:
    def test_short_untouched(self):
        assert _split_long_paragraph("One sentence.") == ["One sentence."]

    def test_long_split_at_sentence_boundaries_under_cap(self):
        par = " ".join(f"Sentence number {i} here." for i in range(60))
        parts = _split_long_paragraph(par)
        assert len(parts) > 1
        assert all(len(p) <= 600 for p in parts)
        assert " ".join(parts) == par


class TestAssemble:
    def test_drops_empty_and_normalizes(self):
        assert _assemble_paragraphs(["  a \n b ", "", "   "]) == "a b"
