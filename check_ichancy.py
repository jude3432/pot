import os
import requests

url = "https://agents.ichancy100.com/login"
proxy_url = os.getenv("ICHANCY_PROXY_URL", "").strip()
request_kwargs = {
    "headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "identity",
    },
    "timeout": 20,
}
if proxy_url:
    request_kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
    print("iChancy connectivity probe: SOCKS5 proxy enabled")
else:
    print("iChancy connectivity probe: direct connection (ICHANCY_PROXY_URL is empty)")

try:
    response = requests.get(url, **request_kwargs)

    print(
        "iChancy status:",
        response.status_code,
        "content-type:",
        response.headers.get("content-type"),
        "server:",
        response.headers.get("server"),
    )
except Exception as exc:
    print("iChancy connection error:", repr(exc))
