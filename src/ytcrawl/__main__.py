import json
import os

from dotenv import load_dotenv
from googleapiclient.discovery import build, Resource

if __name__ == "__main__":
    load_dotenv()

    api_key = os.environ.get("YOUTUBE_API_KEY")
    gcloud_youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
    gcloud_youtube_search = gcloud_youtube.search()
    fixed_query = {
        "part": "snippet",
        "type": "video",
        "maxResults": 50,
        "regionCode": "KR",
        "safeSearch": "none",
        "videoLicense": "creativeCommon",
    }

    results_raw = gcloud_youtube_search.list(
        publishedAfter="2026-06-19T00:00:00Z",
        **fixed_query,
    ).execute(num_retries=0)
    results = json.dumps(results_raw, ensure_ascii=False, indent=2) 
    with open("results/result.json", "w", encoding="utf-8") as f:
        f.write(results)
    
