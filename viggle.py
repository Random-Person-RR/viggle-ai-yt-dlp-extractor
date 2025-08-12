import re
import json
import subprocess

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, float_or_none, int_or_none, determine_ext


class ViggleIE(InfoExtractor):
    _VALID_URL = r'https?://viggle\.ai/(?:[^/]+/)?(?P<id>[0-9a-f\-]+)'
    _API_URL = 'https://viggle.ai/api/share/video-task?id=%s'
    _URL_RE = re.compile(r'https?://[^\s"\'<>]+')

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
        """Use ffprobe to get media metadata. Returns parsed JSON dict or None."""
        cmd = [
            'ffprobe', '-v', 'error',
            '-print_format', 'json',
            '-show_format', '-show_streams',
        ]
        if ua:
            hdr = f"User-Agent: {ua}\r\n"
            cmd += ['-headers', hdr]
        cmd += [url]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.PIPE)
            return json.loads(raw)
        except subprocess.CalledProcessError as e:
            err = None
            try:
                err = e.stderr.decode(errors='ignore') if getattr(e, 'stderr', None) else None
            except Exception:
                err = None
            err = err or 'no stderr'
            if hasattr(self, '_downloader') and getattr(self, '_downloader', None):
                self._downloader.report_warning(f'[ViggleIE] ffprobe error on {url}:\n{err}')
            else:
                # fallback if no downloader present
                self.report_warning(f'[ViggleIE] ffprobe error on {url}:\n{err}')
        except Exception as e:
            if hasattr(self, '_downloader') and getattr(self, '_downloader', None):
                self._downloader.report_warning(f'[ViggleIE] unexpected ffprobe failure on {url}: {e}')
            else:
                self.report_warning(f'[ViggleIE] unexpected ffprobe failure on {url}: {e}')
        return None

    def _is_image_url(self, url):
        """Check if a URL points to a common image format."""
        if not isinstance(url, str):
            return False
        url = url.lower()
        img_exts = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.svg', '.ico')
        # Exclude media extensions that might appear in image-related fields
        non_img_exts = ('.mp4', '.mkv', '.webm', '.mp3', '.aac', '.flac', '.wav', '.3gp', '.mov')
        return url.endswith(img_exts) and not url.endswith(non_img_exts)

    def _choose_extension_from_ffprobe(self, info, vcodec, acodec, media_url):
        """
        Choose extension based on ffprobe's format_name and stream info.

        If both video and audio present and container list contains
        mov, mp4, m4a, 3gp, 3g2, mj2, prefer mp4 over mov/m4a/etc.

        Falls back to URL ext and codec-based defaults if needed.
        """
        fmt = (info.get('format') or {}).get('format_name') or ''
        candidates = [c.strip().lower() for c in fmt.split(',') if c.strip()]
        undesired = {'mov', 'm4a', '3gp', '3g2', 'mj2'}
        has_video = vcodec != 'none'
        has_audio = acodec != 'none'

        # If both video and audio streams present, prefer mp4 over certain containers
        if has_video and has_audio and candidates:
            if 'mp4' in candidates:
                return 'mp4'
            # prefer any candidate that is not in undesired
            filtered = [c for c in candidates if c not in undesired]
            if filtered:
                return filtered[0]
            # otherwise return the first candidate
            return candidates[0]

        # If only video or only audio, pick first candidate if available
        if candidates:
            return candidates[0]

        # fallback to the URL extension (determine_ext returns ext without dot or 'bin')
        url_ext = determine_ext(media_url)
        if url_ext and url_ext != 'bin':
            return url_ext

        # codec-based fallback
        if has_video:
            return 'mp4'
        if has_audio:
            return 'mp3'

        # last resort
        return 'bin'

    def _real_extract(self, url):
        video_id = self._match_id(url)
        ua = self._downloader.params.get('user-agent') or 'yt-dlp'
        headers = {'User-Agent': ua}

        # 1. Fetch JSON data from the API
        json_data = self._download_json(self._API_URL % video_id, video_id, headers=headers)
        data = json_data.get('data') or {}

        # 2. Collect all URLs from the JSON response
        url_list = []
        self._collect_urls(data, '', url_list)

        formats = []
        processed_urls = set()

        # 3. Probe and add all found media URLs as formats
        for key_path, media_url in url_list:
            if media_url in processed_urls:
                continue
            processed_urls.add(media_url)

            # Probe with ffprobe — definitive source for ext detection
            info = self._probe_with_ffprobe(media_url, ua)
            if not info or 'streams' not in info:
                continue

            vcodec = acodec = 'none'
            width = height = None
            for s in info['streams']:
                codec_type = s.get('codec_type')
                if codec_type == 'video':
                    vcodec = s.get('codec_name') or vcodec
                    width = int_or_none(s.get('width'))
                    height = int_or_none(s.get('height'))
                elif codec_type == 'audio':
                    acodec = s.get('codec_name') or acodec

            quality = 1 if key_path == 'result' else 0
            quality = 1 if key_path == 'template_processedHdURL' else 0

            # Use ffprobe's format_name (primary), with the special preference for mp4 when both audio+video
            ext = self._choose_extension_from_ffprobe(info, vcodec, acodec, media_url)

            formats.append({
                'format_id': key_path.replace('.', '_').replace('[', '_').replace(']', ''),
                'url': media_url,
                'ext': ext,
                'quality': quality,
                'vcodec': vcodec,
                'acodec': acodec,
                'width': width,
                'height': height,
                'duration': float_or_none((info.get('format') or {}).get('duration')),
                'filesize': int_or_none((info.get('format') or {}).get('size')),
            })

        if not formats:
            raise ExtractorError('No playable formats found in the API response.', expected=True)

        # 4. Collect thumbnails
        thumbnails = []
        thumb_urls = set()

        def add_thumb(thumb_id, thumb_url):
            if thumb_url and self._is_image_url(thumb_url) and thumb_url not in thumb_urls:
                thumbnails.append({'id': thumb_id, 'url': thumb_url})
                thumb_urls.add(thumb_url)

        for key_path, image_url in url_list:
            if self._is_image_url(image_url):
                add_thumb(key_path.replace('.', '_'), image_url)

        add_thumb('result_cover', data.get('resultCover'))

        # 5. Title selection
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
            'id': video_id,
            'title': title,
            'formats': formats,
            'thumbnails': thumbnails,
            'description': data.get('description') or (data.get('rap') or {}).get('lyrics'),
            'duration': float_or_none(data.get('videoDuration')),
            'uploader': user_info.get('nickname'),
        }