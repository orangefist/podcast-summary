#!/usr/bin/env python3
import os
import re
import logging
import asyncio
import feedparser

# Import YouTubeTranscriptApi to extract captions
from youtube_transcript_api import YouTubeTranscriptApi

# Import Google Generative AI (Gemini)
import google.generativeai as genai

# Import Telegram Bot modules (v20+)
from telegram import Bot
from telegram.request.request import Request

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load sensitive values from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Make sure all required keys are provided
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    logging.error("Missing one or more required environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY")
    exit(1)

# Huberman Lab RSS feed URL
HUBERMAN_RSS_FEED = "https://feeds.megaphone.fm/hubermanlab"

# File to store the ID of the last processed episode
LAST_EPISODE_FILE = "last_episode.txt"

def extract_video_id(url):
    """Extract the YouTube video ID from a URL."""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    if match:
        return match.group(1)
    else:
        raise ValueError("Could not extract video ID from URL.")

def get_youtube_transcript(video_url):
    """Fetches and concatenates transcript text from the YouTube video."""
    video_id = extract_video_id(video_url)
    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
    transcript = " ".join(entry["text"] for entry in transcript_list)
    return transcript

def summarize_transcript(transcript):
    """Uses Google Gemini to generate a concise summary of the transcript."""
    # Configure Gemini with your API key (keys are loaded from environment variables)
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

async def post_to_telegram(summary, title, video_url):
    """Sends a message to the Telegram channel with episode info and summary."""
    # Create a default Request instance (you could customize httpx settings if needed)
    req = Request()
    bot = Bot(token=TELEGRAM_BOT_TOKEN, request=req)
    message = (
        f"New Huberman Lab Episode: {title}\n"
        f"Link: {video_url}\n\n"
        f"Summary:\n{summary}"
    )
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def check_new_episode():
    """Parses the RSS feed and returns the latest entry and its identifier."""
    feed = feedparser.parse(HUBERMAN_RSS_FEED)
    if not feed.entries:
        logging.info("No entries found in the RSS feed.")
        return None, None
    latest_entry = feed.entries[0]
    # Try using the 'yt_videoid' field; fall back to the 'id' field.
    latest_id = latest_entry.get("yt_videoid", latest_entry.get("id"))
    return latest_entry, latest_id

def load_last_episode_id():
    """Loads the ID of the last processed episode from file."""
    if os.path.exists(LAST_EPISODE_FILE):
        with open(LAST_EPISODE_FILE, "r") as f:
            return f.read().strip()
    return None

def save_last_episode_id(episode_id):
    """Saves the latest episode ID to file for future checks."""
    with open(LAST_EPISODE_FILE, "w") as f:
        f.write(episode_id)

async def main():
    # Check the RSS feed for a new episode
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
        video_url = episode.link
        title = episode.title
        try:
            transcript = get_youtube_transcript(video_url)
        except Exception as e:
            logging.error(f"Error fetching transcript: {e}")
            transcript = "Transcript unavailable."
        summary = summarize_transcript(transcript)
        await post_to_telegram(summary, title, video_url)

if __name__ == "__main__":
    asyncio.run(main())
