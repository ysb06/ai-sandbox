from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="ytcrawl review")

@app.get("/", response_class=HTMLResponse)
def hello_world() -> str:
    return """
    <!doctype html>
    <html lang="ko">
      <head>
        <meta charset="utf-8">
        <title>ytcrawl review</title>
      </head>
      <body>
        <h1>Hello World!</h1>
      </body>
    </html>
    """