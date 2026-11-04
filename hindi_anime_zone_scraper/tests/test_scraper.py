import unittest

from scraper import (
    extract_languages,
    extract_qualities,
    is_adult_labelled,
    normalise_text,
    parse_anime_page,
)


SAMPLE_PAGE = """
<!doctype html>
<html>
<head><title>Sample Fantasy Anime Season 1 - Hindi Anime Zone</title></head>
<body>
  <article class="post">
    <h1>Sample Fantasy Anime Season 1 [Hindi-English-Jap] 480p, 720p &amp; 1080p</h1>
    <p>Dub: Hindi · English · Japanese</p>
    <h2>Series Info</h2>
    <p>Seasons</p><p>1</p>
    <p>Episodes</p><p>2</p>
    <p>Language</p><p>Hindi · English · Japanese</p>
    <h3>Episode 1</h3>
    <p>Hindi (Fan-Dub)-Jpn</p>
    <a href="https://media.example.invalid/file-480">480p x264</a>
    <a href="https://media.example.invalid/file-720">720p x264</a>
    <h3>Episode 2</h3>
    <p>Hindi (Fan-Dub)-Jpn</p>
    <a href="https://media.example.invalid/file-1080">1080p x265</a>
    <h2>More like this</h2>
    <a href="https://hindianimeszone.com/another-show/">Another Show Season 4 1080p</a>
  </article>
</body>
</html>
"""


class ParserTests(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(normalise_text("Solo-Leveling!"), "solo leveling")

    def test_quality_detection(self):
        self.assertEqual(extract_qualities("480p / 720P / 1080p / 4K"), ["480p", "720p", "1080p"])

    def test_language_detection(self):
        self.assertEqual(extract_languages("Hindi_Fandub · English · Jap Org"), ["Hindi", "English", "Japanese"])

    def test_parse_page_metadata_without_returning_media_links(self):
        result = parse_anime_page(SAMPLE_PAGE, "https://hindianimeszone.com/sample-fantasy-anime/")
        self.assertEqual(result.title, "Sample Fantasy Anime Season 1 [Hindi-English-Jap] 480p, 720p & 1080p")
        self.assertEqual(result.seasons, [1])
        self.assertEqual(result.seasons_declared, 1)
        self.assertEqual(result.episode_count_declared, 2)
        self.assertEqual([episode.number for episode in result.episodes], [1, 2])
        self.assertEqual(result.quality_labels, ["480p", "720p", "1080p"])
        self.assertIn("Japanese", result.audio_languages)
        self.assertEqual(result.authorised_download_links, [])
        self.assertFalse(hasattr(result, "media_urls"))

    def test_adult_label_filter(self):
        self.assertTrue(is_adult_labelled("18+ Example Series"))
        self.assertTrue(is_adult_labelled("Example Series uncensored"))
        self.assertFalse(is_adult_labelled("Solo Leveling Season 1"))

    def test_parser_rejects_adult_labelled_detail_page(self):
        page = "<html><body><article><h1>18+ Sample Title</h1></article></body></html>"
        with self.assertRaises(ValueError):
            parse_anime_page(page, "https://hindianimeszone.com/sample-title/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
