# How to install

- Go to your yt-dlp folder, then yt_dlp (not yt-dlp in yt-dlp because that doesn't exist in the git yet)
- Go to extractor
- Modify _extractors.py and add `from .viggle import ViggleIE`
- Now you can compile yt-dlp and use Viggle links to download Viggle videos without the WebUI.
