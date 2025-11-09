#!/usr/bin/env python3
import os
import re
import json
import logging
import asyncio
import feedparser
import requests
from bs4 import BeautifulSoup
import time

from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from telegram import Bot
from telegram.constants import ParseMode
import nest_asyncio

# Apply nest_asyncio to allow nested event loops (useful in notebooks or environments with running loops)
nest_asyncio.apply()

# Set up basic logging
logging.basicConfig(level=logging.INFO)

# Load sensitive credentials from environment variables
if os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    logging.error("Missing one or more required environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY")
    exit(1)

# Define constants
HUBERMAN_RSS_FEED = "https://feeds.megaphone.fm/hubermanlab"
LAST_EPISODE_FILE = "last_episode.txt"

ytt_api = YouTubeTranscriptApi()

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


def summarize_transcript(transcript, system_prompt, model = 'gemini-2.5-flash-lite'):
    """
    Summarizes the provided transcript using the Google Gemini model.
    """
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(model)
    prompt = (
        system_prompt +
        transcript 
    )
    response = model.generate_content(prompt)
    summary = response.text.strip()
    return summary

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

async def post_to_telegram(summary, title, youtube_link):
    """
    Posts the episode title, YouTube link, and summary to the Telegram channel.
    Automatically splits the message if it's longer than 4096 characters.
    """
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Construct the main message
    message = (
        f"*New Huberman Lab Episode:* {title}\n"
        f"[Watch here]({youtube_link})\n\n"
        f"*Summary:*\n{summary}"
    )
    
    # Split long messages
    message_parts = split_message(message)

    # Send messages one by one
    for part in message_parts:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=part, parse_mode="Markdown")
        await asyncio.sleep(1)  # Prevent rate-limiting

def split_message(message, max_length=4096):
    """
    Splits a long message into smaller chunks without breaking words or formatting.
    """
    parts = []
    while len(message) > max_length:
        split_index = message[:max_length].rfind("\n")  # Try to split at newline
        if split_index == -1:  # If no newline, split at max_length
            split_index = max_length
        
        parts.append(message[:split_index].strip())
        message = message[split_index:].strip()  # Remove sent part

    parts.append(message)  # Add last chunk
    return parts

def clean_summary(summary):
    """
    Cleans up the summary by removing unnecessary characters and tags.
    """
    # Remove "```html" and "```" from the beginning and end of the summary
    summary = re.sub(r"```markdown", "", summary)
    summary = re.sub(r"```", "", summary)
    summary = summary.strip()
    return summary


async def main():
    episode, latest_id = check_new_episode()
    if episode is None:
        logging.info("No episode found in the RSS feed.")

    last_episode_id = load_last_episode_id()
    if latest_id == last_episode_id:
        logging.info("No new episode found.")
        #return
    else:
        logging.info("New episode found!")
        save_last_episode_id(latest_id)
    # The episode link from the RSS feed points to the Huberman Lab page.
    page_url = episode.link
    title = episode.title

    # Extract the YouTube video ID from the episode page
    youtube_video_id = extract_youtube_video_id_from_url(page_url)
    #youtube_video_id = "c9JmHOUp6VU"
    if youtube_video_id is None:
        raise ValueError("No YouTube video found on the page.")
    else:
        # Build a standard YouTube watch URL from the video ID
        youtube_link = f"https://www.youtube.com/watch?v={youtube_video_id}"
        try:
            transcript_list = ytt_api.fetch(youtube_video_id, languages=['en'])
            transcript = " ".join(entry.text for entry in transcript_list.snippets)
        except Exception as e:
            raise ValueError(f"Error fetching transcript: {e}")

        with open("system_prompt.txt", "r") as file:
            system_prompt = file.read()
        summary = summarize_transcript(transcript, system_prompt)
        cleaned_summary = clean_summary(summary)
        print(cleaned_summary)
        await post_to_telegram(cleaned_summary, title, youtube_link)


if __name__ == "__main__":
    asyncio.run(main())
