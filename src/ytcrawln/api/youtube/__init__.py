from googleapiclient.discovery import build, Resource

def create_youtube_client(api_key: str) -> Resource:
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)