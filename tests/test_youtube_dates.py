"""Unit tests for the YouTube upload-date fallback parsers (pure, no network)."""
from kb.scrapers.youtube import _date_from_title, _extract_upload_date_from_html


class TestDateFromTitle:
    def test_compact_dmy(self):
        # The two real scars that motivated this parser.
        assert _date_from_title(
            "【Honey Bees】英國中、小學生的快樂學習⋯放學就等於放假！ | 15May2022"
        ) == "20220515"
        assert _date_from_title(
            "【中字）美國選果檢定(第三波)】拜登公開指示「去馬」，搖擺兩州即翻盤 | 6Nov2020"
        ) == "20201106"

    def test_spaced_dmy_with_ordinal(self):
        assert _date_from_title("Market Wrap 3rd Sep 2025") == "20250903"
        assert _date_from_title("Market Wrap 15 May, 2026") == "20260515"

    def test_mdy(self):
        assert _date_from_title("Something Big: May 15, 2022 Recap") == "20220515"
        assert _date_from_title("Sept 21 2025 episode") == "20250921"

    def test_iso(self):
        assert _date_from_title("Weekly review 2026-08-02") == "20260802"

    def test_no_date(self):
        assert _date_from_title("The Venezuela Crisis: State Of Disaster | Documentary") is None
        assert _date_from_title("MacroVoices #545 Michael Howell") is None
        assert _date_from_title("") is None
        assert _date_from_title(None) is None

    def test_out_of_youtube_era_rejected(self):
        assert _date_from_title("Wall Street history: 15May 1987 crash") is None
        assert _date_from_title("2030 vision: 1 Jan 2030") is None

    def test_year_only_not_matched(self):
        assert _date_from_title("Best of 2024 compilation") is None


class TestExtractUploadDateFromHtml:
    # Shape of the initial player response embedded in a /watch page.
    _HTML = (
        '<script>var ytInitialPlayerResponse = {"videoDetails":{},'
        '"microformat":{"playerRenderer":{"microformatDataRenderer":{'
        '"publishDate":"2026-08-11T00:00:00-07:00",'
        '"uploadDate":"2026-08-11T00:00:00-07:00",'
        '"title":"Some video"}}}};</script>'
    )

    def test_extracts_upload_date(self):
        assert _extract_upload_date_from_html(self._HTML) == "20260811"

    def test_publish_date_only(self):
        assert _extract_upload_date_from_html(
            '"publishDate":"2022-05-15T10:00:00Z"') == "20220515"

    def test_missing(self):
        assert _extract_upload_date_from_html("<html>consent page</html>") is None
        assert _extract_upload_date_from_html("") is None

    def test_invalid_date_rejected(self):
        assert _extract_upload_date_from_html('"uploadDate":"1987-02-30"') is None
