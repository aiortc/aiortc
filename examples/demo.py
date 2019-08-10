#
# demo application for http3-server.py
#

import os

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles

app = Starlette()


@app.route("/echo", methods=["POST"])
async def echo(request):
    content = await request.body()
    return Response(content)


@app.route("/{size:int}")
def padding(request):
    """
    Dynamically generated data, maximum 50MB.
    """
    size = min(50000000, request.path_params["size"])
    return PlainTextResponse("Z" * size)


app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "htdocs"), html=True),
)
