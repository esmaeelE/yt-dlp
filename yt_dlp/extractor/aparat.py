from .common import InfoExtractor
from ..utils import (
    determine_ext,
    get_element_by_id,
    int_or_none,
    merge_dicts,
    mimetype2ext,
    url_or_none,
)
from ..utils.traversal import traverse_obj


def _sanitize_aparat_title(title):
    """Strip filesystem-reserved characters out of a video title.

    Ported from apyrat's own `Downloader._sanitize_filename` (the original
    apyrat CLI tool this extractor is modeled on). Most notably strips ':',
    which Windows treats as an NTFS Alternate Data Stream separator — a
    Persian title such as "Part 1: Introduction" would otherwise get
    silently truncated to "Part 1" with no visible content past the colon
    when yt-dlp builds the output filename from %(title)s.

    Note: yt-dlp already does its own filename sanitization centrally at
    output-template time (see --windows-filenames), so this is normally
    redundant on Windows and unnecessary on Linux/macOS (where ':' is a
    valid filename character). This is here because it was asked for
    explicitly; if this were being upstreamed, sanitizing 'title' in the
    extractor itself would likely get pushback in review, since extractors
    are expected to return the raw title and let output-template handling
    take care of filesystem safety.
    """
    if not title:
        return title
    for char in '<>:"/\\|?*':
        title = title.replace(char, '')
    return title.strip().strip('.') or title


class AparatIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?aparat\.com/(?:v/|video/video/embed/videohash/)(?P<id>[a-zA-Z0-9]+)'
    _EMBED_REGEX = [r'<iframe .*?src="(?P<url>http://www\.aparat\.com/video/[^"]+)"']
    _API_BASE = 'https://www.aparat.com/api/fa/v1'

    _TESTS = [{
        'url': 'http://www.aparat.com/v/wP8On',
        'md5': '131aca2e14fe7c4dcb3c4877ba300c89',
        'info_dict': {
            'id': 'wP8On',
            'ext': 'mp4',
            'title': 'تیم گلکسی 11 - زومیت',
            'description': 'md5:096bdabcdcc4569f2b8a5e903a3b3028',
            'duration': 231,
            'timestamp': 1387394859,
            'upload_date': '20131218',
            'view_count': int,
        },
    }, {
        # multiple formats
        'url': 'https://www.aparat.com/v/8dflw/',
        'only_matching': True,
    }]

    def _parse_options(self, webpage, video_id, fatal=True):
        return self._parse_json(self._search_regex(
            r'options\s*=\s*({.+?})\s*;', webpage, 'options', default='{}'), video_id)

    def _formats_from_file_link_all(self, file_link_all, video_id):
        formats = []
        for file_link in traverse_obj(file_link_all, (lambda _, v: v.get('urls'))):
            file_url = traverse_obj(file_link, ('urls', 0, {url_or_none}))
            if not file_url:
                continue
            link_type = file_link.get('type') or ''
            profile = file_link.get('profile')
            if 'mpegurl' in link_type.lower() or determine_ext(file_url) == 'm3u8':
                formats.extend(self._extract_m3u8_formats(
                    file_url, video_id, 'mp4',
                    entry_protocol='m3u8_native', m3u8_id='hls', fatal=False))
            else:
                formats.append({
                    'url': file_url,
                    'ext': mimetype2ext(link_type) or determine_ext(file_url, 'mp4'),
                    'format_id': 'http-%s' % (profile or 'sd'),
                    'height': int_or_none(self._search_regex(
                        r'(\d+)[pP]', profile or '', 'height', default=None)),
                })
        return formats

    def _extract_via_api(self, video_id):
        """Primary extraction method: Aparat's own public JSON API.

        This is the same endpoint the apyrat CLI tool uses, and avoids
        relying on scraping an `options = {...}` JS blob or `og:title`
        meta tag out of the webpage, either of which can silently break
        if Aparat changes their page markup.
        """
        video_data = self._download_json(
            f'{self._API_BASE}/video/video/show/videohash/{video_id}',
            video_id, note='Downloading video API JSON', fatal=False)

        attrs = traverse_obj(video_data, ('data', 'attributes', {dict})) or {}
        if not attrs:
            return [], {}

        formats = self._formats_from_file_link_all(
            attrs.get('file_link_all'), video_id)
        if not formats:
            return [], {}

        info = {
            'title': _sanitize_aparat_title(attrs.get('title')),
            'description': attrs.get('description'),
            'thumbnail': url_or_none(
                attrs.get('big_poster') or attrs.get('poster')),
            'duration': int_or_none(attrs.get('duration')),
            'timestamp': int_or_none(attrs.get('sdate_rss') or attrs.get('start_time')),
            'view_count': int_or_none(attrs.get('visit_cnt_str') or attrs.get('visit_cnt')),
        }
        return formats, info

    def _extract_via_webpage(self, url, video_id):
        """Fallback extraction method: scrape the video/embed webpage.

        Used only if the API call above fails or returns no usable
        formats (e.g. the API shape has changed too, or the video is
        geo/age restricted in a way the API doesn't expose).
        """
        webpage = self._download_webpage(url, video_id, fatal=False)
        options = self._parse_options(webpage, video_id, fatal=False)

        if not options:
            webpage = self._download_webpage(
                'http://www.aparat.com/video/video/embed/vt/frame/showvideo/yes/videohash/' + video_id,
                video_id, 'Downloading embed webpage')
            options = self._parse_options(webpage, video_id)

        formats = []
        for sources in (options.get('multiSRC') or []):
            for item in sources:
                if not isinstance(item, dict):
                    continue
                file_url = url_or_none(item.get('src'))
                if not file_url:
                    continue
                item_type = item.get('type')
                if item_type == 'application/vnd.apple.mpegurl':
                    formats.extend(self._extract_m3u8_formats(
                        file_url, video_id, 'mp4',
                        entry_protocol='m3u8_native', m3u8_id='hls',
                        fatal=False))
                else:
                    ext = mimetype2ext(item.get('type'))
                    label = item.get('label')
                    formats.append({
                        'url': file_url,
                        'ext': ext,
                        'format_id': 'http-%s' % (label or ext),
                        'height': int_or_none(self._search_regex(
                            r'(\d+)[pP]', label or '', 'height',
                            default=None)),
                    })

        info = self._search_json_ld(webpage, video_id, default={})

        if not info.get('title'):
            info['title'] = get_element_by_id('videoTitle', webpage) or \
                self._html_search_meta(['og:title', 'twitter:title', 'DC.Title', 'title'], webpage, fatal=True)
        info['title'] = _sanitize_aparat_title(info.get('title'))

        return formats, merge_dicts(info, {
            'thumbnail': url_or_none(options.get('poster')),
            'duration': int_or_none(options.get('duration')),
        })

    def _real_extract(self, url):
        video_id = self._match_id(url)

        formats, info = self._extract_via_api(video_id)
        if not formats:
            formats, info = self._extract_via_webpage(url, video_id)

        return {
            **info,
            'id': video_id,
            'formats': formats,
        }


class AparatPlaylistIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?aparat\.com/playlist/(?P<id>\d+)'
    _API_BASE = 'https://www.aparat.com/api/fa/v1'

    _TESTS = [{
        'url': 'https://www.aparat.com/playlist/448747',
        'info_dict': {
            'id': '448747',
        },
        'playlist_mincount': 1,
    }]

    def _real_extract(self, url):
        playlist_id = self._match_id(url)
        playlist_data = self._download_json(
            f'{self._API_BASE}/video/playlist/one/playlist_id/{playlist_id}',
            playlist_id, 'Downloading playlist JSON')

        entries = [
            self.url_result(
                f'https://www.aparat.com/v/{uid}', AparatIE, uid,
                _sanitize_aparat_title(title))
            for uid, title in traverse_obj(playlist_data, (
                'included', lambda _, v: v['type'] == 'Video',
                'attributes', {lambda a: (a.get('uid'), a.get('title'))}))
            if uid
        ]

        title = _sanitize_aparat_title(
            traverse_obj(playlist_data, ('data', 'attributes', 'title')))

        return self.playlist_result(entries, playlist_id, title)
