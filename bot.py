#!/usr/bin/env python3
import os
import re
import json
import logging
import asyncio
import feedparser
import requests
from bs4 import BeautifulSoup

from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from telegram import Bot
import nest_asyncio

# Apply nest_asyncio to allow nested event loops (useful in notebooks or environments with running loops)
nest_asyncio.apply()

# Set up basic logging
logging.basicConfig(level=logging.INFO)

# Load sensitive credentials from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    logging.error("Missing one or more required environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY")
    exit(1)

# Define constants
HUBERMAN_RSS_FEED = "https://feeds.megaphone.fm/hubermanlab"
LAST_EPISODE_FILE = "last_episode.txt"


def extract_youtube_video_id_from_url(site_url):
    """
    Given a Huberman Lab episode URL, fetch the page HTML,
    parse its JSON‑LD structured data, and extract the YouTube video ID.
    """
    response = requests.get(site_url, verify = False)
    response.raise_for_status()
    html_content = response.text
    soup = BeautifulSoup(html_content, "html.parser")

    # Look for JSON‑LD script tags
    json_ld_tags = soup.find_all("script", type="application/ld+json")
    youtube_url = None
    for tag in json_ld_tags:
        try:
            data = json.loads(tag.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "VideoObject" and "embedUrl" in item:
                    youtube_url = item["embedUrl"]
                    break
            if youtube_url:
                break
        except Exception:
            continue

    if youtube_url:
        # Extract the 11-character video ID using regex
        match = re.search(r"/embed/([A-Za-z0-9_-]{11})", youtube_url)
        if match:
            return match.group(1)
    return None


def summarize_transcript(transcript):
    """
    Summarizes the provided transcript using the Google Gemini model.
    """
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = (
        "Summarize the following podcast transcript:\n\n" +
        transcript +
        "\n\nProvide a concise and informative summary."
    )
    response = model.generate_content(prompt)
    summary = response.text.strip()
    return summary


async def post_to_telegram(summary, title, youtube_link):
    """
    Posts the episode title, YouTube link, and summary to your Telegram channel.
    """
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    message = (
        f"New Huberman Lab Episode: {title}\n"
        f"Watch here: {youtube_link}\n\n"
        f"Summary:\n{summary}"
    )
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)


def check_new_episode():
    """
    Parses the Huberman Lab RSS feed and returns the latest entry and its identifier.
    """
    feed = feedparser.parse(HUBERMAN_RSS_FEED)
    if not feed.entries:
        logging.info("No entries found in feed.")
        return None, None
    latest_entry = feed.entries[0]
    # Use 'yt_videoid' if available; otherwise, fall back to the 'id' field.
    latest_id = latest_entry.get("yt_videoid", latest_entry.get("id"))
    return latest_entry, latest_id


def load_last_episode_id():
    """
    Loads the ID of the last processed episode from a file.
    """
    if os.path.exists(LAST_EPISODE_FILE):
        with open(LAST_EPISODE_FILE, "r") as f:
            return f.read().strip()
    return None


def save_last_episode_id(episode_id):
    """
    Saves the latest episode ID to file for future checks.
    """
    with open(LAST_EPISODE_FILE, "w") as f:
        f.write(episode_id)

def extract_video_id(url):
    """
    Extracts the video ID from a YouTube URL.
    Note: This is a basic implementation and may not cover all URL formats.
    """
    # Try to match the standard URL format.
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    if match:
        return match.group(1)
    else:
        raise ValueError("Invalid YouTube URL or unable to extract video ID.")

def get_youtube_transcript(video_url):
    """
    Fetches the transcript of a YouTube video using its video ID.
    Returns the transcript as a single concatenated string.
    """
    video_id = extract_video_id(video_url)
    # Retrieve the transcript (if available)
    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
    # Concatenate the transcript pieces into a single string.
    transcript = " ".join([entry["text"] for entry in transcript_list])
    return transcript


async def main():
    episode, latest_id = check_new_episode()
    if episode is None:
        logging.info("No episode found in the RSS feed.")
        return

    last_episode_id = load_last_episode_id()
    if latest_id == last_episode_id:
        logging.info("No new episode found.")
        return
    else:
        logging.info("New episode found!")
        save_last_episode_id(latest_id)
        # The episode link from the RSS feed points to the Huberman Lab page.
        page_url = episode.link
        title = episode.title

        # Extract the YouTube video ID from the episode page
        youtube_video_id = extract_youtube_video_id_from_url(page_url)
        if youtube_video_id is None:
            logging.error("No YouTube video found on the page.")
            transcript = "Transcript unavailable."
            # Fallback: use the original page URL
            youtube_link = page_url
        else:
            # Build a standard YouTube watch URL from the video ID
            print(youtube_video_id)
            youtube_link = f"https://www.youtube.com/watch?v={youtube_video_id}"
            print(youtube_link)
            try:
                # transcript_list = YouTubeTranscriptApi.get_transcript(youtube_video_id)
                # transcript = " ".join(entry["text"] for entry in transcript_list)
                transcript = get_youtube_transcript(youtube_link)
            except Exception as e:
                logging.error(f"Error fetching transcript: {e}")
                transcript = "Transcript unavailable."
        summary = summarize_transcript(transcript)
        await post_to_telegram(summary, title, youtube_link)


if __name__ == "__main__":
    asyncio.run(main())
