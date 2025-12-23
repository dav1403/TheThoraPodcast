import os
import requests
from yt_dlp import YoutubeDL
from feedgen.feed import FeedGenerator

# --- CONFIGURATION ---
API_KEY = os.getenv('YOUTUBE_API_KEY')
CHANNEL_ID = 'UCxxxxxxxxxxxx' # Use the ID, not the @name
EMAIL = 'your-email@example.com'
BASE_URL = 'https://your-username.github.io/your-repo/'

def get_latest_video_url():
    # Official API call to get the latest video from the channel
    url = f"https://www.googleapis.com/youtube/v3/search?key={API_KEY}&channelId={CHANNEL_ID}&part=snippet,id&order=date&maxResults=1"
    response = requests.get(url).json()
    video_id = response['items'][0]['id']['videoId']
    return f"https://www.youtube.com/watch?v={video_id}", response['items'][0]['snippet']

def download_audio(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'episodes/%(title)s.%(ext)s',
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        'quiet': True,
        # No cookies needed here usually!
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

# ... rest of the RSS logic remains the same
