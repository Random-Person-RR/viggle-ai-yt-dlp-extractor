# How to install

- Go to your yt-dlp folder, then yt_dlp (not yt-dlp in yt-dlp because that doesn't exist in the git yet)
- Go to extractor
- Modify _extractors.py and add `from .viggle import ViggleIE`, can be at the bottom if you want it won't take a difference.
- Now you can compile yt-dlp and use Viggle links to download Viggle videos without the WebUI.
