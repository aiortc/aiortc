import mimetypes
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "htdocs")


async def app(scope, receive, send):
    """
    Demo ASGI application for use with the http3-server.py example.
    """
    assert scope["type"] == "http"

    path = scope["path"]

    # dynamically generated data, maximum 50MB
    size_match = re.match(r"^/(\d+)$", path)
    if size_match:
        size = min(50000000, int(size_match.group(1)))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"text/plain"]],
            }
        )
        await send({"type": "http.response.body", "body": b"Z" * size})
        return

    if path == "/":
        path = "/index.html"

    # static files
    file_match = re.match(r"^/([a-z0-9]+\.[a-z]+)$", path)
    if file_match:
        file_name = file_match.group(1)
        file_path = os.path.join(ROOT, file_match.group(1))
        try:
            with open(file_path, "rb") as fp:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            [
                                b"content-type",
                                mimetypes.guess_type(file_name)[0].encode("ascii"),
                            ]
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": fp.read()})
                return
        except OSError:
            pass

    # not found
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": b"Not Found"})
