import re
import json
import subprocess
import os
from urllib.parse import urlparse

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, float_or_none, int_or_none, determine_ext

class ViggleIE(InfoExtractor):
    _VALID_URL = r'https?://viggle\.ai/s/(?P<id>[0-9a-f\-]+)'
    _API_URL   = 'https://viggle.ai/api/share/video-task?id=%s'
    _URL_RE    = re.compile(r'https?://[^\s"\'<>]+')

    def _collect_urls(self, obj, prefix, out):
        """Recursively find all URLs in the JSON data."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                nk = f"{prefix}.{k}" if prefix else k
                self._collect_urls(v, nk, out)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                nk = f"{prefix}[{i}]" if prefix else str(i)
                self._collect_urls(v, nk, out)
        elif isinstance(obj, str) and self._URL_RE.match(obj):
            out.append((prefix, obj))

    def _probe_with_ffprobe(self, url, ua):
        """Use ffprobe to get media metadata."""
        cmd = [
            'ffprobe', '-v', 'error',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            url
        ]
        if ua:
            hdr = f"User-Agent: {ua}\r\n"
            cmd.extend(['-headers', hdr])
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.PIPE)
            return json.loads(raw)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors='ignore') or 'no stderr'
            self._downloader.report_warning(f'[ViggleIE] ffprobe error on {url}:\n{err}')
        except Exception as e:
            self._downloader.report_warning(f'[ViggleIE] unexpected ffprobe failure on {url}: {e}')
        return None

    def _is_image_url(self, url):
        """Check if a URL points to a common image format."""
        if not isinstance(url, str):
            return False
        url = url.lower()
        img_exts = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.svg', '.ico')
        # Exclude media extensions that might be in image-related fields
        non_img_exts = ('.mp4', '.mkv', '.webm', '.mp3', '.aac', '.flac', '.wav', '.3gp', '.mov')
        return url.endswith(img_exts) and not url.endswith(non_img_exts)

    def _real_extract(self, url):
        video_id = self._match_id(url)
        ua       = self._downloader.params.get('user-agent') or 'yt-dlp'
        headers  = {'User-Agent': ua}

        # 1. Fetch JSON data from the API
        json_data = self._download_json(self._API_URL % video_id, video_id, headers=headers)
        data      = json_data.get('data') or {}

        # 2. Collect all URLs from the JSON response
        url_list = []
        self._collect_urls(data, '', url_list)

        formats = []
        # Use a set to avoid processing the same URL multiple times
        processed_urls = set()

        # 3. Probe and add all found media URLs as formats
        for key_path, media_url in url_list:
            if media_url in processed_urls:
                continue
            processed_urls.add(media_url)
            
            # REMOVED: No longer filtering by extension, ffprobe will validate media
            
            info = self._probe_with_ffprobe(media_url, ua)
            # If ffprobe fails or finds no streams, it's not a valid media format
            if not info or 'streams' not in info:
                continue

            vcodec = acodec = 'none'
            width = height = None
            for s in info['streams']:
                codec_type = s.get('codec_type')
                if codec_type == 'video':
                    vcodec = s.get('codec_name') or vcodec
                    width  = int_or_none(s.get('width'))
                    height = int_or_none(s.get('height'))
                elif codec_type == 'audio':
                    acodec = s.get('codec_name') or acodec

            # Prioritize the 'result' URL, which is the main video
            quality = 1 if key_path == 'result' else 0

            # Determine file extension from URL, with fallback to codec type
            ext = determine_ext(media_url)
            if ext == 'bin' or not ext: # 'bin' is yt-dlp's default for unknown
                ext = 'mp4' if vcodec != 'none' else 'mp3'

            formats.append({
                'format_id': key_path.replace('.', '_').replace('[', '_').replace(']', ''),
                'url':       media_url,
                'ext':       ext,
                'quality':   quality,
                'vcodec':    vcodec,
                'acodec':    acodec,
                'width':     width,
                'height':    height,
                'duration':  float_or_none(info['format'].get('duration')),
                'filesize':  int_or_none(info['format'].get('size')),
            })

        if not formats:
            raise ExtractorError('No playable formats found in the API response.', expected=True)

        # 4. Dynamically collect all available thumbnails
        thumbnails = []
        thumb_urls = set()

        def add_thumb(thumb_id, thumb_url):
            if thumb_url and self._is_image_url(thumb_url) and thumb_url not in thumb_urls:
                thumbnails.append({'id': thumb_id, 'url': thumb_url})
                thumb_urls.add(thumb_url)

        # Find all image URLs throughout the JSON
        for key_path, image_url in url_list:
             if self._is_image_url(image_url):
                 add_thumb(key_path.replace('.', '_'), image_url)
        
        # Add primary result cover as a fallback
        add_thumb('result_cover', data.get('resultCover'))

        # 5. Determine the best title from multiple possible fields
        template_info = data.get('template') or {}
        rap_info = data.get('rap') or {}
        user_info = data.get('user') or {}
        title = data.get('name') \
            or template_info.get('webCommand') \
            or template_info.get('command') \
            or rap_info.get('title') \
            or user_info.get('nickname') \
            or video_id

        return {
            'id':          video_id,
            'title':       title,
            'formats':     formats,
            'thumbnails':  thumbnails,
            'description': data.get('description') or (data.get('rap') or {}).get('lyrics'),
            'duration':    float_or_none(data.get('videoDuration')),
            'uploader':    user_info.get('nickname'),
        }