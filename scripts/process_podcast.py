import os
import sys
import requests
from yt_dlp import YoutubeDL
from feedgen.feed import FeedGenerator

# --- CONFIGURATION ---
API_KEY = os.getenv('YOUTUBE_API_KEY')
CHANNEL_ID = 'UC9YrWm1ef0uLV2EGOvXXAAw' 
EMAIL = 'thetorahpodcast@gmail.com'
BASE_URL = 'https://dav1403.github.io/TheThoraPodcast/' 

RSS_FILE = 'podcast.xml'
AUDIO_FOLDER = 'episodes'

if not os.path.exists(AUDIO_FOLDER):
    os.makedirs(AUDIO_FOLDER)

def get_latest_video_info():
    print(f"Querying YouTube API for Channel: {CHANNEL_ID}")
    url = f"https://www.googleapis.com/youtube/v3/search?key={API_KEY}&channelId={CHANNEL_ID}&part=snippet,id&order=date&maxResults=1&type=video"
    response = requests.get(url).json()
    
    if 'items' not in response or not response['items']:
        raise Exception(f"API Error or No Videos Found: {response}")
        
    video_id = response['items'][0]['id']['videoId']
    snippet = response['items'][0]['snippet']
    return f"https://www.youtube.com/watch?v={video_id}", snippet

def download_audio(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        # THIS IS THE FIX: Simulates official mobile apps to bypass bot checks
        'extractor_args': {'youtube': {'player_client': ['android', 'ios']}},
        'outtmpl': f'{AUDIO_FOLDER}/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return info['title']

def update_rss():
    fg = FeedGenerator()
    fg.load_extension('podcast')
    fg.title('The Thora Podcast')
    fg.author({'name': 'The Thora Team', 'email': EMAIL})
    fg.link(href=BASE_URL, rel='alternate')
    fg.description('Latest audio from YouTube channel converted for Spotify.')
    fg.language('fr') # Setting to French based on video title
    fg.podcast.itunes_category('Religion & Spirituality') 

    for file in os.listdir(AUDIO_FOLDER):
        if file.endswith(".mp3"):
            fe = fg.add_entry()
            fe.id(file)
            fe.title(file.replace(".mp3", ""))
            # URL Encodes spaces to prevent broken links
            file_url = f"{BASE_URL}{AUDIO_FOLDER}/{file}".replace(" ", "%20")
            fe.enclosure(file_url, 0, 'audio/mpeg')

    fg.rss_file(RSS_FILE)

if __name__ == "__main__":
    try:
        video_url, snippet = get_latest_video_info()
        print(f"Found latest video: {snippet['title']}")
        downloaded_title = download_audio(video_url)
        print(f"Successfully downloaded: {downloaded_title}")
        update_rss()
        print("RSS feed updated successfully.")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
