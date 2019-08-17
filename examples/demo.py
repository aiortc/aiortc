#
# demo application for http3_server.py
#

import os

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles

app = Starlette()


@app.route("/echo", methods=["POST"])
async def echo(request):
    """
    HTTP echo endpoint.
    """
    content = await request.body()
    return Response(content)


@app.route("/{size:int}")
def padding(request):
    """
    Dynamically generated data, maximum 50MB.
    """
    size = min(50000000, request.path_params["size"])
    return PlainTextResponse("Z" * size)


@app.websocket_route("/ws")
async def ws(websocket):
    """
    WebSocket echo endpoint.
    """
    await websocket.accept()
    message = await websocket.receive_text()
    await websocket.send_text(message)
    await websocket.close()


app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "htdocs"), html=True),
)
